import json
import logging

import frappe
from frappe.utils import nowdate, flt, cint

from catalog_extensions.install_support import is_doctype_available, is_optional_app_installed
from catalog_extensions import order_billing
from catalog_extensions.simple_checkout import PAYMENT_MODE_COD, PAYMENT_MODE_PREPAID, get_payment_mode_for_doc
from erpnext_shipping_extended.shipment_provider_fields import get_external_shipment_id

FULFILLMENT_MARKER = "[catalog_extensions_fulfillment]"
PICKUP_MARKER = "[catalog_extensions_pickup]"
DELIVERY_REPAIR_MARKER = "[catalog_extensions_delivery_repair]"
DEFAULT_PARCEL = {
    "length": 10,
    "width": 10,
    "height": 10,
    "weight": 0.5,
    "count": 1,
}


class DeliveryNoteStockBlockedError(Exception):
    def __init__(self, delivery_note_name: str | None, delivery_note_created: bool, original_exception: Exception):
        super().__init__(str(original_exception))
        self.delivery_note_name = delivery_note_name
        self.delivery_note_created = delivery_note_created
        self.original_exception = original_exception


def _debug_log(message: str, **context) -> None:
    payload = ", ".join(f"{key}={value}" for key, value in context.items() if value not in (None, ""))
    if payload:
        message = f"{message} | {payload}"
    try:
        frappe.logger("catalog_extensions.fulfillment").info(message)
    except Exception:
        logging.getLogger("catalog_extensions.fulfillment").info(message)


def _has_comment(reference_doctype: str, reference_name: str, marker: str) -> bool:
    return bool(
        frappe.db.exists(
            "Comment",
            {
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "content": ["like", f"%{marker}%"],
            },
        )
    )


def _add_comment_once(doc, marker: str, message: str) -> None:
    if _has_comment(doc.doctype, doc.name, marker):
        return
    doc.flags.ignore_permissions = True
    doc.add_comment("Comment", f"{marker} {message}")


def _db_set_if_present(doc, values: dict) -> None:
    filtered = {key: value for key, value in values.items() if value is not None}
    if not filtered:
        return
    if hasattr(doc, "db_set"):
        doc.db_set(filtered)
        return
    for key, value in filtered.items():
        setattr(doc, key, value)


def _set_doc_value(doc, key: str, value) -> None:
    setattr(doc, key, value)
    if hasattr(doc, "_values") and isinstance(getattr(doc, "_values"), dict):
        doc._values[key] = value


def _set_child_value(row, key: str, value) -> None:
    if isinstance(row, dict):
        row[key] = value
        return
    setattr(row, key, value)


def _get_child_value(row, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _get_existing_delivery_note_name(sales_order_name: str) -> str | None:
    rows = frappe.db.sql(
        """
        SELECT dn.name
        FROM `tabDelivery Note` dn
        INNER JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        WHERE dn.docstatus < 2
          AND dni.against_sales_order = %s
        ORDER BY dn.creation DESC
        LIMIT 1
        """,
        (sales_order_name,),
        as_dict=True,
    )
    return rows[0]["name"] if rows else None


def _is_negative_stock_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "NegativeStockError"


def _submit_delivery_note(delivery_note, *, created: bool):
    try:
        delivery_note.submit()
    except Exception as exc:
        if _is_negative_stock_error(exc):
            raise DeliveryNoteStockBlockedError(delivery_note.name, created, exc) from exc
        raise
    return delivery_note, created


def _get_existing_shipment_name(delivery_note_name: str) -> str | None:
    if not is_doctype_available("Shipment"):
        return None

    rows = frappe.db.sql(
        """
        SELECT s.name
        FROM `tabShipment` s
        INNER JOIN `tabShipment Delivery Note` sdn ON sdn.parent = s.name
        WHERE s.docstatus < 2
          AND sdn.delivery_note = %s
        ORDER BY s.creation DESC
        LIMIT 1
        """,
        (delivery_note_name,),
        as_dict=True,
    )
    return rows[0]["name"] if rows else None


def _get_linked_delivery_note_names(shipment_doc) -> list[str]:
    delivery_notes = []
    for row in shipment_doc.get("shipment_delivery_note") or []:
        delivery_note = getattr(row, "delivery_note", None)
        if not delivery_note and isinstance(row, dict):
            delivery_note = row.get("delivery_note")
        if delivery_note and delivery_note not in delivery_notes:
            delivery_notes.append(delivery_note)
    return delivery_notes


def _get_sales_order_names_for_delivery_notes(delivery_note_names: list[str]) -> list[str]:
    if not delivery_note_names:
        return []

    rows = frappe.get_all(
        "Delivery Note Item",
        filters={"parent": ["in", delivery_note_names]},
        fields=["against_sales_order"],
    )
    sales_orders = []
    for row in rows:
        sales_order = row.get("against_sales_order")
        if sales_order and sales_order not in sales_orders:
            sales_orders.append(sales_order)
    return sales_orders


def _get_delivery_note_doc(order_doc):
    existing_name = _get_existing_delivery_note_name(order_doc.name)
    if existing_name:
        delivery_note = frappe.get_doc("Delivery Note", existing_name)
        if delivery_note.docstatus == 1:
            return delivery_note, False
        delivery_note.flags.ignore_permissions = True
        return _submit_delivery_note(delivery_note, created=False)

    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

    delivery_note = make_delivery_note(order_doc.name)
    delivery_note.flags.ignore_permissions = True
    delivery_note.insert(ignore_permissions=True)
    return _submit_delivery_note(delivery_note, created=True)


def _get_shipment_content_description(order_doc, delivery_note_doc=None) -> str:
    items = []
    if delivery_note_doc:
        items = list(getattr(delivery_note_doc, "items", None) or delivery_note_doc.get("items") or [])
    if not items:
        items = list(getattr(order_doc, "items", None) or order_doc.get("items") or [])

    for row in items:
        item_name = _get_child_value(row, "item_name") or _get_child_value(row, "item_code")
        if item_name:
            return str(item_name)

    return f"Webshop order {order_doc.name}"


def _ensure_shipment_defaults(shipment_doc, order_doc, delivery_note_doc=None) -> None:
    _set_doc_value(shipment_doc, "pickup_type", shipment_doc.get("pickup_type") or "Pickup")
    _set_doc_value(shipment_doc, "shipment_type", shipment_doc.get("shipment_type") or "Goods")
    _set_doc_value(shipment_doc, "pallets", shipment_doc.get("pallets") or "No")
    _set_doc_value(shipment_doc, "pickup_date", shipment_doc.get("pickup_date") or nowdate())
    _set_doc_value(shipment_doc, "pickup_from", shipment_doc.get("pickup_from") or "09:00:00")
    _set_doc_value(shipment_doc, "pickup_to", shipment_doc.get("pickup_to") or "18:00:00")
    _set_doc_value(
        shipment_doc,
        "description_of_content",
        shipment_doc.get("description_of_content") or _get_shipment_content_description(order_doc, delivery_note_doc),
    )
    shipment_parcels = list(shipment_doc.get("shipment_parcel") or [])
    if not shipment_parcels:
        shipment_doc.append("shipment_parcel", DEFAULT_PARCEL.copy())
        return

    for row in shipment_parcels:
        for key, default_value in DEFAULT_PARCEL.items():
            if not _get_child_value(row, key):
                _set_child_value(row, key, default_value)

    payment_mode = get_payment_mode_for_doc(order_doc)
    if payment_mode == PAYMENT_MODE_COD:
        cod_amount = flt(order_doc.get("rounded_total") or order_doc.get("grand_total") or order_doc.get("base_grand_total") or 0)
        for key, value in (
            ("payment_type", "COD"),
            ("is_cod", 1),
            ("cod_amount", cod_amount),
            ("webshop_payment_mode", PAYMENT_MODE_COD),
        ):
            try:
                _set_doc_value(shipment_doc, key, value)
            except Exception:
                continue
    else:
        for key, value in (
            ("payment_type", "Prepaid"),
            ("is_cod", 0),
            ("cod_amount", 0),
            ("webshop_payment_mode", PAYMENT_MODE_PREPAID),
        ):
            try:
                _set_doc_value(shipment_doc, key, value)
            except Exception:
                continue


def apply_webshop_shipment_defaults(doc, method=None) -> None:
    if getattr(doc, "doctype", None) != "Shipment":
        return

    delivery_note_names = _get_linked_delivery_note_names(doc)
    if not delivery_note_names:
        return

    delivery_note_name = delivery_note_names[0]
    if not frappe.db.exists("Delivery Note", delivery_note_name):
        return

    delivery_note_doc = frappe.get_doc("Delivery Note", delivery_note_name)
    sales_order_name = _get_sales_order_name_for_delivery_note(delivery_note_doc)
    if not sales_order_name or not frappe.db.exists("Sales Order", sales_order_name):
        return

    order_doc = frappe.get_doc("Sales Order", sales_order_name)

    _ensure_shipment_defaults(doc, order_doc, delivery_note_doc)


def _get_shipment_doc(order_doc, delivery_note_doc):
    existing_name = _get_existing_shipment_name(delivery_note_doc.name)
    if existing_name:
        _debug_log(
            "Reusing existing shipment for delivery note",
            sales_order=order_doc.name,
            delivery_note=delivery_note_doc.name,
            shipment=existing_name,
        )
        shipment_doc = frappe.get_doc("Shipment", existing_name)
        _ensure_shipment_defaults(shipment_doc, order_doc, delivery_note_doc)
        return shipment_doc, False

    from erpnext.stock.doctype.delivery_note.delivery_note import make_shipment

    _debug_log(
        "Creating shipment from delivery note",
        sales_order=order_doc.name,
        delivery_note=delivery_note_doc.name,
    )
    shipment_doc = make_shipment(delivery_note_doc.name)
    _ensure_shipment_defaults(shipment_doc, order_doc, delivery_note_doc)
    _debug_log(
        "Prepared shipment defaults before insert",
        sales_order=order_doc.name,
        delivery_note=delivery_note_doc.name,
        pickup_date=shipment_doc.get("pickup_date"),
        description_of_content=shipment_doc.get("description_of_content"),
        parcel_count=len(shipment_doc.get("shipment_parcel") or []),
        first_parcel_weight=_get_child_value((shipment_doc.get("shipment_parcel") or [None])[0], "weight")
        if shipment_doc.get("shipment_parcel")
        else None,
    )
    shipment_doc.flags.ignore_permissions = True
    try:
        shipment_doc.insert(ignore_permissions=True)
        shipment_doc.submit()
    except Exception:
        _debug_log(
            "Shipment insert or submit failed",
            sales_order=order_doc.name,
            delivery_note=delivery_note_doc.name,
            pickup_date=shipment_doc.get("pickup_date"),
            description_of_content=shipment_doc.get("description_of_content"),
            parcel_count=len(shipment_doc.get("shipment_parcel") or []),
            first_parcel_weight=_get_child_value((shipment_doc.get("shipment_parcel") or [None])[0], "weight")
            if shipment_doc.get("shipment_parcel")
            else None,
        )
        raise
    _debug_log(
        "Shipment created successfully",
        sales_order=order_doc.name,
        delivery_note=delivery_note_doc.name,
        shipment=shipment_doc.name,
    )
    return shipment_doc, True


def _is_fully_paid_prepaid_order(order_doc) -> bool:
    order_total = flt(order_doc.get("base_rounded_total") or order_doc.get("base_grand_total") or 0)
    if order_total <= 0:
        return False
    return flt(order_doc.get("advance_paid") or 0) >= (order_total - 0.01)


def is_order_ready_for_fulfillment(order_doc) -> bool:
    payment_mode = get_payment_mode_for_doc(order_doc)
    if payment_mode == PAYMENT_MODE_COD:
        return True
    return _is_fully_paid_prepaid_order(order_doc)


def _is_shipment_delivered(shipment_doc) -> bool:
    normalized = str(shipment_doc.get("normalized_tracking_status") or "").strip().upper()
    tracking_status = str(shipment_doc.get("tracking_status") or "").strip().lower()
    shipment_status = str(shipment_doc.get("status") or "").strip().lower()
    return (
        normalized == "DELIVERED"
        or tracking_status == "delivered"
        or ("deliver" in tracking_status and "out for" not in tracking_status)
        or (shipment_status == "completed" and tracking_status == "delivered")
    )


def _all_shipments_delivered_for_sales_order(sales_order_name: str) -> bool:
    if not is_doctype_available("Shipment"):
        return False

    rows = frappe.db.sql(
        """
        SELECT DISTINCT
            s.name,
            s.status,
            s.tracking_status,
            s.normalized_tracking_status
        FROM `tabShipment` s
        INNER JOIN `tabShipment Delivery Note` sdn ON sdn.parent = s.name
        INNER JOIN `tabDelivery Note Item` dni ON dni.parent = sdn.delivery_note
        WHERE s.docstatus = 1
          AND dni.against_sales_order = %s
        """,
        (sales_order_name,),
        as_dict=True,
    )
    if not rows:
        return False
    return all(_is_shipment_delivered(row) for row in rows)


def _get_best_service_info(shipment_doc):
    if not is_optional_app_installed("erpnext_shipping_extended"):
        return None

    try:
        from erpnext_shipping_extended.api.shipping_extended import fetch_shipping_rates
    except Exception:
        return None

    try:
        rates = fetch_shipping_rates(
            pickup_from_type=shipment_doc.pickup_from_type,
            delivery_to_type=shipment_doc.delivery_to_type,
            pickup_address_name=shipment_doc.pickup_address_name,
            delivery_address_name=shipment_doc.delivery_address_name,
            parcels=json.dumps(shipment_doc.get("shipment_parcel") or []),
            description_of_content=shipment_doc.description_of_content,
            pickup_date=str(shipment_doc.pickup_date),
            value_of_goods=flt(shipment_doc.value_of_goods),
            pickup_contact_name=shipment_doc.pickup_contact_name,
            delivery_contact_name=shipment_doc.delivery_contact_name,
        )
    except Exception:
        frappe.log_error(title="Catalog fulfillment: fetch shipping rates failed", message=frappe.get_traceback())
        return None

    if not rates:
        return None

    return rates[0]


def _queue_dispatch(shipment_doc, service_info) -> dict | None:
    if not is_optional_app_installed("erpnext_shipping_extended"):
        return None

    try:
        from erpnext_shipping_extended.api.shipping_extended import create_shipment
    except Exception:
        return None

    delivery_notes = [row.delivery_note for row in shipment_doc.get("shipment_delivery_note") or [] if row.delivery_note]
    return create_shipment(
        shipment=shipment_doc.name,
        pickup_from_type=shipment_doc.pickup_from_type,
        delivery_to_type=shipment_doc.delivery_to_type,
        pickup_address_name=shipment_doc.pickup_address_name,
        delivery_address_name=shipment_doc.delivery_address_name,
        shipment_parcel=json.dumps(shipment_doc.get("shipment_parcel") or []),
        description_of_content=shipment_doc.description_of_content,
        pickup_date=str(shipment_doc.pickup_date),
        value_of_goods=flt(shipment_doc.value_of_goods),
        service_data=json.dumps(service_info),
        pickup_contact_name=shipment_doc.pickup_contact_name,
        delivery_contact_name=shipment_doc.delivery_contact_name,
        delivery_notes=json.dumps(delivery_notes),
    )


def _finalize_delivery_note(delivery_note_doc, shipment_doc) -> None:
    tracking_update = {
        "tracking_number": shipment_doc.get("awb_number"),
        "tracking_url": shipment_doc.get("tracking_url"),
        "tracking_status": shipment_doc.get("tracking_status"),
        "tracking_status_info": shipment_doc.get("tracking_status_info"),
    }
    _db_set_if_present(delivery_note_doc, tracking_update)
    _add_comment_once(
        delivery_note_doc,
        order_billing.DELIVERY_COMPLETE_MARKER,
        f"Delivery completion confirmed from Shipment {shipment_doc.name}.",
    )
    if hasattr(delivery_note_doc, "update_status"):
        try:
            delivery_note_doc.update_status("Completed")
        except Exception:
            frappe.log_error(title="Catalog fulfillment: delivery note completion failed", message=frappe.get_traceback())


def _finalize_sales_order(order_doc, shipment_doc) -> None:
    _add_comment_once(
        order_doc,
        order_billing.DELIVERY_COMPLETE_MARKER,
        f"Delivery completion confirmed from Shipment {shipment_doc.name}.",
    )
    if hasattr(order_doc, "update_status"):
        try:
            order_doc.update_status("Completed")
        except Exception:
            frappe.log_error(title="Catalog fulfillment: sales order completion failed", message=frappe.get_traceback())
    order_billing.create_sales_invoice_for_fully_paid_webshop_order(order_doc)


def finalize_delivered_webshop_order_from_shipment(shipment_doc, event_source: str = "tracking") -> bool:
    if not _is_shipment_delivered(shipment_doc):
        return False

    delivery_note_names = _get_linked_delivery_note_names(shipment_doc)
    if not delivery_note_names:
        _add_comment_once(
            shipment_doc,
            DELIVERY_REPAIR_MARKER,
            f"Delivered {event_source} update could not resolve a linked Delivery Note. Manual repair is required.",
        )
        return False

    completed_delivery_notes = []
    for delivery_note_name in delivery_note_names:
        if not frappe.db.exists("Delivery Note", delivery_note_name):
            continue
        delivery_note_doc = frappe.get_doc("Delivery Note", delivery_note_name)
        if cint(delivery_note_doc.get("is_return")):
            continue
        _finalize_delivery_note(delivery_note_doc, shipment_doc)
        completed_delivery_notes.append(delivery_note_name)

    if not completed_delivery_notes:
        return False

    sales_order_names = _get_sales_order_names_for_delivery_notes(completed_delivery_notes)
    if not sales_order_names:
        _add_comment_once(
            shipment_doc,
            DELIVERY_REPAIR_MARKER,
            f"Delivered {event_source} update could not resolve a linked webshop Sales Order. Manual repair is required.",
        )
        return False

    finalized = False
    for sales_order_name in sales_order_names:
        if not frappe.db.exists("Sales Order", sales_order_name):
            continue
        order_doc = frappe.get_doc("Sales Order", sales_order_name)
        if order_doc.get("order_type") != "Shopping Cart":
            continue
        if not _is_fully_paid_prepaid_order(order_doc):
            continue
        if flt(order_doc.get("per_delivered")) < 100:
            continue
        if not _all_shipments_delivered_for_sales_order(order_doc.name):
            continue
        _finalize_sales_order(order_doc, shipment_doc)
        finalized = True

    return finalized


def attempt_pickup_after_dispatch(shipment_name: str, sales_order_name: str | None = None) -> None:
    shipment_doc = frappe.get_doc("Shipment", shipment_name)
    order_doc = frappe.get_doc("Sales Order", sales_order_name) if sales_order_name else None

    if shipment_doc.get("service_provider") != "Shiprocket" or not get_external_shipment_id(shipment_doc):
        target = order_doc or shipment_doc
        _add_comment_once(
            target,
            PICKUP_MARKER,
            f"Pickup needs manual follow-up for Shipment {shipment_doc.name}. Carrier dispatch details were not ready for auto-scheduling.",
        )
        return

    if not is_optional_app_installed("erpnext_shipping_extended"):
        target = order_doc or shipment_doc
        _add_comment_once(
            target,
            PICKUP_MARKER,
            f"Pickup auto-scheduling is unavailable for Shipment {shipment_doc.name} because optional app erpnext_shipping_extended is not installed.",
        )
        return

    try:
        from erpnext_shipping_extended.services.pickups import create_pickup_request

        create_pickup_request(shipment_doc.name, pickup_date=str(shipment_doc.get("pickup_date") or nowdate()))
    except Exception:
        target = order_doc or shipment_doc
        _add_comment_once(
            target,
            PICKUP_MARKER,
            f"Pickup auto-scheduling failed for Shipment {shipment_doc.name}. Manual follow-up is required.",
        )
        frappe.log_error(title="Catalog fulfillment: pickup scheduling failed", message=frappe.get_traceback())


def _queue_pickup_followup(order_doc, shipment_doc, result: dict) -> dict:
    frappe.enqueue(
        "catalog_extensions.order_fulfillment.attempt_pickup_after_dispatch",
        queue="long",
        timeout=900,
        enqueue_after_commit=True,
        shipment_name=shipment_doc.name,
        sales_order_name=order_doc.name,
    )
    result["pickup_queued"] = True
    return result


def automate_shipment_for_delivery_note(order_doc, delivery_note_doc) -> dict:
    result = {
        "delivery_note": delivery_note_doc.name,
        "shipment": None,
        "delivery_note_created": False,
        "shipment_created": False,
        "dispatch_queued": False,
        "pickup_queued": False,
    }

    if not is_doctype_available("Shipment"):
        _add_comment_once(
            order_doc,
            FULFILLMENT_MARKER,
            f"Shipment automation was skipped for Delivery Note {delivery_note_doc.name} because Shipment DocType is not available on this bench.",
        )
        return result

    shipment_doc, shipment_created = _get_shipment_doc(order_doc, delivery_note_doc)
    result["shipment"] = shipment_doc.name
    result["shipment_created"] = shipment_created
    _debug_log(
        "Shipment automation stage reached",
        sales_order=order_doc.name,
        delivery_note=delivery_note_doc.name,
        shipment=shipment_doc.name,
        shipment_created=shipment_created,
    )

    service_info = _get_best_service_info(shipment_doc)
    if not service_info:
        _debug_log(
            "No shipping service available for shipment",
            sales_order=order_doc.name,
            delivery_note=delivery_note_doc.name,
            shipment=shipment_doc.name,
        )
        _add_comment_once(
            order_doc,
            FULFILLMENT_MARKER,
            f"Shipment {shipment_doc.name} was created, but carrier booking needs manual follow-up because no shipping service was available.",
        )
        return result

    try:
        dispatch_result = _queue_dispatch(shipment_doc, service_info) or {}
        result["dispatch_queued"] = bool(dispatch_result.get("queued") or shipment_doc.get("shipment_id"))
        _debug_log(
            "Shipment dispatch queued",
            sales_order=order_doc.name,
            delivery_note=delivery_note_doc.name,
            shipment=shipment_doc.name,
            dispatch_queued=result["dispatch_queued"],
        )
    except Exception:
        frappe.log_error(title="Catalog fulfillment: dispatch queue failed", message=frappe.get_traceback())
        _debug_log(
            "Shipment dispatch queue failed",
            sales_order=order_doc.name,
            delivery_note=delivery_note_doc.name,
            shipment=shipment_doc.name,
        )
        _add_comment_once(
            order_doc,
            FULFILLMENT_MARKER,
            f"Shipment {shipment_doc.name} was created, but dispatch booking failed and needs manual follow-up.",
        )
        return result

    return _queue_pickup_followup(order_doc, shipment_doc, result)


def automate_paid_webshop_order_fulfillment(order_doc) -> dict:
    result = {
        "delivery_note": None,
        "shipment": None,
        "delivery_note_created": False,
        "shipment_created": False,
        "dispatch_queued": False,
        "pickup_queued": False,
        "stock_blocked": False,
    }

    try:
        delivery_note_doc, delivery_note_created = _get_delivery_note_doc(order_doc)
    except DeliveryNoteStockBlockedError as exc:
        result["delivery_note"] = exc.delivery_note_name
        result["delivery_note_created"] = exc.delivery_note_created
        result["stock_blocked"] = True
        frappe.log_error(
            title="Catalog fulfillment: delivery note blocked by stock",
            message=frappe.get_traceback(),
        )
        _debug_log(
            "Webshop fulfillment blocked by insufficient stock",
            sales_order=order_doc.name,
            delivery_note=exc.delivery_note_name,
        )
        delivery_note_label = f"Delivery Note {exc.delivery_note_name}" if exc.delivery_note_name else "Delivery Note automation"
        _add_comment_once(
            order_doc,
            FULFILLMENT_MARKER,
            f"{delivery_note_label} could not be submitted automatically because stock is insufficient. Manual stock review is required before fulfillment can continue.",
        )
        return result

    result["delivery_note"] = delivery_note_doc.name
    result["delivery_note_created"] = delivery_note_created
    _debug_log(
        "Starting webshop fulfillment automation",
        sales_order=order_doc.name,
        delivery_note=delivery_note_doc.name,
        delivery_note_created=delivery_note_created,
    )
    return result


def automate_webshop_order_fulfillment_if_allowed(order_doc) -> dict:
    if not is_order_ready_for_fulfillment(order_doc):
        _debug_log(
            "Skipping webshop fulfillment because payment policy is not yet satisfied",
            sales_order=order_doc.name,
            payment_mode=get_payment_mode_for_doc(order_doc),
        )
        return {
            "delivery_note": None,
            "shipment": None,
            "delivery_note_created": False,
            "shipment_created": False,
            "dispatch_queued": False,
            "pickup_queued": False,
            "stock_blocked": False,
            "skipped": True,
        }
    return automate_paid_webshop_order_fulfillment(order_doc)


def _get_sales_order_name_for_delivery_note(delivery_note_doc) -> str | None:
    item_rows = frappe.get_all(
        "Delivery Note Item",
        filters={"parent": delivery_note_doc.name},
        fields=["against_sales_order"],
        order_by="idx asc",
    )
    for row in item_rows:
        if row.get("against_sales_order"):
            return row["against_sales_order"]
    return None


def _enqueue_shipment_ensure(delivery_note_name: str, sales_order_name: str) -> None:
    frappe.enqueue(
        "catalog_extensions.order_fulfillment.ensure_webshop_shipment_for_delivery_note",
        queue="short",
        timeout=900,
        enqueue_after_commit=True,
        delivery_note_name=delivery_note_name,
        sales_order_name=sales_order_name,
    )


def ensure_webshop_shipment_for_delivery_note(delivery_note_name: str, sales_order_name: str | None = None) -> dict | None:
    if not frappe.db.exists("Delivery Note", delivery_note_name):
        _debug_log("Shipment ensure skipped because delivery note was missing", delivery_note=delivery_note_name)
        return None

    delivery_note_doc = frappe.get_doc("Delivery Note", delivery_note_name)
    if cint(delivery_note_doc.get("is_return")) or delivery_note_doc.docstatus != 1:
        _debug_log(
            "Shipment ensure skipped because delivery note was not eligible",
            delivery_note=delivery_note_name,
            is_return=cint(delivery_note_doc.get("is_return")),
            docstatus=delivery_note_doc.docstatus,
        )
        return None

    sales_order_name = sales_order_name or _get_sales_order_name_for_delivery_note(delivery_note_doc)
    if not sales_order_name or not frappe.db.exists("Sales Order", sales_order_name):
        _debug_log(
            "Shipment ensure could not resolve sales order",
            delivery_note=delivery_note_name,
            sales_order=sales_order_name,
        )
        _add_comment_once(
            delivery_note_doc,
            DELIVERY_REPAIR_MARKER,
            "Shipment automation could not resolve the linked webshop Sales Order after Delivery Note submission.",
        )
        return None

    order_doc = frappe.get_doc("Sales Order", sales_order_name)

    try:
        _debug_log(
            "Running shipment ensure for delivery note",
            sales_order=order_doc.name,
            delivery_note=delivery_note_name,
        )
        return automate_shipment_for_delivery_note(order_doc, delivery_note_doc)
    except Exception:
        frappe.log_error(title="Catalog fulfillment: shipment ensure failed", message=frappe.get_traceback())
        _debug_log(
            "Shipment ensure failed",
            sales_order=order_doc.name,
            delivery_note=delivery_note_name,
        )
        _add_comment_once(
            order_doc,
            DELIVERY_REPAIR_MARKER,
            f"Shipment automation failed after Delivery Note {delivery_note_doc.name} submission and needs retry/manual review.",
        )
        return None


def sync_webshop_shipment_after_delivery_note_submit(doc, method=None):
    if doc.doctype != "Delivery Note" or doc.docstatus != 1 or cint(doc.get("is_return")):
        return

    sales_order_name = _get_sales_order_name_for_delivery_note(doc)
    if not sales_order_name or not frappe.db.exists("Sales Order", sales_order_name):
        _debug_log(
            "Delivery note submit hook skipped because sales order was missing",
            delivery_note=doc.name,
            sales_order=sales_order_name,
        )
        return

    order_doc = frappe.get_doc("Sales Order", sales_order_name)

    try:
        _debug_log(
            "Delivery note submit hook attempting immediate shipment creation",
            sales_order=order_doc.name,
            delivery_note=doc.name,
        )
        automate_shipment_for_delivery_note(order_doc, doc)
    except Exception:
        frappe.log_error(title="Catalog fulfillment: shipment creation on submit failed", message=frappe.get_traceback())
        _add_comment_once(
            order_doc,
            DELIVERY_REPAIR_MARKER,
            f"Immediate shipment creation failed after Delivery Note {doc.name} submission. A retry has been queued.",
        )

    _debug_log(
        "Delivery note submit hook queueing shipment ensure",
        sales_order=order_doc.name,
        delivery_note=doc.name,
    )
    _enqueue_shipment_ensure(doc.name, order_doc.name)

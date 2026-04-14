from contextlib import nullcontext
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from catalog_extensions import api
from catalog_extensions import order_billing
from catalog_extensions import order_fulfillment
from catalog_extensions import simple_checkout
from catalog_extensions.overrides.payment_request import PaymentRequest


class _DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


def _fake_cache_hget(_key, _field, generator=None):
    return generator() if generator else None


frappe.local = SimpleNamespace(
    flags=SimpleNamespace(in_test=True, mute_messages=False),
    session=SimpleNamespace(user="test@example.com"),
    conf=SimpleNamespace(host_name="http://test.local", hostname="http://test.local"),
)
frappe.flags = SimpleNamespace(in_test=True, mute_messages=False)
frappe.session = {}
frappe.cache = SimpleNamespace(hget=_fake_cache_hget, get_value=lambda *args, **kwargs: None)
frappe.db = SimpleNamespace(
    exists=lambda *args, **kwargs: False,
    get_value=lambda *args, **kwargs: None,
    get_single_value=lambda *args, **kwargs: None,
    sql=lambda *args, **kwargs: [],
)
frappe.logger = lambda *args, **kwargs: _DummyLogger()
frappe.log_error = lambda *args, **kwargs: None
frappe.get_traceback = lambda: ""
frappe.get_system_settings = lambda *args, **kwargs: None


class DummyOrder:
    def __init__(self, **values):
        self.doctype = values.get("doctype", "Sales Order")
        self.name = values.get("name", "SO-TEST-0001")
        self.docstatus = values.get("docstatus", 1)
        self.flags = SimpleNamespace(ignore_permissions=False)
        self._values = {
            "status": values.get("status", "To Deliver"),
            "per_delivered": values.get("per_delivered", 0),
            "per_billed": values.get("per_billed", 0),
            "per_picked": values.get("per_picked", 0),
            "advance_paid": values.get("advance_paid", 0),
            "base_grand_total": values.get("base_grand_total", 100),
            "base_rounded_total": values.get("base_rounded_total", 0),
            "order_type": values.get("order_type", "Shopping Cart"),
            "grand_total": values.get("grand_total", values.get("base_grand_total", 100)),
            "rounded_total": values.get("rounded_total", values.get("base_grand_total", 100)),
            "webshop_payment_mode": values.get("webshop_payment_mode", "PREPAID"),
        }
        self.comments = []
        self.cancelled = False
        self.cancel_side_effect = values.get("cancel_side_effect")
        self.saved = False

    def get(self, key, default=None):
        return self._values.get(key, default)

    def add_comment(self, comment_type, content):
        self.comments.append((comment_type, content))

    def db_set(self, key, value=None, **kwargs):
        if isinstance(key, dict):
            for fieldname, fieldvalue in key.items():
                self._values[fieldname] = fieldvalue
            return
        self._values[key] = value

    def update_status(self, status):
        self._values["status"] = status

    def cancel(self):
        if self.cancel_side_effect:
            raise self.cancel_side_effect
        self.cancelled = True

    def reload(self):
        return self

    def save(self, ignore_permissions=False):
        self.saved = True
        return self


class DummyShipment:
    def __init__(self, **values):
        self.doctype = values.get("doctype", "Shipment")
        self.name = values.get("name", "SHIP-TEST-0001")
        self.docstatus = values.get("docstatus", 1)
        self.flags = SimpleNamespace(ignore_permissions=False)
        self.comments = []
        self.shipment_delivery_note = values.get("shipment_delivery_note", [])
        self.shipment_parcel = values.get("shipment_parcel", [])
        self._values = {
            "service_provider": values.get("service_provider"),
            "external_order_id": values.get("external_order_id"),
            "external_shipment_id": values.get("external_shipment_id"),
            "shiprocket_shipment_id": values.get("shiprocket_shipment_id"),
            "shiprocket_order_id": values.get("shiprocket_order_id"),
            "status": values.get("status", "Booked"),
            "tracking_status": values.get("tracking_status"),
            "tracking_status_info": values.get("tracking_status_info"),
            "tracking_url": values.get("tracking_url"),
            "awb_number": values.get("awb_number"),
            "normalized_tracking_status": values.get("normalized_tracking_status"),
            "pickup_from_type": values.get("pickup_from_type", "Company"),
            "delivery_to_type": values.get("delivery_to_type", "Customer"),
            "pickup_address_name": values.get("pickup_address_name", "ADDR-PICKUP"),
            "delivery_address_name": values.get("delivery_address_name", "ADDR-DELIVERY"),
            "description_of_content": values.get("description_of_content", "Test shipment"),
            "pickup_date": values.get("pickup_date", "2026-04-06"),
            "value_of_goods": values.get("value_of_goods", 100),
            "pickup_contact_name": values.get("pickup_contact_name"),
            "delivery_contact_name": values.get("delivery_contact_name"),
        }

    def get(self, key, default=None):
        if key == "shipment_delivery_note":
            return self.shipment_delivery_note
        if key == "shipment_parcel":
            return self.shipment_parcel
        return self._values.get(key, default)

    def add_comment(self, comment_type, content):
        self.comments.append((comment_type, content))

    def db_set(self, key, value=None, **kwargs):
        if isinstance(key, dict):
            for fieldname, fieldvalue in key.items():
                self._values[fieldname] = fieldvalue
            return
        self._values[key] = value

    def reload(self):
        return self

    def append(self, key, value):
        if key == "shipment_parcel":
            self.shipment_parcel.append(value)
            return
        if key == "shipment_delivery_note":
            self.shipment_delivery_note.append(value)
            return
        raise KeyError(key)


class DummyQuotation:
    def __init__(self, **values):
        self.doctype = "Quotation"
        self.name = values.get("name", "QTN-CART-0001")
        self.docstatus = values.get("docstatus", 0)
        self.flags = SimpleNamespace(ignore_permissions=False)
        self.quotation_to = values.get("quotation_to", "Customer")
        self.party_name = values.get("party_name", "CUST-0001")
        self.customer_name = values.get("customer_name", "Test Customer")
        self.contact_person = values.get("contact_person", "CONT-0001")
        self.contact_email = values.get("contact_email", "customer@example.com")
        self.shipping_address_name = values.get("shipping_address_name", "ADDR-SHIP")
        self.customer_address = values.get("customer_address", "ADDR-BILL")
        self.payment_terms_template = values.get("payment_terms_template")
        self.selling_price_list = values.get("selling_price_list", "Standard Selling")
        self.company = values.get("company", "Test Company")
        self.currency = values.get("currency", "INR")
        self.party_account_currency = values.get("party_account_currency", "INR")
        self.items = values.get("items", [SimpleNamespace(item_code="ITEM-001", qty=1)])
        self._values = {
            "rounded_total": values.get("rounded_total", 100),
            "grand_total": values.get("grand_total", 100),
            "order_type": values.get("order_type", "Shopping Cart"),
            "webshop_payment_mode": values.get("webshop_payment_mode", "PREPAID"),
        }
        self.saved = False
        self.inserted = False
        self.submitted = False

    def get(self, key, default=None):
        if key == "items":
            return self.items
        return self._values.get(key, getattr(self, key, default))

    def run_method(self, method):
        return None

    def append(self, key, value):
        if key != "items":
            raise KeyError(key)
        self.items.append(SimpleNamespace(**value))

    def insert(self, ignore_permissions=False):
        self.inserted = True
        return self

    def save(self):
        self.saved = True
        return self

    def submit(self):
        self.submitted = True
        self.docstatus = 1
        return self


class NegativeStockError(Exception):
    pass


class DummyReturnDoc:
    def __init__(self, items=None):
        self.name = "SINV-RET-TEST-0001"
        self.items = items or []
        self.flags = SimpleNamespace(ignore_permissions=False)
        self.comments = []
        self.allocate_advances_automatically = 1
        self.inserted = False

    def set(self, key, value):
        setattr(self, key, value)

    def calculate_taxes_and_totals(self):
        return None

    def insert(self):
        self.inserted = True
        return self

    def add_comment(self, comment_type, content):
        self.comments.append((comment_type, content))


class PortalOrderFlowTestCase(TestCase):
    @staticmethod
    def fake_exists(expected_doctypes=None, comments_exist=False):
        expected_doctypes = set(expected_doctypes or [])

        def _exists(doctype, filters=None, *args, **kwargs):
            if doctype == "Comment":
                return comments_exist
            return doctype in expected_doctypes

        return _exists

    def make_context(self, order_doc=None, **overrides):
        context = {
            "order_doc": order_doc or DummyOrder(),
            "flow_visibility": {
                "payment_active": True,
                "shipping_active": True,
                "return_active": True,
                "show_shipment_traceability": True,
                "show_return_traceability": True,
            },
            "delivery_notes": [],
            "shipments": [],
            "invoices": [],
            "payment_requests": [],
            "return_delivery_notes": [],
            "draft_return_delivery_notes": [],
            "return_shipments": [],
            "return_records": [],
            "return_invoices": [],
            "draft_return_invoices": [],
            "eligible_return_items": [],
        }
        context.update(overrides)
        return context

    def test_paid_order_can_be_cancelled_before_fulfillment_starts(self):
        order_doc = DummyOrder(per_billed=100, per_picked=0, per_delivered=0)
        context = self.make_context(order_doc=order_doc, invoices=[{"name": "SINV-0001"}])

        self.assertIsNone(api._get_cancel_unavailable_reason(context))
        actions = api._get_order_actions(context, {"payment_received": True, "eligible_return_items_count": 0})
        self.assertTrue(actions["can_cancel"])

    def test_picked_order_cannot_be_cancelled(self):
        order_doc = DummyOrder(per_billed=100, per_picked=25)
        context = self.make_context(order_doc=order_doc)

        self.assertEqual(
            api._get_cancel_unavailable_reason(context),
            "This order is already in fulfillment and can no longer be cancelled online.",
        )

    def test_cancel_respects_checkout_setting(self):
        order_doc = DummyOrder(per_billed=100, per_picked=0, per_delivered=0)
        context = self.make_context(
            order_doc=order_doc,
            flow_visibility={
                "payment_active": True,
                "shipping_active": True,
                "return_active": True,
                "show_shipment_traceability": True,
                "show_return_traceability": True,
                "cancel_active": False,
            },
        )

        self.assertEqual(
            api._get_cancel_unavailable_reason(context),
            "Order cancellation is disabled for this checkout flow.",
        )

        actions = api._get_order_actions(context, {"payment_received": True, "eligible_return_items_count": 0})
        self.assertFalse(actions["can_cancel"])

    def test_refund_requires_return_receipt(self):
        context = self.make_context(return_invoices=[{"name": "SINV-RET-0001"}])

        blocked_reason = api._get_refund_unavailable_reason(
            context,
            {"payment_received": True, "has_return_received": False, "refund_settled": False},
        )
        self.assertEqual(
            blocked_reason,
            "Refund can be requested only after the returned items are received.",
        )

        allowed_reason = api._get_refund_unavailable_reason(
            context,
            {"payment_received": True, "has_return_received": True, "refund_settled": False},
        )
        self.assertIsNone(allowed_reason)

    def test_cancel_portal_order_adds_refund_marker_for_paid_order(self):
        order_doc = DummyOrder()
        context = self.make_context(order_doc=order_doc)

        with (
            patch("catalog_extensions.api._get_portal_order_doc", return_value=order_doc),
            patch("catalog_extensions.api._build_portal_order_tracking_context", return_value=context),
            patch("catalog_extensions.api._build_status_signals", return_value={"payment_received": True}),
        ):
            result = api.cancel_portal_order(order_doc.name, order_doc.doctype, reason="Changed mind")

        self.assertTrue(order_doc.cancelled)
        self.assertTrue(result["ok"])
        self.assertIn("Payment Request", order_doc.ignore_linked_doctypes)
        self.assertEqual(len(order_doc.comments), 2)
        self.assertIn("Customer requested cancellation: Changed mind", order_doc.comments[0][1])
        self.assertIn(api.PORTAL_REFUND_REQUEST_MARKER, order_doc.comments[1][1])

    def test_cancel_portal_order_skips_refund_marker_for_unpaid_order(self):
        order_doc = DummyOrder()
        context = self.make_context(order_doc=order_doc)

        with (
            patch("catalog_extensions.api._get_portal_order_doc", return_value=order_doc),
            patch("catalog_extensions.api._build_portal_order_tracking_context", return_value=context),
            patch("catalog_extensions.api._build_status_signals", return_value={"payment_received": False}),
        ):
            api.cancel_portal_order(order_doc.name, order_doc.doctype, reason="Changed mind")

        self.assertTrue(order_doc.cancelled)
        self.assertEqual(len(order_doc.comments), 1)
        self.assertNotIn(api.PORTAL_REFUND_REQUEST_MARKER, order_doc.comments[0][1])

    def test_cancel_portal_order_returns_safe_message_when_payment_links_block_cancellation(self):
        order_doc = DummyOrder(cancel_side_effect=frappe.LinkExistsError)
        context = self.make_context(order_doc=order_doc)

        with (
            patch("catalog_extensions.api._get_portal_order_doc", return_value=order_doc),
            patch("catalog_extensions.api._build_portal_order_tracking_context", return_value=context),
            patch("catalog_extensions.api._build_status_signals", return_value={"payment_received": True}),
        ):
            with self.assertRaises(frappe.ValidationError) as exc:
                api.cancel_portal_order(order_doc.name, order_doc.doctype)

        self.assertIn("linked billing or payment records still need staff review", str(exc.exception))
        self.assertEqual(order_doc.comments, [])

    def test_status_signals_treat_full_advance_as_payment_received(self):
        order_doc = DummyOrder(advance_paid=100, base_grand_total=100, per_billed=0)
        context = self.make_context(order_doc=order_doc)

        signals = api._build_status_signals(context)

        self.assertTrue(signals["payment_received"])
        self.assertTrue(signals["sales_order_fully_paid_in_advance"])

    def test_webshop_order_with_delivery_note_is_not_marked_delivered_without_delivered_shipment(self):
        order_doc = DummyOrder(
            name="SO-TEST-TRACK-0001",
            advance_paid=100,
            base_grand_total=100,
            per_delivered=100,
            status="To Deliver",
        )
        context = self.make_context(
            order_doc=order_doc,
            delivery_notes=[{"name": "DN-TEST-TRACK-0001", "posting_date": "2026-04-06"}],
            shipments=[{"name": "SHIP-TEST-TRACK-0001", "status": "Booked", "tracking_status": None}],
        )

        with patch("catalog_extensions.api.frappe.db.get_value", return_value=None):
            signals = api._build_status_signals(context)
            normalized = api._resolve_normalized_status(context)
            delivered_date = api._get_delivered_date(context)

        self.assertFalse(signals["delivered"])
        self.assertFalse(signals["completed"])
        self.assertIsNone(delivered_date)
        self.assertEqual(normalized["normalized_status_code"], "shipped")

    def test_webshop_order_becomes_delivered_only_from_shipment_delivery_signal(self):
        order_doc = DummyOrder(
            name="SO-TEST-TRACK-0002",
            advance_paid=100,
            base_grand_total=100,
            per_delivered=100,
            status="Completed",
        )
        context = self.make_context(
            order_doc=order_doc,
            delivery_notes=[{"name": "DN-TEST-TRACK-0002", "posting_date": "2026-04-06"}],
            shipments=[
                {
                    "name": "SHIP-TEST-TRACK-0002",
                    "status": "Completed",
                    "tracking_status": "Delivered",
                    "modified": "2026-04-07 10:00:00",
                }
            ],
        )

        with patch("catalog_extensions.api.frappe.db.get_value", return_value=None):
            signals = api._build_status_signals(context)
            normalized = api._resolve_normalized_status(context)
            delivered_date = api._get_delivered_date(context)

        self.assertTrue(signals["delivered"])
        self.assertTrue(signals["completed"])
        self.assertEqual(delivered_date, "2026-04-07 10:00:00")
        self.assertEqual(normalized["normalized_status_code"], "completed")

    def test_webshop_return_window_waits_for_actual_delivery_confirmation(self):
        order_doc = DummyOrder(
            name="SO-TEST-TRACK-0003",
            advance_paid=100,
            base_grand_total=100,
            per_delivered=100,
            status="To Deliver",
        )
        context = self.make_context(
            order_doc=order_doc,
            delivery_notes=[{"name": "DN-TEST-TRACK-0003", "posting_date": "2026-04-06"}],
            shipments=[{"name": "SHIP-TEST-TRACK-0003", "status": "Booked"}],
        )

        with patch("catalog_extensions.api.frappe.db.get_value", return_value=None):
            signals = api._build_status_signals(context)

        self.assertFalse(signals["return_window_open"])
        self.assertIsNone(signals["return_window_end_date"])

    def test_order_billing_creates_invoice_only_for_fully_paid_fully_delivered_webshop_order(self):
        order_doc = DummyOrder(per_delivered=100, advance_paid=100, base_grand_total=100, status="Completed")
        invoice_doc = MagicMock()

        with (
            patch("catalog_extensions.order_billing._has_existing_sales_invoice", return_value=False),
            patch("catalog_extensions.order_billing._has_delivery_completion_marker", return_value=True),
            patch(
                "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
                return_value=invoice_doc,
            ) as make_invoice,
        ):
            order_billing.create_sales_invoice_for_fully_paid_webshop_order(order_doc)

        make_invoice.assert_called_once_with(order_doc.name, ignore_permissions=True)
        invoice_doc.insert.assert_called_once_with(ignore_permissions=True)
        invoice_doc.submit.assert_called_once_with()

    def test_order_billing_skips_invoice_before_full_delivery(self):
        order_doc = DummyOrder(per_delivered=50, advance_paid=100, base_grand_total=100)

        with (
            patch("catalog_extensions.order_billing._has_existing_sales_invoice", return_value=False),
            patch("catalog_extensions.order_billing._has_delivery_completion_marker", return_value=True),
            patch("erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice") as make_invoice,
        ):
            order_billing.create_sales_invoice_for_fully_paid_webshop_order(order_doc)

        make_invoice.assert_not_called()

    def test_order_billing_skips_invoice_until_delivery_completion_marker_exists(self):
        order_doc = DummyOrder(per_delivered=100, advance_paid=100, base_grand_total=100, status="To Deliver")

        with (
            patch("catalog_extensions.order_billing._has_existing_sales_invoice", return_value=False),
            patch("catalog_extensions.order_billing._has_delivery_completion_marker", return_value=False),
            patch("erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice") as make_invoice,
        ):
            order_billing.create_sales_invoice_for_fully_paid_webshop_order(order_doc)

        make_invoice.assert_not_called()

    def test_payment_request_set_as_paid_for_webshop_sales_order_skips_invoice_creation(self):
        pr = object.__new__(PaymentRequest)
        pr.payment_channel = "Card"
        pr.reference_doctype = "Sales Order"
        pr.reference_name = "SO-TEST-0001"
        pr.create_payment_entry = MagicMock(return_value="PE-0001")
        pr.reload = MagicMock()
        pr.db_set = MagicMock()

        sales_order = DummyOrder(name="SO-TEST-0001", order_type="Shopping Cart")

        with patch("catalog_extensions.overrides.payment_request.frappe.get_doc", return_value=sales_order):
            payment_entry = PaymentRequest.set_as_paid(pr)

        self.assertEqual(payment_entry, "PE-0001")
        pr.create_payment_entry.assert_called_once_with()
        pr.reload.assert_called_once_with()
        pr.db_set.assert_called_once_with({"status": "Paid", "outstanding_amount": 0})

    def test_payment_request_set_as_paid_reuses_existing_payment_entry(self):
        pr = object.__new__(PaymentRequest)
        pr.payment_channel = "Card"
        pr.reference_doctype = "Sales Order"
        pr.reference_name = "SO-TEST-0001"
        pr.create_payment_entry = MagicMock()
        pr.reload = MagicMock()
        pr.db_set = MagicMock()

        sales_order = DummyOrder(name="SO-TEST-0001", order_type="Shopping Cart")

        with (
            patch("catalog_extensions.overrides.payment_request.frappe.get_doc", return_value=sales_order),
            patch.object(PaymentRequest, "_get_existing_order_payment_entry", return_value="PE-EXISTING"),
        ):
            payment_entry = PaymentRequest.set_as_paid(pr)

        self.assertEqual(payment_entry, "PE-EXISTING")
        pr.create_payment_entry.assert_not_called()
        pr.db_set.assert_called_once_with({"status": "Paid", "outstanding_amount": 0})

    def test_simple_checkout_place_order_delegates_to_core_flow(self):
        quotation = DummyQuotation()
        settings = SimpleNamespace(hide_shipping_on_webshop=1, hide_payment_on_webshop=0)
        order_doc = DummyOrder(name="SO-TEST-0001")

        with (
            patch("catalog_extensions.simple_checkout._get_settings", return_value=settings),
            patch("catalog_extensions.simple_checkout._get_checkout_quotation", return_value=quotation),
            patch("catalog_extensions.simple_checkout.frappe.get_doc", return_value=order_doc),
            patch("catalog_extensions.simple_checkout.core_cart.place_order", return_value="SO-TEST-0001") as place_order,
        ):
            result = simple_checkout.place_order()

        self.assertEqual(result, "SO-TEST-0001")
        self.assertTrue(quotation.saved)
        self.assertEqual(order_doc.get("webshop_payment_mode"), "PREPAID")
        place_order.assert_called_once_with()

    def test_cod_place_order_triggers_fulfillment_without_payment_request(self):
        quotation = DummyQuotation()
        settings = SimpleNamespace(enable_prepaid=1, enable_cod=1, hide_shipping_on_webshop=0, hide_payment_on_webshop=0)
        order_doc = DummyOrder(name="SO-TEST-COD-0001")

        with (
            patch("catalog_extensions.simple_checkout._get_settings", return_value=settings),
            patch("catalog_extensions.simple_checkout._get_checkout_quotation", return_value=quotation),
            patch("catalog_extensions.simple_checkout.frappe.get_doc", return_value=order_doc),
            patch("catalog_extensions.simple_checkout.core_cart.place_order", return_value=order_doc.name),
            patch("catalog_extensions.order_fulfillment.automate_webshop_order_fulfillment_if_allowed") as automate,
        ):
            result = simple_checkout.place_order(payment_mode="COD")

        self.assertEqual(result, order_doc.name)
        self.assertEqual(order_doc.get("webshop_payment_mode"), "COD")
        automate.assert_called_once_with(order_doc)

    def test_payment_request_set_as_paid_creates_sales_order_for_quotation_reference(self):
        pr = object.__new__(PaymentRequest)
        pr.payment_channel = "Card"
        pr.reference_doctype = "Quotation"
        pr.reference_name = "QTN-CART-0001"
        pr.create_payment_entry = MagicMock(return_value="PE-0001")
        pr.reload = MagicMock()
        pr.db_set = MagicMock()

        sales_order = DummyOrder(name="SO-TEST-0001", order_type="Shopping Cart")

        def ensure_reference():
            pr.reference_doctype = "Sales Order"
            pr.reference_name = sales_order.name
            return sales_order

        with patch.object(PaymentRequest, "_ensure_sales_order_reference", side_effect=ensure_reference):
            payment_entry = PaymentRequest.set_as_paid(pr)

        self.assertEqual(payment_entry, "PE-0001")
        pr.create_payment_entry.assert_called_once_with()
        pr.db_set.assert_called_once_with({"status": "Paid", "outstanding_amount": 0})

    def test_payment_authorized_reuses_existing_sales_order_for_quotation(self):
        pr = object.__new__(PaymentRequest)
        pr.payment_channel = "Card"
        pr.reference_doctype = "Quotation"
        pr.reference_name = "QTN-CART-0001"
        pr.create_payment_entry = MagicMock(return_value="PE-0001")
        pr.reload = MagicMock()
        pr.db_set = MagicMock()

        cart_settings = SimpleNamespace(enabled=1, payment_success_url=None)
        sales_order = DummyOrder(name="SO-TEST-0001", order_type="Shopping Cart")

        def ensure_reference():
            pr.reference_doctype = "Sales Order"
            pr.reference_name = sales_order.name
            return sales_order

        with (
            patch("catalog_extensions.overrides.payment_request.frappe.get_doc", side_effect=[cart_settings, sales_order]),
            patch.object(PaymentRequest, "_ensure_sales_order_reference", side_effect=ensure_reference),
            patch.object(PaymentRequest, "_get_existing_order_payment_entry", return_value="PE-EXISTING"),
            patch("catalog_extensions.overrides.payment_request.frappe.local", SimpleNamespace(session=SimpleNamespace(user="test@example.com"))),
            patch("catalog_extensions.overrides.payment_request.frappe.session", {}),
            patch("catalog_extensions.order_fulfillment.automate_paid_webshop_order_fulfillment") as automate,
        ):
            redirect_to = PaymentRequest.on_payment_authorized(pr, "Completed")

        self.assertTrue(redirect_to.endswith("/order-success?order_id=SO-TEST-0001"))
        self.assertEqual(pr.reference_doctype, "Sales Order")
        automate.assert_called_once_with(sales_order)

    def test_fulfillment_automation_creates_delivery_note_only_and_leaves_shipping_to_shipping_flow(self):
        order_doc = DummyOrder(name="SO-TEST-0009")
        delivery_note = MagicMock()
        delivery_note.name = "DN-TEST-0001"

        with (
            patch("catalog_extensions.order_fulfillment._get_delivery_note_doc", return_value=(delivery_note, True)),
        ):
            result = order_fulfillment.automate_paid_webshop_order_fulfillment(order_doc)

        self.assertEqual(result["delivery_note"], delivery_note.name)
        self.assertIsNone(result["shipment"])
        self.assertFalse(result["dispatch_queued"])
        self.assertFalse(result["pickup_queued"])

    def test_fulfillment_automation_no_longer_adds_shipping_followup_comments(self):
        order_doc = DummyOrder(name="SO-TEST-0010")
        delivery_note = MagicMock()
        delivery_note.name = "DN-TEST-0002"

        with (
            patch("catalog_extensions.order_fulfillment._get_delivery_note_doc", return_value=(delivery_note, True)),
        ):
            result = order_fulfillment.automate_paid_webshop_order_fulfillment(order_doc)

        self.assertIsNone(result["shipment"])
        self.assertFalse(result["dispatch_queued"])
        self.assertEqual(order_doc.comments, [])

    def test_fulfillment_automation_marks_insufficient_stock_without_raising(self):
        order_doc = DummyOrder(name="SO-TEST-0010")

        with (
            patch(
                "catalog_extensions.order_fulfillment._get_delivery_note_doc",
                side_effect=order_fulfillment.DeliveryNoteStockBlockedError(
                    "DN-TEST-STOCK-0001",
                    True,
                    NegativeStockError("Insufficient Stock"),
                ),
            ),
            patch("catalog_extensions.order_fulfillment.frappe.log_error") as log_error,
        ):
            result = order_fulfillment.automate_paid_webshop_order_fulfillment(order_doc)

        self.assertEqual(result["delivery_note"], "DN-TEST-STOCK-0001")
        self.assertTrue(result["delivery_note_created"])
        self.assertTrue(result["stock_blocked"])
        self.assertEqual(result["shipment"], None)
        self.assertEqual(len(order_doc.comments), 1)
        self.assertIn(order_fulfillment.FULFILLMENT_MARKER, order_doc.comments[0][1])
        self.assertIn("stock is insufficient", order_doc.comments[0][1])
        log_error.assert_called_once()

    def test_shipment_defaults_fill_mandatory_fields_for_blank_parcel_rows(self):
        order_doc = DummyOrder(name="SO-TEST-0011")
        order_doc.items = [SimpleNamespace(item_name="Demo Item", item_code="ITEM-001")]
        delivery_note = DummyOrder(doctype="Delivery Note", name="DN-TEST-0002")
        delivery_note.items = [SimpleNamespace(item_name="Demo Item", item_code="ITEM-001")]
        shipment_doc = DummyShipment(
            name="SHIP-TEST-MANDATORY-0001",
            pickup_date=None,
            description_of_content=None,
            shipment_parcel=[{"weight": None, "count": None, "length": None, "width": None, "height": None}],
        )

        with patch("catalog_extensions.order_fulfillment.nowdate", return_value="2026-04-07"):
            order_fulfillment._ensure_shipment_defaults(shipment_doc, order_doc, delivery_note)

        parcel = shipment_doc.shipment_parcel[0]
        self.assertEqual(shipment_doc.get("pickup_date"), "2026-04-07")
        self.assertEqual(shipment_doc.get("description_of_content"), "Demo Item")
        self.assertEqual(parcel["weight"], 0.5)
        self.assertEqual(parcel["count"], 1)
        self.assertEqual(parcel["length"], 10)
        self.assertEqual(parcel["width"], 10)
        self.assertEqual(parcel["height"], 10)

    def test_shipment_description_uses_first_item_name(self):
        order_doc = DummyOrder(name="SO-TEST-0011")
        order_doc.items = [
            SimpleNamespace(item_name="Primary Item", item_code="ITEM-001"),
            SimpleNamespace(item_name="Secondary Item", item_code="ITEM-002"),
        ]

        self.assertEqual(
            order_fulfillment._get_shipment_content_description(order_doc),
            "Primary Item",
        )

    def test_shipment_validate_hook_fills_missing_parcel_for_sales_shipment(self):
        order_doc = DummyOrder(name="SO-TEST-0011", order_type="Sales")
        delivery_note = DummyOrder(doctype="Delivery Note", name="DN-TEST-0002")
        delivery_note.items = [SimpleNamespace(item_name="Demo Item", item_code="ITEM-001")]
        shipment_doc = DummyShipment(
            name="SHIP-TEST-MANDATORY-0002",
            pickup_date=None,
            description_of_content=None,
            shipment_delivery_note=[SimpleNamespace(delivery_note=delivery_note.name)],
            shipment_parcel=[],
        )

        with (
            patch(
                "catalog_extensions.order_fulfillment.frappe.db.exists",
                side_effect=self.fake_exists({"Delivery Note", "Sales Order"}),
            ),
            patch(
                "catalog_extensions.order_fulfillment.frappe.get_doc",
                side_effect=[delivery_note, order_doc],
            ),
            patch(
                "catalog_extensions.order_fulfillment._get_sales_order_name_for_delivery_note",
                return_value=order_doc.name,
            ),
            patch("catalog_extensions.order_fulfillment.nowdate", return_value="2026-04-07"),
        ):
            order_fulfillment.apply_webshop_shipment_defaults(shipment_doc)

        self.assertEqual(shipment_doc.get("pickup_date"), "2026-04-07")
        self.assertEqual(shipment_doc.get("description_of_content"), "Demo Item")
        self.assertEqual(len(shipment_doc.shipment_parcel), 1)
        self.assertEqual(shipment_doc.shipment_parcel[0]["weight"], 0.5)
        self.assertEqual(shipment_doc.shipment_parcel[0]["count"], 1)

    def test_delivery_note_submit_hook_creates_shipment_for_any_sales_order(self):
        order_doc = DummyOrder(name="SO-TEST-0012", advance_paid=0, base_grand_total=100, order_type="Sales")
        delivery_note = DummyOrder(doctype="Delivery Note", name="DN-TEST-0003")

        with (
            patch(
                "catalog_extensions.order_fulfillment._get_sales_order_name_for_delivery_note",
                return_value=order_doc.name,
            ),
            patch("catalog_extensions.order_fulfillment.frappe.db.exists", return_value=True),
            patch("catalog_extensions.order_fulfillment.frappe.get_doc", return_value=order_doc),
            patch(
                "catalog_extensions.order_fulfillment.automate_shipment_for_delivery_note",
                return_value={"shipment": "SHIP-TEST-0003"},
            ) as automate_shipment,
            patch("catalog_extensions.order_fulfillment.frappe.enqueue") as enqueue_job,
        ):
            order_fulfillment.sync_webshop_shipment_after_delivery_note_submit(delivery_note)

        automate_shipment.assert_called_once_with(order_doc, delivery_note)
        enqueue_job.assert_called_once()
        self.assertEqual(
            enqueue_job.call_args.kwargs["delivery_note_name"],
            delivery_note.name,
        )
        self.assertEqual(
            enqueue_job.call_args.kwargs["sales_order_name"],
            order_doc.name,
        )

    def test_delivery_note_submit_hook_skips_when_sales_order_is_missing(self):
        order_doc = DummyOrder(name="SO-TEST-0013", advance_paid=0, base_grand_total=100, order_type="Sales")
        delivery_note = DummyOrder(doctype="Delivery Note", name="DN-TEST-0004")

        with (
            patch(
                "catalog_extensions.order_fulfillment._get_sales_order_name_for_delivery_note",
                return_value=None,
            ),
            patch("catalog_extensions.order_fulfillment.frappe.db.exists", return_value=False),
            patch("catalog_extensions.order_fulfillment.frappe.get_doc", return_value=order_doc),
            patch("catalog_extensions.order_fulfillment.automate_shipment_for_delivery_note") as automate_shipment,
        ):
            order_fulfillment.sync_webshop_shipment_after_delivery_note_submit(delivery_note)

        automate_shipment.assert_not_called()

    def test_delivery_note_submit_hook_queues_retry_when_immediate_shipment_creation_fails(self):
        order_doc = DummyOrder(name="SO-TEST-0014", advance_paid=100, base_grand_total=100)
        delivery_note = DummyOrder(doctype="Delivery Note", name="DN-TEST-0005")

        with (
            patch(
                "catalog_extensions.order_fulfillment._get_sales_order_name_for_delivery_note",
                return_value=order_doc.name,
            ),
            patch(
                "catalog_extensions.order_fulfillment.frappe.db.exists",
                side_effect=self.fake_exists({"Sales Order"}, comments_exist=False),
            ),
            patch("catalog_extensions.order_fulfillment.frappe.get_doc", return_value=order_doc),
            patch(
                "catalog_extensions.order_fulfillment.automate_shipment_for_delivery_note",
                side_effect=RuntimeError("shipment failed"),
            ),
            patch("catalog_extensions.order_fulfillment.frappe.log_error") as log_error,
            patch("catalog_extensions.order_fulfillment.frappe.enqueue") as enqueue_job,
        ):
            order_fulfillment.sync_webshop_shipment_after_delivery_note_submit(delivery_note)

        log_error.assert_called_once()
        enqueue_job.assert_called_once()
        self.assertEqual(len(order_doc.comments), 1)
        self.assertIn(order_fulfillment.DELIVERY_REPAIR_MARKER, order_doc.comments[0][1])

    def test_ensure_webshop_shipment_for_delivery_note_runs_shared_shipment_automation(self):
        order_doc = DummyOrder(name="SO-TEST-0016", advance_paid=100, base_grand_total=100)
        delivery_note = DummyOrder(doctype="Delivery Note", name="DN-TEST-0007")

        with (
            patch(
                "catalog_extensions.order_fulfillment.frappe.db.exists",
                side_effect=self.fake_exists({"Delivery Note", "Sales Order"}, comments_exist=False),
            ),
            patch(
                "catalog_extensions.order_fulfillment.frappe.get_doc",
                side_effect=[delivery_note, order_doc],
            ),
            patch("catalog_extensions.order_fulfillment.automate_shipment_for_delivery_note", return_value={"shipment": "SHIP-OK"}) as automate_shipment,
        ):
            result = order_fulfillment.ensure_webshop_shipment_for_delivery_note(delivery_note.name, order_doc.name)

        self.assertEqual(result["shipment"], "SHIP-OK")
        automate_shipment.assert_called_once_with(order_doc, delivery_note)

    def test_attempt_pickup_after_dispatch_adds_followup_when_remote_dispatch_not_ready(self):
        shipment_doc = DummyShipment(
            name="SHIP-TEST-0003",
            service_provider="Shiprocket",
            shiprocket_shipment_id=None,
            external_shipment_id="EXT-SHIP-3",
        )
        order_doc = DummyOrder(name="SO-TEST-0011")

        with (
            patch("catalog_extensions.order_fulfillment.frappe.get_doc", side_effect=[shipment_doc, order_doc]),
            patch("catalog_extensions.order_fulfillment.frappe.db.exists", return_value=False),
        ):
            order_fulfillment.attempt_pickup_after_dispatch(shipment_doc.name, order_doc.name)

        self.assertEqual(len(order_doc.comments), 1)
        self.assertIn(order_fulfillment.PICKUP_MARKER, order_doc.comments[0][1])

    def test_finalize_delivered_webshop_order_updates_delivery_note_order_and_invoice(self):
        shipment_doc = DummyShipment(
            name="SHIP-TEST-0004",
            status="Completed",
            tracking_status="Delivered",
            normalized_tracking_status="DELIVERED",
            awb_number="AWB-1",
            tracking_url="https://track.example/1",
            tracking_status_info="Delivered",
            shipment_delivery_note=[SimpleNamespace(delivery_note="DN-TEST-0005")],
        )
        delivery_note = DummyOrder(doctype="Delivery Note", name="DN-TEST-0005", status="To Deliver")
        sales_order = DummyOrder(name="SO-TEST-0014", advance_paid=100, base_grand_total=100, per_delivered=100)

        with (
            patch(
                "catalog_extensions.order_fulfillment.frappe.db.exists",
                side_effect=self.fake_exists({"Delivery Note", "Sales Order"}, comments_exist=False),
            ),
            patch(
                "catalog_extensions.order_fulfillment.frappe.get_doc",
                side_effect=[delivery_note, sales_order],
            ),
            patch(
                "catalog_extensions.order_fulfillment._get_sales_order_names_for_delivery_notes",
                return_value=[sales_order.name],
            ),
            patch("catalog_extensions.order_fulfillment._all_shipments_delivered_for_sales_order", return_value=True),
            patch(
                "catalog_extensions.order_fulfillment.order_billing.create_sales_invoice_for_fully_paid_webshop_order"
            ) as create_invoice,
        ):
            finalized = order_fulfillment.finalize_delivered_webshop_order_from_shipment(shipment_doc)

        self.assertTrue(finalized)
        self.assertEqual(delivery_note.get("status"), "Completed")
        self.assertEqual(sales_order.get("status"), "Completed")
        self.assertEqual(delivery_note.get("tracking_status"), "Delivered")
        create_invoice.assert_called_once_with(sales_order)

    def test_finalize_delivered_webshop_order_waits_until_all_shipments_are_delivered(self):
        shipment_doc = DummyShipment(
            name="SHIP-TEST-0005",
            status="Completed",
            tracking_status="Delivered",
            normalized_tracking_status="DELIVERED",
            shipment_delivery_note=[SimpleNamespace(delivery_note="DN-TEST-0006")],
        )
        delivery_note = DummyOrder(doctype="Delivery Note", name="DN-TEST-0006", status="To Deliver")
        sales_order = DummyOrder(name="SO-TEST-0015", advance_paid=100, base_grand_total=100, per_delivered=100)

        with (
            patch(
                "catalog_extensions.order_fulfillment.frappe.db.exists",
                side_effect=self.fake_exists({"Delivery Note", "Sales Order"}, comments_exist=False),
            ),
            patch(
                "catalog_extensions.order_fulfillment.frappe.get_doc",
                side_effect=[delivery_note, sales_order],
            ),
            patch(
                "catalog_extensions.order_fulfillment._get_sales_order_names_for_delivery_notes",
                return_value=[sales_order.name],
            ),
            patch("catalog_extensions.order_fulfillment._all_shipments_delivered_for_sales_order", return_value=False),
            patch(
                "catalog_extensions.order_fulfillment.order_billing.create_sales_invoice_for_fully_paid_webshop_order"
            ) as create_invoice,
        ):
            finalized = order_fulfillment.finalize_delivered_webshop_order_from_shipment(shipment_doc)

        self.assertFalse(finalized)
        self.assertEqual(delivery_note.get("status"), "Completed")
        self.assertNotEqual(sales_order.get("status"), "Completed")
        create_invoice.assert_not_called()

    def test_start_portal_refund_processing_after_return_receipt_is_idempotent(self):
        order_doc = DummyOrder()
        return_doc = DummyOrder(doctype="Sales Invoice", name="SINV-RET-0001")
        context = self.make_context(order_doc=order_doc, return_invoices=[{"name": return_doc.name}])
        signals = {
            "payment_received": True,
            "has_return_received": True,
            "refund_settled": False,
        }

        with (
            patch("catalog_extensions.api._has_portal_comment", side_effect=[False, True]),
            patch("catalog_extensions.api.run_as", return_value=nullcontext()),
            patch("catalog_extensions.api.frappe.db.exists", return_value=True),
            patch("catalog_extensions.api.frappe.get_doc", return_value=return_doc),
        ):
            started = api._start_portal_refund_processing(context, signals)
            started_again = api._start_portal_refund_processing(context, signals)

        self.assertTrue(started)
        self.assertFalse(started_again)
        self.assertEqual(len(order_doc.comments), 1)
        self.assertIn(api.PORTAL_REFUND_REQUEST_MARKER, order_doc.comments[0][1])
        self.assertEqual(len(return_doc.comments), 1)

    def test_get_order_delivery_tracking_starts_refund_processing_after_return_receipt(self):
        order_doc = DummyOrder()
        context = self.make_context(order_doc=order_doc, return_invoices=[{"name": "SINV-RET-0001"}])
        normalized = {
            "normalized_status_code": "refund_processing",
            "normalized_status_label": "Refund processing",
            "normalized_status_note": "Your refund is being processed after return receipt.",
            "status_signals": {"has_return_received": True},
        }

        with (
            patch("catalog_extensions.api._get_portal_order_doc", return_value=order_doc),
            patch("catalog_extensions.api._build_portal_order_tracking_context", side_effect=[context, context]),
            patch("catalog_extensions.api._start_portal_refund_processing", return_value=True) as start_refund,
            patch("catalog_extensions.api._resolve_normalized_status", return_value=normalized),
            patch("catalog_extensions.api._build_tracking_milestones", return_value=[]),
            patch("catalog_extensions.api._get_order_actions", return_value={}),
        ):
            result = api.get_order_delivery_tracking(order_doc.name, order_doc.doctype)

        start_refund.assert_called_once_with(context)
        self.assertEqual(result["normalized_status_code"], "refund_processing")
        self.assertTrue(result["return_receipt_confirmed"])

    def test_sync_refund_processing_hook_uses_return_delivery_note_sales_order(self):
        sales_order = DummyOrder(name="SO-TEST-0002")
        return_delivery_note = DummyOrder(doctype="Delivery Note", name="DN-RET-0001")
        return_delivery_note._values.update({"is_return": 1, "return_against": "DN-0001"})

        with (
            patch(
                "catalog_extensions.api._get_linked_sales_orders_for_delivery_note",
                return_value=[sales_order.name],
            ),
            patch("catalog_extensions.api.frappe.db.exists", return_value=True),
            patch("catalog_extensions.api.frappe.get_doc", return_value=sales_order),
            patch("catalog_extensions.api._build_portal_order_tracking_context", return_value={"order_doc": sales_order}),
            patch("catalog_extensions.api._start_portal_refund_processing") as start_refund,
        ):
            api.sync_portal_refund_processing_after_return_receipt(return_delivery_note)

        start_refund.assert_called_once_with({"order_doc": sales_order})

    def test_portal_return_request_degrades_when_optional_shipping_extension_is_missing(self):
        order_doc = DummyOrder(name="SO-TEST-RET-0001")
        shipment_doc = DummyShipment(
            name="SHIP-TEST-RET-0001",
            service_provider="Shiprocket",
            external_order_id="SR-ORDER-1",
            shiprocket_order_id=None,
        )
        context = self.make_context(
            order_doc=order_doc,
            shipments=[{"name": shipment_doc.name}],
            eligible_return_items=[
                {
                    "sales_invoice_item": "SINV-ITEM-1",
                    "remaining_returnable_qty": 1,
                    "is_return_eligible": 1,
                    "item_code": "ITEM-001",
                }
            ],
            return_source_invoice={"name": "SINV-TEST-0001"},
            return_window_open=True,
        )

        with (
            patch("catalog_extensions.api._get_portal_order_doc", return_value=order_doc),
            patch("catalog_extensions.api._build_portal_order_tracking_context", return_value=context),
            patch("catalog_extensions.api._build_status_signals", return_value={"completed": True}),
            patch("catalog_extensions.api._get_return_target_shipment", return_value={"name": shipment_doc.name}),
            patch(
                "raftor_shippinghq.api.returns.submit_return_request",
                return_value={"return_request": "RMA-00001", "message": "Your return request has been submitted for review."},
            ),
            patch("catalog_extensions.api.run_as", return_value=nullcontext()),
        ):
            result = api.create_portal_return_request(order_doc.name, order_doc.doctype)

        self.assertTrue(result["ok"])
        self.assertEqual(result["return_request"], "RMA-00001")
        self.assertIn("submitted for review", result["message"])
        self.assertTrue(any("requested a return for approval" in comment for _, comment in order_doc.comments))


class CheckoutSettingsSemanticsTestCase(TestCase):
    def test_get_cart_quotation_uses_core_workflow_when_sections_are_not_disabled(self):
        settings = SimpleNamespace(hide_shipping_on_webshop=0, hide_payment_on_webshop=0)

        with (
            patch("catalog_extensions.simple_checkout._get_settings", return_value=settings),
            patch("catalog_extensions.simple_checkout.core_cart.get_cart_quotation", return_value={"doc": "core"}) as get_cart,
        ):
            result = simple_checkout.get_cart_quotation()

        self.assertEqual(result, {"doc": "core"})
        get_cart.assert_called_once_with(None)

    def test_apply_defaults_only_sets_payment_terms_when_payment_is_disabled(self):
        quotation = DummyQuotation(
            shipping_address_name=None,
            customer_address=None,
            payment_terms_template=None,
        )
        settings = SimpleNamespace(
            hide_shipping_on_webshop=0,
            hide_payment_on_webshop=1,
            default_payment_term_template="NET-30",
            default_shipping_address_type="Shipping",
        )

        simple_checkout._ensure_defaults_on_quotation(quotation, settings)

        self.assertIsNone(quotation.shipping_address_name)
        self.assertIsNone(quotation.customer_address)
        self.assertEqual(quotation.payment_terms_template, "NET-30")

    def test_apply_defaults_only_sets_address_when_shipping_is_disabled(self):
        quotation = DummyQuotation(
            shipping_address_name=None,
            customer_address=None,
            payment_terms_template=None,
        )
        party = SimpleNamespace(doctype="Customer", name="CUST-0001", customer_name="Test Customer")
        shipping_address = SimpleNamespace(name="ADDR-SHIP", address_type="Shipping")
        settings = SimpleNamespace(
            hide_shipping_on_webshop=1,
            hide_payment_on_webshop=0,
            default_payment_term_template="NET-30",
            default_shipping_address_type="Shipping",
        )

        with (
            patch("catalog_extensions.simple_checkout.core_cart.get_party", return_value=party),
            patch("catalog_extensions.simple_checkout.core_cart.get_address_docs", return_value=[shipping_address]),
            patch("catalog_extensions.simple_checkout.core_cart.apply_cart_settings") as apply_cart_settings,
        ):
            simple_checkout._ensure_defaults_on_quotation(quotation, settings)

        self.assertEqual(quotation.shipping_address_name, "ADDR-SHIP")
        self.assertEqual(quotation.customer_address, "ADDR-SHIP")
        self.assertIsNone(quotation.payment_terms_template)
        apply_cart_settings.assert_called_once_with(quotation=quotation)

    def test_frontend_flags_include_cancel_toggle(self):
        settings = SimpleNamespace(
            enable_prepaid=1,
            enable_cod=1,
            default_payment_mode="COD",
            hide_shipping_on_webshop=1,
            hide_payment_on_webshop=0,
            enable_cancel_order=1,
        )

        with (
            patch("catalog_extensions.simple_checkout._get_settings", return_value=settings),
            patch("catalog_extensions.simple_checkout.core_cart._get_cart_quotation", return_value=DummyQuotation(webshop_payment_mode="COD")),
        ):
            flags = simple_checkout.get_simple_checkout_flags()

        self.assertTrue(flags["enable_simple_checkout"])
        self.assertTrue(flags["hide_shipping_on_webshop"])
        self.assertFalse(flags["hide_payment_on_webshop"])
        self.assertTrue(flags["enable_cancel_order"])
        self.assertTrue(flags["enable_prepaid"])
        self.assertTrue(flags["enable_cod"])
        self.assertEqual(flags["selected_payment_mode"], "COD")
        self.assertEqual(flags["default_payment_mode"], "COD")
        self.assertTrue(flags["show_payment_mode_selector"])
        self.assertFalse(flags["show_online_payment_ui"])

    def test_frontend_compatibility_flag_tracks_hide_flags_instead_of_legacy_toggle(self):
        settings = SimpleNamespace(
            enable_simple_checkout=1,
            hide_shipping_on_webshop=0,
            hide_payment_on_webshop=0,
            enable_cancel_order=1,
        )

        with patch("catalog_extensions.simple_checkout._get_settings", return_value=settings):
            flags = simple_checkout.get_simple_checkout_flags()
            enabled = simple_checkout.is_simple_checkout_enabled()

        self.assertFalse(flags["enable_simple_checkout"])
        self.assertFalse(flags["hide_shipping_on_webshop"])
        self.assertFalse(flags["hide_payment_on_webshop"])
        self.assertFalse(enabled)

    def test_frontend_flags_disable_payment_modes_when_payment_section_is_hidden(self):
        settings = SimpleNamespace(
            enable_prepaid=1,
            enable_cod=1,
            default_payment_mode="COD",
            hide_shipping_on_webshop=0,
            hide_payment_on_webshop=1,
            enable_cancel_order=1,
        )

        with (
            patch("catalog_extensions.simple_checkout._get_settings", return_value=settings),
            patch("catalog_extensions.simple_checkout.core_cart._get_cart_quotation", return_value=DummyQuotation(webshop_payment_mode="COD")),
        ):
            flags = simple_checkout.get_simple_checkout_flags()

        self.assertFalse(flags["enable_prepaid"])
        self.assertFalse(flags["enable_cod"])
        self.assertFalse(flags["show_payment_mode_selector"])
        self.assertFalse(flags["show_online_payment_ui"])
        self.assertEqual(flags["selected_payment_mode"], "PREPAID")

    def test_place_order_skips_payment_request_when_payment_section_is_hidden(self):
        quotation = DummyQuotation()
        settings = SimpleNamespace(hide_shipping_on_webshop=0, hide_payment_on_webshop=1)
        order_doc = DummyOrder(name="SO-TEST-0001")

        with (
            patch("catalog_extensions.simple_checkout._get_settings", return_value=settings),
            patch("catalog_extensions.simple_checkout._get_checkout_quotation", return_value=quotation),
            patch("catalog_extensions.simple_checkout.frappe.get_doc", return_value=order_doc),
            patch("catalog_extensions.simple_checkout.core_cart.place_order", return_value=order_doc.name),
            patch("catalog_extensions.simple_checkout.core_make_payment_request") as make_payment_request,
        ):
            result = simple_checkout.place_order(payment_mode="COD")

        self.assertFalse(result["payment_required"])
        self.assertEqual(result["redirect_to"], "http://test.local/order-success?order_id=SO-TEST-0001")
        self.assertEqual(order_doc.get("webshop_payment_mode"), "PREPAID")
        make_payment_request.assert_not_called()

    def test_make_payment_request_is_blocked_when_payment_section_is_disabled(self):
        settings = SimpleNamespace(hide_shipping_on_webshop=0, hide_payment_on_webshop=1)

        with patch("catalog_extensions.simple_checkout._get_settings", return_value=settings):
            with self.assertRaises(frappe.ValidationError) as exc:
                simple_checkout.make_payment_request(order_type="Shopping Cart", dt="Sales Order", dn="SO-TEST-0001")

        self.assertIn("Payment is disabled for this checkout flow", str(exc.exception))

    def test_make_payment_request_is_blocked_for_cod_orders(self):
        settings = SimpleNamespace(hide_shipping_on_webshop=0, hide_payment_on_webshop=0)
        cod_order = DummyOrder(webshop_payment_mode="COD")

        with (
            patch("catalog_extensions.simple_checkout._get_settings", return_value=settings),
            patch("catalog_extensions.simple_checkout.frappe.get_doc", return_value=cod_order),
        ):
            with self.assertRaises(frappe.ValidationError) as exc:
                simple_checkout.make_payment_request(order_type="Shopping Cart", dt="Sales Order", dn="SO-TEST-0001")

        self.assertIn("Cash on Delivery", str(exc.exception))

    def test_portal_flow_visibility_tracks_payment_shipping_and_cancel_settings(self):
        settings = SimpleNamespace(
            hide_shipping_on_webshop=1,
            hide_payment_on_webshop=1,
            enable_cancel_order=0,
        )

        with (
            patch("catalog_extensions.api.get_simple_checkout_settings", return_value=settings),
        ):
            visibility = api._get_portal_flow_visibility()

        self.assertFalse(visibility["shipping_active"])
        self.assertFalse(visibility["payment_active"])
        self.assertFalse(visibility["return_active"])
        self.assertFalse(visibility["show_shipment_traceability"])
        self.assertFalse(visibility["show_payment_traceability"])
        self.assertFalse(visibility["show_return_traceability"])
        self.assertFalse(visibility["cancel_active"])

    def test_portal_flow_visibility_disables_payment_actions_for_cod_orders(self):
        settings = SimpleNamespace(
            hide_shipping_on_webshop=0,
            hide_payment_on_webshop=0,
            enable_cancel_order=1,
        )

        with patch("catalog_extensions.api.get_simple_checkout_settings", return_value=settings):
            visibility = api._get_portal_flow_visibility(DummyOrder(webshop_payment_mode="COD"))

        self.assertFalse(visibility["payment_active"])
        self.assertFalse(visibility["show_payment_traceability"])

    def test_cod_orders_are_ready_for_fulfillment_without_advance_payment(self):
        cod_order = DummyOrder(webshop_payment_mode="COD", advance_paid=0, base_grand_total=100)
        self.assertTrue(order_fulfillment.is_order_ready_for_fulfillment(cod_order))

    def test_shipment_defaults_copy_cod_fields_from_order(self):
        order_doc = DummyOrder(webshop_payment_mode="COD", grand_total=275, base_grand_total=275)
        shipment_doc = DummyShipment(shipment_parcel=[])
        delivery_note = DummyOrder(doctype="Delivery Note", name="DN-TEST-0009")

        order_fulfillment._ensure_shipment_defaults(shipment_doc, order_doc, delivery_note)

        self.assertEqual(shipment_doc.get("payment_type"), "COD")
        self.assertEqual(shipment_doc.get("is_cod"), 1)
        self.assertEqual(shipment_doc.get("cod_amount"), 275)
        self.assertEqual(shipment_doc.get("webshop_payment_mode"), "COD")

    def test_order_actions_hide_shipping_and_payment_actions_when_flows_are_disabled(self):
        context = {
            "order_doc": DummyOrder(),
            "flow_visibility": {
                "payment_active": False,
                "shipping_active": False,
                "return_active": False,
                "cancel_active": True,
                "show_shipment_traceability": False,
                "show_return_traceability": False,
                "show_payment_traceability": False,
            },
            "delivery_notes": [],
            "shipments": [],
            "invoices": [],
            "payment_requests": [],
            "return_delivery_notes": [],
            "draft_return_delivery_notes": [],
            "return_shipments": [],
            "return_records": [],
            "return_invoices": [],
            "draft_return_invoices": [],
            "eligible_return_items": [],
        }

        with patch("catalog_extensions.api._build_status_signals", return_value={"eligible_return_items_count": 0}):
            actions = api._get_order_actions(context)

        self.assertFalse(actions["show_shipping_actions"])
        self.assertFalse(actions["show_payment_actions"])
        self.assertTrue(actions["show_cancel_actions"])

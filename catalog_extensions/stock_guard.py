import frappe
from frappe import _
from frappe.utils import flt

from webshop.webshop.doctype.webshop_settings.webshop_settings import show_quantity_in_website
from webshop.webshop.shopping_cart import cart as core_cart
from webshop.webshop.utils.product import get_web_item_qty_in_stock


LOW_STOCK_THRESHOLD = 3


def _translate(text: str) -> str:
	try:
		return _(text)
	except Exception:
		return text


def _build_stock_guard_metadata(
	*, available_qty=None, current_qty=0, on_backorder=False, is_stock_item=True, show_stock_qty=False
):
	current_qty = flt(current_qty)
	available_qty = None if available_qty is None else flt(available_qty)

	if on_backorder:
		return {
			"available_qty": available_qty,
			"max_orderable_qty": None,
			"stock_state": "backorder",
			"stock_message": _translate("Available on backorder"),
			"show_stock_qty": bool(show_stock_qty),
			"can_add_to_cart": True,
			"can_increase_qty": True,
		}

	if not is_stock_item:
		return {
			"available_qty": None,
			"max_orderable_qty": None,
			"stock_state": "in_stock",
			"stock_message": "",
			"show_stock_qty": bool(show_stock_qty),
			"can_add_to_cart": True,
			"can_increase_qty": True,
		}

	available_qty = flt(available_qty or 0)
	max_orderable_qty = max(available_qty, current_qty)

	if available_qty <= 0:
		return {
			"available_qty": available_qty,
			"max_orderable_qty": max_orderable_qty,
			"stock_state": "out_of_stock",
			"stock_message": _translate("Out of stock"),
			"show_stock_qty": bool(show_stock_qty),
			"can_add_to_cart": current_qty > 0,
			"can_increase_qty": False,
		}

	if available_qty <= LOW_STOCK_THRESHOLD:
		return {
			"available_qty": available_qty,
			"max_orderable_qty": max_orderable_qty,
			"stock_state": "low_stock",
			"stock_message": (
				_translate("Only {0} left in stock").format(int(available_qty))
				if show_stock_qty
				else ""
			),
			"show_stock_qty": bool(show_stock_qty),
			"can_add_to_cart": True,
			"can_increase_qty": current_qty < max_orderable_qty,
		}

	return {
		"available_qty": available_qty,
		"max_orderable_qty": max_orderable_qty,
		"stock_state": "in_stock",
		"stock_message": _translate("In stock"),
		"show_stock_qty": bool(show_stock_qty),
		"can_add_to_cart": True,
		"can_increase_qty": current_qty < max_orderable_qty,
	}


def get_stock_guard_data(item_code: str, current_qty=0):
	on_backorder = bool(
		frappe.get_cached_value("Website Item", {"item_code": item_code}, "on_backorder")
	)
	stock_status = get_web_item_qty_in_stock(item_code, "website_warehouse")
	available_qty = None
	is_stock_item = True

	if stock_status:
		available_qty = stock_status.get("stock_qty")
		is_stock_item = bool(stock_status.get("is_stock_item", 1))
	show_stock_qty = bool(show_quantity_in_website())

	metadata = _build_stock_guard_metadata(
		available_qty=available_qty,
		current_qty=current_qty,
		on_backorder=on_backorder,
		is_stock_item=is_stock_item,
		show_stock_qty=show_stock_qty,
	)
	metadata["on_backorder"] = on_backorder
	metadata["is_stock_item"] = is_stock_item
	return metadata


def enrich_product_info(item_code: str, product_info: dict | None):
	product_info = frappe._dict(product_info or {})
	metadata = get_stock_guard_data(item_code, current_qty=product_info.get("qty") or 0)

	if metadata.get("available_qty") is not None:
		product_info["stock_qty"] = metadata["available_qty"]
		product_info["in_stock"] = 1 if metadata["available_qty"] > 0 else 0

	product_info.update(metadata)
	return product_info


def enrich_cart_item(item_row):
	if not getattr(item_row, "item_code", None):
		return item_row

	metadata = get_stock_guard_data(item_row.item_code, current_qty=getattr(item_row, "qty", 0))
	for key, value in metadata.items():
		setattr(item_row, key, value)
	return item_row


def validate_requested_cart_qty(item_code: str, requested_qty, current_qty=0):
	requested_qty = flt(requested_qty)
	current_qty = flt(current_qty)
	metadata = get_stock_guard_data(item_code, current_qty=current_qty)

	if metadata.get("on_backorder") or not metadata.get("is_stock_item", True):
		return metadata

	max_orderable_qty = flt(metadata.get("max_orderable_qty") or 0)
	if requested_qty > max_orderable_qty:
		frappe.throw(metadata.get("stock_message") or _translate("Out of stock"))

	return metadata


@frappe.whitelist()
def update_cart(item_code, qty, additional_notes=None, with_items=False):
	quotation = core_cart._get_cart_quotation()

	empty_card = False
	qty = flt(qty)
	quotation_items = quotation.get("items", {"item_code": item_code})
	current_qty = flt(quotation_items[0].qty) if quotation_items else 0

	if qty == 0:
		quotation_items = quotation.get("items", {"item_code": ["!=", item_code]})
		if quotation_items:
			quotation.set("items", quotation_items)
		else:
			empty_card = True
	else:
		validate_requested_cart_qty(item_code, qty, current_qty=current_qty)
		warehouse = frappe.get_cached_value(
			"Website Item", {"item_code": item_code}, "website_warehouse"
		)

		quotation_items = quotation.get("items", {"item_code": item_code})
		if not quotation_items:
			quotation.append(
				"items",
				{
					"doctype": "Quotation Item",
					"item_code": item_code,
					"qty": qty,
					"additional_notes": additional_notes,
					"warehouse": warehouse,
				},
			)
		else:
			quotation_items[0].qty = qty
			quotation_items[0].warehouse = warehouse
			quotation_items[0].additional_notes = additional_notes

	core_cart.apply_cart_settings(quotation=quotation)

	quotation.flags.ignore_permissions = True
	quotation.payment_schedule = []
	if not empty_card:
		quotation.save()
	else:
		quotation.delete()
		quotation = None

	core_cart.set_cart_count(quotation)

	if with_items:
		from catalog_extensions.simple_checkout import get_cart_quotation

		context = get_cart_quotation(quotation)
		return {
			"items": frappe.render_template(
				"templates/includes/cart/cart_items.html", context
			),
			"total": frappe.render_template(
				"templates/includes/cart/cart_items_total.html", context
			),
			"taxes_and_totals": frappe.render_template(
				"templates/includes/cart/cart_payment_summary.html", context
			),
		}

	return {"name": quotation.name if quotation else None}

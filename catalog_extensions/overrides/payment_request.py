from contextlib import contextmanager

import frappe
from frappe.utils import get_url

from webshop.webshop.doctype.override_doctype.payment_request import (
    PaymentRequest as WebshopPaymentRequest,
)
from webshop.webshop.utils.product import get_web_item_qty_in_stock
from erpnext.selling.doctype.quotation.quotation import _make_sales_order
from catalog_extensions.simple_checkout import (
    PAYMENT_MODE_COD,
    get_payment_mode_for_doc,
)


@contextmanager
def run_as(user: str):
    session = getattr(frappe, "session", None)
    previous_user = getattr(session, "user", None)
    if previous_user is None and isinstance(session, dict):
        previous_user = session.get("user")
    previous_user = previous_user or "Guest"
    frappe.set_user(user)
    try:
        yield
    finally:
        frappe.set_user(previous_user)


class PaymentRequest(WebshopPaymentRequest):
    @staticmethod
    def _safe_get_url(path: str) -> str:
        try:
            return get_url(path)
        except Exception:
            return path

    def _get_existing_sales_order_for_quotation(self, quotation_name):
        rows = frappe.db.sql(
            """
            SELECT DISTINCT so.name
            FROM `tabSales Order` so
            INNER JOIN `tabSales Order Item` soi ON soi.parent = so.name
            WHERE so.docstatus < 2
              AND so.order_type = 'Shopping Cart'
              AND soi.prevdoc_docname = %s
            ORDER BY so.creation DESC
            LIMIT 1
            """,
            (quotation_name,),
            as_dict=True,
        )
        return rows[0]["name"] if rows else None

    def _get_existing_order_payment_entry(self):
        rows = frappe.db.sql(
            """
            SELECT per.parent
            FROM `tabPayment Entry Reference` per
            INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
            WHERE pe.docstatus = 1
              AND per.reference_doctype = 'Sales Order'
              AND per.reference_name = %s
            ORDER BY pe.creation DESC
            LIMIT 1
            """,
            (self.reference_name,),
            as_dict=True,
        )
        return rows[0]["parent"] if rows else None

    def _ensure_sales_order_from_quotation(self, quotation):
        existing_order_name = self._get_existing_sales_order_for_quotation(quotation.name)
        if existing_order_name:
            return frappe.get_doc("Sales Order", existing_order_name)

        cart_settings = frappe.get_doc("Webshop Settings")
        quotation.company = quotation.company or cart_settings.company
        quotation.flags.ignore_permissions = True
        if quotation.docstatus == 0:
            quotation.submit()

        sales_order = frappe.get_doc(_make_sales_order(quotation.name, ignore_permissions=True))
        sales_order.payment_schedule = []

        if not frappe.utils.cint(cart_settings.allow_items_not_in_stock):
            for item in sales_order.get("items"):
                item.warehouse = frappe.db.get_value(
                    "Website Item", {"item_code": item.item_code}, "website_warehouse"
                )
                is_stock_item = frappe.db.get_value("Item", item.item_code, "is_stock_item")
                if is_stock_item:
                    item_stock = get_web_item_qty_in_stock(item.item_code, "website_warehouse")
                    if not frappe.utils.cint(item_stock.in_stock):
                        frappe.throw(frappe._("{0} Not in Stock").format(item.item_code))
                    if item.qty > item_stock.stock_qty:
                        frappe.throw(
                            frappe._("Only {0} in Stock for item {1}").format(
                                item_stock.stock_qty, item.item_code
                            )
                        )

        sales_order.flags.ignore_permissions = True
        sales_order.insert(ignore_permissions=True)
        sales_order.submit()
        return sales_order

    def _ensure_sales_order_reference(self):
        if self.reference_doctype != "Quotation":
            return frappe.get_doc(self.reference_doctype, self.reference_name)

        quotation = frappe.get_doc("Quotation", self.reference_name)
        if quotation.get("order_type") != "Shopping Cart":
            return quotation

        sales_order = self._ensure_sales_order_from_quotation(quotation)
        self.db_set(
            {
                "reference_doctype": "Sales Order",
                "reference_name": sales_order.name,
                "subject": frappe._("Payment Request for {0}").format(sales_order.name),
            }
        )
        self.reference_doctype = "Sales Order"
        self.reference_name = sales_order.name
        return sales_order

    def set_as_paid(self):
        ref_doc = self._ensure_sales_order_reference()

        if (
            self.payment_channel == "Phone"
            or self.reference_doctype != "Sales Order"
            or ref_doc.get("order_type") != "Shopping Cart"
        ):
            return super().set_as_paid()

        payment_entry = self._get_existing_order_payment_entry() or self.create_payment_entry()
        self.reload()
        self.db_set({"status": "Paid", "outstanding_amount": 0})
        return payment_entry

    def on_payment_authorized(self, status=None):
        if not status or status not in ("Authorized", "Completed"):
            return

        if not hasattr(frappe.local, "session") or frappe.local.session.user == "Guest":
            return

        cart_settings = frappe.get_doc("Webshop Settings")
        if not cart_settings.enabled:
            return

        redirect_to = self._safe_get_url("/cart")

        try:
            with run_as("Administrator"):
                ref_doc = self._ensure_sales_order_reference()
                self.set_as_paid()
                ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
                redirect_to = self._get_success_redirect(cart_settings)
        except Exception:
            frappe.log_error(
                title="Catalog payment authorization failed",
                message=frappe.get_traceback(),
            )
            return redirect_to

        if self.reference_doctype == "Sales Order" and ref_doc.get("order_type") == "Shopping Cart":
            frappe.session["last_order_id"] = ref_doc.name
            try:
                with run_as("Administrator"):
                    from catalog_extensions.order_fulfillment import automate_webshop_order_fulfillment_if_allowed

                    automate_webshop_order_fulfillment_if_allowed(ref_doc)
            except Exception:
                frappe.log_error(
                    title="Catalog order-flow automation failed",
                    message=frappe.get_traceback(),
                )

        return redirect_to

    def _get_success_redirect(self, cart_settings):
        success_url = cart_settings.payment_success_url
        redirect_target = self.reference_name
        if self.reference_doctype == "Quotation":
            redirect_target = self._get_existing_sales_order_for_quotation(self.reference_name)
            if not redirect_target:
                return self._safe_get_url("/cart")

        redirect_to = self._safe_get_url(f"/order-success?order_id={redirect_target}")

        if success_url:
            redirect_to = (
                {
                    "Orders": "/orders",
                    "Invoices": "/invoices",
                    "My Account": "/me",
                }
            ).get(success_url, "/me")

        return redirect_to

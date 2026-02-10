import frappe
from frappe import _
from frappe.utils import flt, today, cstr

from frappe.core.doctype.data_import.data_import import DataImport
from frappe.core.doctype.data_import.importer import Importer, INSERT, UPDATE, get_id_field


class CustomDataImport(DataImport):
    """Override Data Import to plug in a custom Importer that supports SYNC for Items.

    All existing behavior for Insert / Update remains unchanged; we only add a
    new import path for Items when the `custom_sync_items` flag is enabled.
    """

    def get_importer(self):  # type: ignore[override]
        return SyncImporter(self.reference_doctype, data_import=self)


class SyncImporter(Importer):
    """Importer that adds a SYNC mode for Item imports.

    SYNC semantics:
    - If the target Item does not exist -> create it (same as INSERT).
    - If it exists -> reuse the existing Item but still sync price and stock.
    - Additionally, per-row price and stock are synced using existing
      Item Price and Stock Reconciliation doctypes.
    """

    def process_doc(self, doc):  # type: ignore[override]
        """Route processing.

        Only enable SYNC behavior when:
        - reference doctype is Item, and
        - Data Import has `custom_sync_items` enabled.
        Otherwise fall back to the core Importer implementation.
        """

        sync_enabled = bool(getattr(self.data_import, "custom_sync_items", 0))

        if not sync_enabled or self.doctype != "Item":
            return super().process_doc(doc)

        return self.sync_item_row(doc)

    # ---------------------------------------------------------------------
    # Core SYNC logic for a single Item row
    # ---------------------------------------------------------------------
    def sync_item_row(self, doc):
        """Core SYNC logic for a single Item row.

        - If Item does not exist, insert it using standard insert logic.
        - If it exists, load the current Item document.
        - In both cases, run price and stock sync.
        - If Data Import has publish_to_website flag set, create/update Website Item.
        """

        id_field = get_id_field(self.doctype)
        doc_id = doc.get(id_field.fieldname)
        exists = bool(doc_id and frappe.db.exists(self.doctype, doc_id))

        if not exists:
            item_doc = self.insert_record(doc)
        else:
            item_doc = frappe.get_doc(self.doctype, doc_id)

        messages = self._sync_price_and_stock(item_doc, doc)

        # Publish to Website Item if flag is set on Data Import
        if getattr(self.data_import, "publish_to_website", 0):
            publish_msg = self._publish_item_to_website(item_doc)
            if publish_msg:
                messages.append(publish_msg)

        if messages:
            frappe.msgprint("\n".join(messages), title=_("SYNC Import Details"), indicator="green")

        return item_doc

    # ------------------------------------------------------------------
    # Helpers: price + stock sync (only when values actually differ)
    # ------------------------------------------------------------------
    def _sync_price_and_stock(self, item_doc, row_doc):
        """Sync price and stock for a given Item document and import row."""

        changes: list[str] = []

        # Price
        price = row_doc.get("price") or row_doc.get("standard_rate") or getattr(item_doc, "standard_rate", None)
        price_list = (
            row_doc.get("price_list")
            or frappe.db.get_single_value("Webshop Settings", "price_list")
            or frappe.db.get_single_value("Selling Settings", "selling_price_list")
            or "Standard Selling"
        )
        if price is not None:
            price_msg = self._sync_item_price(item_doc.name, price_list, price)
            if price_msg:
                changes.append(price_msg)

        # Stock
        stock_qty = (
            row_doc.get("stock")
            or row_doc.get("stock_qty")
            or row_doc.get("opening_stock")
            or getattr(item_doc, "opening_stock", None)
        )
        warehouse = (
            row_doc.get("warehouse")
            or row_doc.get("default_warehouse")
            or getattr(item_doc, "default_warehouse", None)
        )
        if stock_qty is not None and warehouse:
            stock_msg = self._sync_item_stock(item_doc, warehouse, stock_qty)
            if stock_msg:
                changes.append(stock_msg)

        return changes

    def _sync_item_price(self, item_code: str, price_list: str, rate):
        """Upsert Item Price only when rate differs.

        Returns a human readable description of the change, or ``None`` if
        nothing was changed.
        """

        existing_name = frappe.db.exists(
            "Item Price",
            {"item_code": item_code, "price_list": price_list},
        )

        target_rate = flt(rate)

        if existing_name:
            ip = frappe.get_doc("Item Price", existing_name)
            current_rate = flt(ip.price_list_rate)

            # Always write the new rate so that any external consumers relying
            # on Item Price updates (e.g. webshop caches) are refreshed,
            # even if the numeric value did not change.
            ip.price_list_rate = target_rate
            # Ensure this price is treated as a selling price so webshop
            # queries that filter on ip.selling = 1 can see it.
            ip.selling = 1
            ip.flags.updater_reference = {
                "doctype": self.data_import.doctype,
                "docname": self.data_import.name,
                "label": _("via Data Import (SYNC)"),
            }
            ip.save()
            return _(
                "Item {0}: set {1} price to {2} (was {3})"
            ).format(item_code, price_list, target_rate, current_rate)

        # Create new Item Price
        company = getattr(self.data_import, "company", None) or frappe.defaults.get_global_default("company")
        currency = frappe.get_cached_value("Company", company, "default_currency") if company else frappe.db.get_default("currency")

        ip = frappe.get_doc(
            {
                "doctype": "Item Price",
                "item_code": item_code,
                "price_list": price_list,
                "price_list_rate": target_rate,
                # Mark as selling price so webshop queries (which filter on
                # ip.selling = 1) include this row.
                "selling": 1,
                "currency": currency,
            }
        )
        ip.flags.updater_reference = {
            "doctype": self.data_import.doctype,
            "docname": self.data_import.name,
            "label": _("via Data Import (SYNC)"),
        }
        ip.insert()
        return _(
            "Item {0}: created {1} price {2}"
        ).format(item_code, price_list, target_rate)

    def _sync_item_stock(self, item_doc, warehouse: str, target_qty):
        """Adjust stock only when quantity differs, using Stock Reconciliation.

        We treat the imported quantity as an absolute physical count.
        """

        item_code = item_doc.name
        current_qty = frappe.db.get_value(
            "Bin",
            {"item_code": item_code, "warehouse": warehouse},
            "actual_qty",
        ) or 0

        current_qty = flt(current_qty)
        target_qty = flt(target_qty)

        if current_qty == target_qty:
            return None  # nothing to do

        company = getattr(item_doc, "company", None) or frappe.defaults.get_global_default("company")
        if not company:
            frappe.throw(_("Company is required to sync stock for Item {0}").format(item_code))

        sr = frappe.get_doc(
            {
                "doctype": "Stock Reconciliation",
                "company": company,
                "posting_date": today(),
                "items": [
                    {
                        "item_code": item_code,
                        "warehouse": warehouse,
                        "qty": target_qty,
                    }
                ],
            }
        )
        sr.flags.updater_reference = {
            "doctype": self.data_import.doctype,
            "docname": self.data_import.name,
            "label": _("via Data Import (SYNC)"),
        }
        sr.insert()
        sr.submit()

        return _(
            "Item {0}: adjusted stock in {1} from {2} to {3}"
        ).format(item_code, warehouse, current_qty, target_qty)

    def _publish_item_to_website(self, item_doc):
        """Create or update Website Item for the given Item document.

        Returns a message string on success or None if nothing was done.
        """
        from webshop.webshop.doctype.website_item.website_item import make_website_item

        item_code = item_doc.name
        # Check if a Website Item already exists for this Item
        if frappe.db.exists("Website Item", {"item_code": item_code}):
            return None  # already published; avoid duplicate

        try:
            result = make_website_item(item_doc.as_dict(), save=True)
            if result and isinstance(result, list) and len(result) >= 2:
                return _("Item {0}: published to Website Item {1}").format(item_code, result[0])
        except Exception as e:
            frappe.log_error(
                message=_("Failed to publish Item {0} to Website Item during import: {1}").format(item_code, e),
                title="Data Import: Website Item Publish"
            )
            return _("Item {0}: failed to publish to Website Item (see Error Log)").format(item_code)

        return None

#!/usr/bin/env python3
"""Automated Custom Fields Setup for Catalog Extensions (script style).

Usage (from bench root):

    ./env/bin/python apps/catalog_extensions/deploy/setup_custom_fields.py --site sitename
"""

import os
import sys
import argparse


def get_frappe_connection(site: str):
    """Initialize Frappe connection for the given site."""

    bench_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if bench_root not in sys.path:
        sys.path.insert(0, bench_root)
    apps_path = os.path.join(bench_root, "apps")
    if apps_path not in sys.path:
        sys.path.insert(0, apps_path)

    try:
        import frappe

        frappe.init(site=site)
        frappe.connect()
        return frappe
    except Exception as e:
        print(f"[ERROR] Cannot connect to Frappe for site {site}: {e}")
        return None


def create_custom_field(frappe, doctype: str, field_config: dict) -> bool:
    """Create a single custom field if it doesn't exist."""

    fieldname = field_config.get("fieldname")
    existing = frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname})
    if existing:
        print(f"[INFO] Field '{doctype}.{fieldname}' already exists")
        return True

    try:
        field_doc = frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": doctype,
                "fieldname": fieldname,
                "label": field_config.get("label"),
                "fieldtype": field_config.get("fieldtype"),
                "options": field_config.get("options", ""),
                "insert_after": field_config.get("insert_after", "last"),
                "reqd": field_config.get("reqd", 0),
                "default": field_config.get("default", ""),
                "description": field_config.get("description", ""),
                "depends_on": field_config.get("depends_on", ""),
                "read_only": field_config.get("read_only", 0),
                "hidden": field_config.get("hidden", 0),
                "print_hide": field_config.get("print_hide", 1),
            }
        )

        field_doc.insert(ignore_permissions=True)
        frappe.db.commit()

        print(f"[SUCCESS] Created field '{doctype}.{fieldname}'")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to create field '{doctype}.{fieldname}': {e}")
        return False


def setup_item_fields(frappe) -> bool:
    """Create custom fields on Item DocType."""

    print("[STEP] Setting up Item custom fields...")

    fields = [
        {
            "fieldname": "custom_consumer_discount",
            "label": "Consumer Discount (%)",
            "fieldtype": "Percent",
            "insert_after": "standard_rate",
            "description": "Discount percentage displayed to consumers on the website",
        },
        {
            "fieldname": "badges",
            "label": "Badges",
            "fieldtype": "Table",
            "options": "Item Badge",
            "insert_after": "custom_consumer_discount",
            "description": "Product badges like New, Bestseller, On Sale, etc.",
        },
    ]

    ok = True
    for field in fields:
        if not create_custom_field(frappe, "Item", field):
            ok = False
    return ok


def setup_website_item_fields(frappe) -> bool:
    """Create custom fields on Website Item DocType."""

    print("[STEP] Setting up Website Item custom fields...")

    fields = [
        {
            "fieldname": "custom_consumer_discount",
            "label": "Consumer Discount (%)",
            "fieldtype": "Percent",
            "insert_after": "price",
            "description": "Mirrored from Item - for display only",
            "read_only": 1,
        },
        {
            "fieldname": "custom_availability",
            "label": "Availability Status",
            "fieldtype": "Select",
            "options": "\nIn stock\nOut of stock",
            "insert_after": "custom_consumer_discount",
            "description": "Stock availability status shown on website",
            "default": "In stock",
        },
        {
            "fieldname": "filterable_offers",
            "label": "Filterable Offers",
            "fieldtype": "Table MultiSelect",
            "options": "Website Offer",
            "insert_after": "offers",
            "hidden": 1,
            "description": "Auto-synced from Offers child table for filtering",
        },
        {
            "fieldname": "filterable_badges",
            "label": "Filterable Badges",
            "fieldtype": "Table MultiSelect",
            "options": "Item Badge",
            "insert_after": "filterable_offers",
            "hidden": 1,
            "description": "Auto-synced from Item Badge child table for filtering",
        },
    ]

    ok = True
    for field in fields:
        if not create_custom_field(frappe, "Website Item", field):
            ok = False
    return ok


def sync_item_badge_doctype(frappe) -> bool:
    """Ensure Item Badge child DocType is synced from JSON (non-critical)."""

    print("[STEP] Syncing Item Badge DocType schema (if needed)...")

    try:
        from frappe.modules.import_file import import_file_by_path
        from frappe.modules import get_module_path

        module_path = get_module_path("Catalog Extensions")
        item_badge_json = os.path.join(
            module_path, "catalog_extensions", "doctype", "item_badge", "item_badge.json"
        )

        if frappe.db.exists("DocType", "Item Badge"):
            print("[INFO] Item Badge DocType already exists")
            return True

        if os.path.exists(item_badge_json):
            import_file_by_path(item_badge_json)
            frappe.db.commit()
            print("[SUCCESS] Item Badge DocType synced")
        else:
            print("[WARNING] Item Badge JSON not found at", item_badge_json)
        return True
    except Exception as e:
        print(f"[WARNING] Schema sync issue (non-critical): {e}")
        return True


def setup_performance_indexes(frappe) -> bool:
    """Create database indexes for custom filter performance.

    These indexes are essential for fast SQL filtering on offers, badges, and price.
    """
    print("[STEP] Setting up performance indexes...")

    indexes = [
        {
            "table": "Website Offer",
            "name": "idx_website_offer_filter",
            "columns": "parent, offer_title",
            "comment": "Index for offer filter queries"
        },
        {
            "table": "Item Badge",
            "name": "idx_item_badge_filter",
            "columns": "parent, badge_type",
            "comment": "Index for badge filter queries"
        },
        {
            "table": "Item Price",
            "name": "idx_item_price_filter",
            "columns": "item_code, price_list, selling, price_list_rate",
            "comment": "Index for price range filter queries"
        },
    ]

    ok = True
    for idx in indexes:
        try:
            # Check if index already exists
            existing = frappe.db.sql(f"""
                SELECT 1 FROM information_schema.STATISTICS
                WHERE table_schema = DATABASE()
                AND table_name = 'tab{idx['table']}'
                AND index_name = '{idx['name']}'
            """)

            if existing:
                print(f"[INFO] Index '{idx['name']}' on {idx['table']} already exists")
                continue

            # Create the index
            frappe.db.sql(f"""
                ALTER TABLE `tab{idx['table']}`
                ADD INDEX {idx['name']} ({idx['columns']})
            """)
            frappe.db.commit()
            print(f"[SUCCESS] Created index '{idx['name']}' on {idx['table']} ({idx['columns']})")

        except Exception as e:
            print(f"[ERROR] Failed to create index '{idx['name']}': {e}")
            ok = False

    return ok


def main():
    parser = argparse.ArgumentParser(
        description="Automated Custom Fields Setup for Catalog Extensions",
    )
    parser.add_argument("--site", required=True, help="Site name to setup")

    args = parser.parse_args()

    print("=" * 60)
    print("CATALOG EXTENSIONS - CUSTOM FIELDS SETUP")
    print("=" * 60)
    print(f"Site: {args.site}")
    print("=" * 60)

    frappe = get_frappe_connection(args.site)
    if not frappe:
        sys.exit(1)

    try:
        total_groups = 3
        success_groups = 0

        if setup_item_fields(frappe):
            success_groups += 1
        if setup_website_item_fields(frappe):
            success_groups += 1
        if setup_performance_indexes(frappe):
            success_groups += 1

        sync_item_badge_doctype(frappe)

        print("=" * 60)
        if success_groups == total_groups:
            print(f"[COMPLETE] All {total_groups} field groups created successfully!")
        else:
            print(f"[WARNING] {success_groups}/{total_groups} field groups created")
        print("=" * 60)
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()

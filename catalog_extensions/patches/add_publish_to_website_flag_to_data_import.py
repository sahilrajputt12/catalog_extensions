import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """Add 'Publish to Website' checkbox to Data Import doctype.

    When checked during Item imports using the SYNC mode, this will
    automatically create/update Website Items for each processed Item.
    """

    custom_fields = {
        "Data Import": [
            {
                "fieldname": "publish_to_website",
                "fieldtype": "Check",
                "label": "Publish to Website (Items only)",
                "insert_after": "submit_after_import",
                "description": (
                    "If checked, imported Items will be automatically published as Website Items. "
                    "Only works when 'custom_sync_items' (SYNC mode) is enabled."
                ),
            }
        ]
    }

    create_custom_fields(custom_fields, update=True)

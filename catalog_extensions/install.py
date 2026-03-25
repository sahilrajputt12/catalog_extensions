import frappe
import os
import sys


def _import_setup_modules():
    """Import setup modules from deploy/ directory (at app root, not inside package)."""
    # Get path to catalog_extensions app root
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    deploy_path = os.path.join(app_root, "deploy")
    
    if deploy_path not in sys.path:
        sys.path.insert(0, deploy_path)
    
    import setup_doctypes
    import setup_custom_fields
    
    return setup_doctypes, setup_custom_fields


def _run_setup():
    """Ensure all required DocTypes, custom fields, and indexes exist.

    This is safe to run multiple times because the underlying helpers
    check for existing DocTypes/fields/indexes before creating them.
    """
    setup_doctypes, setup_custom_fields = _import_setup_modules()
    
    # Create Catalog Price Range DocType + default ranges
    setup_doctypes.create_catalog_price_range_doctype(frappe)
    setup_doctypes.create_default_price_ranges(frappe)

<<<<<<< HEAD
=======
    # Create Webshop Simple Checkout Settings singleton DocType
    setup_doctypes.create_webshop_simple_checkout_settings_doctype(frappe)

>>>>>>> b7a521d (Updated existing files)
    # Create custom fields on Item, Website Item
    setup_custom_fields.setup_item_fields(frappe)
    setup_custom_fields.setup_website_item_fields(frappe)

    # Ensure Item Badge child DocType is present
    setup_custom_fields.sync_item_badge_doctype(frappe)

    # Create performance indexes for custom filter queries
    setup_custom_fields.setup_performance_indexes(frappe)


def after_install():
    """Hook: run after app is installed on a site."""
    _run_setup()


def after_migrate():
    """Hook: run after migrations (helps on existing benches/sites)."""
    _run_setup()

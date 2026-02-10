"""
Lightweight ERPNext Setup Configuration
Excludes Manufacturing, Projects, Assets, Maintenance, Telephony, 
ERPNext Integrations, Subcontracting, EDI and other heavy modules
"""

import frappe
from frappe.desk.page.setup_wizard.setup_wizard import make_records

def get_lightweight_setup_stages(args=None):
    """Override ERPNext setup stages for lightweight installation"""
    from erpnext.setup.setup_wizard.setup_wizard import get_setup_stages as original_get_stages
    
    stages = original_get_stages(args)
    
    # Replace fixtures stage with lightweight version
    for stage in stages:
        for task in stage.get("tasks", []):
            if task.get("fn").__name__ == "stage_fixtures":
                task["fn"] = install_lightweight_fixtures
            if task.get("fn").__name__ == "setup_defaults":
                task["fn"] = setup_lightweight_defaults
    
    return stages


def install_lightweight_fixtures(country=None):
    """Install only essential fixtures, exclude manufacturing-related data"""
    
    records = [
        # Address Template
        {"doctype": "Address Template", "country": country},
        
        # Essential Item Groups only (exclude manufacturing-related)
        {
            "doctype": "Item Group",
            "item_group_name": "All Item Groups",
            "is_group": 1,
            "parent_item_group": "",
        },
        {
            "doctype": "Item Group",
            "item_group_name": "Products",
            "is_group": 0,
            "parent_item_group": "All Item Groups",
            "show_in_website": 1,
        },
        {
            "doctype": "Item Group",
            "item_group_name": "Services",
            "is_group": 0,
            "parent_item_group": "All Item Groups",
        },
        # NOTE: Excluded Raw Material, Sub Assemblies, Consumable - manufacturing related
        
        # Essential Stock Entry Types only
        {
            "doctype": "Stock Entry Type",
            "name": "Material Issue",
            "purpose": "Material Issue",
            "is_standard": 1,
        },
        {
            "doctype": "Stock Entry Type",
            "name": "Material Receipt",
            "purpose": "Material Receipt",
            "is_standard": 1,
        },
        {
            "doctype": "Stock Entry Type",
            "name": "Material Transfer",
            "purpose": "Material Transfer",
            "is_standard": 1,
        },
        # NOTE: Excluded Manufacture, Repack, Disassemble, Send to Subcontractor,
        # Material Transfer for Manufacture, Material Consumption for Manufacture
        
        # Territory
        {
            "doctype": "Territory",
            "territory_name": "All Territories",
            "is_group": 1,
            "name": "All Territories",
            "parent_territory": "",
        },
        {
            "doctype": "Territory",
            "territory_name": country.replace("'", "") if country else "India",
            "is_group": 0,
            "parent_territory": "All Territories",
        },
        {
            "doctype": "Territory",
            "territory_name": "Rest Of The World",
            "is_group": 0,
            "parent_territory": "All Territories",
        },
        
        # Customer Groups
        {
            "doctype": "Customer Group",
            "customer_group_name": "All Customer Groups",
            "is_group": 1,
            "name": "All Customer Groups",
            "parent_customer_group": "",
        },
        {
            "doctype": "Customer Group",
            "customer_group_name": "Individual",
            "is_group": 0,
            "parent_customer_group": "All Customer Groups",
        },
        {
            "doctype": "Customer Group",
            "customer_group_name": "Commercial",
            "is_group": 0,
            "parent_customer_group": "All Customer Groups",
        },
        
        # Supplier Groups - exclude Raw Material, Electrical, Hardware, Pharmaceutical, Distributor
        {
            "doctype": "Supplier Group",
            "supplier_group_name": "All Supplier Groups",
            "is_group": 1,
            "name": "All Supplier Groups",
            "parent_supplier_group": "",
        },
        {
            "doctype": "Supplier Group",
            "supplier_group_name": "Services",
            "is_group": 0,
            "parent_supplier_group": "All Supplier Groups",
        },
        {
            "doctype": "Supplier Group",
            "supplier_group_name": "Local",
            "is_group": 0,
            "parent_supplier_group": "All Supplier Groups",
        },
        
        # Party Types - EXCLUDED Employee, Shareholder (HR related)
        {"doctype": "Party Type", "party_type": "Customer", "account_type": "Receivable"},
        {"doctype": "Party Type", "party_type": "Supplier", "account_type": "Payable"},
        
        # Sales Person
        {
            "doctype": "Sales Person",
            "sales_person_name": "Sales Team",
            "is_group": 1,
            "parent_sales_person": "",
        },
        
        # Mode of Payment
        {"doctype": "Mode of Payment", "mode_of_payment": "Cash", "type": "Cash"},
        {"doctype": "Mode of Payment", "mode_of_payment": "Bank", "type": "Bank"},
        
        # Activity Types - EXCLUDED for lightweight setup (Project/HR related)
        # {"doctype": "Activity Type", "activity_type": _("Planning")},
        
        # Essential Opportunity Types
        {"doctype": "Opportunity Type", "name": "Sales"},
        
        # Essential Project Types - EXCLUDED Projects module for lightweight setup
        # {"doctype": "Project Type", "project_type": "External"},
        
        # Warehouse Type
        {"doctype": "Warehouse Type", "name": "Transit"},
    ]
    
    make_records(records)
    
    # Skip manufacturing-related fixtures
    # NOTE: Skipping add_uom_data() partial - we'll add only essential UOMs
    add_essential_uom_data()
    
    # Skip update_item_variant_settings - manufacturing feature
    
    # Skip update_global_search_doctypes - we'll configure manually
    configure_lightweight_global_search()
    
    # Set up address templates
    from erpnext.regional.address_template.setup import set_up_address_templates
    set_up_address_templates(default_country=country)


def add_essential_uom_data():
    """Add only essential UOMs, skip manufacturing-specific units"""
    essential_uoms = [
        {"uom_name": "Nos", "must_be_whole_number": 1},
        {"uom_name": "Box", "must_be_whole_number": 1},
        {"uom_name": "Kg", "must_be_whole_number": 0},
        {"uom_name": "Gram", "must_be_whole_number": 0},
        {"uom_name": "Meter", "must_be_whole_number": 0},
        {"uom_name": "Hour", "must_be_whole_number": 0},
    ]
    
    for d in essential_uoms:
        if not frappe.db.exists("UOM", d["uom_name"]):
            frappe.get_doc({
                "doctype": "UOM",
                "uom_name": d["uom_name"],
                "name": d["uom_name"],
                "must_be_whole_number": d["must_be_whole_number"],
                "enabled": 1,
            }).db_insert()


def configure_lightweight_global_search():
    """Configure global search for essential doctypes only - EXCLUDES Projects"""
    essential_doctypes = [
        "Customer",
        "Supplier", 
        "Item",
        "Sales Invoice",
        "Sales Order",
        "Purchase Order",
        "Purchase Invoice",
        "Quotation",
        "Delivery Note",
        "Payment Entry",
        "Journal Entry",
        # NOTE: Excluded Project, Task, Timesheet - Projects module removed
    ]
    
    # Clear existing settings
    frappe.db.sql("DELETE FROM `tabGlobal Search DocType`")
    
    for idx, doctype in enumerate(essential_doctypes):
        frappe.get_doc({
            "doctype": "Global Search DocType",
            "document_type": doctype,
            "idx": idx
        }).insert()


def setup_lightweight_defaults(args=None):
    """Setup defaults excluding manufacturing features"""
    from erpnext.setup.setup_wizard.operations.install_fixtures import (
        install_defaults as original_install_defaults
    )
    
    # Call original but with manufacturing exclusions
    original_install_defaults(args)
    
    # Disable manufacturing-related settings
    disable_manufacturing_features()


def disable_manufacturing_features():
    """Disable all manufacturing-related features in settings"""
    
    # Stock Settings - disable manufacturing features
    stock_settings = frappe.get_doc("Stock Settings")
    stock_settings.auto_indent = 0  # Disable auto material request
    stock_settings.save()
    
    # Selling Settings
    selling_settings = frappe.get_doc("Selling Settings")
    selling_settings.so_required = "No"
    selling_settings.dn_required = "No"
    selling_settings.save()
    
    # Buying Settings  
    buying_settings = frappe.get_doc("Buying Settings")
    buying_settings.po_required = "No"
    buying_settings.pr_required = "No"
    buying_settings.save()
    
    # Disable manufacturing-related permissions and roles
    disable_manufacturing_roles()


def disable_manufacturing_roles():
    """Disable roles related to manufacturing"""
    manufacturing_roles = [
        "Manufacturing User",
        "Manufacturing Manager",
        "Workshop User",
        "Quality Manager",
    ]
    
    for role in manufacturing_roles:
        if frappe.db.exists("Role", role):
            frappe.db.set_value("Role", role, "disabled", 1)


def post_install_lightweight_cleanup():
    """Post-install cleanup to remove manufacturing workspaces and reports"""
    
    # Delete excluded workspaces
    excluded_workspaces = [
        "Manufacturing",
        "Quality", 
        "Projects",  # Excluded for lightweight setup
        "Assets",  # Excluded for lightweight setup
        "Maintenance",  # Excluded for lightweight setup
        "Telephony",  # Excluded for lightweight setup
        "Support",  # Optional: Exclude or keep based on needs
    ]
    
    for workspace in excluded_workspaces:
        if frappe.db.exists("Workspace", workspace):
            frappe.delete_doc("Workspace", workspace, force=True, ignore_missing=True)
    
    # Restrict excluded modules to hidden domain
    excluded_modules = [
        "Manufacturing", 
        "Quality Management", 
        "Projects",
        "Assets",
        "Maintenance",
        "Telephony",
        # NOTE: ERPNext Integrations kept as requested
        "Subcontracting",
        "EDI",
    ]
    for module in excluded_modules:
        if frappe.db.exists("Module Def", module):
            frappe.db.set_value("Module Def", module, "restrict_to_domain", "HiddenDomain")
    
    frappe.clear_cache()

"""
Lightweight ERPNext Setup - Implementation Guide
=================================================

This module provides a streamlined ERPNext installation by excluding:
- Manufacturing module (BOM, Work Order, Job Card, Operation, etc.)
- Projects module (Project, Task, Timesheet, Activity Type)
- Assets module (Asset, Asset Maintenance, Depreciation)
- Maintenance module
- Quality Management module
- Telephony module
- Subcontracting module
- EDI module
- Complex manufacturing-related Stock Entry Types
- Manufacturing-specific Item Groups (Raw Material, Sub Assemblies, Consumable)
- HR/Payroll related configurations (Employee, Shareholder party types)

NOTE: ERPNext Integrations module is RETAINED as requested

Created files:
- catalog_extensions/setup/lightweight_setup.py (main configuration)
- catalog_extensions/setup/__init__.py (module init)

Implementation Steps:
=====================

1. UPDATE hooks.py
------------------
Add to your catalog_extensions/hooks.py:

```python
# Override ERPNext setup wizard for lightweight installation
setup_wizard_stages = "catalog_extensions.setup.lightweight_setup.get_lightweight_setup_stages"

# Post-install cleanup
after_install = "catalog_extensions.setup.lightweight_setup.post_install_lightweight_cleanup"
```

2. CREATE PATCH for existing sites
-----------------------------------
Add to patches.txt:
```
catalog_extensions.patches.lightweight_cleanup
```

Create file: catalog_extensions/patches/lightweight_cleanup.py
```python
import frappe

def execute():
    from catalog_extensions.setup.lightweight_setup import post_install_lightweight_cleanup
    post_install_lightweight_cleanup()
```

3. FOR NEW INSTALLATIONS
------------------------
When creating a new site:

```bash
bench new-site lightweight-site --install-app erpnext --install-app catalog_extensions
```

The lightweight setup will automatically run during the setup wizard.

4. MODULES RETAINED (Essential only)
------------------------------------
- Accounts (Core accounting, invoicing, payments)
- Stock (Inventory management without manufacturing)
- Selling (Sales orders, quotations, customers)
- Buying (Purchase orders, suppliers)
- CRM (Leads, Opportunities)
- Support (Issues, maintenance visits)

NOTE: Projects module EXCLUDED

5. MODULES EXCLUDED
-------------------
- Manufacturing (completely excluded)
- Projects (completely excluded)
- Assets (completely excluded)
- Maintenance (completely excluded)
- Quality Management (completely excluded)
- Telephony (completely excluded)
- ERPNext Integrations (completely excluded)
- Subcontracting (completely excluded)
- EDI (completely excluded)
- HR/Payroll configuration (Employee, Shareholder party types excluded)

Note: HR module was moved to separate 'hrms' app in ERPNext v14+

6. DATABASE IMPACT
-----------------
- Manufacturing DocTypes still exist in code but have no data
- No BOM, Work Order, Job Card records created
- Simplified Item Group structure
- Reduced default fixtures (~50% fewer initial records)

Performance Benefits:
=====================
1. Faster initial setup (fewer fixtures to create)
2. Less database bloat (no manufacturing master data)
3. Cleaner UI (no manufacturing workspaces/menus)
4. Reduced permission complexity
5. Simpler stock operations (no manufacture entry types)

To Apply to Existing Site:
==========================
```bash
bench --site your-site.name migrate
```

This will run the lightweight cleanup patch.

Note: This does NOT remove existing manufacturing data if present.
For complete removal of existing data, use the complete removal approach instead.
"""

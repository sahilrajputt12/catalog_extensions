"""Lightweight cleanup patch for existing sites

This patch applies lightweight ERPNext configuration to existing sites
by hiding/removing manufacturing-related features.
"""

import frappe


def execute():
    """Apply lightweight cleanup to existing site"""
    frappe.logger().info("Applying lightweight ERPNext configuration...")
    
    # Import and run the cleanup function
    from catalog_extensions.setup.lightweight_setup import (
        disable_manufacturing_features,
        post_install_lightweight_cleanup,
    )
    
    # Disable manufacturing settings and roles
    disable_manufacturing_features()
    
    # Remove manufacturing workspaces and restrict modules
    post_install_lightweight_cleanup()
    
    frappe.logger().info("Lightweight configuration applied successfully")

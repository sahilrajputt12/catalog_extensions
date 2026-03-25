#!/usr/bin/env python3
"""
Catalog Extensions - Image Zoom Configuration
Utilities for managing zoom mode settings per site
"""

import frappe

ZOOM_MODE_CLICK = "click"
ZOOM_MODE_HOVER = "hover"
ZOOM_SETTING_KEY = "catalog_image_zoom_mode"


def get_zoom_mode():
    """
    Get the configured zoom mode for the current site.
    Returns: 'click' or 'hover'
    Default is 'hover' for better UX
    """
    try:
        # Check site config first
        site_config = frappe.conf.get(ZOOM_SETTING_KEY)
        if site_config in [ZOOM_MODE_CLICK, ZOOM_MODE_HOVER]:
            return site_config

        # Default to hover mode
        return ZOOM_MODE_HOVER
    except Exception:
        return ZOOM_MODE_HOVER


def set_zoom_mode(mode):
    """
    Set the zoom mode for the current site.
    mode: 'click' or 'hover'
    """
    if mode not in [ZOOM_MODE_CLICK, ZOOM_MODE_HOVER]:
        raise ValueError(f"Invalid zoom mode: {mode}. Must be 'click' or 'hover'")

    # Update site_config.json
    site_path = frappe.get_site_path()
    site_config_path = f"{site_path}/site_config.json"

    try:
        with open(site_config_path, 'r') as f:
            import json
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}

    config[ZOOM_SETTING_KEY] = mode

    with open(site_config_path, 'w') as f:
        import json
        json.dump(config, f, indent=2)

    return f"Zoom mode set to: {mode}"


@frappe.whitelist(allow_guest=True)
def get_zoom_assets():
    """
    API endpoint to get the correct zoom assets based on site configuration.
    Returns dict with js and css paths.
    """
    mode = get_zoom_mode()

    if mode == ZOOM_MODE_HOVER:
        return {
            "mode": mode,
            "js": "/assets/catalog_extensions/js/image_zoom_hover.js",
            "css": "/assets/catalog_extensions/css/image_zoom_hover.css"
        }
    else:
        return {
            "mode": mode,
            "js": "/assets/catalog_extensions/js/image_zoom.js",
            "css": "/assets/catalog_extensions/css/image_zoom.css"
        }

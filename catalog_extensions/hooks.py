app_name = "catalog_extensions"
app_title = "Catalog Extensions"
app_publisher = "HaramiHost"
app_description = "Custom catalog features and extensions"
app_email = "support@example.com"
app_license = "MIT"

# Lightweight ERPNext Setup Configuration
# Overrides default ERPNext setup to exclude Manufacturing and other heavy modules
setup_wizard_stages = "catalog_extensions.setup.lightweight_setup.get_lightweight_setup_stages"
after_install = "catalog_extensions.setup.lightweight_setup.post_install_lightweight_cleanup"

# Website assets: load catalog facet sidebar logic and responsive overrides on website pages
web_include_css = [
    "/assets/catalog_extensions/css/catalog_overrides.css",
]

web_include_js = [
    "/assets/catalog_extensions/js/catalog_facets.js",
    "/assets/catalog_extensions/js/product_offers.js",
    "/assets/catalog_extensions/js/listing_quantity.js",
    "/assets/catalog_extensions/js/badges.js",
    "/assets/catalog_extensions/js/image_zoom.js",
]

# Override core webshop API to inject price range handling while preserving
# existing behavior and URL for get_product_filter_data
override_whitelisted_methods = {
    "webshop.webshop.api.get_product_filter_data": "catalog_extensions.api.get_product_filter_data_with_price",
}

# DocType event hooks
doc_events = {
    "Item": {
        # Whenever an Item is updated, sync its Consumer Discount to linked Website Items
        "on_update": [
            "catalog_extensions.api.sync_consumer_discount_to_website_item",
            # When an Item is marked as published_in_website via forms or Data Import,
            # automatically create a Website Item if one does not already exist.
            "catalog_extensions.api.ensure_website_item_for_published_item",
        ],
    }
}

# Override Website Item controller to extend image validation behaviour
override_doctype_class = {
    "Website Item": "catalog_extensions.overrides.website_item.WebsiteItem",
}

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "catalog_extensions",
# 		"logo": "/assets/catalog_extensions/logo.png",
# 		"title": "Catalog Extensions",
# 		"route": "/catalog_extensions",
# 		"has_permission": "catalog_extensions.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/catalog_extensions/css/catalog_extensions.css"
# app_include_js = "/assets/catalog_extensions/js/catalog_extensions.js"

# include js, css files in header of web template
# web_include_css = "/assets/catalog_extensions/css/catalog_extensions.css"
# web_include_js = "/assets/catalog_extensions/js/catalog_extensions.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "catalog_extensions/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "catalog_extensions/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "catalog_extensions.utils.jinja_methods",
# 	"filters": "catalog_extensions.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "catalog_extensions.install.before_install"
# after_install = "catalog_extensions.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "catalog_extensions.uninstall.before_uninstall"
# after_uninstall = "catalog_extensions.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "catalog_extensions.utils.before_app_install"
# after_app_install = "catalog_extensions.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "catalog_extensions.utils.before_app_uninstall"
# after_app_uninstall = "catalog_extensions.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "catalog_extensions.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"catalog_extensions.api.recompute_item_badges",
	],
}

# Testing
# -------

# before_tests = "catalog_extensions.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "catalog_extensions.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "catalog_extensions.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["catalog_extensions.utils.before_request"]
# after_request = ["catalog_extensions.utils.after_request"]

# Job Events
# ----------
# before_job = ["catalog_extensions.utils.before_job"]
# after_job = ["catalog_extensions.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"catalog_extensions.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []


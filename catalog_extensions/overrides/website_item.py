import frappe
from frappe import _

from webshop.webshop.doctype.website_item.website_item import WebsiteItem as CoreWebsiteItem


class WebsiteItem(CoreWebsiteItem):
    """Catalog Extensions override for Website Item.

    Keeps core behaviour but allows fully-qualified external HTTP(S) URLs
    for website_image without requiring a File record, while still
    enforcing public File validation for local file URLs.
    """

    def validate_website_image(self):
        if frappe.flags.in_import:
            return

        # No image set: nothing to validate
        if not self.website_image:
            return

        # Allow fully-qualified external URLs (e.g. R2, CDN, etc.)
        if self.website_image.startswith("http://") or self.website_image.startswith("https://"):
            return

        # For non-HTTP URLs, fall back to core behaviour: check File record
        file_doc = frappe.get_all(
            "File",
            filters={"file_url": self.website_image},
            fields=["name", "is_private"],
            order_by="is_private asc",
            limit_page_length=1,
        )

        if file_doc:
            file_doc = file_doc[0]

        if not file_doc:
            frappe.msgprint(
                _("Website Image {0} attached to Item {1} cannot be found").format(
                    self.website_image, self.name
                )
            )
            self.website_image = None

        elif file_doc.is_private:
            frappe.msgprint(_("Website Image should be a public file or website URL"))
            self.website_image = None

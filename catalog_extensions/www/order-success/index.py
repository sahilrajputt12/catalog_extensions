import frappe

sitemap = 1

def get_context(context):
    """Get context for order success page."""
    # Add homepage as parent
    context.body_class = "order-success-page"
    context.parents = [{"name": frappe._("Home"), "route": "/"}]
    
    # Get order ID from URL parameters or session
    order_id = frappe.form_dict.get('order_id')
    
    if not order_id:
        # Try to get from session (last placed order)
        order_id = frappe.session.get('last_order_id')
    
    context.order_id = order_id
    
    # Get additional order details if available
    if order_id:
        try:
            # Check if it's a Sales Order or Quotation
            if frappe.db.exists("Sales Order", order_id):
                order_doc = frappe.get_cached_doc("Sales Order", order_id)
                context.customer_name = order_doc.customer
                context.order_total = frappe.format(order_doc.grand_total, {'fieldtype': 'Currency'})
            elif frappe.db.exists("Quotation", order_id):
                order_doc = frappe.get_cached_doc("Quotation", order_id)
                context.customer_name = order_doc.party_name or order_doc.lead
                context.order_total = frappe.format(order_doc.grand_total, {'fieldtype': 'Currency'})
        except Exception:
            # If there's any error getting order details, just continue with basic info
            pass
    
    context.no_cache = 1
    return context

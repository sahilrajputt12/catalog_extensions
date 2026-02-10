frappe.listview_settings['Catalog Price Range'] = {
    add_fields: ['label', 'from_amount', 'to_amount', 'enabled', 'sort_order'],
    get_indicator: function(doc) {
        if (doc.enabled) {
            return [__("Enabled"), "green", "enabled,=,1"];
        } else {
            return [__("Disabled"), "gray", "enabled,=,0"];
        }
    }
};

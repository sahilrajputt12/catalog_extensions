frappe.ready(function() {
	// Only run on the cart page
	if (!window.location.pathname || !window.location.pathname.startsWith("/cart")) {
		return;
	}

	// Override the place_order function to redirect to success page
	const originalPlaceOrder = shopping_cart.place_order;
	
	shopping_cart.place_order = function(btn) {
		shopping_cart.freeze();

		return frappe.call({
			type: "POST",
			method: "webshop.webshop.shopping_cart.cart.place_order",
			btn: btn,
			callback: function(r) {
				if(r.exc) {
					shopping_cart.unfreeze();
					var msg = "";
					if(r._server_messages) {
						msg = JSON.parse(r._server_messages || []).join("<br>");
					}

					$("#cart-error")
						.empty()
						.html(msg || frappe._("Something went wrong!"))
						.toggle(true);
				} else {
					$(btn).hide();
					// Redirect to order success page instead of orders list
					window.location.href = '/order-success?order_id=' + encodeURIComponent(r.message);
				}
			}
		});
	};
});

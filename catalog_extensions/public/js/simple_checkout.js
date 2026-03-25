frappe.ready(function () {
	// Only run on the cart page
	if (!window.location.pathname || !window.location.pathname.startsWith("/cart")) {
		return;
	}

	frappe.call({
		method: "catalog_extensions.simple_checkout.get_simple_checkout_flags",
		freeze: false,
		callback: function (r) {
			if (!r || !r.message) return;

			var flags = r.message;

			if (!flags.enable_simple_checkout) {
				// Respect core behaviour when feature is disabled
				return;
			}

			// Hide payment-related UI on the cart sidebar
			if (flags.hide_payment_on_webshop) {
				// Hide only the payment summary and coupon section.
				// Keep the Place Order button visible so checkout can complete.
				[".cart-payment-addresses .payment-summary"].forEach(function (selector) {
					var els = document.querySelectorAll(selector);
					els.forEach(function (el) {
						el.remove();
					});
				});

				// Coupon section (if present)
				var couponButton = document.querySelector(".cart-payment-addresses .bt-coupon");
				if (couponButton && couponButton.parentElement) {
					// Remove the row containing coupon controls
					couponButton.parentElement.remove();
				}
			}

			// Hide shipping/billing address selection UI
			if (flags.hide_shipping_on_webshop) {
				// Remove the visible address cards (shipping + billing)
				var addressSections = document.querySelectorAll('[data-section="shipping-address"], [data-section="billing-address"]');
				addressSections.forEach(function (el) {
					el.remove();
				});

				// Remove the "Billing Address is same as Shipping Address" checkbox row if present
				var sameBillingInput = document.getElementById("input_same_billing");
				if (sameBillingInput && sameBillingInput.closest('.checkbox')) {
					sameBillingInput.closest('.checkbox').remove();
				}
			}
		},
	});
});

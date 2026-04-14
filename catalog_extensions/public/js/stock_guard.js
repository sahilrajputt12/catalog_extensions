frappe.ready(function () {
	applyProductStockGuard();
	applyCartStockGuard();
});

const stockGuardCache = {};

function renderStockAlert(message, state) {
	if (!message) {
		return "";
	}

	return (
		'<div class="ce-stock-alert ce-stock-alert--' +
		frappe.utils.escape_html(state || "in_stock") +
		'">' +
		frappe.utils.escape_html(message) +
		"</div>"
	);
}

function fetchStockGuardData(itemCode, callback, options) {
	if (!itemCode) {
		return;
	}

	const force = options && options.force;
	if (!force && stockGuardCache[itemCode]) {
		callback(stockGuardCache[itemCode]);
		return;
	}

	frappe.call({
		type: "POST",
		method: "webshop.webshop.shopping_cart.product_info.get_product_info_for_website",
		args: { item_code: itemCode, skip_quotation_creation: false },
		callback: function (r) {
			const payload = {
				product_info: r && r.message ? (r.message.product_info || {}) : {},
				cart_settings: r && r.message ? (r.message.cart_settings || {}) : {},
			};
			stockGuardCache[itemCode] = payload;
			callback(payload);
		},
	});
}

function applyProductStockGuard() {
	if (!document.querySelector(".item-cart") || typeof get_item_code !== "function") {
		return;
	}

	const itemCode = get_item_code();
	if (!itemCode) {
		return;
	}

	fetchStockGuardData(itemCode, function (stockData) {
		renderProductStockState(stockData);
	});

	document.addEventListener(
		"click",
		function (event) {
			const upButton = event.target.closest(
				"#item-spinner .number-spinner button[data-dir='up'], #item-update-cart .number-spinner button[data-dir='up'], .item-cart .number-spinner button[data-dir='up']"
			);
			if (!upButton) {
				return;
			}

			if (upButton.dataset.stockGuardBlocked === "1") {
				event.preventDefault();
				event.stopPropagation();
				frappe.msgprint({
					message: upButton.dataset.stockMessage || __("Out of stock"),
					indicator: "orange",
				});
				return;
			}

			const qtyInput = upButton.closest(".number-spinner")?.querySelector(".cart-qty");
			const maxOrderable = qtyInput ? parseFloat(qtyInput.dataset.maxOrderableQty || "") : NaN;
			const currentQty = qtyInput ? parseFloat(qtyInput.value || "0") : 0;
			if (!Number.isNaN(maxOrderable) && currentQty >= maxOrderable) {
				event.preventDefault();
				event.stopPropagation();
				frappe.msgprint({
					message: (qtyInput && qtyInput.dataset.stockMessage) || __("Out of stock"),
					indicator: "orange",
				});
			}
		},
		true
	);

	document.addEventListener(
		"change",
		function (event) {
			const input = event.target.closest("#item-spinner .cart-qty, #item-update-cart .cart-qty, .item-cart .cart-qty");
			if (!input) {
				return;
			}

			const maxOrderable = parseFloat(input.dataset.maxOrderableQty || "");
			const requestedQty = parseFloat(input.value || "0");
			if (!Number.isNaN(maxOrderable) && requestedQty > maxOrderable) {
				input.value = String(maxOrderable);
				frappe.msgprint({
					message: input.dataset.stockMessage || __("Out of stock"),
					indicator: "orange",
				});
			}
		},
		true
	);
}

function renderProductStockState(stockData) {
	const stockNode = document.querySelector(".item-stock");
	const itemCart = document.querySelector(".item-cart");
	if (!stockNode || !itemCart) {
		return;
	}

	const productInfo = stockData && stockData.product_info ? stockData.product_info : {};
	const cartSettings = stockData && stockData.cart_settings ? stockData.cart_settings : {};
	const showStockAvailability = Boolean(cartSettings.show_stock_availability);
	const stockState = productInfo.stock_state || "";
	const stockMessage = productInfo.stock_message || "";
	const canAddToCart = productInfo.can_add_to_cart !== false;
	const canIncreaseQty = productInfo.can_increase_qty !== false;
	const maxOrderableQty = parseFloat(productInfo.max_orderable_qty);
	const useCoreOutOfStockView = stockState === "out_of_stock" && !productInfo.qty;

	if (!showStockAvailability) {
		stockNode.innerHTML = "";
		stockNode.classList.add("hide");
	} else if (stockMessage && !useCoreOutOfStockView) {
		stockNode.innerHTML = renderStockAlert(stockMessage, stockState);
		stockNode.classList.remove("hide");
	}

	document.querySelectorAll(".btn-add-to-cart").forEach(function (button) {
		if (useCoreOutOfStockView) {
			return;
		}
		button.disabled = !canAddToCart;
		button.classList.toggle("disabled", !canAddToCart);
		button.classList.toggle("hide", !canAddToCart);
	});

	document
		.querySelectorAll("#item-spinner .cart-qty, #item-update-cart .cart-qty, .item-cart .cart-qty")
		.forEach(function (input) {
			if (!Number.isNaN(maxOrderableQty)) {
				input.dataset.maxOrderableQty = String(maxOrderableQty);
			} else {
				delete input.dataset.maxOrderableQty;
			}

			input.dataset.stockMessage = stockMessage;
			if (!Number.isNaN(maxOrderableQty) && parseFloat(input.value || "0") > maxOrderableQty) {
				input.value = String(maxOrderableQty);
			}
		});

	document
		.querySelectorAll(
			"#item-spinner .number-spinner button[data-dir='up'], #item-update-cart .number-spinner button[data-dir='up'], .item-cart .number-spinner button[data-dir='up']"
		)
		.forEach(function (button) {
			const qtyInput = button.closest(".number-spinner")?.querySelector(".cart-qty");
			const currentQty = qtyInput ? parseFloat(qtyInput.value || "0") : 0;
			const blockedByMax = !Number.isNaN(maxOrderableQty) && currentQty >= maxOrderableQty;
			button.dataset.stockGuardBlocked = canIncreaseQty && !blockedByMax ? "0" : "1";
			button.dataset.stockMessage = stockMessage;
			button.disabled = !canIncreaseQty || blockedByMax;
			button.classList.toggle("disabled", !canIncreaseQty || blockedByMax);
		});
}

function applyCartStockGuard() {
	if (!window.location.pathname.startsWith("/cart")) {
		return;
	}

	const decorateRow = function (row, stockData) {
		if (!row || !stockData) {
			return;
		}

		const productInfo = stockData.product_info || {};
		const cartSettings = stockData.cart_settings || {};
		const showStockAvailability = Boolean(cartSettings.show_stock_availability);
		const input = row.querySelector(".cart-qty");
		const upButton = row.querySelector(".cart-btn[data-dir='up']");
		const subtitle = row.querySelector(".item-subtitle");
		const maxOrderable = parseFloat(productInfo.max_orderable_qty || "");
		const currentQty = input ? parseFloat(input.value || "0") : 0;
		const blocked = productInfo.can_increase_qty === false || (!Number.isNaN(maxOrderable) && currentQty >= maxOrderable);

		if (input) {
			input.dataset.maxOrderableQty = Number.isNaN(maxOrderable) ? "" : String(maxOrderable);
			input.dataset.stockMessage = productInfo.stock_message || "";
			input.dataset.stockState = productInfo.stock_state || "";
		}

		if (upButton) {
			upButton.disabled = blocked;
			upButton.classList.toggle("disabled", blocked);
			upButton.dataset.stockMessage = productInfo.stock_message || "";
		}

		let note = row.querySelector(".ce-cart-stock-note");
		const shouldShowNote =
			showStockAvailability &&
			(
				(productInfo.stock_state === "low_stock" && Boolean(productInfo.stock_message)) ||
				(productInfo.stock_state === "out_of_stock" && currentQty > 0)
			);

		if (!shouldShowNote) {
			if (note) {
				note.remove();
			}
			return;
		}

		if (!note) {
			note = document.createElement("div");
			note.className = "ce-cart-stock-note";
			if (subtitle && subtitle.parentNode) {
				subtitle.parentNode.insertBefore(note, subtitle.nextSibling);
			}
		}

		note.className = "ce-cart-stock-note ce-cart-stock-note--" + (productInfo.stock_state || "low_stock");
		note.textContent = productInfo.stock_message || __("Out of stock");
	};

	const attach = function () {
		const cartRoot = document.querySelector(".cart-items");
		if (!cartRoot) {
			return;
		}

		cartRoot.querySelectorAll(".cart-qty").forEach(function (input) {
			const row = input.closest("tr");
			const itemCode = input.dataset.itemCode;
			if (!row || !itemCode) {
				return;
			}

			fetchStockGuardData(
				itemCode,
				function (stockData) {
					decorateRow(row, stockData);
				},
				{ force: true }
			);
		});
	};

	attach();

	const cartRoot = document.querySelector(".cart-items");
	if (cartRoot) {
		const observer = new MutationObserver(function () {
			attach();
		});
		observer.observe(cartRoot, { childList: true, subtree: true });

		cartRoot.addEventListener(
			"click",
			function (event) {
				const upButton = event.target.closest(".cart-btn[data-dir='up']");
				if (!upButton) {
					return;
				}

				const row = upButton.closest("tr");
				const input = row ? row.querySelector(".cart-qty") : null;
				if (!input) {
					return;
				}

				const maxOrderable = parseFloat(input.dataset.maxOrderableQty || "");
				const currentQty = parseFloat(input.value || "0");
				if (!Number.isNaN(maxOrderable) && currentQty >= maxOrderable) {
					event.preventDefault();
					event.stopPropagation();
					frappe.msgprint({
						message: input.dataset.stockMessage || __("Out of stock"),
						indicator: "orange",
					});
				}
			},
			true
		);

		cartRoot.addEventListener(
			"change",
			function (event) {
				const input = event.target.closest(".cart-qty");
				if (!input) {
					return;
				}

				const maxOrderable = parseFloat(input.dataset.maxOrderableQty || "");
				if (Number.isNaN(maxOrderable)) {
					return;
				}

				const requestedQty = parseFloat(input.value || "0");
				if (requestedQty > maxOrderable) {
					input.value = String(maxOrderable);
					frappe.msgprint({
						message: input.dataset.stockMessage || __("Out of stock"),
						indicator: "orange",
					});
					event.stopPropagation();
				}
			},
			true
		);
	}
}

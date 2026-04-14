frappe.ready(() => {
	const orderContext = getOrderContext();
	if (!orderContext) {
		return;
	}

	window.catalogOrderTrackingContext = orderContext;

	const mountPoint = ensureTrackingMount(orderContext);
	if (!mountPoint) {
		return;
	}

	loadTracking(mountPoint, orderContext);
});

function getOrderContext() {
	if (window.doc_info && window.doc_info.doctype_name && window.doc_info.doctype) {
		return {
			orderName: window.doc_info.doctype_name,
			orderDoctype: window.doc_info.doctype,
			pageType: "order",
		};
	}

	return null;
}

function ensureTrackingMount(orderContext) {
	const existing = document.getElementById("catalog-delivery-tracking");
	if (existing) {
		existing.classList.add("ce-delivery-tracking");
		return existing;
	}

	if (orderContext.pageType !== "order") {
		return null;
	}

	const anchor = document.querySelector(".order-container") || document.querySelector(".indicator-container");
	if (!anchor || !anchor.parentNode) {
		return null;
	}

	const mount = document.createElement("section");
	mount.id = "catalog-delivery-tracking";
	mount.className = "ce-delivery-tracking";
	anchor.parentNode.insertBefore(mount, anchor);
	return mount;
}

function loadTracking(mountPoint, orderContext) {
	mountPoint.innerHTML = buildStateMarkup("Loading delivery updates...");

	frappe.call({
		method: "catalog_extensions.api.get_order_delivery_tracking",
		args: {
			order_name: orderContext.orderName,
			order_doctype: orderContext.orderDoctype,
		},
		callback: (response) => renderTracking(mountPoint, response.message || {}),
		error: () => {
			mountPoint.innerHTML = buildStateMarkup(
				"Delivery updates are not available right now. Please refresh in a moment."
			);
		},
	});
}

function renderTracking(mountPoint, data) {
	window.catalogOrderTrackingData = data || {};

	const order = data.order || {};
	const flowVisibility = data.flow_visibility || {};
	const shipments = Array.isArray(data.shipments) ? data.shipments : [];
	const returnShipments = Array.isArray(data.return_shipments) ? data.return_shipments : [];
	const milestones = Array.isArray(data.milestones) ? data.milestones : [];
	const statusSignals = data.status_signals || {};
	const paymentRequests = Array.isArray(data.payment_requests) ? data.payment_requests : [];
	const actions = data.actions || {};
	const normalizedLabel = data.normalized_status_label || data.headline || "Processing";
	const normalizedNote =
		data.normalized_status_note || data.status_note || "We will post delivery updates here as your order moves forward.";
	const showShipmentTraceability = Boolean(flowVisibility.show_shipment_traceability);
	const showReturnTraceability = Boolean(flowVisibility.show_return_traceability);
	const showPaymentTraceability = Boolean(flowVisibility.show_payment_traceability);

	const shipmentMarkup = showShipmentTraceability && shipments.length
		? shipments.map(buildShipmentCard).join("")
		: showShipmentTraceability
			? '<div class="ce-tracking-empty">A courier tracking number will appear here as soon as the shipment is booked.</div>'
			: "";
	const returnShipmentMarkup = showReturnTraceability && returnShipments.length
		? returnShipments.map(buildShipmentCard).join("")
		: showReturnTraceability
			? '<div class="ce-tracking-empty">Return shipment updates will appear here when the reverse pickup starts.</div>'
			: "";

	const signalMarkup = buildSignalMarkup(statusSignals, paymentRequests, flowVisibility);
	const shippingMarkup = buildShippingMarkup(order, flowVisibility);
	const trackingTreeMarkup =
		showShipmentTraceability || showReturnTraceability || showPaymentTraceability
			? buildTrackingTreeMarkup(milestones, shipmentMarkup, returnShipmentMarkup, flowVisibility)
			: "";
	const tabsMarkup = buildTabsMarkup(trackingTreeMarkup, flowVisibility);

	mountPoint.innerHTML = `
		<div class="ce-tracking-card ce-tracking-card--compact">
			<div class="ce-tracking-header">
				<div>
					<p class="ce-tracking-eyebrow">Order updates</p>
					<h3>${escapeHtml(normalizedLabel)}</h3>
					<p class="ce-tracking-note">${escapeHtml(normalizedNote)}</p>
				</div>
			</div>
			<div class="ce-tracking-stats">
				<div class="ce-tracking-stat">
					<span>Total</span>
					<strong>${escapeHtml(formatCurrency(order.grand_total, order.currency))}</strong>
				</div>
				<div class="ce-tracking-stat">
					<span>Placed on</span>
					<strong>${escapeHtml(formatDate(order.transaction_date))}</strong>
				</div>
				<div class="ce-tracking-stat">
					<span>Delivered</span>
					<strong>${escapeHtml(formatPercent(order.per_delivered))}</strong>
				</div>
			</div>
			${signalMarkup}
			${shippingMarkup}
			${tabsMarkup}
		</div>
	`;

	bindCollapsiblePanels(mountPoint);
	syncPrimaryPaymentAction(order, statusSignals, flowVisibility);
	renderPageActions(actions);
}

function syncPrimaryPaymentAction(order, statusSignals, flowVisibility) {
	const actionButton = document.getElementById("pay-for-order");
	if (!actionButton) {
		return;
	}

	if (flowVisibility && flowVisibility.payment_active === false) {
		actionButton.style.display = "none";
		return;
	}

	const settledInvoices = Array.isArray(statusSignals && statusSignals.settled_invoices)
		? statusSignals.settled_invoices
		: [];
	const paymentReceived = Boolean(statusSignals && statusSignals.payment_received);

	if (paymentReceived) {
		const receiptHref = getReceiptLink(order, settledInvoices);
		if (!receiptHref) {
			actionButton.style.display = "none";
			return;
		}

		actionButton.href = receiptHref;
		actionButton.textContent = "Payment Receipt";
		actionButton.setAttribute("target", "_blank");
		actionButton.setAttribute("rel", "noopener noreferrer");
		actionButton.style.display = "";
		return;
	}

	actionButton.removeAttribute("target");
	actionButton.removeAttribute("rel");
	actionButton.style.display = "";
}

function getReceiptLink(order, settledInvoices) {
	if (settledInvoices.length) {
		return `/printview?doctype=Sales%20Invoice&name=${encodeURIComponent(settledInvoices[0])}&format=Standard`;
	}

	if (order && order.doctype === "Sales Invoice" && order.name) {
		return `/printview?doctype=Sales%20Invoice&name=${encodeURIComponent(order.name)}&format=Standard`;
	}

	return "";
}

function shouldHideTrackingDetails(flowVisibility) {
	if (!flowVisibility || !flowVisibility.simple_checkout_enabled) {
		return false;
	}

	return flowVisibility.show_payment_traceability === false
		|| flowVisibility.show_shipment_traceability === false;
}

function buildTabsMarkup(trackingTreeMarkup, flowVisibility) {
	if (shouldHideTrackingDetails(flowVisibility)) {
		return "";
	}

	const trackingPanel = trackingTreeMarkup
		? `<div class="ce-tracking-tab-panel is-active" data-tab-panel="tracking">
				${buildCollapsiblePanel(
					"tracking-details",
					"Tracking details",
					"See where your order is right now.",
					trackingTreeMarkup,
					true
				)}
			</div>`
		: `<div class="ce-tracking-tab-panel is-active" data-tab-panel="tracking">
				${buildCollapsiblePanel(
					"tracking-details",
					"Tracking details",
					"See where your order is right now.",
					'<div class="ce-tracking-empty">Tracking updates will appear here as your order moves forward.</div>',
					true
				)}
			</div>`;

	return `
		<div class="ce-tracking-tab-panels">
			${trackingPanel}
		</div>
	`;
}

function buildCollapsiblePanel(key, title, message, content, isOpen) {
	return `
		<section class="ce-collapsible-panel ${isOpen ? "is-open" : ""}" data-collapsible-panel="${key}">
			<button
				type="button"
				class="ce-collapsible-trigger"
				data-collapsible-trigger="${key}"
				aria-expanded="${isOpen ? "true" : "false"}">
				<span class="ce-collapsible-copy">
					<span class="ce-collapsible-title">${escapeHtml(title)}</span>
					<span class="ce-collapsible-message">${escapeHtml(message)}</span>
				</span>
				<span class="ce-collapsible-icon" aria-hidden="true"></span>
			</button>
			<div class="ce-collapsible-body" data-collapsible-body="${key}">
				${content}
			</div>
		</section>
	`;
}

function bindCollapsiblePanels(mountPoint) {
	const triggers = Array.from(mountPoint.querySelectorAll(".ce-collapsible-trigger"));
	triggers.forEach((trigger) => {
		trigger.addEventListener("click", () => {
			const key = trigger.getAttribute("data-collapsible-trigger");
			const panel = mountPoint.querySelector(`[data-collapsible-panel="${key}"]`);
			if (!panel) {
				return;
			}

			const isOpen = panel.classList.toggle("is-open");
			trigger.setAttribute("aria-expanded", isOpen ? "true" : "false");
		});
	});
}

function buildTrackingTreeMarkup(milestones, shipmentMarkup, returnShipmentMarkup, flowVisibility) {
	if (!milestones.length) {
		return "";
	}

	return `
		<ol class="ce-tracking-tree">
			${milestones
				.map(
					(step) => `
						<li class="ce-tracking-node ${step.done ? "is-done" : ""} ${step.active ? "is-active" : ""}">
							<span class="ce-tracking-node-dot"></span>
								<div class="ce-tracking-node-body">
									<div class="ce-tracking-step-label">${escapeHtml(step.label || "")}</div>
									<div class="ce-tracking-step-date">${escapeHtml(formatDate(step.date))}</div>
									${
										step.show_shipments
										&& canShowStepShipments(step, flowVisibility)
											? buildStepShipmentDisclosure(
												step,
												isReturnShipmentStep(step) ? returnShipmentMarkup : shipmentMarkup
											)
										: ""
								}
							</div>
						</li>
					`
				)
				.join("")}
		</ol>
	`;
}

function buildStepShipmentDisclosure(step, content) {
	if (!content) {
		return "";
	}

	const key = cssEscape(step.key || "shipment");
	const open = step.active || step.done;

	return `
		<details class="ce-tracking-step-disclosure"${open ? " open" : ""}>
			<summary class="ce-tracking-step-summary">
				<span>${escapeHtml(step.label || (isReturnShipmentStep(step) ? "Return shipment" : "Shipment initiated"))}</span>
			</summary>
			<div class="ce-tracking-branches">
				${content}
			</div>
		</details>
	`;
}

function buildActivityLogMarkup(context) {
	const entries = [];

	if (context.order && context.order.transaction_date) {
		entries.push({
			title: "Order placed",
			text: "Your order was successfully placed and is now being prepared.",
			date: context.order.transaction_date,
		});
	}

	(context.paymentRequests || []).forEach((request) => {
		entries.push({
			title: "Payment update",
			text: request.status || "Payment request created for this order.",
			date: request.creation || request.modified,
		});
	});

	(context.shipments || []).forEach((shipment) => {
		entries.push({
			title: "Shipment update",
			text: shipment.tracking_status_info || shipment.tracking_status || shipment.status || "Shipment created.",
			date: shipment.pickup_date || shipment.modified || shipment.creation,
		});
	});

	(context.returnShipments || []).forEach((shipment) => {
		entries.push({
			title: "Return update",
			text: shipment.tracking_status_info || shipment.tracking_status || shipment.status || "Return shipment created.",
			date: shipment.pickup_date || shipment.modified || shipment.creation,
		});
	});

	(context.milestones || []).forEach((step) => {
		if (!step || !step.label) {
			return;
		}
		entries.push({
			title: step.label,
			text: step.active ? "This is the current stage for your order." : step.done ? "This stage has been completed." : "This stage is waiting for the previous step to finish.",
			date: step.date,
		});
	});

	const uniqueEntries = dedupeActivityEntries(entries)
		.filter((entry) => entry.title || entry.text || entry.date)
		.sort((a, b) => compareActivityDates(b.date, a.date));

	if (!uniqueEntries.length) {
		return "";
	}

	return `
		<ol class="ce-activity-log">
			${uniqueEntries
				.map(
					(entry) => `
						<li class="ce-activity-log-item">
							<div class="ce-activity-log-dot"></div>
							<div class="ce-activity-log-body">
								<div class="ce-activity-log-title">${escapeHtml(entry.title || context.normalizedLabel || "Order update")}</div>
								${entry.text ? `<div class="ce-activity-log-text">${escapeHtml(entry.text)}</div>` : ""}
								${entry.date ? `<div class="ce-activity-log-date">${escapeHtml(formatDate(entry.date))}</div>` : ""}
							</div>
						</li>
					`
				)
				.join("")}
		</ol>
	`;
}

function dedupeActivityEntries(entries) {
	const seen = new Set();
	return entries.filter((entry) => {
		const key = [entry.title || "", entry.text || "", entry.date || ""].join("|");
		if (seen.has(key)) {
			return false;
		}
		seen.add(key);
		return true;
	});
}

function compareActivityDates(left, right) {
	const leftTime = parseActivityDate(left);
	const rightTime = parseActivityDate(right);
	return leftTime - rightTime;
}

function parseActivityDate(value) {
	if (!value) {
		return 0;
	}

	const parsed = Date.parse(value);
	return Number.isNaN(parsed) ? 0 : parsed;
}

function canShowStepShipments(step, flowVisibility) {
	if (isReturnShipmentStep(step)) {
		return Boolean(flowVisibility.show_return_traceability);
	}

	return Boolean(flowVisibility.show_shipment_traceability);
}

function isReturnShipmentStep(step) {
	return Boolean(
		step
		&& (step.shipment_group === "return"
			|| String(step.key || "").startsWith("return"))
	);
}

function buildSignalMarkup(statusSignals, paymentRequests, flowVisibility) {
	const chips = [];

	if (statusSignals.order_status) {
		chips.push(`Order: ${statusSignals.order_status}`);
	}
	if (
		flowVisibility.show_payment_traceability &&
		statusSignals.payment_request_statuses &&
		statusSignals.payment_request_statuses.length
	) {
		chips.push(`Payment: ${statusSignals.payment_request_statuses[0]}`);
	} else if (flowVisibility.show_payment_traceability && paymentRequests.length) {
		chips.push(`Payment: ${paymentRequests[0].status || "Requested"}`);
	}
	if (
		flowVisibility.show_shipment_traceability &&
		statusSignals.shipment_tracking_statuses &&
		statusSignals.shipment_tracking_statuses.length
	) {
		chips.push(`Tracking: ${statusSignals.shipment_tracking_statuses[0]}`);
	}
	if (
		flowVisibility.show_return_traceability &&
		statusSignals.return_tracking_statuses &&
		statusSignals.return_tracking_statuses.length
	) {
		chips.push(`Return: ${statusSignals.return_tracking_statuses[0]}`);
	} else if (
		flowVisibility.show_return_traceability &&
		statusSignals.return_record_statuses &&
		statusSignals.return_record_statuses.length
	) {
		chips.push(`Return: ${statusSignals.return_record_statuses[0]}`);
	}

	if (!chips.length) {
		return "";
	}

	return `
		<div class="ce-tracking-signal-row">
			${chips.map((chip) => `<span class="ce-tracking-chip ce-tracking-chip--signal">${escapeHtml(chip)}</span>`).join("")}
		</div>
	`;
}

function buildShippingMarkup(order, flowVisibility) {
	if (!flowVisibility.show_shipment_traceability) {
		return "";
	}

	if (!order.address_display && !order.contact_display && !order.shipping_address_name) {
		return "";
	}

	return `
		<div class="ce-tracking-meta ce-tracking-meta--shipping">
			<div class="ce-tracking-meta-title">Shipping information</div>
			${order.shipping_address_name ? `<div class="ce-tracking-ship-name">${escapeHtml(order.shipping_address_name)}</div>` : ""}
			${order.contact_display ? `<p class="ce-tracking-ship-text">${escapeHtml(order.contact_display)}</p>` : ""}
			${order.address_display ? `<div class="ce-tracking-ship-text">${formatMultiline(order.address_display)}</div>` : ""}
		</div>
	`;
}

function buildShipmentSummaryMarkup(shipments, returnShipments, flowVisibility) {
	const showShipmentTraceability = Boolean(flowVisibility.show_shipment_traceability);
	const showReturnTraceability = Boolean(flowVisibility.show_return_traceability);

	if (!showShipmentTraceability && !showReturnTraceability) {
		return "";
	}

	const outboundMarkup = showShipmentTraceability
		? shipments.length
			? shipments.map(buildShipmentCard).join("")
			: '<div class="ce-tracking-empty">Shipment tracking will appear here once the courier booking is created.</div>'
		: "";

	const returnMarkup = showReturnTraceability && returnShipments.length
		? `
			<div class="ce-tracking-meta">
				<div class="ce-tracking-meta-title">Return shipment tracking</div>
				<div class="ce-tracking-branches">${returnShipments.map(buildShipmentCard).join("")}</div>
			</div>
		`
		: "";

	if (!outboundMarkup && !returnMarkup) {
		return "";
	}

	return `
		<div class="ce-tracking-meta">
			<div class="ce-tracking-meta-title">Shipment tracking</div>
			<div class="ce-tracking-branches">${outboundMarkup}</div>
		</div>
		${returnMarkup}
	`;
}

function renderPageActions(actions) {
	const menu = getOrderActionsMenu();
	if (!menu) {
		return;
	}

	menu.querySelectorAll(".ce-order-action-item, .ce-order-action-divider").forEach((node) => {
		node.remove();
	});

	const items = [];
	if (actions.show_cancel_actions !== false) {
		pushActionMenuItem(items, "cancel", actions.cancel_label || "Cancel order", actions.can_cancel, actions.cancel_reason);
	}
	if (actions.show_shipping_actions !== false) {
		pushActionMenuItem(items, "return", actions.return_label || "Return request", actions.can_return, actions.return_reason);
	}
	if (actions.show_payment_actions !== false) {
		pushActionMenuItem(items, "refund", actions.refund_label || "Request refund", actions.can_refund, actions.refund_reason);
	}

	if (items.length) {
		const divider = document.createElement("div");
		divider.className = "dropdown-divider ce-order-action-divider";
		menu.appendChild(divider);
		items.forEach((item) => menu.appendChild(item));
	}
}

function getOrderActionsMenu() {
	const actionButtons = Array.from(document.querySelectorAll(".dropdown-toggle"));
	const orderActionButton = actionButtons.find((button) => {
		const label = button.querySelector(".font-md");
		return label && label.textContent.trim() === "Actions" && button.closest(".row");
	});

	if (!orderActionButton) {
		return null;
	}

	const dropdown = orderActionButton.closest(".dropdown");
	return dropdown ? dropdown.querySelector(".dropdown-menu[role='menu']") || dropdown.querySelector(".dropdown-menu") : null;
}

function pushActionMenuItem(items, action, label, enabled, reason) {
	if (!enabled && !reason) {
		return;
	}

	items.push(buildActionMenuItem(action, label, enabled, reason));
}

function buildActionMenuItem(action, label, enabled, reason) {
	const item = document.createElement("a");
	item.href = "#";
	item.className = `dropdown-item ce-order-action-item${enabled ? "" : " disabled text-muted"}`;
	item.setAttribute("data-action", action);
	item.setAttribute("aria-disabled", enabled ? "false" : "true");
	item.textContent = label;
	if (reason) {
		item.setAttribute("data-unavailable-reason", reason);
		item.setAttribute("title", reason);
	}
	item.addEventListener("click", (event) => {
		event.preventDefault();
		handleOrderAction(item);
	});
	return item;
}

function handleOrderAction(button) {
	if (button.getAttribute("aria-disabled") === "true") {
		const reason = button.getAttribute("data-unavailable-reason");
		if (reason) {
			frappe.msgprint({
				title: "Action unavailable",
				message: reason,
				indicator: "orange",
			});
		}
		return;
	}

	const action = button.getAttribute("data-action");
	const orderContext = window.catalogOrderTrackingContext;
	const mountPoint = document.getElementById("catalog-delivery-tracking");
	if (!action || !orderContext || !mountPoint) {
		return;
	}

	const config = {
		cancel: {
			title: "Cancel order",
			label: "Cancel order",
			method: "catalog_extensions.api.cancel_portal_order",
			message: "Your order has been cancelled.",
			confirmText: "Cancel this order now?",
			fieldLabel: "Cancellation reason",
		},
		return: {
			title: "Return request",
			label: "Return request",
			method: "catalog_extensions.api.create_portal_return_request",
			message: "Your return request has been submitted for review.",
			confirmText: "Create a return request for this order?",
			fieldLabel: "Return reason",
		},
		refund: {
			title: "Request refund",
			label: "Request refund",
			method: "catalog_extensions.api.create_portal_refund_request",
			message: "Your refund request has been submitted.",
			confirmText: "Request a refund for this order now?",
			fieldLabel: "Refund reason",
		},
	}[action];

	if (!config) {
		return;
	}

	if (action === "return") {
		openReturnRequestDialog(button, mountPoint, orderContext, config);
		return;
	}

	if (!window.confirm(config.confirmText)) {
		return;
	}

	const reason = getActionReason(config);
	if (reason === false) {
		return;
	}

	submitOrderAction(button, mountPoint, orderContext, config, reason);
}

function openReturnRequestDialog(button, mountPoint, orderContext, config) {
	const trackingData = window.catalogOrderTrackingData || {};
	const eligibleItems = Array.isArray(trackingData.eligible_return_items) ? trackingData.eligible_return_items : [];
	const selectableItems = eligibleItems.filter((item) => item && item.is_return_eligible);

	if (!selectableItems.length) {
		frappe.msgprint({
			title: config.title || "Return request",
			message: "No eligible items are available for return on this order.",
			indicator: "orange",
		});
		return;
	}

	const dialog = buildReturnDialog(selectableItems, trackingData);
	document.body.appendChild(dialog.overlay);
	dialog.reasonField.focus();

	dialog.overlay.addEventListener("click", (event) => {
		if (event.target === dialog.overlay) {
			closeReturnDialog(dialog.overlay);
		}
	});

	bindReturnDialogInputs(dialog.form, selectableItems);

	dialog.cancelButton.addEventListener("click", () => closeReturnDialog(dialog.overlay));
	dialog.closeButton.addEventListener("click", () => closeReturnDialog(dialog.overlay));
	dialog.form.addEventListener("submit", (event) => {
		event.preventDefault();
		const selection = collectReturnSelection(dialog.form, selectableItems);
		if (!selection.items.length) {
			showReturnDialogError(dialog.errorNode, "Select at least one item and quantity to continue.");
			return;
		}

		showReturnDialogError(dialog.errorNode, "");
		closeReturnDialog(dialog.overlay);
		submitOrderAction(button, mountPoint, orderContext, config, selection.reason, {
			selected_items: selection.items,
		}).then((message) => {
			if (selection.files.length && message && message.return_request) {
				uploadReturnEvidenceFiles(message.return_request, selection.files)
					.then((count) => {
						if (count > 0) {
							frappe.show_alert({
								message: `${count} evidence file${count === 1 ? "" : "s"} uploaded to your return request.`,
								indicator: "blue",
							});
						}
					})
					.catch(() => {
						frappe.show_alert({
							message: "Your return request was created, but some evidence files could not be uploaded.",
							indicator: "orange",
						});
					});
			}
		});
	});
}

function buildReturnDialog(eligibleItems, trackingData) {
	const overlay = document.createElement("div");
	overlay.className = "ce-return-dialog-backdrop";
	overlay.innerHTML = `
		<div class="ce-return-dialog" role="dialog" aria-modal="true" aria-labelledby="ce-return-dialog-title">
			<div class="ce-return-dialog-header">
				<div>
					<p class="ce-return-dialog-eyebrow">Return request</p>
					<h3 id="ce-return-dialog-title">Select items to return</h3>
					<p class="ce-return-dialog-note">
						Choose the eligible items and quantities to include in this return.
						${trackingData.return_window_end_date ? ` Return window ends on ${escapeHtml(formatDate(trackingData.return_window_end_date))}.` : ""}
					</p>
				</div>
				<button type="button" class="ce-return-dialog-close" aria-label="Close return dialog">&times;</button>
			</div>
			<form class="ce-return-dialog-form">
				<div class="ce-return-dialog-list">
					${eligibleItems.map((item, index) => buildReturnItemRow(item, index)).join("")}
				</div>
				<label class="ce-return-dialog-reason">
					<span>Return reason</span>
					<textarea name="return_reason" rows="3" placeholder="Optional reason for the return"></textarea>
				</label>
				<label class="ce-return-dialog-reason">
					<span>Photos or documents</span>
					<input type="file" name="return_evidence" multiple accept="image/*,.pdf">
				</label>
				<div class="ce-return-dialog-error" aria-live="polite"></div>
				<div class="ce-return-dialog-actions">
					<button type="button" class="btn btn-secondary ce-return-dialog-cancel">Cancel</button>
					<button type="submit" class="btn btn-primary ce-return-dialog-submit">Submit return request</button>
				</div>
			</form>
		</div>
	`;

	const form = overlay.querySelector(".ce-return-dialog-form");
	const reasonField = overlay.querySelector('textarea[name="return_reason"]');
	const cancelButton = overlay.querySelector(".ce-return-dialog-cancel");
	const closeButton = overlay.querySelector(".ce-return-dialog-close");
	const errorNode = overlay.querySelector(".ce-return-dialog-error");

	return { overlay, form, reasonField, cancelButton, closeButton, errorNode };
}

function buildReturnItemRow(item, index) {
	const inputId = `ce-return-item-${index}`;
	const qtyId = `ce-return-qty-${index}`;
	return `
		<label class="ce-return-item" for="${escapeAttribute(inputId)}">
			<div class="ce-return-item-main">
				<input
					type="checkbox"
					id="${escapeAttribute(inputId)}"
					name="return_item"
					value="${escapeAttribute(item.sales_invoice_item || "")}"
					data-max-qty="${escapeAttribute(String(item.remaining_returnable_qty || 0))}">
				<div class="ce-return-item-copy">
					<div class="ce-return-item-title">${escapeHtml(item.item_name || item.item_code || "Item")}</div>
					<div class="ce-return-item-meta">
						<span>${escapeHtml(item.item_code || "")}</span>
						<span>Eligible qty: ${escapeHtml(String(item.remaining_returnable_qty || 0))} ${escapeHtml(item.uom || "")}</span>
					</div>
					${item.return_unavailable_reason ? `<div class="ce-return-item-note">${escapeHtml(item.return_unavailable_reason)}</div>` : ""}
				</div>
			</div>
			<div class="ce-return-item-qty">
				<span>Qty</span>
				<input
					type="number"
					id="${escapeAttribute(qtyId)}"
					name="return_qty_${escapeAttribute(item.sales_invoice_item || "")}"
					min="0"
					step="0.01"
					max="${escapeAttribute(String(item.remaining_returnable_qty || 0))}"
					value="0">
			</div>
		</label>
	`;
}

function collectReturnSelection(form, eligibleItems) {
	const items = [];
	const reasonField = form.querySelector('textarea[name="return_reason"]');
	const fileInput = form.querySelector('input[name="return_evidence"]');

	eligibleItems.forEach((item) => {
		const itemId = item.sales_invoice_item;
		if (!itemId) {
			return;
		}

		const checkbox = form.querySelector(`input[name="return_item"][value="${cssEscape(itemId)}"]`);
		const qtyInput = form.querySelector(`input[name="return_qty_${cssEscape(itemId)}"]`);
		const maxQty = Number(item.remaining_returnable_qty || 0);
		const requestedQty = Number(qtyInput ? qtyInput.value : 0);

		if (!checkbox || !checkbox.checked) {
			return;
		}
		if (!requestedQty || requestedQty <= 0 || requestedQty > maxQty) {
			return;
		}

		items.push({
			sales_invoice_item: itemId,
			qty: requestedQty,
		});
	});

	return {
		items,
		reason: String(reasonField ? reasonField.value : "").trim(),
		files: Array.from((fileInput && fileInput.files) || []),
	};
}

function bindReturnDialogInputs(form, eligibleItems) {
	eligibleItems.forEach((item) => {
		const itemId = item.sales_invoice_item;
		if (!itemId) {
			return;
		}

		const checkbox = form.querySelector(`input[name="return_item"][value="${cssEscape(itemId)}"]`);
		const qtyInput = form.querySelector(`input[name="return_qty_${cssEscape(itemId)}"]`);
		const maxQty = Number(item.remaining_returnable_qty || 0);
		if (!checkbox || !qtyInput) {
			return;
		}

		checkbox.addEventListener("change", () => {
			if (checkbox.checked && Number(qtyInput.value || 0) <= 0) {
				qtyInput.value = String(maxQty >= 1 ? 1 : maxQty || 0);
			}
			if (!checkbox.checked) {
				qtyInput.value = "0";
			}
		});

		qtyInput.addEventListener("input", () => {
			const currentValue = Number(qtyInput.value || 0);
			if (currentValue > 0) {
				checkbox.checked = true;
			}
			if (currentValue <= 0) {
				checkbox.checked = false;
				qtyInput.value = "0";
			}
			if (currentValue > maxQty) {
				qtyInput.value = String(maxQty);
			}
		});
	});
}

function showReturnDialogError(node, message) {
	if (!node) {
		return;
	}
	node.textContent = message || "";
	node.style.display = message ? "block" : "none";
}

function closeReturnDialog(overlay) {
	if (overlay && overlay.parentNode) {
		overlay.parentNode.removeChild(overlay);
	}
}

function getActionReason(config) {
	if (!config.fieldLabel) {
		return "";
	}

	const promptMessage = `${config.fieldLabel} (optional)`;
	const response = window.prompt(promptMessage, "");
	if (response === null) {
		return false;
	}

	return String(response || "").trim();
}

function submitOrderAction(button, mountPoint, orderContext, config, reason, extraArgs) {
	const originalLabel = button.textContent;
	button.setAttribute("aria-disabled", "true");
	button.classList.add("disabled");
	button.textContent = "Working...";

	const args = Object.assign(
		{
			order_name: orderContext.orderName,
			order_doctype: orderContext.orderDoctype,
			reason: reason || "",
		},
		extraArgs || {}
	);

	return new Promise((resolve, reject) => {
		frappe.call({
			method: config.method,
			args,
			callback: (response) => {
				frappe.show_alert({
					message: (response.message && response.message.message) || config.message,
					indicator: "green",
				});
				loadTracking(mountPoint, orderContext);
				resolve(response.message || {});
			},
			error: () => {
				button.removeAttribute("aria-disabled");
				button.classList.remove("disabled");
				button.textContent = originalLabel;
				reject(new Error("Order action failed"));
			},
		});
	});
}

function uploadReturnEvidenceFiles(requestName, files) {
	const uploads = (files || []).map((file) => uploadReturnEvidenceFile(requestName, file));
	return Promise.all(uploads).then((results) => results.filter(Boolean).length);
}

function uploadReturnEvidenceFile(requestName, file) {
	const formData = new FormData();
	formData.append("file", file, file.name);
	formData.append("doctype", "Return Approval Request");
	formData.append("docname", requestName);
	formData.append("is_private", "1");
	if (window.csrf_token || (window.frappe && frappe.csrf_token)) {
		formData.append("csrf_token", window.csrf_token || frappe.csrf_token);
	}

	return window.fetch("/api/method/upload_file", {
		method: "POST",
		body: formData,
		credentials: "same-origin",
	}).then((response) => {
		if (!response.ok) {
			throw new Error(`Upload failed for ${file.name}`);
		}
		return response.json();
	});
}

function buildShipmentCard(shipment) {
	const shipmentTitle = shipment.name || "Shipment";
	const primaryStatus = shipment.tracking_status || shipment.status || "Shipment created";
	const statusNote = shipment.tracking_status_info || "";
	const updatedOn = shipment.pickup_date || shipment.modified || shipment.creation;
	const trackingCta = shipment.tracking_url
		? `<a class="ce-tracking-link" href="${escapeAttribute(shipment.tracking_url)}" target="_blank" rel="noopener noreferrer">Track package</a>`
		: "";

	const notes = [
		shipment.carrier || shipment.service_provider,
		shipment.carrier_service,
		shipment.awb_number ? `AWB ${shipment.awb_number}` : "",
	]
		.filter(Boolean)
		.map((entry) => `<span class="ce-tracking-chip">${escapeHtml(entry)}</span>`)
		.join("");
	const trackingEvents = Array.isArray(shipment.tracking_events) ? shipment.tracking_events : [];
	const trackingTimeline = trackingEvents.length ? buildShipmentEventTimeline(trackingEvents) : "";

	return `
		<article class="ce-tracking-branch">
			<div class="ce-tracking-shipment-top">
				<div>
					<div class="ce-tracking-shipment-kicker">${escapeHtml(shipmentTitle)}</div>
					<h4>${escapeHtml(primaryStatus)}</h4>
				</div>
				${trackingCta}
			</div>
			<div class="ce-tracking-chip-row">${notes}</div>
			${statusNote ? `<p class="ce-tracking-shipment-note">${escapeHtml(statusNote)}</p>` : ""}
			<div class="ce-tracking-shipment-meta">
				${updatedOn ? `<span>Updated ${escapeHtml(formatDate(updatedOn))}</span>` : ""}
			</div>
			${trackingTimeline}
		</article>
	`;
}

function buildShipmentEventTimeline(events) {
	return `
		<div class="ce-tracking-event-block">
			<div class="ce-tracking-event-title">Tracking history</div>
			<ol class="ce-tracking-event-list">
				${events
					.map(
						(event) => `
							<li class="ce-tracking-event-item">
								<span class="ce-tracking-event-dot"></span>
								<div class="ce-tracking-event-body">
									<div class="ce-tracking-event-status">${escapeHtml(
										event.external_status || event.normalized_status || "Tracking update"
									)}</div>
									${
										event.normalized_status
											? `<div class="ce-tracking-event-note">${escapeHtml(formatNormalizedStatus(event.normalized_status))}</div>`
											: ""
									}
									${
										event.event_time
											? `<div class="ce-tracking-event-date">${escapeHtml(formatDate(event.event_time))}</div>`
											: ""
									}
								</div>
							</li>
						`
					)
					.join("")}
			</ol>
		</div>
	`;
}

function formatNormalizedStatus(value) {
	return String(value || "")
		.toLowerCase()
		.split("_")
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");
}

function buildStateMarkup(message) {
	return `
		<div class="ce-tracking-card ce-tracking-card--state">
			<p class="ce-tracking-note">${escapeHtml(message)}</p>
		</div>
	`;
}

function formatDate(value) {
	if (!value) {
		return "";
	}

	try {
		if (frappe.datetime && frappe.datetime.str_to_user) {
			return frappe.datetime.str_to_user(value);
		}
	} catch (error) {
		// Ignore formatting issues and fall back below.
	}

	return String(value);
}

function formatCurrency(value, currency) {
	if (value === undefined || value === null || value === "") {
		return "";
	}

	try {
		if (typeof format_currency === "function") {
			return format_currency(value, currency);
		}
	} catch (error) {
		// Ignore formatting issues and fall back below.
	}

	return `${value}${currency ? ` ${currency}` : ""}`;
}

function formatPercent(value) {
	if (value === undefined || value === null || value === "") {
		return "0%";
	}

	return `${Math.round(Number(value) || 0)}%`;
}

function escapeHtml(value) {
	return String(value || "")
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#39;");
}

function escapeAttribute(value) {
	return escapeHtml(value);
}

function formatMultiline(value) {
	return escapeHtml(value).replace(/\n/g, "<br>");
}

function cssEscape(value) {
	if (window.CSS && typeof window.CSS.escape === "function") {
		return window.CSS.escape(value);
	}

	return String(value || "").replace(/(["\\])/g, "\\$1");
}

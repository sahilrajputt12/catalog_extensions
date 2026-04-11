(function forceGridListingView() {
	if (typeof window === "undefined" || typeof window.localStorage === "undefined") {
		return;
	}

	const isAllProductsPage = window.location.pathname === "/all-products";
	const isItemGroupListing = Boolean(document.querySelector(".item-group-content[data-item-group]"));

	if (!isAllProductsPage && !isItemGroupListing) {
		return;
	}

	const setGridView = () => {
		window.localStorage.setItem("product_view", "Grid View");
	};

	setGridView();

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", setGridView, { once: true });
	} else {
		setGridView();
	}

	window.addEventListener("pageshow", setGridView);
})();

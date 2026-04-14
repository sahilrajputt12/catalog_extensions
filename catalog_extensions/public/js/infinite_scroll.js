/**
 * FIXED Infinite Scroll for Frappe Webshop
 * 
 * ✅ Works in BOTH grid and list views
 * ✅ Loads NEW products (not repeating same ones)
 * ✅ Properly tracks pagination
 */

function extractItemCodeFromCard(card) {
	if (!card) return null;
	const byDataset = card.querySelector('[data-item-code]')?.dataset?.itemCode;
	if (byDataset) return byDataset;

	const byButton = card.querySelector('[data-item-code]')?.getAttribute('data-item-code');
	if (byButton) return byButton;

	const href = card.querySelector('a[href*="/product/"]')?.getAttribute('href');
	if (!href) return null;
	const parts = href.split('/').filter(Boolean);
	return parts.length ? parts[parts.length - 1] : null;
}

function buildQueryKeyFromUrl() {
	// A stable key representing the current query context (filters/sort/search).
	// Exclude `start` so paging increments don't cause resets.
	try {
		const url = new URL(window.location.href);
		const params = url.searchParams;
		const entries = [];
		params.forEach((v, k) => {
			if (k === 'start') return;
			entries.push([k, v]);
		});
		entries.sort((a, b) => (a[0] + '=' + a[1]).localeCompare(b[0] + '=' + b[1]));
		return entries.map(([k, v]) => `${k}=${v}`).join('&');
	} catch (e) {
		return '';
	}
}

function resetStateFromDom(state) {
	const nextKey = buildQueryKeyFromUrl();
	if (state.lastQueryKey === nextKey) return;

	state.isLoading = false;
	state.hasMore = true;
	state.pageLength = null;
	state.loadedItemCodes = new Set();

	const currentProducts = document.querySelectorAll('.item-card');
	currentProducts.forEach((card) => {
		const itemCode = extractItemCodeFromCard(card);
		if (itemCode) state.loadedItemCodes.add(itemCode);
	});
	state.start = currentProducts.length;
	state.lastQueryKey = nextKey;
}

frappe.ready(() => {
	// Only run on product listing pages
	const isProductPage = window.location.pathname.includes('all-products') || 
	                      window.location.pathname.includes('item-group') ||
	                      document.getElementById('product-listing');
	
	if (!isProductPage) {
		return;
	}

	// Wait for page to fully load
	setTimeout(() => {
		initInfiniteScroll();
	}, 1000);
});

function initInfiniteScroll() {
	if (window.__catalogExtensionsInfiniteScrollInitialized) {
		return;
	}
	window.__catalogExtensionsInfiniteScrollInitialized = true;

	const state = {
		isLoading: false,
		hasMore: true,
		start: 0,
		loadedItemCodes: new Set(), // Track which items we've already loaded
		pageLength: null,
		lastQueryKey: '',
		lastLoadAt: 0,
		minLoadIntervalMs: 900
	};

	// Count initial products and track their item codes
	const initialProducts = document.querySelectorAll('.item-card');
	initialProducts.forEach(card => {
		const itemCode = extractItemCodeFromCard(card);
		if (itemCode) {
			state.loadedItemCodes.add(itemCode);
		}
	});
	
	state.start = initialProducts.length;
	state.lastQueryKey = buildQueryKeyFromUrl();

	console.log('✅ Infinite scroll initialized');
	console.log('📦 Initial products:', state.start);
	console.log('🗂️ Loaded item codes:', Array.from(state.loadedItemCodes));

	// Setup scroll listener
	let scrollTimeout;
	window.addEventListener('scroll', () => {
		clearTimeout(scrollTimeout);
		scrollTimeout = setTimeout(() => {
			handleScroll(state);
		}, 200);
	});

	// If the listing DOM is replaced (common on filter/sort actions), re-sync state.
	// This avoids cases where state.start points past available items (or resets incorrectly).
	const listingRoot = document.getElementById('product-listing');
	if (listingRoot && window.MutationObserver) {
		let resetTimer = null;
		const listingObserver = new MutationObserver(() => {
			if (resetTimer) window.clearTimeout(resetTimer);
			resetTimer = window.setTimeout(() => resetStateFromDom(state), 350);
		});
		listingObserver.observe(listingRoot, { childList: true, subtree: true });
	}

	// Reset infinite scroll state when URL changes (filters/sort/search)
	window.addEventListener('popstate', () => resetStateFromDom(state));
	const origPushState = history.pushState;
	history.pushState = function () {
		origPushState.apply(this, arguments);
		resetStateFromDom(state);
	};
	const origReplaceState = history.replaceState;
	history.replaceState = function () {
		origReplaceState.apply(this, arguments);
		resetStateFromDom(state);
	};

	// Many webshop filters trigger a navigation (window.location.href). For UI that
	// mutates the URL without reload, also reset on filter checkbox changes.
	document.addEventListener('change', (e) => {
		if (e.target && e.target.closest && e.target.closest('.product-filter')) {
			resetStateFromDom(state);
		}
	});

	// Listen for view toggle
	document.addEventListener('click', (e) => {
		if (e.target.closest('.btn-grid-view, .btn-list-view, #grid, #list')) {
			console.log('🔄 View toggled');
			// Update start count after view change
			setTimeout(() => {
				const currentProducts = document.querySelectorAll('.item-card');
				console.log('📦 Products after toggle:', currentProducts.length);
			}, 100);
		}
	});

	// If the first page is shorter than the viewport, start loading immediately.
	setTimeout(() => {
		handleScroll(state);
	}, 250);
}

function handleScroll(state) {
	if (state.isLoading || !state.hasMore) {
		return;
	}

	// Prevent rapid re-triggers when the page is short or append changes scrollHeight.
	const now = Date.now();
	if (state.lastLoadAt && now - state.lastLoadAt < state.minLoadIntervalMs) {
		return;
	}

	const scrollPosition = window.innerHeight + window.scrollY;
	const documentHeight = Math.max(
		document.body.scrollHeight,
		document.documentElement.scrollHeight,
		document.body.offsetHeight,
		document.documentElement.offsetHeight
	);
	const threshold = documentHeight - 300;

	if (scrollPosition >= threshold) {
		console.log('📜 Scroll threshold reached, loading more...');
		loadMore(state);
	}
}

function loadMore(state) {
	state.isLoading = true;
	state.lastLoadAt = Date.now();
	showLoading();

	// Get current filters from URL and page
	const filters = getCurrentFilters();

	console.log('🔍 Loading products from start:', state.start);
	console.log('🔍 Filters:', filters);

	// Call Frappe API
	// NOTE: This method is overridden in catalog_extensions to accept a single
	// `query_args` dict (mirroring core signature). Passing raw args would drop
	// `start` and cause the API to always return page 1.
	frappe.call({
		method: 'webshop.webshop.api.get_product_filter_data',
		args: {
			query_args: {
				start: state.start,
				from_filters: 0,
				...filters
			}
		},
		callback: (r) => {
			hideLoading();
			
			console.log('📥 API Response:', r);
			
			if (r.message && r.message.items && r.message.items.length > 0) {
				const newItems = r.message.items;
				console.log('📦 Received items:', newItems.length);
				if (!state.pageLength) {
					state.pageLength = newItems.length;
				}
				
				// Filter out duplicates
				const uniqueNewItems = newItems.filter(item => {
					const itemCode = item.item_code || item.name;
					if (state.loadedItemCodes.has(itemCode)) {
						console.log('⚠️ Skipping duplicate:', itemCode);
						return false;
					}
					state.loadedItemCodes.add(itemCode);
					return true;
				});

				console.log('✨ Unique new items:', uniqueNewItems.length);

				// If the API keeps returning the same page, we may get 0 unique items.
				// In that case, stop infinite scroll to prevent a continuous network loop.
				if (uniqueNewItems.length === 0) {
					state.hasMore = false;
					showEndMessage();
					console.log('🏁 No new unique products; stopping infinite scroll');
					state.isLoading = false;
					return;
				} else {
					// Append products to BOTH views
					appendProducts(uniqueNewItems, r.message.settings || {});
				}
				
				// ✅ CRITICAL: Increment start position (based on raw response)
				state.start += newItems.length;
				console.log('📍 New start position:', state.start);

				// Check if we have more products
				const pageLen = state.pageLength || 12;
				if (newItems.length < pageLen) {
					state.hasMore = false;
					showEndMessage();
					console.log('🏁 No more products available');
				}
			} else {
				state.hasMore = false;
				showEndMessage();
				console.log('🏁 No items in response');
			}
			
			state.isLoading = false;
			queueBottomCheck(state);
		},
		error: (err) => {
			console.error('❌ Error loading products:', err);
			hideLoading();
			state.isLoading = false;
			showError();
		}
	});
}

function queueBottomCheck(state) {
	window.setTimeout(() => {
		handleScroll(state);
	}, state.minLoadIntervalMs);
}

function getCurrentFilters() {
	const filters = {};

	// Prefer URL params (authoritative for current listing state)
	const urlParams = (frappe?.utils?.get_query_params && frappe.utils.get_query_params()) || {};

	// Standard top-level params
	if (urlParams.search) filters.search = urlParams.search;
	if (urlParams.item_group) filters.item_group = urlParams.item_group;
	if (urlParams.brand) filters.brand = urlParams.brand;

	// Preserve core JSON params if present
	if (urlParams.field_filters) filters.field_filters = urlParams.field_filters;
	if (urlParams.attribute_filters) filters.attribute_filters = urlParams.attribute_filters;

	// Also accept price filters (our override reads these from q or field_filters)
	if (urlParams.price_from) filters.price_from = urlParams.price_from;
	if (urlParams.price_to) filters.price_to = urlParams.price_to;

	// Fallback: if URL doesn't have search but input does, use input
	if (!filters.search) {
		const searchInput = document.querySelector('input[name="query"], .product-search');
		if (searchInput && searchInput.value) {
			filters.search = searchInput.value;
		}
	}

	return filters;
}

function appendProducts(products, settings) {
	// ✅ FIXED: Find BOTH grid and list containers
	const gridArea = document.getElementById('products-grid-area');
	const listArea = document.getElementById('products-list-area');
	
	let gridContainer = null;
	let listContainer = null;
	let gridExists = false;
	let listExists = false;

	function resolveContainer(areaEl) {
		if (!areaEl) return null;
		// If the area itself is already the row/container, append directly into it.
		if (areaEl.classList && areaEl.classList.contains('row')) {
			return areaEl;
		}
		return (
			areaEl.querySelector('.products-list') ||
			areaEl.querySelector('.row') ||
			areaEl.querySelector('.item-card-group-section .row') ||
			areaEl.querySelector('.item-card-group-section')
		);
	}
	
	// Check which views exist and are active
	if (gridArea) {
		gridContainer = resolveContainer(gridArea);
		// If grid area exists but no obvious container exists (depends on theme/templates),
		// create a .row container so appended cards have a consistent parent.
		if (!gridContainer) {
			gridContainer = document.createElement('div');
			gridContainer.className = 'row';
			gridArea.appendChild(gridContainer);
		}
		gridExists = !!gridContainer;
		console.log('📊 Grid container found:', !!gridContainer);
	}
	
	if (listArea) {
		listContainer = resolveContainer(listArea);
		if (!listContainer) {
			listContainer = document.createElement('div');
			listContainer.className = 'row';
			listArea.appendChild(listContainer);
		}
		listExists = !!listContainer;
		console.log('📋 List container found:', !!listContainer);
	}

	// Fallback: if we can't determine active view, find any .products-list
	if (!gridContainer && !listContainer) {
		const fallbackContainer = document.querySelector('.products-list, .row');
		if (fallbackContainer) {
			console.log('⚠️ Using fallback container');
			gridContainer = fallbackContainer;
			gridExists = true;
		}
	}

	if (!gridExists && !listExists) {
		console.log('⚠️ No product containers found to append items');
		return;
	}

	let appendedCount = 0;

	// ✅ FIXED: Append to BOTH views (they render independently)
	products.forEach(product => {
		// Append to grid if it exists
		if (gridContainer && gridExists) {
			const gridHtml = createProductHTML(product, 'grid', settings);
			gridContainer.insertAdjacentHTML('beforeend', gridHtml);
			appendedCount++;
		}
		
		// Append to list if it exists
		if (listContainer && listExists) {
			const listHtml = createProductHTML(product, 'list', settings);
			listContainer.insertAdjacentHTML('beforeend', listHtml);
		}
	});

	console.log('✅ Appended', appendedCount, 'products');

	// Reinitialize cart handlers
	if (window.webshop && webshop.shopping_cart) {
		webshop.shopping_cart.bind_add_to_cart_action();
	}

	// Trigger custom event
	document.dispatchEvent(new CustomEvent('products-loaded', { 
		detail: { count: products.length } 
	}));

	// Allow other listing enhancement scripts to re-run on newly appended cards
	if (typeof window.catalogExtensionsOnProductsLoaded === 'function') {
		try {
			window.catalogExtensionsOnProductsLoaded({ count: products.length });
		} catch (e) {
			// ignore
		}
	}
}

function createProductHTML(product, viewType, settings) {
	const asCheckboxValue = (value) => {
		if (typeof value === "string") {
			return value === "1";
		}
		return Boolean(value);
	};
	const name = product.item_name || product.web_item_name || '';
	const code = product.item_code || product.name || '';
	const route = product.route || `/product/${code}`;
	const href = route && route.startsWith('/') ? route : `/${route}`;
	const image = product.website_image || product.image || '/assets/frappe/images/default-image.svg';
	const price = product.formatted_price || '';
	const inStock = asCheckboxValue(product.in_stock);
	const allowItemsNotInStock = asCheckboxValue(settings && settings.allow_items_not_in_stock);
	const showStockAvailability = asCheckboxValue(settings && settings.show_stock_availability);
	const canAddToCart = allowItemsNotInStock || inStock;
	const shortDesc = product.short_description || '';
	const category = product.item_group || product.item_group_name || product.item_group_title || '';

	if (viewType === 'list') {
		return `
			<div class="col-12 item-card">
				<div class="card list-row">
					<div class="row no-gutters">
						<div class="col-sm-3">
							<a href="${href}">
								<img src="${image}" alt="${name}" class="list-image" loading="lazy">
							</a>
						</div>
						<div class="col-sm-9">
							<div class="card-body">
								<h5 class="card-title">
									<a href="${href}">${name}</a>
								</h5>
								${category ? `<div class="product-category" itemprop="name">${category}</div>` : ''}
								${shortDesc ? `<p class="card-text">${shortDesc}</p>` : ''}
								${price ? `<p class="product-price">${price}</p>` : ''}
								${showStockAvailability && !inStock ? '<div class="out-of-stock">Out of Stock</div>' : ''}
								${canAddToCart ? `
									<div class="btn btn-sm btn-primary btn-add-to-cart-list"
										data-item-code="${code}">
										Add to Cart
									</div>
								` : ''}
							</div>
						</div>
					</div>
				</div>
			</div>
		`;
	} else {
		// Grid view
		return `
			<div class="col-sm-4 item-card"><div class="card text-left">
					<div class="card-img-container">
						<a href="${href}" style="text-decoration: none;">
							<img class="card-img" src="${image}" alt="${name}" loading="lazy">
						</a>
					</div>
					<div class="card-body text-left card-body-flex" style="width:100%">
						<div style="margin-top: 1rem; display: flex;">
							<a href="${href}">
								<div class="product-title" itemprop="name">${name}</div>
							</a>
							<div class="cart-indicator hidden" data-item-code="${code}">1</div>
						</div>
						${category ? `<div class="product-category" itemprop="name">${category}</div>` : ''}
						${price ? `<div class="product-price">${price}</div>` : ''}
						${showStockAvailability && !inStock ? '<div class="out-of-stock">Out of Stock</div>' : ''}
						${canAddToCart ? `
							<div class="btn btn-sm btn-primary btn-add-to-cart-list w-100 mt-2"
								data-item-code="${code}">
								<span class="mr-2">
									<svg class="icon icon-md">
										<use href="#icon-assets"></use>
									</svg>
								</span>
								Add to Cart
							</div>
						` : ''}
					</div>
				</div></div>
		`;
	}
}

function showLoading() {
	let loader = document.getElementById('infinite-scroll-loader');
	if (!loader) {
		loader = document.createElement('div');
		loader.id = 'infinite-scroll-loader';
		loader.className = 'infinite-scroll-loading';
		loader.innerHTML = '<div class="spinner"></div><p>Loading more products...</p>';
		
		const container = document.getElementById('product-listing') || 
		                  document.querySelector('.products-list')?.parentElement ||
		                  document.body;
		container.appendChild(loader);
	}
	loader.style.display = 'block';
}

function hideLoading() {
	const loader = document.getElementById('infinite-scroll-loader');
	if (loader) {
		loader.style.display = 'none';
	}
}

function showEndMessage() {
	let msg = document.getElementById('infinite-scroll-end');
	if (!msg) {
		msg = document.createElement('div');
		msg.id = 'infinite-scroll-end';
		msg.className = 'infinite-scroll-end';
		msg.innerHTML = '<p>You\'ve reached the end of the catalog</p>';
		
		const container = document.getElementById('product-listing') || 
		                  document.querySelector('.products-list')?.parentElement ||
		                  document.body;
		container.appendChild(msg);
	}
	msg.style.display = 'block';
}

function showError() {
	let error = document.getElementById('infinite-scroll-error');
	if (!error) {
		error = document.createElement('div');
		error.id = 'infinite-scroll-error';
		error.style.cssText = 'text-align: center; padding: 2rem; color: #dc3545;';
		error.innerHTML = '<p>Unable to load more products. Please try again.</p>';
		
		const container = document.getElementById('product-listing') || 
		                  document.querySelector('.products-list')?.parentElement ||
		                  document.body;
		container.appendChild(error);
	}
	error.style.display = 'block';
	
	setTimeout(() => {
		error.style.display = 'none';
	}, 5000);
}

// Simple quantity controls on listing cards, layered on top of core Add to Cart logic.
// Rules:
// - When quantity is 0: show default Add to Cart button.
// - When quantity > 0: show +/- spinner and keep the cart-indicator; hide the Go to Cart button.

frappe.ready(function () {
  var listing = document.getElementById('product-listing');
  if (!listing) return;
  var stockCache = {};

  function asCheckboxValue(value) {
    if (typeof value === 'string') {
      return value === '1';
    }
    return Boolean(value);
  }

  function getCurrentQty(card, itemCode) {
    var input = card.querySelector('.listing-qty-wrapper[data-item-code="' + itemCode + '"] .cart-qty');
    if (input) {
      var value = parseInt(input.value || '0', 10);
      return isNaN(value) ? 0 : Math.max(0, value);
    }

    var indicator = card.querySelector('.cart-indicator[data-item-code="' + itemCode + '"]');
    if (indicator && !indicator.classList.contains('hidden')) {
      var indicatorValue = parseInt((indicator.textContent || '').trim(), 10);
      if (!isNaN(indicatorValue)) {
        return Math.max(0, indicatorValue);
      }
      return 1;
    }

    var goToCart = card.querySelector('.go-to-cart-grid[data-item-code="' + itemCode + '"]');
    if (goToCart && !goToCart.classList.contains('hidden')) {
      return 1;
    }

    return 0;
  }

  function fetchListingStockData(itemCode, callback, options) {
    if (!itemCode) return;
    var force = options && options.force;
    if (!force && stockCache[itemCode]) {
      callback(stockCache[itemCode]);
      return;
    }

    frappe.call({
      type: 'POST',
      method: 'webshop.webshop.shopping_cart.product_info.get_product_info_for_website',
      args: {
        item_code: itemCode,
        skip_quotation_creation: false
      },
      callback: function (r) {
        var payload = {
          product_info: r && r.message ? (r.message.product_info || {}) : {},
          cart_settings: r && r.message ? (r.message.cart_settings || {}) : {}
        };
        stockCache[itemCode] = payload;
        callback(payload);
      }
    });
  }

  function renderListingStockMessage(card, stockData, currentQty) {
    if (!card) return;
    var existing = card.querySelector('.ce-listing-stock-note');
    if (existing) existing.remove();

    var productInfo = stockData && stockData.product_info ? stockData.product_info : {};
    var cartSettings = stockData && stockData.cart_settings ? stockData.cart_settings : {};
    var showStockAvailability = asCheckboxValue(cartSettings.show_stock_availability);
    var state = productInfo.stock_state;
    var message = productInfo.stock_message;
    var hasCoreOutOfStockLabel = state === 'out_of_stock' && !!card.querySelector('.out-of-stock');
    var shouldShow =
      state === 'out_of_stock' && currentQty > 0;

    if (!showStockAvailability) return;
    if (hasCoreOutOfStockLabel) return;
    if (!shouldShow || !message) return;

    var note = document.createElement('div');
    note.className = 'ce-listing-stock-note';
    note.innerHTML =
      '<div class="ce-stock-alert ce-stock-alert--' +
      frappe.utils.escape_html(state) +
      '">' +
      frappe.utils.escape_html(message) +
      '</div>';

    var cartActionContainer = card.querySelector('.cart-action-container') || card.querySelector('.card-body');
    if (cartActionContainer) {
      cartActionContainer.appendChild(note);
    }
  }

  function applyListingStockState(card, itemCode, stockData) {
    if (!card || !itemCode || !stockData) return;

    var productInfo = stockData.product_info || {};
    var addBtn = card.querySelector('.btn-add-to-cart-list[data-item-code="' + itemCode + '"]');
    var wrapper = card.querySelector('.listing-qty-wrapper[data-item-code="' + itemCode + '"]');
    var currentQty = getCurrentQty(card, itemCode);
    var maxOrderableQty = parseFloat(productInfo.max_orderable_qty || '');
    var canAddToCart = productInfo.can_add_to_cart !== false;

    if (!canAddToCart) {
      if (wrapper) wrapper.classList.add('hidden');
      if (addBtn) addBtn.classList.add('hidden');
      renderListingStockMessage(card, stockData, currentQty);
      return;
    }

    if (addBtn) {
      addBtn.classList.toggle('hidden', currentQty > 0 || productInfo.can_add_to_cart === false);
    }
    if (wrapper) {
      wrapper.classList.toggle('hidden', currentQty <= 0);
      var input = wrapper.querySelector('.cart-qty');
      var upButton = wrapper.querySelector('.cart-btn[data-dir="up"]');
      if (input) {
        input.dataset.maxOrderableQty = Number.isNaN(maxOrderableQty) ? '' : String(maxOrderableQty);
        input.dataset.stockMessage = productInfo.stock_message || '';
        input.value = String(currentQty || 0);
      }
      if (upButton) {
        var blocked = productInfo.can_increase_qty === false ||
          (!Number.isNaN(maxOrderableQty) && currentQty >= maxOrderableQty);
        upButton.disabled = blocked;
        upButton.classList.toggle('disabled', blocked);
        upButton.dataset.stockMessage = productInfo.stock_message || '';
      }
    }

    renderListingStockMessage(card, stockData, currentQty);
  }

  function renderQtyControls(card, itemCode, initialQty) {
    if (!card || !itemCode) return;

    // Avoid duplicating controls
    var existing = card.querySelector('.listing-qty-wrapper[data-item-code="' + itemCode + '"]');
    if (existing) {
      existing.classList.remove('hidden');
      var inputExisting = existing.querySelector('.cart-qty');
      if (inputExisting && initialQty != null) {
        inputExisting.value = String(initialQty);
      }
      return;
    }

    var wrapper = document.createElement('div');
    wrapper.className = 'listing-qty-wrapper mt-1';
    wrapper.setAttribute('data-item-code', itemCode);

    wrapper.innerHTML = [
      '<div class="d-flex align-items-center listing-qty-inner">',
      '  <div class="input-group number-spinner">',
      '    <span class="input-group-prepend d-sm-inline-block">',
      '      <button class="btn cart-btn" data-dir="dwn">&minus;</button>',
      '    </span>',
      '    <input class="form-control text-center cart-qty"',
      '           value="' + (initialQty != null ? initialQty : 1) + '"',
      '           data-item-code="' + frappe.utils.escape_html(itemCode) + '"',
      '           style="max-width: 70px;">',
      '    <span class="input-group-append d-sm-inline-block">',
      '      <button class="btn cart-btn" data-dir="up">+</button>',
      '    </span>',
      '  </div>',
      '  <a href="/cart" class="listing-go-to-cart ml-2" aria-label="Go to cart">',
      '    <svg class="icon sm" width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">',
      '      <path d="M6 16a1 1 0 1 1-2 0 1 1 0 0 1 2 0zm8 0a1 1 0 1 1-2 0 1 1 0 0 1 2 0zM3 3h1.2l.9 6h7.8l1.1-4.4A.5.5 0 0 0 13.5 4h-8" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>',
      '    </svg>',
      '  </a>',
      '</div>'
    ].join('');

    // Insert the controls near the primary cart actions
    var cartActionContainer = card.querySelector('.cart-action-container') || card.querySelector('.card-body');
    if (cartActionContainer) {
      cartActionContainer.appendChild(wrapper);
    } else {
      card.appendChild(wrapper);
    }

    // Hide the Add to Cart button for this item while quantity > 0
    var addBtn = card.querySelector('.btn-add-to-cart-list[data-item-code="' + itemCode + '"]');
    if (addBtn) {
      addBtn.classList.add('hidden');
    }

    fetchListingStockData(itemCode, function (stockData) {
      applyListingStockState(card, itemCode, stockData);
    });
  }

  function updateQty(card, itemCode, newQty) {
    if (newQty < 0) newQty = 0;

    if (!frappe || !frappe.call) {
      return;
    }

    frappe.call({
      type: "POST",
      method: "webshop.webshop.shopping_cart.cart.update_cart",
      args: {
        item_code: itemCode,
        qty: newQty,
        with_items: 1,
      },
      callback: function (r) {
        if (r && r.exc) {
          return; // do not change UI on backend error
        }

        var wrapper = card.querySelector('.listing-qty-wrapper[data-item-code="' + itemCode + '"]');
        if (wrapper) {
          var input = wrapper.querySelector('.cart-qty');
          if (input) input.value = String(newQty);
        }

        fetchListingStockData(itemCode, function (stockData) {
          applyListingStockState(card, itemCode, stockData);
        }, { force: true });
      }
    });
  }

  function syncExistingInCartCards(root) {
    var scope = root || listing;

    // When core marks items as in_cart (via cart-indicator), reflect that with qty controls.
    Array.prototype.forEach.call(
      scope.querySelectorAll('.cart-indicator[data-item-code]'),
      function (indicator) {
        if (indicator.classList.contains('hidden')) return;
        var itemCode = indicator.getAttribute('data-item-code');
        if (!itemCode) return;
        var card = indicator.closest('.item-card, .card');
        if (!card) return;
        renderQtyControls(card, itemCode, getCurrentQty(card, itemCode) || 1);
      }
    );

    // Additionally, handle cases where core only rendered a visible go-to-cart-grid
    // button (item.in_cart true) without a cart-indicator element.
    Array.prototype.forEach.call(
      scope.querySelectorAll('.go-to-cart-grid[data-item-code]'),
      function (btn) {
        if (btn.classList.contains('hidden')) return;
        var itemCode = btn.getAttribute('data-item-code');
        if (!itemCode) return;
        var card = btn.closest('.item-card, .card');
        if (!card) return;
        renderQtyControls(card, itemCode, getCurrentQty(card, itemCode) || 1);
      }
    );

    Array.prototype.forEach.call(
      scope.querySelectorAll('.btn-add-to-cart-list[data-item-code]'),
      function (btn) {
        var itemCode = btn.getAttribute('data-item-code');
        if (!itemCode) return;
        var card = btn.closest('.item-card, .card');
        if (!card) return;
        fetchListingStockData(itemCode, function (stockData) {
          applyListingStockState(card, itemCode, stockData);
        });
      }
    );
  }

  // Initial pass after DOM ready (in case grid HTML is already present)
  syncExistingInCartCards();

  // Watch for product cards being added later (grid rendered dynamically)
  var observer = new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      Array.prototype.forEach.call(mutation.addedNodes, function (node) {
        if (!(node instanceof HTMLElement)) return;
        syncExistingInCartCards(node);
        
        // Force mobile layout when products are added
        applyMobileLayoutIfNeeded();
      });
    });
  });

  observer.observe(listing, { childList: true, subtree: true });

  // Force mobile layout on initial load and when viewport changes
  window.applyMobileLayoutIfNeeded = function() {
    if (window.innerWidth <= 767) {
      // Target the actual structure: .list-row inside #product-listing
      const listRows = document.querySelectorAll('#product-listing .list-row');
      listRows.forEach(function(row) {
        row.style.display = 'flex';
        row.style.flexWrap = 'wrap';
        row.style.gap = '0.05rem';
        
        const cols = row.querySelectorAll('[class*="col-"]');
        cols.forEach(function(col) {
          col.style.flex = '0 0 50%';
          col.style.maxWidth = '50%';
          col.style.paddingLeft = '0.1rem';
          col.style.paddingRight = '0.1rem';
        });
      });
    }
  }

  // Apply on load
  applyMobileLayoutIfNeeded();
  
  // Apply on resize
  window.addEventListener('resize', applyMobileLayoutIfNeeded);
  // After the core Add to Cart handler runs, convert the button into qty controls
  listing.addEventListener('click', function (event) {
    var btn = event.target.closest('.btn-add-to-cart-list[data-item-code]');
    if (!btn) return;

    // Ignore Go to Cart buttons; they are hidden via CSS and/or handled by core
    if (btn.classList.contains('go-to-cart-grid')) {
      return;
    }

    // Let core handler manage guests and backend update
    if (frappe.session && frappe.session.user === 'Guest') {
      return;
    }

    var itemCode = btn.getAttribute('data-item-code');
    if (!itemCode) return;

    var card = btn.closest('.item-card, .card');
    if (!card) return;

    // Hide the clicked Add to Cart immediately to avoid overlap with quantity UI
    btn.classList.add('hidden');

    // Wait briefly so core JS can toggle any other classes, then show our controls
    setTimeout(function () {
      renderQtyControls(card, itemCode, 1);
    }, 200);
  });

  // Delegate +/- clicks for listing qty controls
  listing.addEventListener('click', function (event) {
    var upDownBtn = event.target.closest('.listing-qty-wrapper .cart-btn');
    if (!upDownBtn) return;

    var wrapper = upDownBtn.closest('.listing-qty-wrapper');
    if (!wrapper) return;

    var itemCode = wrapper.getAttribute('data-item-code');
    if (!itemCode) return;

    var card = wrapper.closest('.item-card, .card');
    if (!card) return;

    var input = wrapper.querySelector('.cart-qty');
    var current = parseInt(input && input.value ? input.value : '1', 10);
    if (isNaN(current) || current < 1) current = 1;
    var maxOrderable = parseInt(input && input.dataset.maxOrderableQty ? input.dataset.maxOrderableQty : '', 10);

    var dir = upDownBtn.getAttribute('data-dir');
    if (dir === 'up') {
      if (!isNaN(maxOrderable) && current >= maxOrderable) {
        frappe.msgprint({
          message: upDownBtn.dataset.stockMessage || (input && input.dataset.stockMessage) || __('Out of stock'),
          indicator: 'orange'
        });
        return;
      }
      updateQty(card, itemCode, current + 1);
    } else if (dir === 'dwn') {
      var next = current - 1;
      if (next < 0) next = 0;
      updateQty(card, itemCode, next);
    }
  });
});

// Simple quantity controls on listing cards, layered on top of core Add to Cart logic.
// Rules:
// - When quantity is 0: show default Add to Cart button.
// - When quantity > 0: show +/- spinner and keep the cart-indicator; hide the Go to Cart button.

frappe.ready(function () {
  var listing = document.getElementById('product-listing');
  if (!listing) return;

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
    wrapper.className = 'listing-qty-wrapper mt-2';
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
        if (!wrapper) return;
        var input = wrapper.querySelector('.cart-qty');

        if (newQty === 0) {
          // Remove controls and show Add to Cart again
          wrapper.classList.add('hidden');
          if (input) input.value = '0';
          var addBtn = card.querySelector('.btn-add-to-cart-list[data-item-code="' + itemCode + '"]');
          if (addBtn) addBtn.classList.remove('hidden');
        } else {
          if (input) input.value = String(newQty);
        }
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
        renderQtyControls(card, itemCode, 1);
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
        renderQtyControls(card, itemCode, 1);
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
      });
    });
  });

  observer.observe(listing, { childList: true, subtree: true });

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

    var dir = upDownBtn.getAttribute('data-dir');
    if (dir === 'up') {
      updateQty(card, itemCode, current + 1);
    } else if (dir === 'dwn') {
      var next = current - 1;
      if (next < 0) next = 0;
      updateQty(card, itemCode, next);
    }
  });
});

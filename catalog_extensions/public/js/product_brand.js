frappe.ready(function () {
  var maxRetries = 5;
  var retryCount = 0;
  
  function injectProductBrands() {
    console.log('[Brand] Starting brand injection... (attempt ' + (retryCount + 1) + ')');
    var listing = document.getElementById('product-listing');
    if (!listing) {
      console.log('[Brand] No product-listing element found');
      retryCount++;
      if (retryCount < maxRetries) {
        console.log('[Brand] Retrying in 500ms...');
        setTimeout(injectProductBrands, 500);
      }
      return;
    }
    console.log('[Brand] Found product-listing element');

    var itemCodeEls = listing.querySelectorAll('[data-item-code]');
    console.log('[Brand] Found ' + itemCodeEls.length + ' elements with data-item-code');
    if (!itemCodeEls.length) {
      retryCount++;
      if (retryCount < maxRetries) {
        setTimeout(injectProductBrands, 500);
      }
      return;
    }

    var codesSet = new Set();
    itemCodeEls.forEach(function (el) {
      var code = el.getAttribute('data-item-code');
      if (code) codesSet.add(code);
    });
    var itemCodes = Array.from(codesSet);
    console.log('[Brand] Unique item codes:', itemCodes);
    if (!itemCodes.length) return;

    frappe.call({
      method: 'catalog_extensions.api.get_item_brands',
      args: { item_codes: itemCodes },
      callback: function (r) {
        console.log('[Brand] API response:', r.message);
        if (!r.message) {
          console.log('[Brand] No response from API');
          return;
        }
        var brandsByCode = r.message;

        itemCodes.forEach(function (code) {
          var brand = brandsByCode[code];
          console.log('[Brand] Code:', code, 'Brand:', brand);
          if (!brand) return;

          // Find every card instance with this item code
          var targets = listing.querySelectorAll('[data-item-code="' + code + '"]');
          console.log('[Brand] Found ' + targets.length + ' cards for code ' + code);
          targets.forEach(function (el) {
            var card = el.closest('.item-card, .card');
            if (!card) {
              console.log('[Brand] No card found for element', el);
              return;
            }

            // Avoid duplicate injection
            if (card.querySelector('.brand-container')) {
              console.log('[Brand] Brand already injected for this card');
              return;
            }

            // Find the product title element
            var nameEl = card.querySelector('.product-title');
            console.log('[Brand] Product title element:', nameEl ? 'found: ' + nameEl.textContent.trim().substr(0,30) : 'not found');
            
            var insertPoint = null;
            if (nameEl) {
              // Find the flex container that holds the title (parent of the <a> tag)
              var titleLink = nameEl.closest('a');
              if (titleLink) {
                var flexContainer = titleLink.parentNode; // This is the <div style="display: flex">
                if (flexContainer) {
                  insertPoint = flexContainer.parentNode; // This is the card-body or similar
                  // We'll insert before the flex container
                  insertPoint = { parent: insertPoint, before: flexContainer };
                }
              }
            }
            
            // Fallback: if we can't find the right structure, just put it in card-body
            if (!insertPoint) {
              var cardBody = card.querySelector('.card-body');
              if (cardBody) {
                insertPoint = { parent: cardBody, before: cardBody.firstChild };
              }
            }
            
            if (!insertPoint) {
              console.log('[Brand] Cannot find insertion point, skipping');
              return;
            }

            var brandDiv = document.createElement('div');
            brandDiv.className = 'brand-container mb-1';
            // Amazon-style: small, uppercase, gray brand above title
            brandDiv.innerHTML = '<span class="brand-badge" style="font-size: 0.7rem; color: var(--gray-700); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 500; line-height: 1.2;">' + 
              __(brand) + '</span>';

            // Insert at the correct position
            insertPoint.parent.insertBefore(brandDiv, insertPoint.before);
            console.log('[Brand] Brand injected for', code);
          });
        });
      },
      error: function(err) {
        console.error('[Brand] API error:', err);
      }
    });
  }

  // Initial try after grid renders
  setTimeout(injectProductBrands, 800);

  // Observe product listing for changes (e.g. filters, sorting) and re-inject.
  var observerInitialized = false;
  function setupBrandObserver() {
    if (observerInitialized) return;
    var listing = document.getElementById('product-listing');
    if (!listing || !window.MutationObserver) return;

    observerInitialized = true;
    var timeoutId = null;
    var observer = new MutationObserver(function () {
      // Debounce rapid DOM changes into a single inject call
      if (timeoutId) clearTimeout(timeoutId);
      timeoutId = setTimeout(injectProductBrands, 200);
    });

    observer.observe(listing, { childList: true, subtree: true });
  }

  setTimeout(setupBrandObserver, 1000);
});

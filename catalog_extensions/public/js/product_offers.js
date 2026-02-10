frappe.ready(function () {
  function injectProductOffers() {
    var listing = document.getElementById('product-listing');
    if (!listing) return;

    var itemCodeEls = listing.querySelectorAll('[data-item-code]');
    if (!itemCodeEls.length) return;

    var codesSet = new Set();
    itemCodeEls.forEach(function (el) {
      var code = el.getAttribute('data-item-code');
      if (code) codesSet.add(code);
    });
    var itemCodes = Array.from(codesSet);
    if (!itemCodes.length) return;

    frappe.call({
      method: 'catalog_extensions.api.get_item_offers',
      args: { item_codes: itemCodes },
      callback: function (r) {
        if (!r.message) return;
        var offersByCode = r.message;

        itemCodes.forEach(function (code) {
          var offers = offersByCode[code];
          if (!offers || !offers.length) return;

          // Find every card instance with this item code
          var targets = listing.querySelectorAll('[data-item-code="' + code + '"]');
          targets.forEach(function (el) {
            var card = el.closest('.item-card, .card');
            if (!card) return;

            // Avoid duplicate injection
            if (card.querySelector('.offer-container')) return;

            var priceEl = card.querySelector('.product-price');
            if (!priceEl) return;

            var container = document.createElement('div');
            container.className = 'offer-container';

            // Heading
            var heading = document.createElement('div');
            heading.className = 'offers-heading mb-2';
            heading.innerHTML = '<span class="mr-1 tag-icon">' +
              '<svg class="icon icon-lg"><use href="#icon-tag"></use></svg>' +
              '</span><b>' + __('Available Offers') + '</b>';

            // Lines
            var listWrap = document.createElement('div');
            offers.forEach(function (o) {
              var line = document.createElement('div');
              line.className = 'mt-2 d-flex';
              line.innerHTML =
                '<div class="mr-2">' +
                '<svg width="24" height="24" viewBox="0 0 24 24" stroke="var(--yellow-500)" fill="none" xmlns="http://www.w3.org/2000/svg">' +
                '<path d="M19 15.6213C19 15.2235 19.158 14.842 19.4393 14.5607L20.9393 13.0607C21.5251 12.4749 21.5251 11.5251 20.9393 10.9393L19.4393 9.43934C19.158 9.15804 19 8.7765 19 8.37868V6.5C19 5.67157 18.3284 5 17.5 5H15.6213C15.2235 5 14.842 4.84196 14.5607 4.56066L13.0607 3.06066C12.4749 2.47487 11.5251 2.47487 10.9393 3.06066L9.43934 4.56066C9.15804 4.84196 8.7765 5 8.37868 5H6.5C5.67157 5 5 5.67157 5 6.5V8.37868C5 8.7765 4.84196 9.15804 4.56066 9.43934L3.06066 10.9393C2.47487 11.5251 2.47487 12.4749 3.06066 13.0607L4.56066 14.5607C4.84196 14.842 5 15.2235 5 15.6213V17.5C5 18.3284 5.67157 19 6.5 19H8.37868C8.7765 19 9.15804 19.158 9.43934 19.4393L10.9393 20.9393C11.5251 21.5251 12.4749 21.5251 13.0607 20.9393L14.5607 19.4393C14.842 19.158 15.2235 19 15.6213 19H17.5C18.3284 19 19 18.3284 19 17.5V15.6213Z" stroke-miterlimit="10" stroke-linecap="round" stroke-linejoin="round"/>' +
                '<path d="M15 9L9 15" stroke-miterlimit="10" stroke-linecap="round" stroke-linejoin="round"/>' +
                '<path d="M10.5 9.5C10.5 10.0523 10.0523 10.5 9.5 10.5C8.94772 10.5 8.5 10.0523 8.5 9.5C8.5 8.94772 8.94772 8.5 9.5 8.5C10.0523 8.5 10.5 8.94772 10.5 9.5Z" fill="white" stroke-linecap="round" stroke-linejoin="round"/>' +
                '<path d="M15.5 14.5C15.5 15.0523 15.0523 15.5 14.5 15.5C13.9477 15.5 13.5 15.0523 13.5 14.5C13.5 13.9477 13.9477 13.5 14.5 13.5C15.0523 13.5 15.5 13.9477 15.5 14.5Z" fill="white" stroke-linecap="round" stroke-linejoin="round"/>' +
                '</svg>' +
                '</div>' +
                '<p class="mr-1 mb-1">' +
                __(o.offer_title || '') + (o.offer_subtitle ? (': ' + __(o.offer_subtitle)) : '') +
                '</p>';
              listWrap.appendChild(line);
            });

            var wrapper = document.createElement('div');
            wrapper.className = 'mt-2';
            wrapper.appendChild(heading);
            wrapper.appendChild(container);
            wrapper.appendChild(listWrap);

            // Insert after price
            if (priceEl.nextSibling) {
              priceEl.parentNode.insertBefore(wrapper, priceEl.nextSibling);
            } else {
              priceEl.parentNode.appendChild(wrapper);
            }
          });
        });
      }
    });
  }

  // Initial try after grid renders
  setTimeout(injectProductOffers, 600);
});

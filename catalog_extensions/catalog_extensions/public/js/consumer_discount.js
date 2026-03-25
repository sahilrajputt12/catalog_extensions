frappe.ready(function () {
  // Wait until ProductView has rendered cards with data-item-code before injecting
  function injectConsumerDiscount() {
    var listing = document.getElementById('product-listing');
    if (!listing) return;

    var itemCodeEls = listing.querySelectorAll('[data-item-code]');
    if (!itemCodeEls.length) {
      return; // nothing to do yet
    }

    var codesSet = new Set();
    itemCodeEls.forEach(function (el) {
      var code = el.getAttribute('data-item-code');
      if (code) codesSet.add(code);
    });

    var itemCodes = Array.from(codesSet);
    if (!itemCodes.length) return;

    frappe.call({
      method: 'catalog_extensions.api.get_consumer_discounts',
      args: { item_codes: itemCodes },
      callback: function (r) {
        if (!r.message) return;
        var discounts = r.message;

        itemCodes.forEach(function (code) {
          var discount = discounts[code];
          if (!discount) return;

          var cardButtons = listing.querySelectorAll('[data-item-code="' + code + '"]');
          cardButtons.forEach(function (btn) {
            var card = btn.closest('.item-card, .card');
            if (!card) return;

            var priceEl = card.querySelector('.product-price');
            if (!priceEl) return;

            if (card.querySelector('.customer-discount-info')) return;

            var info = document.createElement('div');
            info.className = 'customer-discount-info';
            info.textContent = __('Consumer Offer') + ': ' + discount + '%';

            if (priceEl.nextSibling) {
              priceEl.parentNode.insertBefore(info, priceEl.nextSibling);
            } else {
              priceEl.parentNode.appendChild(info);
            }
          });
        });
      }
    });
  }

  // Give ProductView some time to render then attempt injection once
  setTimeout(injectConsumerDiscount, 500);
});

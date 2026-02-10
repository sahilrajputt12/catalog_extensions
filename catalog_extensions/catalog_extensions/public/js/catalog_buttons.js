// Catalog Extensions: enhance webshop buttons for responsive behavior

frappe.ready(() => {
  // Wrap the text label of Add to Cart / Add to Quote and View in Cart / View in Quote
  // buttons in a span.cart-label so CSS can hide the label on mobile while keeping icons.

  function wrapButtonLabel(btn) {
    if (!btn) return;

    // Avoid double-wrapping
    if (btn.querySelector('.cart-label')) return;

    const childNodes = Array.from(btn.childNodes);
    childNodes.forEach(node => {
      // Node.TEXT_NODE === 3
      if (node.nodeType === 3) {
        const text = node.textContent || '';
        if (text.trim().length) {
          const span = document.createElement('span');
          span.className = 'cart-label';
          span.textContent = text.trim();
          btn.replaceChild(span, node);
        }
      }
    });
  }

  // Apply to existing buttons on page load
  document.querySelectorAll('.btn-add-to-cart, .btn-view-in-cart').forEach(wrapButtonLabel);

  // In case Webshop dynamically renders product cards after load,
  // observe DOM changes under the main content and wrap as needed.
  const root = document.querySelector('.page_content') || document.body;
  if (window.MutationObserver && root) {
    const observer = new MutationObserver(mutations => {
      let dirty = false;
      mutations.forEach(m => {
        if (m.addedNodes && m.addedNodes.length) {
          dirty = true;
        }
      });
      if (!dirty) return;
      root.querySelectorAll('.btn-add-to-cart, .btn-view-in-cart').forEach(wrapButtonLabel);
    });

    observer.observe(root, { childList: true, subtree: true });
  }
});

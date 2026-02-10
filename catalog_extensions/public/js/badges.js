// Catalog Extensions: render item badges on grid and list product cards

frappe.ready(() => {
  if (typeof frappe === "undefined" || !frappe.call) return;

  function injectBadges() {
    const root = document.getElementById("product-listing");
    if (!root) return;

    const itemCodeEls = root.querySelectorAll("[data-item-code]");
    if (!itemCodeEls.length) return;

    // Collect unique item codes from any element that has data-item-code
    const codeSet = new Set();
    itemCodeEls.forEach((el) => {
      const code = el.getAttribute("data-item-code");
      if (code) codeSet.add(code);
    });

    const itemCodes = Array.from(codeSet);
    if (!itemCodes.length) return;

    frappe.call({
      method: "catalog_extensions.api.get_item_badges",
      args: { item_codes: itemCodes },
      callback: (r) => {
        const mapping = (r && r.message) || {};
        if (!mapping || typeof mapping !== "object") return;

        itemCodes.forEach((code) => {
          const badges = mapping[code] || [];
          if (!badges.length) return;

          // For each occurrence of this item on the page, decorate the card
          root
            .querySelectorAll(`[data-item-code="${CSS.escape(code)}"]`)
            .forEach((el) => {
              const card = el.closest(".card") || el.closest(".item-card") || el.closest(".list-row");
              const cardBody = card?.querySelector?.(".card-body") || card;
              if (!cardBody) return;

              // Avoid duplicate injection
              if (cardBody.querySelector(".ce-badges")) return;

              const container = document.createElement("div");
              container.className = "ce-badges";

              badges.forEach((b) => {
                const span = document.createElement("span");
                const type = (b.badge_type || "").toString();
                const slug = type.toLowerCase().replace(/[^a-z0-9]+/g, "-");
                span.className = `ce-badge ce-badge-${slug}`;
                span.textContent = type;
                container.appendChild(span);
              });

              // Insert just below the title if present, otherwise at top
              const titleEl = cardBody.querySelector?.(".product-title") || cardBody.querySelector?.("a, h4, h5");
              if (titleEl && titleEl.parentNode) {
                titleEl.parentNode.insertBefore(container, titleEl.nextSibling);
              } else {
                cardBody.insertBefore(container, cardBody.firstChild);
              }
            });
        });
      },
    });
  }

  // Initial tries (listing renders asynchronously)
  setTimeout(injectBadges, 600);
  setTimeout(injectBadges, 1500);
  setTimeout(injectBadges, 3000);

  // Observe listing updates (filters/paging re-render)
  const root = document.getElementById("product-listing");
  if (root && window.MutationObserver) {
    let timer = null;
    const observer = new MutationObserver(() => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(injectBadges, 400);
    });
    observer.observe(root, { childList: true, subtree: true });
  }
});

// Catalog facet sidebar logic (lives in custom app, usable across themes/pages)

function initCatalogFacets() {
  // Only run if frappe is available
  if (typeof frappe === "undefined" || !frappe.call) {
    return;
  }

  const sidebar = document.getElementById("product-filters");
  if (!sidebar) {
    return;
  }

  // In webshop's All Products page, filters live inside #product-filters.
  // To avoid modifying webshop templates, dynamically create facet containers
  // inside this sidebar if it exists.
  const ensureFacetContainers = () => {
    const ensureBlock = (id) => {
      if (document.getElementById(id)) return;
      const block = document.createElement("div");
      block.id = id;
      block.className = "mb-4 filter-block pb-5";
      sidebar.appendChild(block);
    };

    // These IDs are where catalog_extensions will render its facets.
    ensureBlock("facet-price-ranges");
    ensureBlock("facet-offers");
    ensureBlock("facet-badges");
  };

  // Create containers (no-op on pages without #product-filters)
  ensureFacetContainers();

  frappe.call({
    method: "catalog_extensions.api.get_filter_facets",
    args: {},
    callback: (r) => {
      const facets = r && r.message;
      if (!facets) {
        return;
      }

      // Render price range slider integrated with webshop filters.
      // If site has configured Catalog Price Range buckets, use them as
      // discrete slider steps (Amazon-style). Otherwise, fall back to a
      // continuous min/max slider.
      renderPriceRanges("facet-price-ranges", facets.price_ranges || []);

      // Render Offers filter (Has Offers) if container exists.
      renderOffersFilter("facet-offers", facets.offers || []);

      // Render Badges filter (New, Bestseller, etc.) if container exists.
      renderBadgesFilter("facet-badges", facets.badges || []);
    },
  });
}

// Prefer frappe.ready on Frappe websites so this runs after the standard
// product views and filters are set up, but fall back to DOMContentLoaded
// if frappe.ready is not available for some reason.
if (typeof frappe !== "undefined" && typeof frappe.ready === "function") {
  frappe.ready(initCatalogFacets);
  frappe.ready(initMobileFilterToggle);
} else {
  document.addEventListener("DOMContentLoaded", () => {
    initCatalogFacets();
    initMobileFilterToggle();
  });
}

function renderFacetList(containerId, items, labelKey) {
  const container = document.getElementById(containerId);
  if (!container || !Array.isArray(items)) return;

  container.innerHTML = items
    .map((it) => {
      const label = it[labelKey];
      const count = it.count;
      const safeLabel = (label || "").toString();
      const id =
        containerId + "-" + safeLabel.replace(/[^a-zA-Z0-9]+/g, "-").toLowerCase();

      return `
        <div class="filter-option">
          <input type="checkbox" id="${id}" value="${safeLabel}">
          <label for="${id}">${safeLabel} (${count})</label>
        </div>
      `;
    })
    .join("");
}

function renderOffersFilter(containerId, offersFacets) {
  const container = document.getElementById(containerId);
  if (!container || !Array.isArray(offersFacets) || !offersFacets.length) return;

  const applyFieldFilterChange = (filterName, filterValue, checked) => {
    const url = new URL(window.location.href);
    const search = url.searchParams;

    let fieldFilters = {};
    const rawFieldFilters = search.get("field_filters");
    if (rawFieldFilters) {
      try {
        fieldFilters = JSON.parse(rawFieldFilters) || {};
      } catch (e) {
        fieldFilters = {};
      }
    }

    const existing = Array.isArray(fieldFilters[filterName]) ? fieldFilters[filterName].map(String) : [];
    const valueStr = (filterValue || "").toString();

    let next = existing.slice();
    if (checked) {
      if (!next.includes(valueStr)) next.push(valueStr);
    } else {
      next = next.filter((v) => v !== valueStr);
    }

    if (next.length) {
      fieldFilters[filterName] = next;
    } else {
      delete fieldFilters[filterName];
    }

    if (Object.keys(fieldFilters).length) {
      search.set("field_filters", JSON.stringify(fieldFilters));
    } else {
      search.delete("field_filters");
    }

    search.set("from_filters", "1");
    search.delete("start");

    url.search = search.toString();
    window.location.href = url.toString();
  };

  // Read existing field_filters from URL to pre-check state
  const params = frappe.utils.get_query_params();
  let activeOffers = [];
  if (params.field_filters) {
    try {
      const existing = JSON.parse(params.field_filters) || {};
      if (existing.offers_title && Array.isArray(existing.offers_title)) {
        activeOffers = existing.offers_title.map(String);
      }
    } catch (e) {
      // ignore
    }
  }

  const optionsHtml = offersFacets
    .map((f) => {
      const code = (f.code || '').toString();
      const label = f.label || code || '';
      const id = `offers-${code.replace(/[^a-zA-Z0-9]+/g, '-').toLowerCase()}`;
      const checked = activeOffers.includes(code) ? 'checked' : '';
      return `
        <div class="filter-option">
          <label class="d-flex align-items-center">
            <input type="checkbox"
                   class="product-filter field-filter mr-2"
                   id="${id}"
                   data-filter-name="offers_title"
                   data-filter-value="${code}"
                   ${checked}>
            <span>${label} (${f.count})</span>
          </label>
        </div>
      `;
    })
    .join('');

  container.innerHTML = `
    <div class="filter-label mb-2">Offers</div>
    ${optionsHtml}
  `;

  // Bind change handler for dynamically rendered checkboxes
  container.querySelectorAll('input.product-filter.field-filter[data-filter-name="offers_title"]').forEach((el) => {
    el.addEventListener("change", (e) => {
      const target = e.target;
      applyFieldFilterChange(
        target.getAttribute("data-filter-name"),
        target.getAttribute("data-filter-value"),
        target.checked
      );
    });
  });
}

function renderBadgesFilter(containerId, badgeFacets) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const applyFieldFilterChange = (filterName, filterValue, checked) => {
    const url = new URL(window.location.href);
    const search = url.searchParams;

    let fieldFilters = {};
    const rawFieldFilters = search.get("field_filters");
    if (rawFieldFilters) {
      try {
        fieldFilters = JSON.parse(rawFieldFilters) || {};
      } catch (e) {
        fieldFilters = {};
      }
    }

    const existing = Array.isArray(fieldFilters[filterName]) ? fieldFilters[filterName].map(String) : [];
    const valueStr = (filterValue || "").toString();

    let next = existing.slice();
    if (checked) {
      if (!next.includes(valueStr)) next.push(valueStr);
    } else {
      next = next.filter((v) => v !== valueStr);
    }

    if (next.length) {
      fieldFilters[filterName] = next;
    } else {
      delete fieldFilters[filterName];
    }

    if (Object.keys(fieldFilters).length) {
      search.set("field_filters", JSON.stringify(fieldFilters));
    } else {
      search.delete("field_filters");
    }

    search.set("from_filters", "1");
    search.delete("start");

    url.search = search.toString();
    window.location.href = url.toString();
  };

  // Always render the section header so you can see it even if no badges exist yet
  if (!Array.isArray(badgeFacets) || !badgeFacets.length) {
    container.innerHTML = `
      <div class="filter-label mb-2">Badges</div>
      <div class="text-muted small">No badges available</div>
    `;
    return;
  }

  // Read existing field_filters from URL to pre-check state
  const params = frappe.utils.get_query_params();
  let activeBadges = [];
  if (params.field_filters) {
    try {
      const existing = JSON.parse(params.field_filters) || {};
      if (existing.badges && Array.isArray(existing.badges)) {
        activeBadges = existing.badges.map(String);
      }
    } catch (e) {
      // ignore
    }
  }

  const optionsHtml = badgeFacets
    .map((f) => {
      const code = (f.code || "").toString();
      const label = f.label || code || "";
      const id = `badges-${code.replace(/[^a-zA-Z0-9]+/g, "-").toLowerCase()}`;
      const checked = activeBadges.includes(code) ? "checked" : "";
      return `
        <div class="filter-option">
          <label class="d-flex align-items-center">
            <input type="checkbox"
                   class="product-filter field-filter mr-2"
                   id="${id}"
                   data-filter-name="badges"
                   data-filter-value="${code}"
                   ${checked}>
            <span>${label} (${f.count})</span>
          </label>
        </div>
      `;
    })
    .join("");

  container.innerHTML = `
    <div class="filter-label mb-2">Badges</div>
    ${optionsHtml}
  `;

  // Bind change handler here since these checkboxes are rendered dynamically
  // after ProductView binds its filter events.
  container.querySelectorAll('input.product-filter.field-filter[data-filter-name="badges"]').forEach((el) => {
    el.addEventListener("change", (e) => {
      const target = e.target;
      applyFieldFilterChange(
        target.getAttribute("data-filter-name"),
        target.getAttribute("data-filter-value"),
        target.checked
      );
    });
  });
}


function renderPriceRanges(containerId, ranges) {
  const container = document.getElementById(containerId);
  if (!container) return;

  // Compute dynamic max: thousands round-up of actual max price from data
  let maxPrice = 100000;
  if (Array.isArray(ranges) && ranges.length) {
    const toValues = ranges
      .map(r => r.to_amount)
      .filter(v => typeof v === "number" && !isNaN(v));

    if (toValues.length) {
      const rawMax = Math.max.apply(null, toValues);
      // Round up to nearest thousand
      maxPrice = Math.ceil(rawMax / 1000) * 1000;
    }
  }

  // Read existing field_filters from URL to pre-fill slider state
  let activeFrom = null;
  let activeTo = null;
  if (typeof frappe !== "undefined" && frappe.utils && frappe.utils.get_query_params) {
    const params = frappe.utils.get_query_params();
    if (params.field_filters) {
      try {
        const existing = JSON.parse(params.field_filters);
        if (existing.price_from && existing.price_from.length) {
          activeFrom = parseFloat(existing.price_from[0]);
        }
        if (existing.price_to && existing.price_to.length) {
          activeTo = parseFloat(existing.price_to[0]);
        }
      } catch (e) {
        // ignore parse errors and fall back to defaults
      }
    }
  }

  // Helper to write price_from / price_to and reload
  const applyPriceFilter = (fromValue, toValue) => {
    const url = new URL(window.location.href);
    const search = url.searchParams;

    let fieldFilters = {};
    const rawFieldFilters = search.get("field_filters");
    if (rawFieldFilters) {
      try {
        fieldFilters = JSON.parse(rawFieldFilters) || {};
      } catch (e) {
        fieldFilters = {};
      }
    }

    if (fromValue != null && !isNaN(fromValue)) {
      fieldFilters.price_from = [fromValue];
    } else {
      delete fieldFilters.price_from;
    }

    if (toValue != null && !isNaN(toValue)) {
      fieldFilters.price_to = [toValue];
    } else {
      delete fieldFilters.price_to;
    }

    if (Object.keys(fieldFilters).length) {
      search.set("field_filters", JSON.stringify(fieldFilters));
    } else {
      search.delete("field_filters");
    }

    search.set("from_filters", "1");
    search.delete("start");

    url.search = search.toString();
    window.location.href = url.toString();
  };

  // Always use a continuous dual-handle slider for min/max price.
  container.innerHTML = `
      <div class="filter-label mb-2">Price</div>
      <style>
        .ce-price-slider-wrapper {
          padding: 4px 0 0;
        }
        .ce-price-slider-track {
          position: relative;
          height: 4px;
          background: #dbeafe;
          border-radius: 999px;
        }
        .ce-price-slider-range {
          position: relative;
          height: 18px;
          margin-top: -8px;
        }
        .ce-price-slider-range input[type=range] {
          -webkit-appearance: none;
          appearance: none;
          position: absolute;
          width: 100%;
          height: 18px;
          background: transparent;
          pointer-events: none;
        }
        .ce-price-slider-range input[type=range]::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          height: 16px;
          width: 16px;
          border-radius: 50%;
          background: #2563eb;
          border: 2px solid #eff6ff;
          box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.4);
          cursor: pointer;
          pointer-events: auto;
          margin-top: -6px;
        }
        .ce-price-slider-range input[type=range]::-moz-range-thumb {
          height: 16px;
          width: 16px;
          border-radius: 50%;
          background: #2563eb;
          border: 2px solid #eff6ff;
          box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.4);
          cursor: pointer;
          pointer-events: auto;
        }
        .ce-price-inputs {
          display: flex;
          gap: 8px;
          margin-top: 8px;
        }
        .ce-price-inputs input[type=number] {
          flex: 1 1 0;
          min-width: 0;
        }
      </style>
      <div class="filter-option flex-column mb-2 ce-price-slider-wrapper">
        <div class="ce-price-slider-track"></div>
        <div class="ce-price-slider-range">
          <input type="range" id="price-min" min="0" max="${maxPrice}" step="1">
          <input type="range" id="price-max" min="0" max="${maxPrice}" step="1" value="${maxPrice}">
        </div>
        <div class="small text-muted mt-1" id="price-range-display">All</div>
        <div class="ce-price-inputs">
          <input type="number" class="form-control form-control-sm" id="price-input-min" placeholder="Min">
          <input type="number" class="form-control form-control-sm" id="price-input-max" placeholder="Max">
        </div>
      </div>
      <button type="button" class="btn btn-sm btn-secondary mt-1" id="apply-price-filter">Apply</button>
    `;

  const minSlider = document.getElementById("price-min");
  const maxSlider = document.getElementById("price-max");
  const display = document.getElementById("price-range-display");
  const minInputBox = document.getElementById("price-input-min");
  const maxInputBox = document.getElementById("price-input-max");
  const applyBtn = document.getElementById("apply-price-filter");

  // Initialize from active URL filters if present; otherwise use full range
  const initialMin =
    activeFrom != null && !isNaN(activeFrom) && activeFrom >= 0
      ? activeFrom
      : 0;
  const initialMax =
    activeTo != null && !isNaN(activeTo) && activeTo > 0
      ? activeTo
      : maxPrice;

  minSlider.value = initialMin;
  maxSlider.value = initialMax;
  minInputBox.value = initialMin;
  maxInputBox.value = initialMax;

  const updateDisplayContinuous = () => {
    const minVal = parseFloat(minSlider.value || "0");
    const maxVal = parseFloat(maxSlider.value || "0");
    if (!isNaN(minVal) && !isNaN(maxVal)) {
      if (minVal === 0 && maxVal === maxPrice) {
        display.textContent = "All";
      } else {
        display.textContent = `${minVal} - ${maxVal}`;
      }
    }
  };

  const enforceOrderContinuous = () => {
    let minV = parseFloat(minSlider.value || "0");
    let maxV = parseFloat(maxSlider.value || "0");
    if (minV > maxV) {
      const tmp = minV; minV = maxV; maxV = tmp;
      minSlider.value = minV;
      maxSlider.value = maxV;
    }
    minInputBox.value = minSlider.value;
    maxInputBox.value = maxSlider.value;
    updateDisplayContinuous();
  };

  minSlider.addEventListener("input", enforceOrderContinuous);
  maxSlider.addEventListener("input", enforceOrderContinuous);

  minInputBox.addEventListener("change", () => {
    const v = parseFloat(minInputBox.value);
    if (isNaN(v)) return;
    minSlider.value = v;
    enforceOrderContinuous();
  });

  maxInputBox.addEventListener("change", () => {
    const v = parseFloat(maxInputBox.value);
    if (isNaN(v)) return;
    maxSlider.value = v;
    enforceOrderContinuous();
  });

  updateDisplayContinuous();

  applyBtn.addEventListener("click", () => {
    const minVal = parseFloat(minSlider.value);
    const maxVal = parseFloat(maxSlider.value);
    applyPriceFilter(minVal, maxVal);
  });
}

// Mobile filter toggle for item_group pages that use collapse d-md-block
function initMobileFilterToggle() {
  const sidebar = document.getElementById("product-filters");
  if (!sidebar) return;

  // Check if this is an item_group page using the old collapse pattern (not offcanvas-filters)
  if (sidebar.classList.contains("offcanvas-filters")) return;

  // Find the product listing column to add the toggle button
  const productListing = document.getElementById("product-listing");
  if (!productListing) return;

  const parentCol = productListing.closest(".col-md-9, .col-12");
  if (!parentCol) return;

  // Check if toggle button already exists
  if (document.getElementById("toggle-filters")) return;

  // Add offcanvas-filters class to sidebar for CSS targeting
  sidebar.classList.add("offcanvas-filters");
  // Remove Bootstrap collapse classes so the sidebar isn't force-hidden on mobile
  sidebar.classList.remove("collapse");
  sidebar.classList.remove("show");

  // Create and insert mobile toggle button at the start of the column
  const toggleBtn = document.createElement("div");
  toggleBtn.className = "d-flex justify-content-end mb-3 d-md-none";
  toggleBtn.innerHTML = `
    <button type="button" class="btn btn-outline-secondary btn-sm" id="toggle-filters">
      ${typeof __ !== "undefined" ? __("Filters") : "Filters"}
    </button>
  `;

  // Prepend to the product listing column (product-listing IS the column div)
  productListing.prepend(toggleBtn);

  // Create backdrop if not exists
  let backdrop = document.getElementById("filters-backdrop");
  if (!backdrop) {
    backdrop = document.createElement("div");
    backdrop.className = "filters-backdrop d-md-none";
    backdrop.id = "filters-backdrop";
    document.body.appendChild(backdrop);
  }

  const body = document.body;

  function openFilters() {
    body.classList.add("filters-open");
  }

  function closeFilters() {
    body.classList.remove("filters-open");
  }

  // Bind toggle button click
  const btn = document.getElementById("toggle-filters");
  if (btn) {
    btn.addEventListener("click", () => {
      if (body.classList.contains("filters-open")) {
        closeFilters();
      } else {
        openFilters();
      }
    });
  }

  // Bind backdrop click
  if (backdrop) {
    backdrop.addEventListener("click", () => {
      closeFilters();
    });
  }
}


// Catalog facet sidebar logic (lives in custom app, usable across themes/pages)

document.addEventListener("DOMContentLoaded", () => {
  // Only run if frappe is available
  if (typeof frappe === "undefined" || !frappe.call) return;

  frappe.call({
    method: "catalog_extensions.api.get_filter_facets",
    args: {},
    callback: (r) => {
      const facets = r && r.message;
      if (!facets) return;

      // Render price range slider integrated with webshop filters.
      // If site has configured Catalog Price Range buckets, use them as
      // discrete slider steps (Amazon-style). Otherwise, fall back to a
      // continuous min/max slider.
      renderPriceRanges("facet-price-ranges", facets.price_ranges || []);

      // Render availability filter (In stock / Out of stock) if container exists.
      renderAvailabilityFilter("facet-availability", facets.availability || []);

      // Render Offers filter (Has Offers) if container exists.
      renderOffersFilter("facet-offers", facets.offers || []);

      // Render Badges filter (New, Bestseller, etc.) if container exists.
      renderBadgesFilter("facet-badges", facets.badges || []);
    },
  });
});

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
}

function renderAvailabilityFilter(containerId, availabilityFacets) {
  const container = document.getElementById(containerId);
  if (!container || !Array.isArray(availabilityFacets) || !availabilityFacets.length) return;

  // Read existing field_filters from URL to pre-check state
  const params = frappe.utils.get_query_params();
  let activeAvailability = [];
  if (params.field_filters) {
    try {
      const existing = JSON.parse(params.field_filters);
      if (existing.custom_availability && Array.isArray(existing.custom_availability)) {
        activeAvailability = existing.custom_availability.map(String);
      }
    } catch (e) {
      // ignore
    }
  }

  const optionsHtml = availabilityFacets
    .map((f) => {
      const code = (f.code || "").toString();
      const label = f.label || code || "";
      const id = `availability-${code.replace(/[^a-zA-Z0-9]+/g, "-").toLowerCase()}`;
      const checked = activeAvailability.includes(code) ? "checked" : "";
      return `
        <div class="filter-option">
          <label class="d-flex align-items-center">
            <input type="checkbox"
                   class="product-filter field-filter mr-2"
                   id="${id}"
                   data-filter-name="custom_availability"
                   data-filter-value="${code}"
                   ${checked}>
            <span>${label}</span>
          </label>
        </div>
      `;
    })
    .join("");

  container.innerHTML = `
    <div class="filter-label mb-2">Availability</div>
    ${optionsHtml}
  `;
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


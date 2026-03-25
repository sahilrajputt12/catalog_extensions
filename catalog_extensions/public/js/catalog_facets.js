// Catalog facet sidebar logic (lives in custom app, usable across themes/pages)

document.addEventListener("DOMContentLoaded", () => {
  // Only run if frappe is available
  if (typeof frappe === "undefined" || !frappe.call) return;

  // Initialize with retry for pages where filters load dynamically
  let attempts = 0;
  const maxAttempts = 5;
  
  function initFacets() {
    attempts++;
    
    // Work with vanilla webshop templates: if our facet containers are not
    // present in the core HTML, create them inside the existing filters
    // sidebar so we don't need to touch core templates.
    const filtersSidebar =
      document.getElementById("product-filters") ||
      document.querySelector(".filters-section") ||
      document.querySelector(".webshop-filters") ||
      document.querySelector(".item-filters") ||
      document.querySelector("[data-filters-section]");

    if (!filtersSidebar && attempts < maxAttempts) {
      // Retry if sidebar not found yet
      setTimeout(initFacets, 300);
      return;
    }
    
    if (!filtersSidebar) {
      console.log('[CatalogFacets] No filters sidebar found after retries');
      return;
    }

    // Try to detect where core field filters (Brand, Item Group) end and where
    // attribute filters (Color, Size, etc.) begin so we can place our facets
    // in a stable order: Brand/Item Group (core) -> Price/Offers/Badges (ours)
    // -> attribute filters.
    let firstAttributeBlock = null;
    let lastFieldFilterBlock = null;
    let allFilterBlocks = [];

    allFilterBlocks = Array.from(filtersSidebar.querySelectorAll(".filter-block"));
    allFilterBlocks.forEach((block) => {
      if (block.querySelector && block.querySelector("input.attribute-filter")) {
        if (!firstAttributeBlock) {
          firstAttributeBlock = block;
        }
      } else if (block.querySelector && block.querySelector("input.field-filter")) {
        lastFieldFilterBlock = block;
      }
    });
    
    // If no field filters detected but filter blocks exist, use the last one
    if (!lastFieldFilterBlock && allFilterBlocks.length > 0) {
      lastFieldFilterBlock = allFilterBlocks[allFilterBlocks.length - 1];
    }

    const ensureFacetContainer = (id) => {
      if (document.getElementById(id)) return;

      const div = document.createElement("div");
      div.id = id;
      // Match webshop filter-block styling
      div.className = "mb-4 filter-block pb-5";

      // Preferred placement: directly after the last field filter block
      if (lastFieldFilterBlock && lastFieldFilterBlock.parentNode) {
        lastFieldFilterBlock.parentNode.insertBefore(div, lastFieldFilterBlock.nextSibling);
      } else if (firstAttributeBlock && firstAttributeBlock.parentNode) {
        // Fallback: before first attribute filter block if we couldn't detect
        // any field filter blocks.
        firstAttributeBlock.parentNode.insertBefore(div, firstAttributeBlock);
      } else {
        // Final fallback: after static filters title/header, or at top
        const header =
          filtersSidebar.querySelector(".filters-title") ||
          filtersSidebar.querySelector(".filter-section-title") ||
          null;
        if (header && header.parentNode) {
          header.parentNode.insertBefore(div, header.nextSibling);
        } else {
          filtersSidebar.insertBefore(div, filtersSidebar.firstChild);
        }
      }
    };

    // Create facet containers in desired display order: Price first, then Offers, then Badges
    // We insert them one by one, each before the reference point, so process in reverse
    const facetIds = ["facet-badges", "facet-offers", "facet-price-ranges"];
    facetIds.forEach((id) => ensureFacetContainer(id));

    // Detect if we're on an item group page
    const itemGroupEl = document.querySelector('[data-item-group]');
    const itemGroup = itemGroupEl ? itemGroupEl.getAttribute('data-item-group') : null;

    frappe.call({
      method: "catalog_extensions.api.get_filter_facets",
      args: itemGroup ? { item_group: itemGroup } : {},
      callback: (r) => {
        const facets = r && r.message;
        if (!facets) return;

        // Cache facets globally so mobile clone can re-render with bindings
        window.__catalogFacetsData = facets;

        // Render price range slider integrated with webshop filters.
        // Use true min/max from DB if available.
        renderPriceRanges(
          "facet-price-ranges",
          facets.price_ranges || [],
          facets.price_min_max || {}
        );

        // Render Offers filter (Has Offers) if container exists.
        renderOffersFilter("facet-offers", facets.offers || []);

        // Render Badges filter (New, Bestseller, etc.) if container exists.
        renderBadgesFilter("facet-badges", facets.badges || []);

        // Inject counts into core webshop filters (Brand, Item Group)
        injectCountsIntoCoreFilters(facets.brands || [], facets.item_groups || []);

        // After all facets are rendered, set up the mobile off-canvas UI
        setupMobileOffCanvasFilters();
      },
    });
  }
  
  // Start initialization
  initFacets();
});

function setupMobileOffCanvasFilters() {
  // Use a single shared filters DOM; only enable off-canvas on small screens
  if (window.innerWidth >= 768) {
    return;
  }

  const filtersContainer = document.getElementById("product-filters");
  if (!filtersContainer) return;

  const parent = filtersContainer.parentElement;
  if (parent && !parent.querySelector(".mobile-filters-toggle")) {
    // Toggle button to open off-canvas
    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "btn btn-outline-primary d-md-none mb-3 w-100 mobile-filters-toggle";
    toggleBtn.innerHTML = `
      <svg class="icon icon-sm" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5M9 12h3.75M9 12a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H3.75M9 12h3.75M9 12a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H3.75" />
      </svg>
      Filters
    `;
    toggleBtn.addEventListener("click", () => {
      document.body.classList.add("ce-mobile-filters-open");
    });
    parent.insertBefore(toggleBtn, filtersContainer);
  }

  // Create off-canvas overlay and sidebar
  const overlay = document.createElement("div");
  overlay.className = "ce-mobile-filters-overlay";
  overlay.addEventListener("click", () => {
    document.body.classList.remove("ce-mobile-filters-open");
  });

  const sidebar = document.createElement("div");
  sidebar.className = "ce-mobile-filters-sidebar";
  sidebar.innerHTML = `
    <div class="ce-mobile-filters-header">
      <button class="ce-mobile-filters-close btn btn-link">
        <svg class="icon icon-md" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
      <h5 class="mb-0">Filters</h5>
    </div>
    <div class="ce-mobile-filters-body">
      <!-- Move original filters content here -->
    </div>
  `;

  // Move the existing filters container into the off-canvas sidebar body so
  // there is only a single source of truth for filters (no cloning).
  const sidebarBody = sidebar.querySelector(".ce-mobile-filters-body");

  filtersContainer.classList.remove("collapse", "d-md-block", "mr-4");
  filtersContainer.classList.add("offcanvas-filters");
  sidebarBody.appendChild(filtersContainer);

  // Append overlay and sidebar to body (only visible on mobile)
  document.body.appendChild(overlay);
  document.body.appendChild(sidebar);

  // Close button inside sidebar
  sidebar.querySelector(".ce-mobile-filters-close").addEventListener("click", () => {
    document.body.classList.remove("ce-mobile-filters-open");
  });
}

function injectCountsIntoCoreFilters(brands, itemGroups) {
  // Create lookup maps for quick access
  const brandCounts = {};
  brands.forEach(b => {
    brandCounts[b.brand] = b.count;
  });
  
  const groupCounts = {};
  itemGroups.forEach(g => {
    groupCounts[g.item_group_name] = g.count;
  });

  // Inject counts into Brand filter labels
  document.querySelectorAll('#product-filters .filter-block, .filters-section .filter-block').forEach(block => {
    const label = block.querySelector('.filter-label');
    if (!label) return;
    
    const labelText = label.textContent.trim().toLowerCase();
    
    // Handle Brand filters
    if (labelText.includes('brand')) {
      block.querySelectorAll('.filter-lookup-wrapper label, .filter-options label').forEach(filterLabel => {
        // Find or create label-area span
        let labelArea = filterLabel.querySelector('.label-area');
        if (!labelArea) {
          // Wrap text content in label-area span if not already wrapped
          const text = filterLabel.textContent.trim();
          if (text && !filterLabel.querySelector('span')) {
            labelArea = document.createElement('span');
            labelArea.className = 'label-area';
            labelArea.textContent = text;
            // Clear and re-append
            filterLabel.innerHTML = '';
            // Keep the input if present
            const input = filterLabel.querySelector('input');
            if (input) filterLabel.appendChild(input);
            filterLabel.appendChild(labelArea);
          }
        }
        
        if (labelArea) {
          const text = labelArea.textContent.trim().replace(/\s*\(\d+\)$/, ''); // Remove existing count
          const count = brandCounts[text];
          if (count !== undefined && !labelArea.textContent.includes('(')) {
            labelArea.innerHTML = `${text} <span class="text-muted">(${count})</span>`;
          }
        }
      });
    }
    
    // Handle Item Group filters
    if (labelText.includes('category') || labelText.includes('item group')) {
      block.querySelectorAll('.filter-lookup-wrapper label, .filter-options label').forEach(filterLabel => {
        let labelArea = filterLabel.querySelector('.label-area');
        if (!labelArea) {
          const text = filterLabel.textContent.trim();
          if (text && !filterLabel.querySelector('span')) {
            labelArea = document.createElement('span');
            labelArea.className = 'label-area';
            labelArea.textContent = text;
            const input = filterLabel.querySelector('input');
            filterLabel.innerHTML = '';
            if (input) filterLabel.appendChild(input);
            filterLabel.appendChild(labelArea);
          }
        }
        
        if (labelArea) {
          const text = labelArea.textContent.trim().replace(/\s*\(\d+\)$/, '');
          const count = groupCounts[text];
          if (count !== undefined && !labelArea.textContent.includes('(')) {
            labelArea.innerHTML = `${text} <span class="text-muted">(${count})</span>`;
          }
        }
      });
    }
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
  if (!container) return;

  if (!Array.isArray(offersFacets) || !offersFacets.length) {
    container.innerHTML = "";
    container.style.display = "none";
    return;
  }

  container.style.display = "";

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
        <div class="filter-lookup-wrapper checkbox" data-value="${code}">
          <label for="${id}">
            <input type="checkbox"
                   class="product-filter field-filter"
                   id="${id}"
                   data-filter-name="offers_title"
                   data-filter-value="${code}"
                   style="width: 14px !important"
                   ${checked}>
            <span class="label-area">${label} <span class="text-muted">(${f.count})</span></span>
          </label>
        </div>
      `;
    })
    .join('');

  container.innerHTML = `
    <div class="filter-label mb-3">Offers</div>
    <div class="filter-options">
      ${optionsHtml}
    </div>
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

  if (!Array.isArray(badgeFacets) || !badgeFacets.length) {
    container.innerHTML = "";
    container.style.display = "none";
    return;
  }

  container.style.display = "";

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
        <div class="filter-lookup-wrapper checkbox" data-value="${code}">
          <label for="${id}">
            <input type="checkbox"
                   class="product-filter field-filter"
                   id="${id}"
                   data-filter-name="badges"
                   data-filter-value="${code}"
                   style="width: 14px !important"
                   ${checked}>
            <span class="label-area">${label} <span class="text-muted">(${f.count})</span></span>
          </label>
        </div>
      `;
    })
    .join("");

  container.innerHTML = `
    <div class="filter-label mb-3">Badges</div>
    <div class="filter-options">
      ${optionsHtml}
    </div>
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


function renderPriceRanges(containerId, ranges, minMax) {
  const container = document.getElementById(containerId);
  if (!container) return;

  // Compute min/max from backend if provided; otherwise fall back to heuristic.
  let minPrice = 0;
  let maxPrice = 100000;

  if (minMax && typeof minMax.min === "number" && !isNaN(minMax.min)) {
    minPrice = Math.max(0, Math.floor(minMax.min));
  }
  if (minMax && typeof minMax.max === "number" && !isNaN(minMax.max)) {
    const rawMax = minMax.max;
    // Round up to nearest thousand for nicer slider UI
    maxPrice = Math.ceil(rawMax / 1000) * 1000;
  } else if (Array.isArray(ranges) && ranges.length) {
    const toValues = ranges
      .map(r => r.to_amount)
      .filter(v => typeof v === "number" && !isNaN(v));

    if (toValues.length) {
      const rawMax = Math.max.apply(null, toValues);
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
      <div class="filter-label mb-3">Price</div>
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
          <input type="range" id="price-min" min="${minPrice}" max="${maxPrice}" step="1">
          <input type="range" id="price-max" min="${minPrice}" max="${maxPrice}" step="1" value="${maxPrice}">
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
      : minPrice;
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


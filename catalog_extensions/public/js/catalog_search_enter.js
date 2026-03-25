// catalog_search_enter.js
// Custom app: catalog_extensions
// Behavior:
// 1) On Enter in #search-box, update URL ?search=<value> and reset start.
// 2) Monkey-patch webshop.ProductView.get_query_filters to pass search into
//    backend query_args, without touching core files.

(function() {
    'use strict';

    function init() {
        if (typeof frappe === 'undefined' || typeof window.webshop === 'undefined' || !window.webshop.ProductView) {
            // Wait until core JS is loaded
            setTimeout(init, 100);
            return;
        }

        patchProductView();
        bindEnterKey();
    }

    function patchProductView() {
        var ProductView = window.webshop && window.webshop.ProductView;
        if (!ProductView || !ProductView.prototype) return;

        var proto = ProductView.prototype;
        if (proto.__catalog_search_patched) {
            return;
        }

        var originalGetQueryFilters = proto.get_query_filters;
        proto.get_query_filters = function() {
            // Call original implementation to keep native behavior
            var args = originalGetQueryFilters
                ? originalGetQueryFilters.call(this)
                : {};

            try {
                var qp = frappe.utils.get_query_params();
                if (qp && qp.search) {
                    args.search = qp.search;
                }
            } catch (e) {
                // Fail-safe: don't break listing if something goes wrong
            }

            return args;
        };

        proto.__catalog_search_patched = true;
    }

    function bindEnterKey() {
        if (document.__catalog_search_global_bound) return;
        document.__catalog_search_global_bound = true;

        document.addEventListener('keydown', function(e) {
            var target = e.target || e.srcElement;
            if (!target || !target.id || target.id !== 'search-box') {
                return;
            }

            if (e.key === 'Enter') {
                e.preventDefault();
                var query = (target.value || '').trim();
                var url = new URL(window.location.href);

                if (query) {
                    url.searchParams.set('search', query);
                } else {
                    url.searchParams.delete('search');
                }

                // Reset pagination when a new search is triggered
                url.searchParams.delete('start');

                window.location.href = url.toString();
            }
        }, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

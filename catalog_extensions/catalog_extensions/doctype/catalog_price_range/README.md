# Catalog Price Range

Define configurable price buckets for the catalog. Used by catalog_extensions to render price range facets.

- `label`: Display label (e.g. "Under 1000", "1000–5000", "Above 5000").
- `from_amount`: Optional lower bound (inclusive).
- `to_amount`: Optional upper bound (exclusive).
- `enabled`: Whether this range is active.
- `sort_order`: Order to display ranges; lower numbers first.

Only enabled ranges are used by the facet API.

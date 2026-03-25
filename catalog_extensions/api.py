import frappe
from typing import Any, Dict, List, Optional, Tuple
from frappe.utils import add_days, nowdate, getdate, flt, cint
from webshop.webshop.product_data_engine.query import ProductQuery
from webshop.webshop.product_data_engine.filters import ProductFiltersBuilder
from webshop.webshop.doctype.override_doctype.item_group import get_child_groups_for_website


@frappe.whitelist(allow_guest=True)
def get_filter_facets(item_group: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Return facet counts for filter UI (categories, brands, price ranges).

    All logic lives in this custom app. Facets are:
    - item_groups: Website Item item_group counts
    - brands: Website Item brand counts
    - price_ranges: Configurable ranges from Catalog Price Range DocType per site
    - availability: In stock / Out of stock counts
    
    Admin can control visibility via site config:
    - bench --site site.local set-config catalog_extensions_show_offers_filter 0
    - bench --site site.local set-config catalog_extensions_show_badges_filter 0
    
    Args:
        item_group: Optional item group name to filter facets contextually
    """

    facets: Dict[str, List[Dict[str, Any]]] = {}
    
    # Check site config for filter visibility (default to enabled)
    # Use flat keys: catalog_extensions_show_offers_filter
    show_offers = cint(frappe.conf.get("catalog_extensions_show_offers_filter", 1))
    show_badges = cint(frappe.conf.get("catalog_extensions_show_badges_filter", 1))
    
    # Build item group filter clause if context is provided
    item_group_filter = ""
    params: Dict[str, Any] = {}
    if item_group:
        # Get all child item groups for this category (including itself)
        child_groups = get_child_groups_for_website(item_group, include_self=True)
        group_names = [g.name for g in child_groups] if child_groups else [item_group]
        if group_names:
            item_group_filter = " AND wi.item_group IN %(item_groups)s"
            params["item_groups"] = tuple(group_names)

    # Item Group (categories) - contextual
    facets["item_groups"] = frappe.db.sql(
        f"""
        SELECT ig.item_group_name, COUNT(DISTINCT wi.name) AS count
        FROM `tabWebsite Item` wi
        JOIN `tabItem Group` ig ON wi.item_group = ig.name
        WHERE wi.published = 1 {item_group_filter}
        GROUP BY ig.item_group_name
        ORDER BY count DESC
        """,
        params,
        as_dict=True,
    )

    # Brand - contextual
    facets["brands"] = frappe.db.sql(
        f"""
        SELECT brand, COUNT(*) AS count
        FROM `tabWebsite Item`
        WHERE published = 1 AND brand IS NOT NULL AND brand != '' {item_group_filter.replace('wi.', '')}
        GROUP BY brand
        ORDER BY count DESC
        LIMIT 20
        """,
        params,
        as_dict=True,
    )

    # Site-configurable price ranges from Catalog Price Range DocType
    facets["price_ranges"] = _get_price_range_facets()

    # Global min/max price for active price list (for continuous slider bounds)
    facets["price_min_max"] = _get_price_min_max()

    # Offers facet - controlled by site config
    if show_offers:
        offers_rows = frappe.db.sql(
            """
            SELECT wo.offer_title AS offer_title, COUNT(DISTINCT wi.name) AS count
            FROM `tabWebsite Offer` wo
            JOIN `tabWebsite Item` wi ON wi.name = wo.parent
            WHERE wi.published = 1 AND wo.offer_title IS NOT NULL AND wo.offer_title != ''
            GROUP BY wo.offer_title
            ORDER BY count DESC, wo.offer_title ASC
            """,
            as_dict=True,
        )
        facets["offers"] = [
            {"label": row["offer_title"], "code": row["offer_title"], "count": row["count"]}
            for row in offers_rows
        ]

    # Badges facet - controlled by site config
    if show_badges:
        badge_rows = frappe.db.sql(
            """
            SELECT ib.badge_type AS badge_type, COUNT(DISTINCT wi.name) AS count
            FROM `tabItem Badge` ib
            JOIN `tabItem` i ON ib.parent = i.name
            JOIN `tabWebsite Item` wi ON wi.item_code = i.name
            WHERE wi.published = 1 AND ib.badge_type IS NOT NULL AND ib.badge_type != ''
            GROUP BY ib.badge_type
            ORDER BY count DESC, ib.badge_type ASC
            """,
            as_dict=True,
        )
        facets["badges"] = [
            {"label": row["badge_type"], "code": row["badge_type"], "count": row["count"]}
            for row in badge_rows
        ]

    return facets


def _get_price_min_max() -> Dict[str, Optional[float]]:
    """Return global min/max price across published Website Items for active price list.

    Used by the frontend price slider as true dataset bounds.
    """

    price_list = (
        frappe.db.get_single_value("Webshop Settings", "price_list")
        or frappe.db.get_single_value("Selling Settings", "selling_price_list")
        or "Standard Selling"
    )

    row = frappe.db.sql(
        """
        SELECT
            MIN(ip.price_list_rate) AS min_rate,
            MAX(ip.price_list_rate) AS max_rate
        FROM `tabWebsite Item` wi
        JOIN `tabItem` i ON i.name = wi.item_code
        JOIN `tabItem Price` ip ON ip.item_code = i.name
        WHERE wi.published = 1
          AND ip.selling = 1
          AND ip.price_list = %(price_list)s
        """,
        {"price_list": price_list},
        as_dict=True,
    )

    if not row:
        return {"min": None, "max": None}

    min_rate = row[0].get("min_rate")
    max_rate = row[0].get("max_rate")

    # Normalize to plain floats for JSON
    return {
        "min": float(min_rate) if min_rate is not None else None,
        "max": float(max_rate) if max_rate is not None else None,
    }


def _get_price_range_facets() -> List[Dict[str, Any]]:
    """Return price range facets based on Catalog Price Range records.

    Each site can define its own ranges in the `Catalog Price Range` DocType.
    We count Website Items whose Item Price (for the active price list) falls
    within each configured range.
    """

    # Try to get a relevant selling price list for this site
    price_list = (
        frappe.db.get_single_value("Webshop Settings", "price_list")
        or frappe.db.get_single_value("Selling Settings", "selling_price_list")
        or "Standard Selling"
    )

    ranges = frappe.get_all(
        "Catalog Price Range",
        filters={"enabled": 1},
        fields=["name", "label", "from_amount", "to_amount", "sort_order"],
        order_by="COALESCE(sort_order, 9999), from_amount asc, to_amount asc, name asc",
    )

    if not ranges:
        return []

    facets: List[Dict[str, Any]] = []

    for r in ranges:
        where_clauses = [
            "wi.published = 1",
            "ip.selling = 1",
            "ip.price_list = %(price_list)s",
        ]

        params: Dict[str, Any] = {"price_list": price_list}

        if r.get("from_amount") is not None:
            where_clauses.append("ip.price_list_rate >= %(from_amount)s")
            params["from_amount"] = r["from_amount"]

        if r.get("to_amount") is not None:
            where_clauses.append("ip.price_list_rate < %(to_amount)s")
            params["to_amount"] = r["to_amount"]

        where_sql = " AND ".join(where_clauses)

        count_res = frappe.db.sql(
            f"""
            SELECT COUNT(DISTINCT wi.name) AS count
            FROM `tabWebsite Item` wi
            JOIN `tabItem` i ON i.name = wi.item_code
            JOIN `tabItem Price` ip ON ip.item_code = i.name
            WHERE {where_sql}
            """,
            params,
            as_dict=True,
        )

        count = (count_res[0]["count"] if count_res else 0) or 0

        facets.append(
            {
                "name": r["name"],
                "label": r["label"],
                "from_amount": r.get("from_amount"),
                "to_amount": r.get("to_amount"),
                "count": count,
            }
        )

    return facets


def _get_availability_facets() -> List[Dict[str, Any]]:
    """Return availability facets (In stock / Out of stock) based on
    Website Item.custom_availability.

    This assumes a custom Select field `custom_availability` on Website Item with
    options like "In stock" and "Out of stock". All control is at Website
    Item level; no Bin/warehouse logic is applied here.
    """

    counts = frappe.db.sql(
        """
        SELECT
            SUM(CASE WHEN wi.custom_availability = 'In stock' THEN 1 ELSE 0 END) AS in_stock,
            SUM(CASE WHEN wi.custom_availability = 'Out of stock' THEN 1 ELSE 0 END) AS out_of_stock
        FROM `tabWebsite Item` wi
        WHERE wi.published = 1
        """,
        as_dict=True,
    )

    row = counts[0] if counts else {"in_stock": 0, "out_of_stock": 0}

    return [
        {
            "label": "In stock",
            "code": "In stock",
            "count": row.get("in_stock") or 0,
        },
        {
            "label": "Out of stock",
            "code": "Out of stock",
            "count": row.get("out_of_stock") or 0,
        },
    ]


def _is_date_active(valid_from: Optional[str], valid_upto: Optional[str]) -> bool:
    """Return True if today's date is within the optional valid_from / valid_upto window."""

    today = getdate(nowdate())
    if valid_from:
        try:
            if today < getdate(valid_from):
                return False
        except Exception:
            pass

    if valid_upto:
        try:
            if today > getdate(valid_upto):
                return False
        except Exception:
            pass

    return True


@frappe.whitelist()
def recompute_item_badges() -> None:
    """Recompute automatic badges (New, Bestseller, On Sale, Low Stock) for all Website Items.

    Manual badges (source = "Manual") are never touched.
    """

    # Configuration: tweak these thresholds as needed
    days_for_new = 30
    low_stock_threshold = 5
    bestseller_top_n = 50

    today = nowdate()
    new_since = add_days(today, -days_for_new)

    # Map item_code -> total qty sold in the last 30 days (very rough bestseller metric)
    sales_rows = frappe.db.sql(
        """
        SELECT si_item.item_code, SUM(si_item.qty) AS qty
        FROM `tabSales Invoice Item` si_item
        JOIN `tabSales Invoice` si ON si.name = si_item.parent
        WHERE si.docstatus = 1 AND si.posting_date >= %(from_date)s
        GROUP BY si_item.item_code
        """,
        {"from_date": new_since},
        as_dict=True,
    )

    sales_by_item: Dict[str, float] = {
        row["item_code"]: flt(row["qty"]) for row in sales_rows if row.get("item_code")
    }

    # Determine bestseller cutoff
    sorted_items = sorted(sales_by_item.items(), key=lambda x: x[1], reverse=True)
    bestseller_codes = {code for code, _ in sorted_items[:bestseller_top_n]}

    # Consider only Items that have a published Website Item
    website_items = frappe.get_all(
        "Website Item",
        filters={"published": 1},
        fields=["name", "item_code"],
    )

    item_codes = {wi["item_code"] for wi in website_items if wi.get("item_code")}
    if not item_codes:
        return

    # Prefetch basic data for low stock & new
    items = frappe.get_all(
        "Item",
        filters={"name": ["in", list(item_codes)]},
        fields=["name", "creation", "is_stock_item"],
    )

    # Prefetch Bin quantities (sum across all warehouses for simplicity)
    bin_rows = frappe.db.sql(
        """
        SELECT item_code, SUM(actual_qty) AS qty
        FROM `tabBin`
        WHERE item_code in %(items)s
        GROUP BY item_code
        """,
        {"items": tuple(item_codes)},
        as_dict=True,
    )

    qty_by_item: Dict[str, float] = {
        row["item_code"]: flt(row["qty"]) for row in bin_rows if row.get("item_code")
    }

    # Helper to upsert an automatic badge row on Item
    def upsert_auto_badge(item_name: str, badge_type: str) -> None:
        item_doc = frappe.get_doc("Item", item_name)

        # Remove any auto rows of this type that might be duplicated
        remaining = []
        exists = False
        for row in item_doc.get("badges") or []:
            if row.get("badge_type") == badge_type and row.get("source") == "Auto":
                if not exists:
                    exists = True
                    remaining.append(row)
                # drop extras
            else:
                remaining.append(row)

        item_doc.set("badges", remaining)

        if not exists:
            item_doc.append(
                "badges",
                {
                    "badge_type": badge_type,
                    "source": "Auto",
                },
            )

        item_doc.flags.ignore_validate = True
        item_doc.flags.ignore_permissions = True
        item_doc.save(ignore_permissions=True)

    # Helper to drop auto badge if rule no longer applies
    def clear_auto_badge(item_name: str, badge_type: str) -> None:
        item_doc = frappe.get_doc("Item", item_name)
        new_rows = [
            row
            for row in (item_doc.get("badges") or [])
            if not (row.get("badge_type") == badge_type and row.get("source") == "Auto")
        ]
        if len(new_rows) == len(item_doc.get("badges") or []):
            return

        item_doc.set("badges", new_rows)
        item_doc.flags.ignore_validate = True
        item_doc.flags.ignore_permissions = True
        item_doc.save(ignore_permissions=True)

    # Compute per-item flags and upsert/clear automatic badges
    for item in items:
        code = item["name"]

        # New: creation date within last N days
        is_new = False
        try:
            is_new = getdate(item.get("creation")) >= getdate(new_since)
        except Exception:
            is_new = False

        if is_new:
            upsert_auto_badge(code, "New")
        else:
            clear_auto_badge(code, "New")

        # Bestseller: in top N by qty sold
        if code in bestseller_codes:
            upsert_auto_badge(code, "Bestseller")
        else:
            clear_auto_badge(code, "Bestseller")

        # On Sale: use Website Item.custom_consumer_discount as simple proxy
        discount = None
        wi_rows = [wi for wi in website_items if wi["item_code"] == code]
        if wi_rows:
            discount = frappe.db.get_value(
                "Website Item",
                wi_rows[0]["name"],
                "custom_consumer_discount",
            )
        if flt(discount) > 0:
            upsert_auto_badge(code, "On Sale")
        else:
            clear_auto_badge(code, "On Sale")

        # Low Stock: only for stock items with total qty below threshold
        total_qty = qty_by_item.get(code, 0.0)
        if item.get("is_stock_item") and total_qty > 0 and total_qty <= low_stock_threshold:
            upsert_auto_badge(code, "Low Stock")
        else:
            clear_auto_badge(code, "Low Stock")


@frappe.whitelist(allow_guest=True)
def get_item_badges(item_codes: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Return mapping of item_code -> active badges (auto + manual).

    Used by frontend JS to render badges on product cards.
    """

    # Normalize input (may be JSON string)
    if isinstance(item_codes, str):
        try:
            import json as _json

            item_codes = _json.loads(item_codes)
        except Exception:
            item_codes = [item_codes]

    if not isinstance(item_codes, (list, tuple)):
        item_codes = [item_codes]

    if not item_codes:
        return {}

    website_items = frappe.get_all(
        "Website Item",
        filters={"item_code": ["in", list(item_codes)], "published": 1},
        fields=["name", "item_code"],
    )

    by_code: Dict[str, str] = {row["item_code"]: row["item_code"] for row in website_items}
    if not by_code:
        return {}

    items = frappe.get_all(
        "Item",
        filters={"name": ["in", list(by_code.keys())]},
        fields=["name"],
    )

    result: Dict[str, List[Dict[str, Any]]] = {code: [] for code in item_codes}

    if not items:
        return result

    badge_rows = frappe.get_all(
        "Item Badge",
        filters={"parent": ["in", [i["name"] for i in items]]},
        fields=["parent", "badge_type", "source", "valid_from", "valid_upto"],
        order_by="idx asc",
    )

    parent_to_code = {row["name"]: row["name"] for row in items}

    for row in badge_rows:
        parent_item = row["parent"]
        code = parent_to_code.get(parent_item)
        if not code:
            continue
        if not row.get("badge_type"):
            continue
        if not _is_date_active(row.get("valid_from"), row.get("valid_upto")):
            continue

        result.setdefault(code, []).append(
            {
                "badge_type": row.get("badge_type"),
                "source": row.get("source"),
                "valid_from": row.get("valid_from"),
                "valid_upto": row.get("valid_upto"),
            }
        )

    return result

@frappe.whitelist(allow_guest=True)
def get_product_filter_data_with_price(query_args=None):
    """Product filter data with custom Offers/Badges/Price filters.

    Uses standard ProductQuery with Table MultiSelect fields for filtering.
    No custom SQL - relies on core Frappe ORM for child table filtering.
    """

    # Ensure dict form, mirroring core behavior of the original function
    if isinstance(query_args, str):
        import json
        query_args = json.loads(query_args)

    q = frappe._dict(query_args or {})

    # Normalize field_filters to a dict (it may arrive as a JSON string in URL)
    import json as _json
    raw_field_filters = q.get("field_filters") or {}
    if isinstance(raw_field_filters, str):
        try:
            raw_field_filters = _json.loads(raw_field_filters) or {}
        except Exception:
            raw_field_filters = {}

    field_filters: Dict[str, Any] = dict(raw_field_filters)

    # Map top-level brand param (used by header dropdown links) into
    # field_filters["brand"] so it behaves like a normal field filter.
    brand = q.get("brand")
    if brand:
        if isinstance(brand, str):
            brand_values = [brand]
        else:
            brand_values = list(brand)
        existing = field_filters.get("brand") or []
        if not isinstance(existing, list):
            existing = [existing]
        for b in brand_values:
            if b not in existing:
                existing.append(b)
        field_filters["brand"] = existing

    # Map custom filters to Table MultiSelect fields on Website Item
    # These fields are synced from Website Offer and Item Badge child tables
    offers_filter = field_filters.pop("offers", None) or field_filters.pop("offers_title", None)
    badges_filter = field_filters.pop("badges", None)
    
    # Remove price filters - they're handled at SQL level via item_code subquery
    price_from_filter = field_filters.pop("price_from", None) or q.get("price_from")
    price_to_filter = field_filters.pop("price_to", None) or q.get("price_to")
    
    # Handle price filtering at SQL level using item_code subquery
    if price_from_filter or price_to_filter:
        # Get item_codes that match the price range from Item Price table
        price_item_codes = _get_item_codes_by_price_range(price_from_filter, price_to_filter)
        if price_item_codes:
            # Add to field_filters - Website Item will filter by these item_codes
            existing_item_codes = field_filters.get("item_code", [])
            if not isinstance(existing_item_codes, list):
                existing_item_codes = [existing_item_codes] if existing_item_codes else []
            
            # Intersect with existing item_code filter if present
            if existing_item_codes:
                price_item_codes = list(set(price_item_codes) & set(existing_item_codes))
            
            field_filters["item_code"] = price_item_codes
    
    if offers_filter:
        field_filters["filterable_offers"] = _normalize_filter_values(offers_filter)
    if badges_filter:
        field_filters["filterable_badges"] = _normalize_filter_values(badges_filter)

    # Core-style query args
    attribute_filters = q.get("attribute_filters") or {}
    search = q.get("search")
    start = cint(q.start) if q.get("start") else 0
    item_group = q.get("item_group")
    from_filters = q.get("from_filters")

    # If new filter is checked, reset start to show filtered items from page 1
    if from_filters:
        start = 0

    sub_categories: List[Dict[str, Any]] = []
    if item_group:
        sub_categories = get_child_groups_for_website(item_group, immediate=True)

    engine = ProductQuery()

    # Price filtering is now handled at SQL level via item_code IN subquery
    # (see logic above where we added item_code to field_filters)

    try:
        result = engine.query(
            attribute_filters,
            field_filters,
            search_term=search,
            start=start,
            item_group=item_group,
        )
    except Exception as e:
        import traceback
        frappe.log_error(f"Product query failed: {str(e)}\n{traceback.format_exc()}")
        return {"exc": f"Something went wrong! Error: {str(e)}"}

    items = result.get("items") or []
    total_count = result.get("items_count", 0)

    return {
        "items": items,
        "filters": _build_discount_filters(result),
        "settings": engine.settings,
        "sub_categories": sub_categories,
        "items_count": total_count,
    }


def _normalize_filter_values(val: Any) -> List[str]:
    """Helper to coerce single values / JSON strings into a flat list[str]."""
    import json as _json
    if val is None:
        return []
    if isinstance(val, str):
        try:
            parsed = _json.loads(val)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
            return [str(parsed)]
        except Exception:
            return [val]
    if isinstance(val, (list, tuple, set)):
        return [str(v) for v in val]
    return [str(val)]


def _get_item_codes_by_price_range(price_from: Any, price_to: Any) -> List[str]:
    """Get item_codes from Item Price table that match the price range.
    
    This enables SQL-level price filtering via item_code IN subquery.
    """
    from frappe.utils import flt
    
    # Handle list inputs from frontend
    if isinstance(price_from, (list, tuple)) and price_from:
        price_from = price_from[0]
    if isinstance(price_to, (list, tuple)) and price_to:
        price_to = price_to[0]
    
    price_from_f = flt(price_from) if price_from else None
    price_to_f = flt(price_to) if price_to else None
    
    # Get active price list from webshop settings
    price_list = (
        frappe.db.get_single_value("Webshop Settings", "price_list")
        or frappe.db.get_single_value("Selling Settings", "selling_price_list")
        or "Standard Selling"
    )
    
    # Build filters for Item Price query
    filters = {
        "selling": 1,
        "price_list": price_list,
    }
    
    # Query Item Price table with range filters
    if price_from_f is not None and price_to_f is not None:
        # Both bounds
        item_prices = frappe.db.get_all(
            "Item Price",
            filters=filters,
            fields=["item_code", "price_list_rate"],
        )
        # Filter in Python since Frappe ORM doesn't support range queries well
        return [
            ip["item_code"] 
            for ip in item_prices 
            if price_from_f <= flt(ip["price_list_rate"]) <= price_to_f
        ]
    elif price_from_f is not None:
        # Only min price
        item_prices = frappe.db.get_all(
            "Item Price",
            filters=filters,
            fields=["item_code", "price_list_rate"],
        )
        return [
            ip["item_code"] 
            for ip in item_prices 
            if flt(ip["price_list_rate"]) >= price_from_f
        ]
    elif price_to_f is not None:
        # Only max price
        item_prices = frappe.db.get_all(
            "Item Price",
            filters=filters,
            fields=["item_code", "price_list_rate"],
        )
        return [
            ip["item_code"] 
            for ip in item_prices 
            if flt(ip["price_list_rate"]) <= price_to_f
        ]
    
    return []


@frappe.request_cache
def _get_product_price_cached(item_code: str) -> Optional[float]:
    """Get product price with request-level caching for performance."""
    from webshop.webshop.product_data_engine.product_info import get_product_info_for_website
    
    product_info = get_product_info_for_website(item_code, skip_quotation_creation=True).get("product_info")
    if product_info and product_info.get("price"):
        return product_info["price"].get("price_list_rate")
    return None


def _filter_by_price(items, price_from, price_to):
    """Filter items by price range with caching for performance."""
    from frappe.utils import flt
    
    # Handle case where price values come as lists from frontend
    if isinstance(price_from, (list, tuple)) and price_from:
        price_from = price_from[0]
    if isinstance(price_to, (list, tuple)) and price_to:
        price_to = price_to[0]
    
    price_from_f = flt(price_from) if price_from else None
    price_to_f = flt(price_to) if price_to else None
    
    filtered = []
    for item in items:
        # Use cached price lookup
        rate = item.get("price_list_rate")
        if rate is None:
            # Try to get from cache
            rate = _get_product_price_cached(item.get("item_code"))
            if rate is None:
                continue
        
        rate_f = flt(rate)
        if price_from_f is not None and rate_f < price_from_f:
            continue
        if price_to_f is not None and rate_f > price_to_f:
            continue
        filtered.append(item)
    
    return filtered


def _build_discount_filters(result):
    """Build discount filter data from query result."""
    filters: Dict[str, Any] = {}
    discounts = result.get("discounts")
    if discounts:
        filter_engine = ProductFiltersBuilder()
        filters["discount_filters"] = filter_engine.get_discount_filters(discounts)
    return filters


def sync_offers_to_filterable_field(doc, method=None):
    """Sync Website Offer child table entries to filterable_offers MultiSelect field.
    
    Called when Website Item is saved or when Website Offer is modified.
    This allows ProductQuery to filter by offers using standard Table MultiSelect filtering.
    """
    if not getattr(doc, "doctype", None):
        return
        
    # Handle Website Item save
    if doc.doctype == "Website Item":
<<<<<<< HEAD
        offer_titles = [o.offer_title for o in (doc.offers or []) if o.offer_title]
        # MultiSelect fields store as list of dicts with 'offer_title' as the link field
        doc.filterable_offers = [{"offer_title": t} for t in offer_titles]
=======
        parent = doc.name
        offer_titles = [o.offer_title for o in (doc.offers or []) if o.offer_title]

        # Update filterable_offers via SQL to avoid mutating the in-memory doc child table
        # with plain dicts (which breaks update_global_search).
        frappe.db.sql(
            "DELETE FROM `tabWebsite Offer` WHERE parent = %(parent)s AND parentfield = 'filterable_offers'",
            {"parent": parent},
        )
        for idx, t in enumerate(offer_titles, 1):
            frappe.db.sql(
                """
                INSERT INTO `tabWebsite Offer` (name, parent, parentfield, parenttype, offer_title, idx)
                VALUES (%(name)s, %(parent)s, %(parentfield)s, %(parenttype)s, %(offer_title)s, %(idx)s)
                """,
                {
                    "name": frappe.generate_hash(length=10),
                    "parent": parent,
                    "parentfield": "filterable_offers",
                    "parenttype": "Website Item",
                    "offer_title": t,
                    "idx": idx,
                },
            )
        frappe.db.commit()
>>>>>>> b7a521d (Updated existing files)
        
    # Handle Website Offer save/update
    elif doc.doctype == "Website Offer":
        parent = doc.parent
        parenttype = doc.parenttype
        if parent and parenttype == "Website Item":
            wi = frappe.get_doc("Website Item", parent)
            offer_titles = [o.offer_title for o in (wi.offers or []) if o.offer_title]
            # Use direct SQL to avoid document API issues with child tables
            frappe.db.sql(
                "DELETE FROM `tabWebsite Offer` WHERE parent = %(parent)s AND parentfield = 'filterable_offers'",
                {"parent": parent}
            )
            for idx, t in enumerate(offer_titles, 1):
                frappe.db.sql(
                    """
                    INSERT INTO `tabWebsite Offer` (name, parent, parentfield, parenttype, offer_title, idx)
                    VALUES (%(name)s, %(parent)s, %(parentfield)s, %(parenttype)s, %(offer_title)s, %(idx)s)
                    """,
                    {
                        "name": frappe.generate_hash(length=10),
                        "parent": parent,
                        "parentfield": "filterable_offers",
                        "parenttype": "Website Item",
                        "offer_title": t,
                        "idx": idx,
                    }
                )
            frappe.db.commit()


def sync_badges_to_filterable_field(doc, method=None):
    """Sync Item Badge entries to Website Item's filterable_badges MultiSelect field.
    
    Called when Item is saved or when Item Badge is modified.
    This allows ProductQuery to filter by badges using standard Table MultiSelect filtering.
    """
    if not getattr(doc, "doctype", None):
        return
        
    # Handle Item save
    if doc.doctype == "Item":
        badge_types = [b.badge_type for b in (doc.badges or []) if b.badge_type]
        # Update all linked Website Items
        website_items = frappe.get_all(
            "Website Item",
            filters={"item_code": doc.name},
            pluck="name"
        )
        for wi_name in website_items:
            # Use direct SQL to avoid document API issues
            frappe.db.sql(
                "DELETE FROM `tabItem Badge` WHERE parent = %(parent)s AND parenttype = 'Website Item' AND parentfield = 'filterable_badges'",
                {"parent": wi_name}
            )
            for idx, t in enumerate(badge_types, 1):
                frappe.db.sql(
                    """
                    INSERT INTO `tabItem Badge` (name, parent, parentfield, parenttype, badge_type, idx, source)
                    VALUES (%(name)s, %(parent)s, %(parentfield)s, %(parenttype)s, %(badge_type)s, %(idx)s, %(source)s)
                    """,
                    {
                        "name": frappe.generate_hash(length=10),
                        "parent": wi_name,
                        "parentfield": "filterable_badges",
                        "parenttype": "Website Item",
                        "badge_type": t,
                        "idx": idx,
                        "source": "Auto",
                    }
                )
        frappe.db.commit()
            
    # Handle Item Badge save/update (via parent Item)
    elif doc.doctype == "Item Badge":
        parent = doc.parent
        if parent:
            item = frappe.get_doc("Item", parent)
            badge_types = [b.badge_type for b in (item.badges or []) if b.badge_type]
            website_items = frappe.get_all(
                "Website Item",
                filters={"item_code": parent},
                pluck="name"
            )
            for wi_name in website_items:
                # Use direct SQL to avoid document API issues
                frappe.db.sql(
                    "DELETE FROM `tabItem Badge` WHERE parent = %(parent)s AND parenttype = 'Website Item' AND parentfield = 'filterable_badges'",
                    {"parent": wi_name}
                )
                for idx, t in enumerate(badge_types, 1):
                    frappe.db.sql(
                        """
                        INSERT INTO `tabItem Badge` (name, parent, parentfield, parenttype, badge_type, idx, source)
                        VALUES (%(name)s, %(parent)s, %(parentfield)s, %(parenttype)s, %(badge_type)s, %(idx)s, %(source)s)
                        """,
                        {
                            "name": frappe.generate_hash(length=10),
                            "parent": wi_name,
                            "parentfield": "filterable_badges",
                            "parenttype": "Website Item",
                            "badge_type": t,
                            "idx": idx,
                            "source": "Auto",
                        }
                    )
            frappe.db.commit()


@frappe.whitelist()
def rebuild_filterable_badges() -> None:
    """Backfill filterable_badges MultiSelect for all Website Items.

    This is safe to run multiple times and is intended as an admin/maintenance
    operation after enabling badge-based filters.
    Uses direct SQL to avoid document API complexities with child tables.
    """

    # Get all Website Items and their item_code
    website_items = frappe.get_all(
        "Website Item",
        fields=["name", "item_code"],
    )

    if not website_items:
        return

    by_item_code: Dict[str, List[str]] = {}
    for wi in website_items:
        code = wi.get("item_code")
        if not code:
            continue
        by_item_code.setdefault(code, []).append(wi["name"])

    item_codes = list(by_item_code.keys())
    if not item_codes:
        return

    # Fetch all badges for these Items
    badge_rows = frappe.get_all(
        "Item Badge",
        filters={"parent": ["in", item_codes]},
        fields=["parent", "badge_type"],
    )

    badges_by_item: Dict[str, List[str]] = {}
    for row in badge_rows:
        code = row.get("parent")
        btype = row.get("badge_type")
        if not code or not btype:
            continue
        badges_by_item.setdefault(code, []).append(btype)

    # First, clear all existing filterable_badges rows for Website Items
    frappe.db.sql(
        "DELETE FROM `tabItem Badge` WHERE parenttype = 'Website Item' AND parentfield = 'filterable_badges'"
    )

    # Insert new rows directly via SQL
    idx = 0
    for item_code, wi_names in by_item_code.items():
        badge_types = badges_by_item.get(item_code) or []
        for wi_name in wi_names:
            for t in badge_types:
                idx += 1
                frappe.db.sql(
                    """
                    INSERT INTO `tabItem Badge`
                    (name, parent, parentfield, parenttype, badge_type, idx, source)
                    VALUES (%(name)s, %(parent)s, %(parentfield)s, %(parenttype)s, %(badge_type)s, %(idx)s, %(source)s)
                    """,
                    {
                        "name": frappe.generate_hash(length=10),
                        "parent": wi_name,
                        "parentfield": "filterable_badges",
                        "parenttype": "Website Item",
                        "badge_type": t,
                        "idx": idx,
                        "source": "Auto",
                    }
                )

    frappe.db.commit()



def _resolve_price_bucket(name: str) -> Tuple[Optional[float], Optional[float]]:
    """Look up Catalog Price Range by name and return (from_amount, to_amount).

    If not found, returns (None, None).
    """

    if not name:
        return None, None

    doc = frappe.db.get_value(
        "Catalog Price Range",
        name,
        ["from_amount", "to_amount"],
        as_dict=True,
    )

    if not doc:
        return None, None

    return doc.get("from_amount"), doc.get("to_amount")


@frappe.whitelist(allow_guest=True)
def get_template_price_range(template_item_code: str) -> Dict[str, float]:
    price_list = (
        frappe.db.get_single_value("Webshop Settings", "price_list")
        or frappe.db.get_single_value("Selling Settings", "selling_price_list")
        or "Standard Selling"
    )

    variants = frappe.get_all(
        "Item",
        filters={"variant_of": template_item_code},
        pluck="name",
    )

    if not variants:
        return {"min": 0.0, "max": 0.0}

    prices = frappe.get_all(
        "Item Price",
        filters={
            "item_code": ["in", variants],
            "selling": 1,
            "price_list": price_list,
        },
        fields=["price_list_rate"],
    )

    rates = [p["price_list_rate"] for p in prices if p.get("price_list_rate") is not None]
    if not rates:
        return {"min": 0.0, "max": 0.0}

    return {"min": float(min(rates)), "max": float(max(rates))}


@frappe.whitelist(allow_guest=True)
def get_template_discount_range(template_item_code: str) -> Dict[str, float]:
    variants = frappe.get_all(
        "Item",
        filters={"variant_of": template_item_code},
        pluck="name",
    )

    if not variants:
        return {"min": 0.0, "max": 0.0}

    rows = frappe.get_all(
        "Website Item",
        filters={"item_code": ["in", variants]},
        fields=["custom_consumer_discount"],
    )

    discounts = [r["custom_consumer_discount"] for r in rows if r.get("custom_consumer_discount") is not None]
    if not discounts:
        return {"min": 0.0, "max": 0.0}

    return {"min": float(min(discounts)), "max": float(max(discounts))}


@frappe.whitelist(allow_guest=True)
def get_variants_for_template(template_item_code: str) -> List[Dict[str, Any]]:
    variants = frappe.get_all(
        "Item",
        filters={"variant_of": template_item_code},
        fields=["name", "item_name"],
    )

    if not variants:
        return []

    variant_names = [v["name"] for v in variants]

    attrs = frappe.get_all(
        "Item Variant Attribute",
        filters={"parent": ["in", variant_names]},
        fields=["parent", "attribute", "attribute_value"],
    )

    attributes_by_variant: Dict[str, Dict[str, str]] = {}
    for row in attrs:
        parent = row["parent"]
        if parent not in attributes_by_variant:
            attributes_by_variant[parent] = {}
        attributes_by_variant[parent][row["attribute"]] = row["attribute_value"]

    price_list = (
        frappe.db.get_single_value("Webshop Settings", "price_list")
        or frappe.db.get_single_value("Selling Settings", "selling_price_list")
        or "Standard Selling"
    )

    prices = frappe.get_all(
        "Item Price",
        filters={
            "item_code": ["in", variant_names],
            "selling": 1,
            "price_list": price_list,
        },
        fields=["item_code", "price_list_rate"],
    )

    price_by_item: Dict[str, float] = {}
    for row in prices:
        if row.get("price_list_rate") is not None:
            price_by_item[row["item_code"]] = float(row["price_list_rate"])

    discount_rows = frappe.get_all(
        "Website Item",
        filters={"item_code": ["in", variant_names]},
        fields=["item_code", "custom_consumer_discount"],
    )

    discount_by_item: Dict[str, Optional[float]] = {}
    for row in discount_rows:
        discount_by_item[row["item_code"]] = row.get("custom_consumer_discount")

    result: List[Dict[str, Any]] = []
    for v in variants:
        name = v["name"]
        result.append(
            {
                "item_code": name,
                "item_name": v.get("item_name"),
                "attributes": attributes_by_variant.get(name, {}),
                "price": price_by_item.get(name),
                "consumer_discount": discount_by_item.get(name),
            }
        )

    return result


@frappe.whitelist(allow_guest=True)
def get_item_offers(item_codes: Any) -> Dict[str, Any]:
    """Return a mapping of item_code -> list of offer dicts for Website Items.

    Each offer dict contains: name, offer_title, offer_subtitle.
    This is used to display Available Offers on product cards in list/grid view.
    """

    # Normalize input (may be JSON string)
    if isinstance(item_codes, str):
        try:
            import json as _json
            item_codes = _json.loads(item_codes)
        except Exception:
            item_codes = [item_codes]

    if not isinstance(item_codes, (list, tuple)):
        item_codes = [item_codes]

    if not item_codes:
        return {}

    # Map Website Item name by item_code
    website_items = frappe.get_all(
        "Website Item",
        filters={"item_code": ["in", list(item_codes)]},
        fields=["name", "item_code"],
    )

    by_code: Dict[str, Any] = {wi["item_code"]: wi["name"] for wi in website_items}

    result: Dict[str, Any] = {code: [] for code in item_codes}

    if not by_code:
        return result

    # Fetch child offers for all Website Items
    offers = frappe.get_all(
        "Website Offer",
        filters={
            "parent": ["in", list(by_code.values())],
            "parenttype": "Website Item",
            "parentfield": "offers",
        },
        fields=["name", "offer_title", "offer_subtitle", "parent"],
        order_by="idx asc",
    )

    # Reverse map parent -> item_code
    parent_to_code = {v: k for k, v in by_code.items()}

    for row in offers:
        code = parent_to_code.get(row["parent"])
        if not code:
            continue
        result.setdefault(code, []).append(
            {
                "name": row.get("name"),
                "offer_title": row.get("offer_title"),
                "offer_subtitle": row.get("offer_subtitle"),
            }
        )

    return result


def sync_consumer_discount_to_website_item(doc, method: Optional[str] = None) -> None:
    """Sync custom_consumer_discount from Item to its linked Website Item(s).

    This keeps the informational "Consumer Discount" percent in Website Item
    in sync with the source Item, without affecting any pricing logic.
    """

    # Ensure we are working with an Item document
    if not getattr(doc, "doctype", None) == "Item":
        return

    discount = doc.get("custom_consumer_discount")

    # Find all Website Items that point to this Item via item_code
    website_item_names = frappe.get_all(
        "Website Item",
        filters={"item_code": doc.name},
        pluck="name",
    )

    if not website_item_names:
        return

    for wi_name in website_item_names:
        wi = frappe.get_doc("Website Item", wi_name)
        # Set or clear the consumer discount field to mirror Item
        wi.db_set("custom_consumer_discount", discount, update_modified=False)


@frappe.whitelist(allow_guest=True)
def get_consumer_discounts(item_codes: Any) -> Dict[str, Optional[float]]:
    """Return a mapping of item_code -> custom_consumer_discount for Website Items.

    Used by the frontend to render informational Consumer Discount labels on
    product cards. Does not affect any pricing logic.
    """

    # item_codes may come as JSON string, list, or tuple
    if isinstance(item_codes, str):
        try:
            import json as _json
            item_codes = _json.loads(item_codes)
        except Exception:
            item_codes = [item_codes]

    if not isinstance(item_codes, (list, tuple)):
        item_codes = [item_codes]

    if not item_codes:
        return {}

    rows = frappe.get_all(
        "Website Item",
        filters={"item_code": ["in", list(item_codes)]},
        fields=["item_code", "custom_consumer_discount"],
    )

    result: Dict[str, Optional[float]] = {}
    for row in rows:
        result[row["item_code"]] = row.get("custom_consumer_discount")

    return result


@frappe.whitelist(allow_guest=True)
def get_item_brands(item_codes: Any) -> Dict[str, Optional[str]]:
    """Return a mapping of item_code -> brand for Website Items.

    Used by the frontend to render brand labels on product cards.
    """

    # Normalize input (may be JSON string)
    if isinstance(item_codes, str):
        try:
            import json as _json
            item_codes = _json.loads(item_codes)
        except Exception:
            item_codes = [item_codes]

    if not isinstance(item_codes, (list, tuple)):
        item_codes = [item_codes]

    if not item_codes:
        return {}

    # Get Website Items with their brand
    website_items = frappe.get_all(
        "Website Item",
        filters={"item_code": ["in", list(item_codes)]},
        fields=["item_code", "brand"],
    )

    result: Dict[str, Optional[str]] = {}
    for row in website_items:
        result[row["item_code"]] = row.get("brand")

    return result

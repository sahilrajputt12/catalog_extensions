import json
from contextlib import contextmanager

import frappe
from typing import Any, Dict, List, Optional, Tuple
from frappe.utils import add_days, nowdate, getdate, flt, cint
from webshop.templates.pages.product_search import get_category_suggestions
from webshop.webshop.doctype.override_doctype.item_group import (
    get_child_groups_for_website,
    get_item_for_list_in_html,
)
from webshop.webshop.product_data_engine.query import ProductQuery
from webshop.webshop.product_data_engine.filters import ProductFiltersBuilder
from webshop.webshop.shopping_cart.product_info import (
    get_product_info_for_website as core_get_product_info_for_website,
)
from webshop.webshop.shopping_cart.product_info import set_product_info_for_website

from catalog_extensions.brand_filtering import (
    apply_brand_filter,
    assert_item_allowed,
    get_brand_filter_context,
)
from catalog_extensions import order_billing
from catalog_extensions.simple_checkout import (
    _get_settings as get_simple_checkout_settings,
    PAYMENT_MODE_COD,
    get_payment_mode_for_doc,
)
from catalog_extensions.install_support import is_doctype_available, is_optional_app_installed
from catalog_extensions.stock_guard import enrich_product_info

RETURN_WINDOW_DAYS = 7
RETURN_ITEM_FLAG_FIELDS = (
    "custom_is_returnable",
    "is_returnable",
    "custom_returnable",
    "custom_enable_returns",
    "enable_returns",
)
RETURN_RECORD_RECEIVED_STATUSES = {"DELIVERED", "CLOSED"}
RETURN_RECORD_IN_TRANSIT_STATUSES = {"IN_TRANSIT"}
RETURN_APPROVAL_ACTIVE_STATUSES = {
    "REQUESTED",
    "UNDER_REVIEW",
    "APPROVED_FOR_RETURN",
    "RETURN_ORDER_CREATED",
    "REVERSE_SHIPMENT_CREATED",
    "IN_TRANSIT",
    "RECEIVED",
}
RETURN_APPROVAL_RECEIVED_STATUSES = {"RECEIVED", "APPROVED_ON_RECEIPT", "REJECTED_ON_RECEIPT", "CLOSED"}
PORTAL_RETURN_REQUEST_MARKER = "[catalog_extensions_return_request]"


def _get_conf_bool(*keys: str, default: int = 1) -> int:
    for key in keys:
        value = frappe.conf.get(key)
        if value is not None:
            return cint(value)
    return cint(default)


@contextmanager
def run_as(user: str):
    session = getattr(frappe, "session", None)
    previous_user = getattr(session, "user", None)
    if previous_user is None and isinstance(session, dict):
        previous_user = session.get("user")
    previous_user = previous_user or "Guest"
    frappe.set_user(user)
    try:
        yield
    finally:
        frappe.set_user(previous_user)


@frappe.whitelist(allow_guest=True)
def get_filter_facets(
    item_group: Optional[str] = None, query_args: Optional[Any] = None
) -> Dict[str, List[Dict[str, Any]]]:
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

    if isinstance(query_args, str):
        import json as _json

        try:
            query_args = _json.loads(query_args)
        except Exception:
            query_args = {}

    query_args = frappe._dict(query_args or {})
    item_group = item_group or query_args.get("item_group")
    
    # Check site config for filter visibility (default to enabled)
    # Use flat keys: catalog_extensions_show_offers_filter
    show_offers = _get_conf_bool(
        "catalog_extensions_show_offers_filter",
        "catalog_extensions.show_offers_filter",
        default=1,
    )
    show_badges = _get_conf_bool(
        "catalog_extensions_show_badges_filter",
        "catalog_extensions.show_badges_filter",
        default=1,
    )
    
    brand_context = get_brand_filter_context()
    brand_base_where, brand_base_params = _build_facet_where_clause(
        query_args, brand_context, exclude_fields={"brand"}
    )
    full_where, full_params = _build_facet_where_clause(query_args, brand_context)
    price_where, price_params = _build_facet_where_clause(
        query_args, brand_context, exclude_fields={"price_from", "price_to"}
    )

    facets["item_groups"] = frappe.db.sql(
        f"""
        SELECT ig.item_group_name, COUNT(DISTINCT wi.name) AS count
        FROM `tabWebsite Item` wi
        JOIN `tabItem Group` ig ON wi.item_group = ig.name
        WHERE {brand_base_where}
        GROUP BY ig.item_group_name
        ORDER BY count DESC, ig.item_group_name ASC
        """,
        brand_base_params,
        as_dict=True,
    )

    facets["brands"] = frappe.db.sql(
        f"""
        SELECT wi.brand, COUNT(DISTINCT wi.name) AS count
        FROM `tabWebsite Item` wi
        WHERE {brand_base_where}
          AND wi.brand IS NOT NULL
          AND wi.brand != ''
        GROUP BY wi.brand
        ORDER BY count DESC, wi.brand ASC
        LIMIT 20
        """,
        brand_base_params,
        as_dict=True,
    )

    # Contextual price facets based on the same filtered result set.
    facets["price_ranges"] = _get_price_range_facets(price_where, price_params)
    facets["price_min_max"] = _get_price_min_max(price_where, price_params)

    # Offers facet - controlled by site config
    if show_offers:
        offers_rows = frappe.db.sql(
            f"""
            SELECT wo.offer_title AS offer_title, COUNT(DISTINCT wi.name) AS count
            FROM `tabWebsite Offer` wo
            JOIN `tabWebsite Item` wi ON wi.name = wo.parent
            WHERE {full_where}
              AND wo.parentfield = 'offers'
              AND wo.offer_title IS NOT NULL
              AND wo.offer_title != ''
            GROUP BY wo.offer_title
            ORDER BY count DESC, wo.offer_title ASC
            """,
            full_params,
            as_dict=True,
        )
        facets["offers"] = [
            {"label": row["offer_title"], "code": row["offer_title"], "count": row["count"]}
            for row in offers_rows
        ]

    # Badges facet - controlled by site config
    if show_badges:
        badge_rows = frappe.db.sql(
            f"""
            SELECT ib.badge_type AS badge_type, COUNT(DISTINCT wi.name) AS count
            FROM `tabItem Badge` ib
            JOIN `tabItem` i ON ib.parent = i.name
            JOIN `tabWebsite Item` wi ON wi.item_code = i.name
            WHERE {full_where}
              AND ib.badge_type IS NOT NULL
              AND ib.badge_type != ''
            GROUP BY ib.badge_type
            ORDER BY count DESC, ib.badge_type ASC
            """,
            full_params,
            as_dict=True,
        )
        facets["badges"] = [
            {"label": row["badge_type"], "code": row["badge_type"], "count": row["count"]}
            for row in badge_rows
        ]

    return facets


def _build_facet_where_clause(
    query_args: Optional[Dict[str, Any]] = None,
    brand_context: Optional[frappe._dict] = None,
    exclude_fields: Optional[set] = None,
) -> Tuple[str, Dict[str, Any]]:
    query_args = frappe._dict(query_args or {})
    exclude_fields = set(exclude_fields or set())
    params: Dict[str, Any] = {}
    conditions = ["wi.published = 1"]

    brand_context = brand_context or get_brand_filter_context()

    raw_field_filters = query_args.get("field_filters") or {}
    if isinstance(raw_field_filters, str):
        import json as _json

        try:
            raw_field_filters = _json.loads(raw_field_filters) or {}
        except Exception:
            raw_field_filters = {}

    field_filters = dict(raw_field_filters)

    top_level_brand = query_args.get("brand")
    if top_level_brand and "brand" not in exclude_fields:
        existing_brand_filters = _normalize_filter_values(field_filters.get("brand"))
        extra_brands = (
            [top_level_brand] if isinstance(top_level_brand, str) else list(top_level_brand)
        )
        for brand in extra_brands:
            if brand not in existing_brand_filters:
                existing_brand_filters.append(brand)
        field_filters["brand"] = existing_brand_filters

    if "brand" in exclude_fields:
        field_filters.pop("brand", None)

    field_filters, no_match, _context = apply_brand_filter(field_filters)
    if no_match:
        return "1 = 0", params

    if brand_context.restricted and "brand" in exclude_fields:
        params["allowed_brands"] = tuple(brand_context.allowed_brands)
        conditions.append("wi.brand IN %(allowed_brands)s")

    search = query_args.get("search")
    if search:
        params["search"] = f"%{search}%"
        conditions.append(
            "("
            "wi.item_name LIKE %(search)s OR "
            "wi.web_item_name LIKE %(search)s OR "
            "wi.brand LIKE %(search)s OR "
            "wi.web_long_description LIKE %(search)s"
            ")"
        )

    item_group = query_args.get("item_group")
    if item_group:
        child_groups = get_child_groups_for_website(item_group, include_self=True)
        group_names = [g.name for g in child_groups] if child_groups else [item_group]
        if group_names:
            params["item_groups"] = tuple(group_names)
            conditions.append("wi.item_group IN %(item_groups)s")

    if "brand" not in exclude_fields:
        brand_filters = _normalize_filter_values(field_filters.get("brand"))
        if brand_filters:
            params["brand_filters"] = tuple(brand_filters)
            conditions.append("wi.brand IN %(brand_filters)s")

    item_code_filters = _normalize_filter_values(field_filters.get("item_code"))
    if item_code_filters:
        params["item_codes"] = tuple(item_code_filters)
        conditions.append("wi.item_code IN %(item_codes)s")

    offers_filters = _normalize_filter_values(
        field_filters.get("offers_title") or field_filters.get("offers") or field_filters.get("filterable_offers")
    )
    if offers_filters and "offers_title" not in exclude_fields and "offers" not in exclude_fields:
        params["offers_filters"] = tuple(offers_filters)
        conditions.append(
            """
            EXISTS (
                SELECT 1
                FROM `tabWebsite Offer` wo_filter
                WHERE wo_filter.parent = wi.name
                  AND wo_filter.offer_title IN %(offers_filters)s
            )
            """
        )

    badges_filters = _normalize_filter_values(
        field_filters.get("badges") or field_filters.get("filterable_badges")
    )
    if badges_filters and "badges" not in exclude_fields:
        params["badges_filters"] = tuple(badges_filters)
        conditions.append(
            """
            EXISTS (
                SELECT 1
                FROM `tabItem Badge` ib_filter
                WHERE ib_filter.parent = wi.item_code
                  AND ib_filter.badge_type IN %(badges_filters)s
            )
            """
        )

    price_from_filter = None if "price_from" in exclude_fields else field_filters.get("price_from")
    price_to_filter = None if "price_to" in exclude_fields else field_filters.get("price_to")
    if price_from_filter is None and "price_from" not in exclude_fields:
        price_from_filter = query_args.get("price_from")
    if price_to_filter is None and "price_to" not in exclude_fields:
        price_to_filter = query_args.get("price_to")

    price_item_codes = _get_item_codes_by_price_range(price_from_filter, price_to_filter)
    if (price_from_filter or price_to_filter) and not price_item_codes:
        return "1 = 0", params
    if price_item_codes:
        params["price_item_codes"] = tuple(price_item_codes)
        conditions.append("wi.item_code IN %(price_item_codes)s")

    return " AND ".join(conditions), params


def _get_price_min_max(
    base_where: Optional[str] = None, base_params: Optional[Dict[str, Any]] = None
) -> Dict[str, Optional[float]]:
    """Return min/max price across the current filtered Website Item result set.

    Used by the frontend price slider as true dataset bounds.
    """

    price_list = (
        frappe.db.get_single_value("Webshop Settings", "price_list")
        or frappe.db.get_single_value("Selling Settings", "selling_price_list")
        or "Standard Selling"
    )

    where_sql = base_where or "wi.published = 1"
    params = dict(base_params or {})
    params["price_list"] = price_list

    row = frappe.db.sql(
        f"""
        SELECT
            MIN(ip.price_list_rate) AS min_rate,
            MAX(ip.price_list_rate) AS max_rate
        FROM `tabWebsite Item` wi
        JOIN `tabItem` i ON i.name = wi.item_code
        JOIN `tabItem Price` ip ON ip.item_code = i.name
        WHERE {where_sql}
          AND ip.selling = 1
          AND ip.price_list = %(price_list)s
        """,
        params,
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


def _get_price_range_facets(
    base_where: Optional[str] = None, base_params: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Return price range facets based on Catalog Price Range records.

    Each site can define its own ranges in the `Catalog Price Range` DocType.
    We count Website Items whose Item Price (for the active price list) falls
    within each configured range for the current filtered result set.
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
    base_where = base_where or "wi.published = 1"

    for r in ranges:
        where_clauses = [
            base_where,
            "ip.selling = 1",
            "ip.price_list = %(price_list)s",
        ]

        params: Dict[str, Any] = dict(base_params or {})
        params["price_list"] = price_list

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

    show_badges = _get_conf_bool(
        "catalog_extensions_show_badges_filter",
        "catalog_extensions.show_badges_filter",
        default=1,
    )

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

    if not show_badges:
        return {code: [] for code in item_codes}

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
def get_products(query_args=None):
    """Return webshop products with custom filters and customer-group brand restriction."""

    if isinstance(query_args, str):
        import json

        query_args = json.loads(query_args)

    q = frappe._dict(query_args or {})

    import json as _json

    raw_field_filters = q.get("field_filters") or {}
    if isinstance(raw_field_filters, str):
        try:
            raw_field_filters = _json.loads(raw_field_filters) or {}
        except Exception:
            raw_field_filters = {}

    field_filters: Dict[str, Any] = dict(raw_field_filters)

    brand = q.get("brand")
    if brand:
        brand_values = [brand] if isinstance(brand, str) else list(brand)
        existing = _normalize_filter_values(field_filters.get("brand"))
        for value in brand_values:
            if value not in existing:
                existing.append(value)
        field_filters["brand"] = existing

    field_filters, no_match, _context = apply_brand_filter(field_filters)

    offers_filter = field_filters.pop("offers", None) or field_filters.pop("offers_title", None)
    badges_filter = field_filters.pop("badges", None)
    price_from_filter = field_filters.pop("price_from", None) or q.get("price_from")
    price_to_filter = field_filters.pop("price_to", None) or q.get("price_to")

    if offers_filter:
        field_filters["filterable_offers"] = _normalize_filter_values(offers_filter)
    if badges_filter:
        field_filters["filterable_badges"] = _normalize_filter_values(badges_filter)

    attribute_filters = q.get("attribute_filters") or {}
    search = q.get("search")
    start = cint(q.start) if q.get("start") else 0
    item_group = q.get("item_group")
    from_filters = q.get("from_filters")

    if from_filters:
        start = 0

    sub_categories: List[Dict[str, Any]] = []
    if item_group:
        sub_categories = get_child_groups_for_website(item_group, immediate=True)

    engine = ProductQuery()

    if no_match:
        return {
            "items": [],
            "filters": {},
            "settings": engine.settings,
            "sub_categories": sub_categories,
            "items_count": 0,
        }

    if price_from_filter or price_to_filter:
        price_item_codes = _get_item_codes_by_price_range(price_from_filter, price_to_filter)
        if not price_item_codes:
            return {
                "items": [],
                "filters": {},
                "settings": engine.settings,
                "sub_categories": sub_categories,
                "items_count": 0,
            }

        existing_item_codes = _normalize_filter_values(field_filters.get("item_code"))
        if existing_item_codes:
            price_item_codes = list(set(price_item_codes) & set(existing_item_codes))
        if not price_item_codes:
            return {
                "items": [],
                "filters": {},
                "settings": engine.settings,
                "sub_categories": sub_categories,
                "items_count": 0,
            }
        field_filters["item_code"] = price_item_codes

    try:
        result = engine.query(
            attribute_filters,
            field_filters,
            search_term=search,
            start=start,
            item_group=item_group,
        )

        if result.get("items"):
            seen = set()
            unique_items = []
            for item in result["items"]:
                item_name = item.get("name") or item.get("item_code")
                if item_name in seen:
                    continue
                seen.add(item_name)
                unique_items.append(item)

            result["items"] = unique_items
            result["items_count"] = len(unique_items)

    except Exception as e:
        import traceback

        frappe.log_error(f"Product query failed: {str(e)}\n{traceback.format_exc()}")
        return {"exc": f"Something went wrong! Error: {str(e)}"}

    return {
        "items": result.get("items") or [],
        "filters": _build_discount_filters(result),
        "settings": engine.settings,
        "sub_categories": sub_categories,
        "items_count": result.get("items_count", 0),
    }


@frappe.whitelist(allow_guest=True)
def get_product_filter_data_with_price(query_args=None):
    return get_products(query_args=query_args)


@frappe.whitelist(allow_guest=True)
def get_product_info(item_code, skip_quotation_creation=False):
    assert_item_allowed(item_code)
    response = core_get_product_info_for_website(
        item_code, skip_quotation_creation=skip_quotation_creation
    )
    response["product_info"] = enrich_product_info(item_code, response.get("product_info"))
    return response


@frappe.whitelist(allow_guest=True)
def get_product_list(search=None, start=0, limit=12):
    data = get_product_data(search=search, start=start, limit=limit)

    for item in data:
        set_product_info_for_website(item)

    return [get_item_for_list_in_html(row) for row in data]


def get_product_data(search=None, start=0, limit=12):
    context = get_brand_filter_context()
    filters = {"published": 1}
    if context.restricted:
        filters["brand"] = ["in", context.allowed_brands]

    or_filters = []
    if search:
        search = f"%{search}%"
        for field in ("item_name", "web_item_name", "brand", "web_long_description"):
            or_filters.append([field, "like", search])

    return frappe.db.get_all(
        "Website Item",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "web_item_name",
            "item_name",
            "item_code",
            "brand",
            "route",
            "website_image",
            "thumbnail",
            "item_group",
            "description",
            "web_long_description as website_description",
            "website_warehouse",
            "ranking",
        ],
        order_by="ranking desc, modified desc",
        limit_start=cint(start),
        limit_page_length=cint(limit),
    )


@frappe.whitelist(allow_guest=True)
def search_products(query):
    product_results = product_search(query)
    category_results = get_category_suggestions(query)

    return {
        "product_results": product_results.get("results") or [],
        "category_results": category_results.get("results") or [],
    }


@frappe.whitelist(allow_guest=True)
def product_search(query, limit=10, fuzzy_search=True):
    del fuzzy_search

    results = get_product_data(search=query, start=0, limit=limit)
    return {"from_redisearch": False, "results": results}


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


def _get_portal_order_doc(order_name: str, order_doctype: Optional[str] = None):
    """Return an order-like portal document after verifying website access."""

    allowed_doctypes = ("Sales Order", "Quotation", "Sales Invoice", "Delivery Note")
    doctypes = [order_doctype] if order_doctype else list(allowed_doctypes)

    for doctype in doctypes:
        if doctype not in allowed_doctypes:
            frappe.throw(frappe._("Unsupported order type: {0}").format(doctype))

        if not frappe.db.exists(doctype, order_name):
            continue

        doc = frappe.get_doc(doctype, order_name)
        if not frappe.has_website_permission(doc):
            frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)

        if doctype == "Delivery Note":
            return _get_portal_order_doc_from_delivery_note(doc)

        return doc

    frappe.throw(frappe._("Order {0} was not found.").format(order_name), frappe.DoesNotExistError)


def _get_portal_order_doc_from_delivery_note(delivery_note_doc):
    """Resolve a portal-safe order document from a Delivery Note."""

    source_delivery_note = delivery_note_doc
    return_against = delivery_note_doc.get("return_against")
    if return_against and frappe.db.exists("Delivery Note", return_against):
        source_delivery_note = frappe.get_doc("Delivery Note", return_against)
        if not frappe.has_website_permission(source_delivery_note):
            frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)

    item_rows = frappe.get_all(
        "Delivery Note Item",
        filters={"parent": source_delivery_note.name},
        fields=["against_sales_order", "against_sales_invoice"],
        order_by="idx asc",
    )

    for row in item_rows:
        sales_order = row.get("against_sales_order")
        if sales_order and frappe.db.exists("Sales Order", sales_order):
            sales_order_doc = frappe.get_doc("Sales Order", sales_order)
            if not frappe.has_website_permission(sales_order_doc):
                frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
            sales_order_doc.flags.portal_delivery_note_name = delivery_note_doc.name
            sales_order_doc.flags.portal_delivery_note_is_return = cint(delivery_note_doc.get("is_return"))
            return sales_order_doc

        sales_invoice = row.get("against_sales_invoice")
        if sales_invoice and frappe.db.exists("Sales Invoice", sales_invoice):
            sales_invoice_doc = frappe.get_doc("Sales Invoice", sales_invoice)
            if not frappe.has_website_permission(sales_invoice_doc):
                frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
            sales_invoice_doc.flags.portal_delivery_note_name = delivery_note_doc.name
            sales_invoice_doc.flags.portal_delivery_note_is_return = cint(delivery_note_doc.get("is_return"))
            return sales_invoice_doc

    delivery_note_doc.flags.portal_delivery_note_name = delivery_note_doc.name
    delivery_note_doc.flags.portal_delivery_note_is_return = cint(delivery_note_doc.get("is_return"))
    return delivery_note_doc


PAYMENT_REQUEST_OPEN_STATUSES = {
    "Draft",
    "Requested",
    "Initiated",
    "Partially Paid",
    "Payment Ordered",
}

NORMALIZED_STATUS_META = {
    "ordered": ("Ordered", "We have your order and will update this page as it moves ahead."),
    "paid": ("Paid", "Payment is confirmed and shipment can now begin."),
    "processing_shipment": ("Processing shipment", "Payment is confirmed and we are preparing your order for dispatch."),
    "shipped": ("Shipped", "Your shipment is on the way and tracking is available."),
    "delivered": ("Delivered", "Your shipment has been marked as delivered."),
    "completed": ("Completed", "Your order has been delivered and completed."),
    "delivery_exception": ("Delivery exception", "There is a delivery issue and the shipment needs attention."),
    "cancelled": ("Cancelled", "This order was cancelled before completion."),
    "return_requested": ("Return requested", "Your return request has been submitted and is under review."),
    "return_shipment_created": ("Return shipment created", "A reverse shipment has been created for your return."),
    "return_in_transit": ("Return in transit", "The return shipment is on its way back."),
    "return_received": ("Return received", "The returned items have been received and are being verified."),
    "return_completed": ("Return completed", "Your return has been processed successfully."),
    "return_rejected": ("Return rejected", "Your return request was rejected after review."),
    "return_cancelled": ("Return cancelled", "Your return request was cancelled."),
    "refund_processing": ("Refund processing", "Your refund is being processed after return receipt."),
    "refunded": ("Refunded", "The refund or credit settlement has been completed."),
}

TRACKING_STATUS_NORMALIZATION_MAP = {
    "delivered": "Delivered",
    "delivery": "Delivered",
    "returned": "Returned",
    "rto delivered": "Returned",
    "returned to origin": "Returned",
    "lost": "Lost",
    "damaged": "Lost",
    "pickup scheduled": "Pickup Scheduled",
    "booked": "Pickup Scheduled",
    "picked up": "Picked Up",
    "shipped": "Picked Up",
    "in transit": "In Transit",
    "reached at destination hub": "In Transit",
    "arrived at destination hub": "In Transit",
    "out for delivery": "In Transit",
}

PORTAL_REFUND_REQUEST_MARKER = "[catalog_extensions_refund_request]"


def _get_portal_flow_visibility(order_doc=None) -> Dict[str, bool]:
    """Return which webshop checkout flows should expose customer-facing traceability."""
    settings = get_simple_checkout_settings()
    shipping_disabled = bool(settings and getattr(settings, "hide_shipping_on_webshop", 0))
    payment_disabled = bool(settings and getattr(settings, "hide_payment_on_webshop", 0))
    if order_doc and get_payment_mode_for_doc(order_doc) == PAYMENT_MODE_COD:
        payment_disabled = True
    checkout_overrides_active = shipping_disabled or payment_disabled

    shipping_active = True
    payment_active = True
    return_active = True
    cancel_active = True
    show_shipment_traceability = True
    show_payment_traceability = True
    show_return_traceability = True

    if checkout_overrides_active:
        shipping_active = not shipping_disabled
        payment_active = not payment_disabled
        return_active = not shipping_disabled
        cancel_active = bool(getattr(settings, "enable_cancel_order", 0))
        show_shipment_traceability = not shipping_disabled
        show_return_traceability = not shipping_disabled
        show_payment_traceability = not payment_disabled

    return {
        "simple_checkout_enabled": checkout_overrides_active,
        "shipping_active": shipping_active,
        "payment_active": payment_active,
        "cancel_active": cancel_active,
        "return_active": return_active,
        "show_shipment_traceability": show_shipment_traceability,
        "show_payment_traceability": show_payment_traceability,
        "show_return_traceability": show_return_traceability,
    }


def _get_sales_invoices_for_sales_order(sales_order: str) -> List[Dict[str, Any]]:
    """Return submitted sales invoices linked to a Sales Order."""

    rows = frappe.db.sql(
        """
        SELECT
            si.name,
            si.status,
            si.posting_date,
            si.due_date,
            si.grand_total,
            si.currency,
            si.outstanding_amount,
            si.is_return,
            si.return_against,
            si.modified
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
        WHERE si.docstatus = 1
          AND sii.sales_order = %(sales_order)s
        GROUP BY si.name
        ORDER BY si.posting_date DESC, si.modified DESC
        """,
        {"sales_order": sales_order},
        as_dict=True,
    )
    return [dict(row) for row in rows]


def _get_related_return_invoices(invoice_names: List[str]) -> List[Dict[str, Any]]:
    """Return submitted return invoices linked to the original invoices."""

    if not invoice_names:
        return []

    rows = frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            "is_return": 1,
            "return_against": ["in", invoice_names],
        },
        fields=[
            "name",
            "status",
            "posting_date",
            "due_date",
            "grand_total",
            "currency",
            "outstanding_amount",
            "is_return",
            "return_against",
            "modified",
        ],
        order_by="posting_date desc, modified desc",
    )
    return [dict(row) for row in rows]


def _get_related_draft_return_invoices(invoice_names: List[str]) -> List[Dict[str, Any]]:
    """Return draft return invoices linked to original invoices."""

    if not invoice_names:
        return []

    rows = frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 0,
            "is_return": 1,
            "return_against": ["in", invoice_names],
        },
        fields=["name", "posting_date", "modified", "return_against"],
        order_by="modified desc",
    )
    return [dict(row) for row in rows]


def _get_delivery_notes_for_sales_invoice(sales_invoice: str) -> List[Dict[str, Any]]:
    """Return submitted delivery notes connected to a Sales Invoice."""

    rows = frappe.db.sql(
        """
        SELECT
            dn.name,
            dn.posting_date,
            dn.status,
            dn.lr_no,
            dn.lr_date,
            dn.transporter_name,
            dn.vehicle_no,
            dn.grand_total,
            dn.modified
        FROM `tabDelivery Note` dn
        INNER JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        WHERE dn.docstatus = 1
          AND dni.against_sales_invoice = %(sales_invoice)s
        GROUP BY dn.name
        ORDER BY dn.posting_date DESC, dn.modified DESC
        """,
        {"sales_invoice": sales_invoice},
        as_dict=True,
    )
    return [dict(row) for row in rows]


def _get_delivery_notes_for_sales_order(sales_order: str) -> List[Dict[str, Any]]:
    """Return submitted delivery notes connected to a Sales Order."""

    rows = frappe.db.sql(
        """
        SELECT
            dn.name,
            dn.posting_date,
            dn.status,
            dn.lr_no,
            dn.lr_date,
            dn.transporter_name,
            dn.vehicle_no,
            dn.grand_total,
            dn.modified
        FROM `tabDelivery Note` dn
        INNER JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        WHERE dn.docstatus = 1
          AND dni.against_sales_order = %(sales_order)s
        GROUP BY dn.name
        ORDER BY dn.posting_date DESC, dn.modified DESC
        """,
        {"sales_order": sales_order},
        as_dict=True,
    )
    return [dict(row) for row in rows]


def _get_return_delivery_notes(delivery_note_names: List[str], docstatus: int = 1) -> List[Dict[str, Any]]:
    """Return return delivery notes linked to the original delivery notes."""

    if not delivery_note_names:
        return []

    rows = frappe.get_all(
        "Delivery Note",
        filters={
            "docstatus": docstatus,
            "is_return": 1,
            "return_against": ["in", delivery_note_names],
        },
        fields=[
            "name",
            "posting_date",
            "status",
            "return_against",
            "grand_total",
            "modified",
        ],
        order_by="posting_date desc, modified desc",
    )
    return [dict(row) for row in rows]


def _get_shipments_for_delivery_notes(delivery_note_names: List[str]) -> List[Dict[str, Any]]:
    """Return shipments linked to the provided delivery notes."""

    if not delivery_note_names or not is_doctype_available("Shipment"):
        return []

    shipment_rows = frappe.db.sql(
        """
        SELECT
            s.name,
            s.status,
            s.pickup_date,
            s.carrier,
            s.carrier_service,
            s.awb_number,
            s.tracking_url,
            s.tracking_status,
            s.tracking_status_info,
            s.shipment_id,
            s.service_provider,
            s.modified,
            s.creation,
            GROUP_CONCAT(sdn.delivery_note ORDER BY sdn.delivery_note SEPARATOR ', ') AS delivery_notes
        FROM `tabShipment` s
        INNER JOIN `tabShipment Delivery Note` sdn ON sdn.parent = s.name
        WHERE s.docstatus < 2
          AND sdn.delivery_note IN %(delivery_notes)s
        GROUP BY s.name
        ORDER BY s.modified DESC, s.creation DESC
        """,
        {"delivery_notes": tuple(delivery_note_names)},
        as_dict=True,
    )

    shipments: List[Dict[str, Any]] = []
    for row in shipment_rows:
        shipment = dict(row)
        shipment["tracking_url"] = _sanitize_tracking_url(shipment.get("tracking_url"))
        shipment["has_tracking"] = bool(
            shipment.get("tracking_url") or shipment.get("awb_number") or shipment.get("tracking_status")
        )
        shipment["delivery_notes"] = [
            dn.strip() for dn in (shipment.get("delivery_notes") or "").split(",") if dn.strip()
        ]
        shipments.append(shipment)

    _attach_tracking_events(shipments)
    return shipments


def _get_shipments_by_names(shipment_names: List[str]) -> List[Dict[str, Any]]:
    """Return shipment rows for the provided Shipment names."""

    shipment_names = [name for name in shipment_names if name]
    if not shipment_names or not is_doctype_available("Shipment"):
        return []

    rows = frappe.get_all(
        "Shipment",
        filters={"name": ["in", shipment_names], "docstatus": ["<", 2]},
        fields=[
            "name",
            "status",
            "pickup_date",
            "carrier",
            "carrier_service",
            "awb_number",
            "tracking_url",
            "tracking_status",
            "tracking_status_info",
            "shipment_id",
            "service_provider",
            "modified",
            "creation",
        ],
        order_by="modified desc, creation desc",
    )

    shipments: List[Dict[str, Any]] = []
    for row in rows:
        shipment = dict(row)
        shipment["tracking_url"] = _sanitize_tracking_url(shipment.get("tracking_url"))
        shipment["has_tracking"] = bool(
            shipment.get("tracking_url") or shipment.get("awb_number") or shipment.get("tracking_status")
        )
        shipments.append(shipment)

    _attach_tracking_events(shipments)
    return shipments


def _attach_tracking_events(shipments: List[Dict[str, Any]]) -> None:
    shipment_names = [shipment.get("name") for shipment in shipments if shipment.get("name")]
    if not shipment_names or not frappe.db.exists("DocType", "Tracking Event"):
        for shipment in shipments:
            shipment["tracking_events"] = []
        return

    event_rows = frappe.get_all(
        "Tracking Event",
        filters={"shipment": ["in", shipment_names]},
        fields=["shipment", "external_status", "normalized_status", "event_time"],
        order_by="event_time desc, creation desc",
    )

    events_by_shipment: Dict[str, List[Dict[str, Any]]] = {}
    for row in event_rows:
        event = dict(row)
        events_by_shipment.setdefault(event["shipment"], []).append(event)

    for shipment in shipments:
        shipment["tracking_events"] = events_by_shipment.get(shipment.get("name"), [])


def _dedupe_named_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for row in rows:
        name = row.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        deduped.append(row)
    return deduped


def _get_return_records_for_shipments(shipment_names: List[str]) -> List[Dict[str, Any]]:
    """Return reverse-logistics records linked to outbound shipments."""

    shipment_names = [name for name in shipment_names if name]
    if not shipment_names or not frappe.db.exists("DocType", "Return Shipment"):
        return []

    rows = frappe.get_all(
        "Return Shipment",
        filters={"original_shipment": ["in", shipment_names]},
        fields=[
            "name",
            "original_shipment",
            "reverse_shipment",
            "replacement_shipment",
            "return_type",
            "return_reason",
            "return_status",
            "external_return_order_id",
            "external_return_shipment_id",
            "provider_reference",
            "shiprocket_order_id",
            "shiprocket_shipment_id",
            "reverse_awb",
            "pickup_id",
            "modified",
            "creation",
        ],
        order_by="modified desc, creation desc",
    )
    return [dict(row) for row in rows]


def _get_return_approval_requests_for_shipments(shipment_names: List[str]) -> List[Dict[str, Any]]:
    shipment_names = [name for name in shipment_names if name]
    if not shipment_names or not frappe.db.exists("DocType", "Return Approval Request"):
        return []

    rows = frappe.get_all(
        "Return Approval Request",
        filters={"original_shipment": ["in", shipment_names]},
        fields=[
            "name",
            "original_shipment",
            "sales_invoice",
            "return_invoice",
            "request_status",
            "reverse_shipment",
            "return_shipment_record",
            "provider_reference",
            "received_on",
            "modified",
            "creation",
        ],
        order_by="modified desc, creation desc",
    )
    return [dict(row) for row in rows]


def _sanitize_tracking_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    value = value.strip()
    if value.startswith(("http://", "https://")):
        return value

    return ""


def _get_payment_requests_for_references(references: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Return payment requests linked to the provided document references."""

    if not references:
        return []

    clauses = []
    params: Dict[str, Any] = {}
    for index, (doctype, name) in enumerate(references):
        clauses.append(
            f"(reference_doctype = %(ref_dt_{index})s AND reference_name = %(ref_dn_{index})s)"
        )
        params[f"ref_dt_{index}"] = doctype
        params[f"ref_dn_{index}"] = name

    rows = frappe.db.sql(
        f"""
        SELECT
            name,
            status,
            outstanding_amount,
            grand_total,
            reference_doctype,
            reference_name,
            modified,
            creation
        FROM `tabPayment Request`
        WHERE docstatus < 2
          AND payment_request_type = 'Inward'
          AND ({' OR '.join(clauses)})
        ORDER BY modified DESC, creation DESC
        """,
        params,
        as_dict=True,
    )
    return [dict(row) for row in rows]


def _status_active(code: str, current_code: str, ordered_codes: List[str]) -> bool:
    return code == current_code and current_code in ordered_codes


def _status_done(code: str, current_code: str, ordered_codes: List[str]) -> bool:
    if current_code not in ordered_codes or code not in ordered_codes:
        return False
    return ordered_codes.index(code) <= ordered_codes.index(current_code)


def _get_status_metadata(code: str) -> Dict[str, str]:
    label, note = NORMALIZED_STATUS_META.get(code, ("Processing", "We are updating this order."))
    return {
        "normalized_status_code": code,
        "normalized_status_label": label,
        "normalized_status_note": note,
    }


def _pick_first_non_empty(*values):
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def _get_portal_comment_date(reference_doctype: str, reference_name: str, marker: str) -> Optional[str]:
    comment = frappe.db.get_value(
        "Comment",
        {
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content": ["like", f"%{marker}%"],
        },
        ["modified", "creation"],
        as_dict=True,
        order_by="modified desc, creation desc",
    )
    if not comment:
        return None

    return comment.get("modified") or comment.get("creation")


def _is_shipment_driven_webshop_order(context: Dict[str, Any]) -> bool:
    order_doc = context["order_doc"]
    return order_doc.doctype == "Sales Order" and (order_doc.get("order_type") or "") == "Shopping Cart"


def _get_delivery_completion_confirmation_date(context: Dict[str, Any]) -> Optional[Any]:
    order_doc = context["order_doc"]

    delivered_shipment_date = next(
        (
            shipment.get("modified")
            or shipment.get("pickup_date")
            or shipment.get("creation")
            for shipment in context.get("shipments", [])
            if _normalize_tracking_status_label(shipment.get("tracking_status")) == "Delivered"
        ),
        None,
    )
    if delivered_shipment_date:
        return delivered_shipment_date

    if order_doc.doctype in ("Sales Order", "Delivery Note"):
        completion_marker_date = _get_portal_comment_date(
            order_doc.doctype,
            order_doc.name,
            order_billing.DELIVERY_COMPLETE_MARKER,
        )
        if completion_marker_date:
            return completion_marker_date

    for delivery_note in context.get("delivery_notes", []):
        if not delivery_note.get("name"):
            continue
        completion_marker_date = _get_portal_comment_date(
            "Delivery Note",
            delivery_note["name"],
            order_billing.DELIVERY_COMPLETE_MARKER,
        )
        if completion_marker_date:
            return completion_marker_date

    return None


def _get_delivered_date(context: Dict[str, Any]) -> Optional[Any]:
    order_doc = context["order_doc"]

    if _is_shipment_driven_webshop_order(context):
        return _get_delivery_completion_confirmation_date(context)

    return _pick_first_non_empty(
        next(
            (
                shipment.get("modified")
                or shipment.get("pickup_date")
                or shipment.get("creation")
                for shipment in context.get("shipments", [])
                if _normalize_tracking_status_label(shipment.get("tracking_status")) == "Delivered"
            ),
            None,
        ),
        context["delivery_notes"][0].get("posting_date") if context.get("delivery_notes") else None,
        order_doc.get("modified") if flt(order_doc.get("per_delivered")) >= 100 else None,
    )


def _get_return_window_end_date(context: Dict[str, Any]) -> Optional[str]:
    delivered_date = _get_delivered_date(context)
    if not delivered_date:
        return None
    return str(add_days(getdate(delivered_date), RETURN_WINDOW_DAYS))


def _is_order_fulfilled(context: Dict[str, Any]) -> bool:
    order_doc = context["order_doc"]
    if order_doc.docstatus != 1 or (order_doc.get("status") or "") in ("Cancelled", "Closed"):
        return False

    if order_doc.doctype == "Sales Order":
        if _is_shipment_driven_webshop_order(context):
            return (order_doc.get("status") or "") == "Completed" or bool(_get_delivered_date(context))
        return (
            (order_doc.get("status") or "") == "Completed"
            or flt(order_doc.get("per_delivered")) >= 100
            or bool(context.get("delivery_notes") and _get_delivered_date(context))
        )

    return bool(_get_delivered_date(context))


def _get_item_returnable_field() -> Optional[str]:
    meta = frappe.get_meta("Item")
    for fieldname in RETURN_ITEM_FLAG_FIELDS:
        if meta.has_field(fieldname):
            return fieldname
    return None


def _parse_selected_return_items(selected_items: Optional[Any]) -> List[Dict[str, Any]]:
    if not selected_items:
        return []

    payload = selected_items
    if isinstance(payload, str):
        payload = payload.strip()
        if not payload:
            return []
        payload = json.loads(payload)

    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("selected_items") or []

    if not isinstance(payload, list):
        frappe.throw(frappe._("Selected return items payload is invalid."))

    normalized = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        item_row = (
            row.get("sales_invoice_item")
            or row.get("item_row")
            or row.get("name")
            or row.get("invoice_item")
        )
        qty = flt(row.get("qty") or row.get("quantity"))
        if item_row and qty > 0:
            normalized.append({"sales_invoice_item": item_row, "qty": qty})
    return normalized


def _get_returned_qty_by_source_item(invoice_name: str) -> Dict[str, float]:
    rows = frappe.db.sql(
        """
        SELECT
            sii.sales_invoice_item,
            SUM(ABS(sii.qty)) AS returned_qty
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
        WHERE si.docstatus = 1
          AND si.is_return = 1
          AND si.return_against = %(invoice_name)s
        GROUP BY sii.sales_invoice_item
        """,
        {"invoice_name": invoice_name},
        as_dict=True,
    )

    returned = {}
    for row in rows:
        if row.get("sales_invoice_item"):
            returned[row["sales_invoice_item"]] = flt(row.get("returned_qty"))
    return returned


def _get_eligible_return_items(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    source_invoice = context.get("return_source_invoice") or _get_return_source_invoice(context)
    if not source_invoice:
        return []

    source_items = frappe.get_all(
        "Sales Invoice Item",
        filters={"parent": source_invoice["name"]},
        fields=[
            "name",
            "item_code",
            "item_name",
            "description",
            "qty",
            "stock_qty",
            "uom",
            "stock_uom",
            "rate",
            "amount",
            "image",
        ],
        order_by="idx asc",
    )
    if not source_items:
        return []

    item_codes = sorted({row["item_code"] for row in source_items if row.get("item_code")})
    item_fields = ["name", "is_stock_item", "disabled"]
    item_returnable_field = _get_item_returnable_field()
    if item_returnable_field:
        item_fields.append(item_returnable_field)

    item_docs = frappe.get_all("Item", filters={"name": ["in", item_codes]}, fields=item_fields)
    item_by_code = {row["name"]: row for row in item_docs}
    returned_qty_by_item = _get_returned_qty_by_source_item(source_invoice["name"])
    fulfilled = _is_order_fulfilled(context)
    delivered_date = _get_delivered_date(context)
    return_window_end_date = _get_return_window_end_date(context)
    return_window_open = bool(
        fulfilled and delivered_date and return_window_end_date and getdate(nowdate()) <= getdate(return_window_end_date)
    )

    eligible_items: List[Dict[str, Any]] = []
    for source_item in source_items:
        item_meta = item_by_code.get(source_item.get("item_code")) or {}
        explicit_flag = item_meta.get(item_returnable_field) if item_returnable_field else None
        if explicit_flag is None:
            base_returnable = bool(cint(item_meta.get("is_stock_item")))
        else:
            base_returnable = bool(cint(explicit_flag))

        remaining_qty = max(flt(abs(source_item.get("qty"))) - flt(returned_qty_by_item.get(source_item["name"])), 0.0)
        item_reason = ""
        if not fulfilled:
            item_reason = "Return is available only after the order is completed."
        elif not delivered_date:
            item_reason = "Return is available only after delivery is confirmed."
        elif not return_window_open:
            item_reason = f"Return is available only within {RETURN_WINDOW_DAYS} days from delivery."
        elif cint(item_meta.get("disabled")):
            item_reason = "This item is inactive and cannot be returned online."
        elif not base_returnable:
            item_reason = "This item is not eligible for return."
        elif remaining_qty <= 0:
            item_reason = "No returnable quantity is left for this item."

        eligible_items.append(
            {
                "sales_invoice_item": source_item["name"],
                "item_code": source_item.get("item_code"),
                "item_name": source_item.get("item_name"),
                "description": source_item.get("description"),
                "image": source_item.get("image"),
                "qty": flt(abs(source_item.get("qty"))),
                "remaining_returnable_qty": remaining_qty,
                "uom": source_item.get("uom"),
                "stock_uom": source_item.get("stock_uom"),
                "rate": flt(source_item.get("rate")),
                "amount": flt(abs(source_item.get("amount"))),
                "is_return_eligible": not item_reason,
                "return_window_open": return_window_open,
                "return_window_end_date": return_window_end_date,
                "return_unavailable_reason": item_reason,
            }
        )

    return eligible_items


def _get_return_target_shipment(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    shipments = context.get("shipments") or []
    if not shipments:
        return None

    delivered_shipments = [
        shipment
        for shipment in shipments
        if _normalize_tracking_status_label(shipment.get("tracking_status")) == "Delivered"
    ]
    return delivered_shipments[0] if delivered_shipments else shipments[0]


def _normalize_tracking_status_label(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    raw_value = str(value).strip()
    if not raw_value:
        return None

    normalized_key = raw_value.lower().replace("_", " ").replace("-", " ")
    normalized_key = " ".join(normalized_key.split())

    if normalized_key in TRACKING_STATUS_NORMALIZATION_MAP:
        return TRACKING_STATUS_NORMALIZATION_MAP[normalized_key]

    if "deliver" in normalized_key and "out for" not in normalized_key:
        return "Delivered"
    if "return" in normalized_key or "rto" in normalized_key:
        return "Returned"
    if "lost" in normalized_key or "damage" in normalized_key:
        return "Lost"
    if "transit" in normalized_key or "hub" in normalized_key or "out for delivery" in normalized_key:
        return "In Transit"
    if "pickup" in normalized_key and "schedule" in normalized_key:
        return "Pickup Scheduled"
    if "pickup" in normalized_key or "ship" in normalized_key or "booked" in normalized_key:
        return "Picked Up"

    return raw_value


def _build_status_signals(context: Dict[str, Any]) -> Dict[str, Any]:
    order_doc = context["order_doc"]
    flow_visibility = context["flow_visibility"]
    payment_requests = context["payment_requests"]
    shipments = context["shipments"]
    return_shipments = context["return_shipments"]
    return_records = context.get("return_records", [])
    invoices = context["invoices"]
    return_invoices = context["return_invoices"]
    delivery_notes = context["delivery_notes"]
    return_delivery_notes = context["return_delivery_notes"]
    eligible_return_items = context.get("eligible_return_items", [])

    open_payment_requests = [
        pr for pr in payment_requests
        if pr.get("status") in PAYMENT_REQUEST_OPEN_STATUSES
        and abs(flt(pr.get("outstanding_amount"))) > 0.01
    ]
    invoices_with_balance = [invoice for invoice in invoices if flt(invoice.get("outstanding_amount")) > 0.01]
    settled_invoices = [invoice for invoice in invoices if flt(invoice.get("outstanding_amount")) <= 0.01]
    return_invoices_with_balance = [
        invoice for invoice in return_invoices if abs(flt(invoice.get("outstanding_amount"))) > 0.01
    ]
    settled_return_invoices = [
        invoice for invoice in return_invoices if abs(flt(invoice.get("outstanding_amount"))) <= 0.01
    ]
    sales_order_fully_paid_in_advance = (
        order_doc.doctype == "Sales Order"
        and flt(order_doc.get("advance_paid")) >= flt(order_doc.get("base_rounded_total") or order_doc.get("base_grand_total") or 0) - 0.01
        and flt(order_doc.get("base_rounded_total") or order_doc.get("base_grand_total") or 0) > 0
    )
    payment_received = bool(
        settled_invoices
        or (
            order_doc.doctype == "Sales Invoice"
            and flt(order_doc.get("outstanding_amount")) <= 0.01
            and order_doc.docstatus == 1
        )
        or sales_order_fully_paid_in_advance
        or (
            order_doc.doctype == "Sales Order"
            and flt(order_doc.get("per_billed")) >= 100
            and not invoices_with_balance
            and bool(invoices)
        )
    )

    shipment_tracking_statuses = [
        _normalize_tracking_status_label(shipment.get("tracking_status"))
        for shipment in shipments
        if _normalize_tracking_status_label(shipment.get("tracking_status"))
    ]
    return_tracking_statuses = [
        _normalize_tracking_status_label(shipment.get("tracking_status"))
        for shipment in return_shipments
        if _normalize_tracking_status_label(shipment.get("tracking_status"))
    ]

    delivery_confirmed = bool(_get_delivery_completion_confirmation_date(context))
    delivered = any(status == "Delivered" for status in shipment_tracking_statuses) or (
        delivery_confirmed if _is_shipment_driven_webshop_order(context)
        else (order_doc.doctype == "Sales Order" and flt(order_doc.get("per_delivered")) >= 100)
    )
    in_transit = any(
        status not in ("Delivered", "Returned", "Lost") for status in shipment_tracking_statuses
    )
    pickup_scheduled = bool(
        shipments
        and not delivered
        and not in_transit
        and any(shipment.get("pickup_date") or shipment.get("status") in ("Submitted", "Booked") for shipment in shipments)
    )
    picked_up = bool(
        shipments
        and not delivered
        and not in_transit
        and any(
            shipment.get("status") in ("Booked", "Completed")
            or shipment.get("awb_number")
            or shipment.get("tracking_url")
            for shipment in shipments
        )
    )
    delivery_exception = any(status == "Lost" for status in shipment_tracking_statuses)
    cancelled = order_doc.docstatus == 2 or (order_doc.get("status") or "") in ("Cancelled", "Closed")
    completed = _is_order_fulfilled(context) and delivered

    return_record_statuses = [
        str(record.get("return_status") or "").upper() for record in return_records if record.get("return_status")
    ]
    return_approval_requests = context.get("return_approval_requests", [])
    return_approval_statuses = [
        str(request.get("request_status") or "").upper()
        for request in return_approval_requests
        if request.get("request_status")
    ]
    refund_requested = _has_portal_comment(order_doc.doctype, order_doc.name, PORTAL_REFUND_REQUEST_MARKER)
    return_requested = bool(return_approval_requests or context["draft_return_invoices"] or return_invoices or return_records)
    return_shipment_created = bool(
        return_records
        or return_shipments
        or any(
            status in {"REVERSE_SHIPMENT_CREATED", "IN_TRANSIT", "RECEIVED", "APPROVED_ON_RECEIPT", "REJECTED_ON_RECEIPT", "CLOSED"}
            for status in return_approval_statuses
        )
    )
    return_in_transit = bool(
        any(status in RETURN_RECORD_IN_TRANSIT_STATUSES for status in return_record_statuses)
        or any(status == "IN_TRANSIT" for status in return_approval_statuses)
        or any(
            status and status not in ("Delivered", "Returned", "Lost")
            for status in return_tracking_statuses
        )
    )
    return_received = bool(
        return_delivery_notes
        or any(status in RETURN_RECORD_RECEIVED_STATUSES for status in return_record_statuses)
        or any(status in RETURN_APPROVAL_RECEIVED_STATUSES for status in return_approval_statuses)
        or any(status == "Delivered" for status in return_tracking_statuses)
    )
    return_completed = bool(
        any(status in {"APPROVED_ON_RECEIPT", "CLOSED"} for status in return_approval_statuses)
        or (return_received and return_invoices)
    )
    return_rejected = bool(any(status in {"REJECTED", "REJECTED_ON_RECEIPT"} for status in return_approval_statuses))
    return_cancelled = bool(any(status == "CANCELLED" for status in return_approval_statuses))
    refund_processing = bool(refund_requested and return_received and not cancelled)
    return_window_end_date = _get_return_window_end_date(context)
    return_window_open = bool(
        return_window_end_date and getdate(nowdate()) <= getdate(return_window_end_date)
    )

    signals = {
        "flow_visibility": flow_visibility,
        "order_doctype": order_doc.doctype,
        "order_status": order_doc.get("status"),
        "order_docstatus": order_doc.docstatus,
        "has_delivery_note": bool(delivery_notes),
        "has_shipment": bool(shipments),
        "has_tracking": any(shipment.get("has_tracking") for shipment in shipments),
        "shipment_statuses": [shipment.get("status") for shipment in shipments if shipment.get("status")],
        "shipment_tracking_statuses": shipment_tracking_statuses,
        "has_invoice": bool(invoices),
        "invoice_names": [invoice.get("name") for invoice in invoices],
        "invoice_outstanding_total": sum(flt(invoice.get("outstanding_amount")) for invoice in invoices),
        "has_open_payment_request": bool(open_payment_requests),
        "payment_request_statuses": [pr.get("status") for pr in payment_requests if pr.get("status")],
        "delivered": delivered,
        "delivery_confirmed": delivery_confirmed,
        "completed": completed,
        "in_transit": in_transit,
        "pickup_scheduled": pickup_scheduled,
        "picked_up": picked_up,
        "delivery_exception": delivery_exception,
        "cancelled": cancelled,
        "return_delivery_count": len(return_delivery_notes),
        "return_invoice_count": len(return_invoices),
        "return_record_count": len(return_records),
        "return_shipment_count": len(return_shipments),
        "return_record_statuses": return_record_statuses,
        "return_approval_statuses": return_approval_statuses,
        "return_tracking_statuses": return_tracking_statuses,
        "has_return_requested": return_requested,
        "has_return_shipment_created": return_shipment_created,
        "return_in_transit": return_in_transit,
        "has_return_received": return_received,
        "return_completed": return_completed,
        "return_rejected": return_rejected,
        "return_cancelled": return_cancelled,
        "refund_requested": refund_requested,
        "refund_pending": refund_processing,
        "refund_settled": bool(return_invoices and settled_return_invoices and not return_invoices_with_balance),
        "invoices_with_balance": [invoice.get("name") for invoice in invoices_with_balance],
        "settled_invoices": [invoice.get("name") for invoice in settled_invoices],
        "payment_received": payment_received,
        "sales_order_fully_paid_in_advance": sales_order_fully_paid_in_advance,
        "eligible_return_items_count": len([row for row in eligible_return_items if row.get("is_return_eligible")]),
        "return_window_open": return_window_open,
        "return_window_end_date": return_window_end_date,
    }
    return signals


def _resolve_normalized_status(context: Dict[str, Any]) -> Dict[str, Any]:
    signals = _build_status_signals(context)
    flow_visibility = context["flow_visibility"]
    payment_active = flow_visibility["payment_active"]
    shipping_active = flow_visibility["shipping_active"]
    return_active = flow_visibility["return_active"]

    if signals["cancelled"]:
        code = "cancelled"
    elif return_active and signals.get("return_cancelled"):
        code = "return_cancelled"
    elif return_active and signals.get("return_rejected"):
        code = "return_rejected"
    elif return_active and payment_active and signals["refund_settled"]:
        code = "refunded"
    elif return_active and payment_active and signals["refund_pending"]:
        code = "refund_processing"
    elif return_active and signals["return_completed"]:
        code = "return_completed"
    elif return_active and signals["has_return_received"]:
        code = "return_received"
    elif return_active and signals["return_in_transit"]:
        code = "return_in_transit"
    elif return_active and signals["has_return_shipment_created"]:
        code = "return_shipment_created"
    elif return_active and signals["has_return_requested"]:
        code = "return_requested"
    elif shipping_active and signals["delivery_exception"]:
        code = "delivery_exception"
    elif shipping_active and signals["completed"]:
        code = "completed"
    elif shipping_active and signals["delivered"]:
        code = "delivered"
    elif shipping_active and (signals["in_transit"] or signals["picked_up"] or signals["pickup_scheduled"]):
        code = "shipped"
    elif shipping_active and signals["payment_received"] and (signals["has_delivery_note"] or signals["has_shipment"]):
        code = "processing_shipment"
    elif payment_active and signals["payment_received"]:
        code = "paid"
    else:
        code = "ordered"

    resolved = _get_status_metadata(code)
    resolved["status_signals"] = signals
    return resolved


def _build_standard_milestones(context: Dict[str, Any], status_code: str) -> List[Dict[str, Any]]:
    order_doc = context["order_doc"]
    ordered_codes = [
        "ordered",
        "paid",
        "processing_shipment",
        "delivered",
        "completed",
    ]

    latest_shipment = context["shipments"][0] if context["shipments"] else {}
    latest_delivery = context["delivery_notes"][0] if context["delivery_notes"] else {}
    latest_invoice = context["invoices"][0] if context["invoices"] else {}

    return [
        {
            "key": "ordered",
            "label": "Order placed",
            "done": True,
            "active": _status_active("ordered", status_code, ordered_codes),
            "date": order_doc.get("transaction_date") or order_doc.get("posting_date") or order_doc.get("creation"),
        },
        {
            "key": "payment",
            "label": "Payment received",
            "done": _status_done("paid", status_code, ordered_codes),
            "active": status_code == "paid",
            "date": _pick_first_non_empty(latest_invoice.get("posting_date"), latest_invoice.get("modified")),
        },
        {
            "key": "shipment",
            "label": "Shipment initiated",
            "done": _status_done("processing_shipment", status_code, ordered_codes),
            "active": status_code in ("processing_shipment", "shipped"),
            "date": latest_delivery.get("posting_date"),
            "show_shipments": bool(context["shipments"]) and status_code in ("processing_shipment", "shipped", "delivery_exception", "delivered", "completed"),
        },
        {
            "key": "delivered",
            "label": "Delivered",
            "done": _status_done("delivered", status_code, ordered_codes),
            "active": status_code == "delivered",
            "date": _get_delivered_date(context),
            "show_shipments": False,
        },
        {
            "key": "completed",
            "label": "Completed",
            "done": _status_done("completed", status_code, ordered_codes),
            "active": status_code == "completed",
            "date": _pick_first_non_empty(
                _get_portal_comment_date(
                    order_doc.doctype,
                    order_doc.name,
                    order_billing.DELIVERY_COMPLETE_MARKER,
                ),
                order_doc.get("modified"),
                _get_delivered_date(context),
            ),
        },
    ]


def _build_cancelled_milestones(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    order_doc = context["order_doc"]
    signals = _build_status_signals(context)
    flow_visibility = context["flow_visibility"]
    latest_invoice = context["invoices"][0] if context["invoices"] else {}
    refund_requested_date = _get_portal_comment_date(
        order_doc.doctype,
        order_doc.name,
        PORTAL_REFUND_REQUEST_MARKER,
    )
    refund_date = _pick_first_non_empty(
        refund_requested_date,
        context["return_invoices"][0].get("modified") if context.get("return_invoices") else None,
    )

    milestones = [
        {
            "key": "ordered",
            "label": "Order placed",
            "done": True,
            "active": False,
            "date": order_doc.get("transaction_date") or order_doc.get("posting_date") or order_doc.get("creation"),
        },
        {
            "key": "cancelled",
            "label": "Cancelled",
            "done": True,
            "active": not (signals.get("refund_requested") or signals.get("refund_settled")),
            "date": order_doc.get("modified"),
        },
    ]

    show_payment_flow = bool(
        flow_visibility.get("show_payment_traceability")
        and (
            signals.get("payment_received")
            or signals.get("refund_requested")
            or signals.get("refund_settled")
        )
    )
    if not show_payment_flow:
        return milestones

    return [
        milestones[0],
        {
            "key": "payment",
            "label": "Payment received",
            "done": bool(signals.get("payment_received")),
            "active": False,
            "date": _pick_first_non_empty(latest_invoice.get("posting_date"), latest_invoice.get("modified")),
        },
        milestones[1],
        {
            "key": "refund",
            "label": "Refund",
            "done": bool(signals.get("refund_settled")),
            "active": bool(signals.get("refund_requested") and not signals.get("refund_settled")),
            "date": refund_date,
        },
    ]


def _build_return_milestones(context: Dict[str, Any], status_code: str) -> List[Dict[str, Any]]:
    order_doc = context["order_doc"]
    return_request = context["return_approval_requests"][0] if context.get("return_approval_requests") else {}
    return_record = context["return_records"][0] if context.get("return_records") else {}
    return_invoice = context["return_invoices"][0] if context["return_invoices"] else {}
    return_shipment = context["return_shipments"][0] if context["return_shipments"] else {}
    delivered_date = _pick_first_non_empty(_get_delivered_date(context), order_doc.get("modified"))

    ordered_codes = [
        "return_requested",
        "return_shipment_created",
        "return_in_transit",
        "return_received",
        "return_completed",
        "return_rejected",
        "return_cancelled",
        "refund_processing",
        "refunded",
    ]

    return [
        {
            "key": "ordered",
            "label": "Order placed",
            "done": True,
            "active": False,
            "date": order_doc.get("transaction_date") or order_doc.get("posting_date") or order_doc.get("creation"),
        },
        {
            "key": "completed",
            "label": "Completed",
            "done": True,
            "active": False,
            "date": delivered_date,
        },
        {
            "key": "return_requested",
            "label": "Return requested",
            "done": _status_done("return_requested", status_code, ordered_codes),
            "active": status_code == "return_requested",
            "date": _pick_first_non_empty(return_request.get("creation"), return_request.get("modified"), return_invoice.get("posting_date"), return_invoice.get("modified")),
        },
        {
            "key": "return_shipment",
            "label": "Return shipment",
            "done": _status_done("return_in_transit", status_code, ordered_codes),
            "active": status_code in ("return_shipment_created", "return_in_transit"),
            "date": _pick_first_non_empty(return_shipment.get("pickup_date"), return_request.get("modified"), return_record.get("modified")),
            "shipment_group": "return",
            "show_shipments": bool(context["return_shipments"]),
        },
        {
            "key": "return_received",
            "label": "Return received",
            "done": _status_done("return_received", status_code, ordered_codes),
            "active": status_code == "return_received",
            "date": _pick_first_non_empty(return_request.get("received_on"), return_record.get("modified"), return_invoice.get("modified")),
        },
        {
            "key": "return_completed",
            "label": "Return completed",
            "done": _status_done("return_completed", status_code, ordered_codes),
            "active": status_code == "return_completed",
            "date": _pick_first_non_empty(return_request.get("modified"), return_invoice.get("modified"), return_invoice.get("posting_date")),
        },
        {
            "key": "refund",
            "label": "Refund",
            "done": status_code == "refunded",
            "active": status_code == "refund_processing",
            "date": _pick_first_non_empty(return_invoice.get("modified"), return_invoice.get("posting_date")),
        },
    ]


def _build_tracking_milestones(context: Dict[str, Any], status_code: str) -> List[Dict[str, Any]]:
    flow_visibility = context["flow_visibility"]
    if status_code == "cancelled":
        return _build_cancelled_milestones(context)

    if flow_visibility["show_return_traceability"] and status_code in {
        "return_requested",
        "return_shipment_created",
        "return_in_transit",
        "return_received",
        "return_completed",
        "return_rejected",
        "return_cancelled",
        "refund_processing",
        "refunded",
    }:
        return _build_return_milestones(context, status_code)

    milestones = _build_standard_milestones(context, status_code)
    if not flow_visibility["show_shipment_traceability"]:
        milestones = [step for step in milestones if step.get("key") in ("ordered", "payment")]
    if not flow_visibility["show_payment_traceability"]:
        milestones = [step for step in milestones if step.get("key") != "payment"]
    return milestones


def _build_portal_order_tracking_context(order_doc) -> Dict[str, Any]:
    flow_visibility = _get_portal_flow_visibility(order_doc)
    if order_doc.doctype == "Sales Order":
        delivery_notes = _get_delivery_notes_for_sales_order(order_doc.name)
        invoices = _get_sales_invoices_for_sales_order(order_doc.name)
        original_invoices = [invoice for invoice in invoices if not cint(invoice.get("is_return"))]
        return_invoices = _get_related_return_invoices([invoice["name"] for invoice in original_invoices])
        draft_return_invoices = _get_related_draft_return_invoices([invoice["name"] for invoice in original_invoices])
        payment_reference_pairs = [("Sales Order", order_doc.name)]
        payment_reference_pairs.extend(("Sales Invoice", invoice["name"]) for invoice in original_invoices)
    elif order_doc.doctype == "Sales Invoice":
        delivery_notes = _get_delivery_notes_for_sales_invoice(order_doc.name)
        if cint(order_doc.get("is_return")):
            original_invoices = []
            if order_doc.get("return_against") and frappe.db.exists("Sales Invoice", order_doc.get("return_against")):
                original_invoice_doc = frappe.get_doc("Sales Invoice", order_doc.get("return_against"))
                original_invoices = [
                    {
                        "name": original_invoice_doc.name,
                        "status": original_invoice_doc.get("status"),
                        "posting_date": original_invoice_doc.get("posting_date"),
                        "due_date": original_invoice_doc.get("due_date"),
                        "grand_total": original_invoice_doc.get("grand_total"),
                        "currency": original_invoice_doc.get("currency"),
                        "outstanding_amount": original_invoice_doc.get("outstanding_amount"),
                        "is_return": original_invoice_doc.get("is_return"),
                        "return_against": original_invoice_doc.get("return_against"),
                        "modified": original_invoice_doc.get("modified"),
                    }
                ]
            invoices = original_invoices
            return_invoices = [
                {
                    "name": order_doc.name,
                    "status": order_doc.get("status"),
                    "posting_date": order_doc.get("posting_date"),
                    "due_date": order_doc.get("due_date"),
                    "grand_total": order_doc.get("grand_total"),
                    "currency": order_doc.get("currency"),
                    "outstanding_amount": order_doc.get("outstanding_amount"),
                    "is_return": order_doc.get("is_return"),
                    "return_against": order_doc.get("return_against"),
                    "modified": order_doc.get("modified"),
                }
            ]
            draft_return_invoices = []
        else:
            invoices = [
                {
                    "name": order_doc.name,
                    "status": order_doc.get("status"),
                    "posting_date": order_doc.get("posting_date"),
                    "due_date": order_doc.get("due_date"),
                    "grand_total": order_doc.get("grand_total"),
                    "currency": order_doc.get("currency"),
                    "outstanding_amount": order_doc.get("outstanding_amount"),
                    "is_return": order_doc.get("is_return"),
                    "return_against": order_doc.get("return_against"),
                    "modified": order_doc.get("modified"),
                }
            ]
            return_invoices = _get_related_return_invoices([order_doc.name])
            draft_return_invoices = _get_related_draft_return_invoices([order_doc.name])
        payment_reference_pairs = [("Sales Invoice", order_doc.name)]
    elif order_doc.doctype == "Delivery Note":
        current_delivery_note = {
            "name": order_doc.name,
            "posting_date": order_doc.get("posting_date"),
            "status": order_doc.get("status"),
            "lr_no": order_doc.get("lr_no"),
            "lr_date": order_doc.get("lr_date"),
            "transporter_name": order_doc.get("transporter_name"),
            "vehicle_no": order_doc.get("vehicle_no"),
            "grand_total": order_doc.get("grand_total"),
            "modified": order_doc.get("modified"),
            "return_against": order_doc.get("return_against"),
        }

        if cint(order_doc.get("is_return")):
            delivery_notes = []
            return_delivery_notes = [current_delivery_note]
            draft_return_delivery_notes = []
        else:
            delivery_notes = [current_delivery_note]
            return_delivery_notes = _get_return_delivery_notes([order_doc.name], docstatus=1)
            draft_return_delivery_notes = _get_return_delivery_notes([order_doc.name], docstatus=0)

        invoices = []
        return_invoices = []
        draft_return_invoices = []
        payment_reference_pairs = []
    else:
        delivery_notes = []
        invoices = []
        return_invoices = []
        draft_return_invoices = []
        payment_reference_pairs = []

    shipments = _get_shipments_for_delivery_notes([dn["name"] for dn in delivery_notes])
    return_delivery_notes = _get_return_delivery_notes([dn["name"] for dn in delivery_notes], docstatus=1)
    draft_return_delivery_notes = _get_return_delivery_notes([dn["name"] for dn in delivery_notes], docstatus=0)
    return_shipments = _get_shipments_for_delivery_notes([dn["name"] for dn in return_delivery_notes])
    return_records = _get_return_records_for_shipments([shipment["name"] for shipment in shipments])
    return_approval_requests = _get_return_approval_requests_for_shipments([shipment["name"] for shipment in shipments])
    return_shipments = _dedupe_named_rows(
        return_shipments + _get_shipments_by_names([record.get("reverse_shipment") for record in return_records])
    )
    payment_requests = _get_payment_requests_for_references(payment_reference_pairs)

    requested_delivery_note = getattr(order_doc.flags, "portal_delivery_note_name", None)
    requested_delivery_note_is_return = cint(getattr(order_doc.flags, "portal_delivery_note_is_return", 0))

    if requested_delivery_note:
        if requested_delivery_note_is_return:
            return_delivery_notes = [
                note for note in return_delivery_notes if note.get("name") == requested_delivery_note
            ]
            draft_return_delivery_notes = [
                note for note in draft_return_delivery_notes if note.get("name") == requested_delivery_note
            ]
            return_shipments = _get_shipments_for_delivery_notes([requested_delivery_note])
        else:
            delivery_notes = [note for note in delivery_notes if note.get("name") == requested_delivery_note]
            shipments = _get_shipments_for_delivery_notes([requested_delivery_note])
            return_delivery_notes = _get_return_delivery_notes([requested_delivery_note], docstatus=1)
            draft_return_delivery_notes = _get_return_delivery_notes([requested_delivery_note], docstatus=0)
            return_shipments = _get_shipments_for_delivery_notes([dn["name"] for dn in return_delivery_notes])

    return_records = _get_return_records_for_shipments([shipment["name"] for shipment in shipments])
    return_approval_requests = _get_return_approval_requests_for_shipments([shipment["name"] for shipment in shipments])
    return_shipments = _dedupe_named_rows(
        return_shipments + _get_shipments_by_names([record.get("reverse_shipment") for record in return_records])
    )

    context = {
        "order_doc": order_doc,
        "flow_visibility": flow_visibility,
        "delivery_notes": delivery_notes,
        "shipments": shipments,
        "invoices": invoices,
        "payment_requests": payment_requests,
        "return_delivery_notes": return_delivery_notes,
        "draft_return_delivery_notes": draft_return_delivery_notes,
        "return_shipments": return_shipments,
        "return_records": return_records,
        "return_approval_requests": return_approval_requests,
        "return_invoices": return_invoices,
        "draft_return_invoices": draft_return_invoices,
    }
    context["return_source_invoice"] = _get_return_source_invoice(context)
    context["eligible_return_items"] = _get_eligible_return_items(context)
    context["return_window_end_date"] = _get_return_window_end_date(context)
    context["return_window_open"] = bool(
        context["return_window_end_date"]
        and getdate(nowdate()) <= getdate(context["return_window_end_date"])
    )
    return context


def _get_return_source_invoice(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    order_doc = context["order_doc"]

    if order_doc.doctype == "Sales Invoice" and not cint(order_doc.get("is_return")):
        return next((invoice for invoice in context["invoices"] if invoice.get("name") == order_doc.name), None)

    if order_doc.doctype == "Sales Order":
        return next((invoice for invoice in context["invoices"] if not cint(invoice.get("is_return"))), None)

    return None


def _has_fulfillment_started(context: Dict[str, Any]) -> bool:
    order_doc = context["order_doc"]

    if order_doc.doctype != "Sales Order":
        return bool(context["delivery_notes"] or context["shipments"])

    if flt(order_doc.get("per_picked")) > 0:
        return True

    if context["delivery_notes"] or context["shipments"]:
        return True

    if flt(order_doc.get("per_delivered")) > 0:
        return True

    return False


def _get_cancel_unavailable_reason(context: Dict[str, Any]) -> Optional[str]:
    order_doc = context["order_doc"]
    flow_visibility = context["flow_visibility"]

    if not flow_visibility.get("cancel_active", True):
        return "Order cancellation is disabled for this checkout flow."

    if order_doc.doctype != "Sales Order":
        return "Cancellation is available only for sales orders."
    if order_doc.docstatus != 1 or (order_doc.get("status") or "") in ("Cancelled", "Closed"):
        return "This order can no longer be cancelled."
    if _has_fulfillment_started(context):
        return "This order is already in fulfillment and can no longer be cancelled online."
    if context["return_invoices"] or context["draft_return_invoices"]:
        return "A return has already been started for this order."

    return None


def _get_return_unavailable_reason(context: Dict[str, Any], signals: Optional[Dict[str, Any]] = None) -> Optional[str]:
    order_doc = context["order_doc"]
    flow_visibility = context["flow_visibility"]
    signals = signals or _build_status_signals(context)
    source_invoice = context.get("return_source_invoice") or _get_return_source_invoice(context)
    eligible_items = context.get("eligible_return_items") or []

    if order_doc.doctype not in ("Sales Order", "Sales Invoice"):
        return "Returns are available only for placed orders."
    if cint(order_doc.get("is_return")):
        return "This document is already a return."
    if order_doc.docstatus != 1 or (order_doc.get("status") or "") in ("Cancelled", "Closed"):
        return "Cancelled orders cannot start a return online."
    if context["draft_return_invoices"]:
        draft_invoice = context["draft_return_invoices"][0]
        return f"A return request already exists as {draft_invoice.get('name')}."
    active_request = next(
        (
            request
            for request in context.get("return_approval_requests", [])
            if str(request.get("request_status") or "").upper() in RETURN_APPROVAL_ACTIVE_STATUSES
        ),
        None,
    )
    if active_request:
        return f"A return request is already active as {active_request.get('name')}."
    if context["return_invoices"]:
        return "A return has already been created for this order."
    if not flow_visibility["return_active"]:
        return "Returns are not active for this checkout flow."
    if not signals.get("completed"):
        return "Return request is available only after the sales order is fully fulfilled."
    if not source_invoice:
        return "A submitted sales invoice is required before a return request can be created online."
    if not context.get("shipments"):
        return "A shipped order record is required before a return request can be created online."
    if not context.get("return_window_open"):
        return f"Return request is available only within {RETURN_WINDOW_DAYS} days from delivery."
    if not any(item.get("is_return_eligible") for item in eligible_items):
        return "No eligible items are available for return on this order."

    return None


def _has_portal_comment(reference_doctype: str, reference_name: str, marker: str) -> bool:
    return bool(
        frappe.db.exists(
            "Comment",
            {
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "content": ["like", f"%{marker}%"],
            },
        )
    )


def _start_portal_refund_processing(
    context: Dict[str, Any],
    signals: Optional[Dict[str, Any]] = None,
) -> bool:
    order_doc = context["order_doc"]
    signals = signals or _build_status_signals(context)

    if order_doc.doctype not in ("Sales Order", "Sales Invoice"):
        return False
    if order_doc.docstatus != 1 or (order_doc.get("status") or "") in ("Cancelled", "Closed"):
        return False
    if not signals.get("payment_received"):
        return False
    if not context.get("return_invoices"):
        return False
    if not signals.get("has_return_received"):
        return False
    if signals.get("refund_settled"):
        return False
    if _has_portal_comment(order_doc.doctype, order_doc.name, PORTAL_REFUND_REQUEST_MARKER):
        return False

    refund_message = (
        f"{PORTAL_REFUND_REQUEST_MARKER} Refund processing started automatically after return receipt. "
        "Manual finance review is required."
    )

    with run_as("Administrator"):
        order_doc.flags.ignore_permissions = True
        order_doc.add_comment("Comment", refund_message)

        for return_invoice in context["return_invoices"]:
            if return_invoice.get("name") and frappe.db.exists("Sales Invoice", return_invoice["name"]):
                return_doc = frappe.get_doc("Sales Invoice", return_invoice["name"])
                return_doc.flags.ignore_permissions = True
                return_doc.add_comment(
                    "Comment",
                    "Refund processing started automatically after return receipt. Manual finance review is required.",
                )

    return True


def _get_refund_unavailable_reason(context: Dict[str, Any], signals: Optional[Dict[str, Any]] = None) -> Optional[str]:
    order_doc = context["order_doc"]
    flow_visibility = context["flow_visibility"]
    signals = signals or _build_status_signals(context)

    if order_doc.doctype not in ("Sales Order", "Sales Invoice"):
        return "Refunds are available only for placed orders."
    if cint(order_doc.get("is_return")):
        return "This document is already a return."
    if order_doc.docstatus != 1 or (order_doc.get("status") or "") in ("Cancelled", "Closed"):
        return "Cancelled orders cannot start a refund online."
    if not flow_visibility["payment_active"]:
        return "Refunds are not available when payment flow is bypassed."
    if not signals.get("payment_received"):
        return "Refund can be requested only after payment is received."
    if signals.get("refund_settled"):
        return "This order is already refunded."
    if _has_portal_comment(order_doc.doctype, order_doc.name, PORTAL_REFUND_REQUEST_MARKER):
        return "A refund request has already been submitted for this order."
    if not context["return_invoices"]:
        return "A return must be started before a refund can be requested online."
    if not signals.get("has_return_received"):
        return "Refund can be requested only after the returned items are received."

    return None


def _get_order_actions(context: Dict[str, Any], signals: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    signals = signals or _build_status_signals(context)
    return_source_invoice = context.get("return_source_invoice") or _get_return_source_invoice(context)
    cancel_reason = _get_cancel_unavailable_reason(context)
    return_reason = _get_return_unavailable_reason(context, signals)
    refund_reason = _get_refund_unavailable_reason(context, signals)
    flow_visibility = context["flow_visibility"]
    return {
        "can_cancel": bool(flow_visibility.get("cancel_active", True)) and not cancel_reason,
        "cancel_reason": cancel_reason,
        "cancel_label": "Cancel order",
        "show_cancel_actions": bool(flow_visibility.get("cancel_active", True)),
        "can_return": not return_reason,
        "return_reason": return_reason,
        "return_label": "Return request",
        "show_shipping_actions": bool(flow_visibility.get("shipping_active", True))
        and bool(flow_visibility.get("return_active", True)),
        "can_refund": not refund_reason,
        "refund_reason": refund_reason,
        "refund_label": "Request refund",
        "show_payment_actions": bool(flow_visibility.get("payment_active", True)),
        "return_source_invoice": return_source_invoice.get("name") if return_source_invoice else None,
        "eligible_return_items_count": signals.get("eligible_return_items_count", 0),
    }


@frappe.whitelist(allow_guest=True)
def get_order_delivery_tracking(
    order_name: str,
    order_doctype: Optional[str] = None,
) -> Dict[str, Any]:
    """Return portal-safe delivery tracking details for webshop orders."""

    order_doc = _get_portal_order_doc(order_name, order_doctype)
    context = _build_portal_order_tracking_context(order_doc)
    if _start_portal_refund_processing(context):
        context = _build_portal_order_tracking_context(order_doc)
    normalized = _resolve_normalized_status(context)
    context["status_signals"] = normalized["status_signals"]
    milestones = _build_tracking_milestones(context, normalized["normalized_status_code"])
    actions = _get_order_actions(context, normalized["status_signals"])

    response: Dict[str, Any] = {
        "order": {
            "name": order_doc.name,
            "doctype": order_doc.doctype,
            "status": order_doc.get("status"),
            "transaction_date": order_doc.get("transaction_date") or order_doc.get("posting_date"),
            "grand_total": order_doc.get("grand_total"),
            "currency": order_doc.get("currency"),
            "per_delivered": flt(order_doc.get("per_delivered")),
            "customer_name": order_doc.get("customer_name"),
            "contact_display": order_doc.get("contact_display"),
            "shipping_address_name": order_doc.get("shipping_address_name")
            or order_doc.get("shipping_address")
            or order_doc.get("customer_address"),
            "address_display": order_doc.get("address_display"),
        },
        "delivery_notes": context["delivery_notes"],
        "shipments": context["shipments"],
        "return_delivery_notes": context["return_delivery_notes"],
        "return_approval_requests": context.get("return_approval_requests", []),
        "return_records": context.get("return_records", []),
        "return_shipments": context["return_shipments"],
        "return_tracking": context["return_shipments"],
        "payment_requests": context["payment_requests"],
        "milestones": milestones,
        "flow_visibility": context["flow_visibility"],
        "headline": normalized["normalized_status_label"],
        "status_note": normalized["normalized_status_note"],
        "normalized_status_code": normalized["normalized_status_code"],
        "normalized_status_label": normalized["normalized_status_label"],
        "normalized_status_note": normalized["normalized_status_note"],
        "status_signals": normalized["status_signals"],
        "actions": actions,
        "eligible_return_items": context.get("eligible_return_items", []),
        "return_window_open": context.get("return_window_open"),
        "return_window_end_date": context.get("return_window_end_date"),
        "return_receipt_confirmed": normalized["status_signals"].get("has_return_received"),
        "has_tracking": any(
            shipment.get("has_tracking")
            for shipment in context["shipments"] + context["return_shipments"]
        ),
    }
    return response


@frappe.whitelist()
def cancel_portal_order(
    order_name: str,
    order_doctype: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    order_doc = _get_portal_order_doc(order_name, order_doctype)
    context = _build_portal_order_tracking_context(order_doc)
    unavailable_reason = _get_cancel_unavailable_reason(context)
    if unavailable_reason:
        raise frappe.ValidationError(frappe._(unavailable_reason))

    order_doc.flags.ignore_permissions = True
    existing_ignored_doctypes = order_doc.get("ignore_linked_doctypes") or ()
    if isinstance(existing_ignored_doctypes, str):
        existing_ignored_doctypes = (existing_ignored_doctypes,)
    order_doc.ignore_linked_doctypes = tuple(existing_ignored_doctypes) + ("Payment Request",)
    signals = _build_status_signals(context)
    refund_message = (
        f"{PORTAL_REFUND_REQUEST_MARKER} Customer requested a refund after cancellation from the webshop order page."
    )
    if reason and reason.strip():
        refund_message += f" Reason: {reason.strip()}"

    try:
        order_doc.cancel()
    except frappe.LinkExistsError:
        raise frappe.ValidationError(
            frappe._(
                "This paid order cannot be cancelled online yet because linked billing or payment records still need staff review."
            )
        )

    if reason:
        order_doc.add_comment("Comment", f"Customer requested cancellation: {reason.strip()}")

    if signals.get("payment_received"):
        order_doc.add_comment("Comment", refund_message)

    return {
        "ok": True,
        "order_name": order_doc.name,
        "order_doctype": order_doc.doctype,
        "message": "Your order has been cancelled. Refund processing will begin after return receipt or finance review, where applicable.",
    }


@frappe.whitelist()
def create_portal_return_request(
    order_name: str,
    order_doctype: Optional[str] = None,
    reason: Optional[str] = None,
    selected_items: Optional[Any] = None,
) -> Dict[str, Any]:
    from raftor_shippinghq.api.returns import submit_return_request

    order_doc = _get_portal_order_doc(order_name, order_doctype)
    context = _build_portal_order_tracking_context(order_doc)
    signals = _build_status_signals(context)
    unavailable_reason = _get_return_unavailable_reason(context, signals)
    if unavailable_reason:
        frappe.throw(frappe._(unavailable_reason))

    source_invoice = context.get("return_source_invoice") or _get_return_source_invoice(context)
    if not source_invoice:
        frappe.throw(frappe._("A submitted sales invoice is required before a return can be started online."))

    eligible_items = context.get("eligible_return_items") or []
    eligible_by_row = {
        item["sales_invoice_item"]: item for item in eligible_items if item.get("sales_invoice_item")
    }
    requested_items = _parse_selected_return_items(selected_items)
    if requested_items:
        selected_by_row = {row["sales_invoice_item"]: flt(row["qty"]) for row in requested_items}
    else:
        selected_by_row = {
            item["sales_invoice_item"]: flt(item.get("remaining_returnable_qty"))
            for item in eligible_items
            if item.get("is_return_eligible") and flt(item.get("remaining_returnable_qty")) > 0
        }

    if not selected_by_row:
        frappe.throw(frappe._("Select at least one eligible item to return."))

    for sales_invoice_item, qty in selected_by_row.items():
        eligible_row = eligible_by_row.get(sales_invoice_item)
        if not eligible_row or not eligible_row.get("is_return_eligible"):
            frappe.throw(frappe._("One or more selected items are not eligible for return."))
        if qty <= 0 or qty - flt(eligible_row.get("remaining_returnable_qty")) > 0.0001:
            frappe.throw(
                frappe._("Requested return quantity exceeds the remaining returnable quantity for item {0}.").format(
                    eligible_row.get("item_code") or sales_invoice_item
                )
            )

    outbound_shipment = _get_return_target_shipment(context)
    if not outbound_shipment:
        frappe.throw(
            frappe._("A shipped order record is required before a return request can be created online.")
        )

    selected_rows = []
    for sales_invoice_item, qty in selected_by_row.items():
        eligible_row = eligible_by_row.get(sales_invoice_item) or {}
        selected_rows.append(
            {
                "sales_invoice_item": sales_invoice_item,
                "qty": qty,
                "item_code": eligible_row.get("item_code"),
                "item_name": eligible_row.get("item_name"),
                "uom": eligible_row.get("uom"),
                "remaining_returnable_qty": eligible_row.get("remaining_returnable_qty"),
            }
        )

    with run_as("Administrator"):
        request_result = submit_return_request(
            order_doctype=order_doc.doctype,
            order_name=order_doc.name,
            sales_invoice=source_invoice["name"],
            original_shipment=outbound_shipment["name"],
            customer=order_doc.get("customer"),
            customer_name=order_doc.get("customer_name"),
            customer_email=order_doc.get("contact_email") or order_doc.get("contact_display"),
            return_reason=reason.strip() if reason else "Customer return request from webshop",
            customer_remarks=reason.strip() if reason else "",
            items=selected_rows,
        )
        order_doc.flags.ignore_permissions = True
        if reason:
            order_doc.add_comment(
                "Comment",
                f"{PORTAL_RETURN_REQUEST_MARKER} Customer requested a return for approval: {reason.strip()}",
            )
        else:
            order_doc.add_comment(
                "Comment",
                f"{PORTAL_RETURN_REQUEST_MARKER} Customer started a return request from the webshop order page.",
            )

    return {
        "ok": True,
        "order_name": order_doc.name,
        "order_doctype": order_doc.doctype,
        "return_request": request_result.get("return_request"),
        "message": request_result.get("message") or "Your return request has been submitted for review.",
    }


@frappe.whitelist()
def create_portal_refund_request(
    order_name: str,
    order_doctype: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    order_doc = _get_portal_order_doc(order_name, order_doctype)
    context = _build_portal_order_tracking_context(order_doc)
    signals = _build_status_signals(context)
    unavailable_reason = _get_refund_unavailable_reason(context, signals)
    if unavailable_reason:
        frappe.throw(frappe._(unavailable_reason))

    order_doc.flags.ignore_permissions = True
    refund_message = f"{PORTAL_REFUND_REQUEST_MARKER} Customer requested a refund from the webshop order page."
    if reason and reason.strip():
        refund_message += f" Reason: {reason.strip()}"
    order_doc.add_comment(
        "Comment",
        refund_message,
    )

    for return_invoice in context["return_invoices"]:
        if return_invoice.get("name") and frappe.db.exists("Sales Invoice", return_invoice["name"]):
            return_doc = frappe.get_doc("Sales Invoice", return_invoice["name"])
            return_doc.flags.ignore_permissions = True
            return_comment = "Customer requested refund settlement from the webshop order page."
            if reason and reason.strip():
                return_comment += f" Reason: {reason.strip()}"
            return_doc.add_comment(
                "Comment",
                return_comment,
            )

    return {
        "ok": True,
        "order_name": order_doc.name,
        "order_doctype": order_doc.doctype,
        "message": "Your refund request has been submitted.",
    }


def _get_linked_sales_orders_for_delivery_note(delivery_note_doc) -> List[str]:
    delivery_note_names = [delivery_note_doc.name]
    if delivery_note_doc.get("return_against"):
        delivery_note_names.append(delivery_note_doc.get("return_against"))

    item_rows = frappe.get_all(
        "Delivery Note Item",
        filters={"parent": ["in", delivery_note_names]},
        fields=["against_sales_order"],
    )
    sales_orders = []
    for row in item_rows:
        sales_order = row.get("against_sales_order")
        if sales_order and sales_order not in sales_orders:
            sales_orders.append(sales_order)
    return sales_orders


def sync_portal_refund_processing_after_return_receipt(doc, method=None):
    if doc.doctype != "Delivery Note" or doc.docstatus != 1 or not cint(doc.get("is_return")):
        return

    for sales_order_name in _get_linked_sales_orders_for_delivery_note(doc):
        if not frappe.db.exists("Sales Order", sales_order_name):
            continue
        order_doc = frappe.get_doc("Sales Order", sales_order_name)
        context = _build_portal_order_tracking_context(order_doc)
        _start_portal_refund_processing(context)

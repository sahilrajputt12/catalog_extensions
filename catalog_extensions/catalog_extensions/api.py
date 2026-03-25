import frappe
from typing import Any, Dict, List, Optional, Tuple
from frappe.utils import add_days, nowdate, getdate, flt


def ensure_website_item_for_published_item(doc, method: Optional[str] = None) -> None:
    """Ensure a Website Item exists when an Item is marked as published_in_website.

    This is mainly to support CSV/Data Import: if an Item row sets
    published_in_website = 1, core logic does *not* automatically create a
    Website Item; normally that only happens when the user clicks
    "Publish in Website" button on the Item form.

    Here we mirror that behavior on the server side:
    - If Item.published_in_website is 1 and no Website Item exists yet,
      call webshop.webshop.doctype.website_item.website_item.make_website_item.
    - If a Website Item already exists, do nothing.
    """

    # Make sure we are dealing with Item
    if getattr(doc, "doctype", None) != "Item":
        return

    # Normalized boolean check: handle 1/0, "1"/"0", "Yes"/"No", True/False
    flag = doc.get("published_in_website")
    if isinstance(flag, str):
        flag = flag.strip().lower()
        truthy = {"1", "yes", "y", "true"}
        if flag not in truthy:
            # Debug: log why we skip
            frappe.logger().info(
                f"ensure_website_item_for_published_item: Item {doc.name} skipped because published_in_website = {flag!r}"
            )
            return
    elif not flag:
        frappe.logger().info(
            f"ensure_website_item_for_published_item: Item {doc.name} skipped because published_in_website is falsy"
        )
        return

    # If a Website Item already exists for this Item, nothing to do
    if frappe.db.exists("Website Item", {"item_code": doc.name}):
        frappe.logger().info(
            f"ensure_website_item_for_published_item: Item {doc.name} already has Website Item; skipping creation"
        )
        return

    # Import lazily to avoid circular imports at module load time
    from webshop.webshop.doctype.website_item.website_item import make_website_item

    try:
        # Reuse the same helper the UI button uses. It accepts either an Item
        # doc or a dict; we can just pass the Item doc here.
        frappe.logger().info(
            f"ensure_website_item_for_published_item: Creating Website Item for Item {doc.name}"
        )
        make_website_item(doc)
        frappe.logger().info(
            f"ensure_website_item_for_published_item: Successfully created Website Item for Item {doc.name}"
        )
    except Exception:
        # Do not break the import / save; just log the error for inspection.
        frappe.log_error(
            frappe.get_traceback(),
            f"Failed to auto-create Website Item for Item {doc.name}",
        )


@frappe.whitelist(allow_guest=True)
def get_filter_facets() -> Dict[str, List[Dict[str, Any]]]:
    """Return facet counts for filter UI (categories, brands, price ranges).

    All logic lives in this custom app. Facets are:
    - item_groups: Website Item item_group counts
    - brands: Website Item brand counts
    - price_ranges: Configurable ranges from Catalog Price Range DocType per site
    - availability: In stock / Out of stock counts
    """

    facets: Dict[str, List[Dict[str, Any]]] = {}

    # Item Group (categories)
    facets["item_groups"] = frappe.db.sql(
        """
        SELECT ig.item_group_name, COUNT(DISTINCT wi.name) AS count
        FROM `tabWebsite Item` wi
        JOIN `tabItem Group` ig ON wi.item_group = ig.name
        WHERE wi.published = 1
        GROUP BY ig.item_group_name
        ORDER BY count DESC
        """,
        as_dict=True,
    )

    # Brand
    facets["brands"] = frappe.db.sql(
        """
        SELECT brand, COUNT(*) AS count
        FROM `tabWebsite Item`
        WHERE published = 1 AND brand IS NOT NULL AND brand != ''
        GROUP BY brand
        ORDER BY count DESC
        LIMIT 20
        """,
        as_dict=True,
    )

    # Site-configurable price ranges from Catalog Price Range DocType
    facets["price_ranges"] = _get_price_range_facets()

    # Offers facet: buckets by Offer Title so UI can filter by specific offer types
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

    # Badges facet: buckets by Item Badge.badge_type across published Website Items
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

    # If the DocType/table does not exist (e.g. before migrate), just skip
    if not frappe.db.table_exists("Catalog Price Range"):
        return []

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
    """Wrapper around Webshop get_product_filter_data that can honor price_bucket.

    This method is intended to be registered via override_whitelisted_methods so
    that existing Webshop JS continues to call the same endpoint name while we
    inject price range logic from this custom app.
    """

    from webshop.webshop.api import get_product_filter_data as core_get_product_filter_data

    # Ensure dict form, mirroring core behavior of the original function
    if isinstance(query_args, str):
        import json

        query_args = json.loads(query_args)

    q = frappe._dict(query_args or {})

    # Normalize field_filters to a dict (it may arrive as a JSON string in URL)
    import json as _json

    field_filters = q.get("field_filters") or {}
    if isinstance(field_filters, str):
        try:
            field_filters = _json.loads(field_filters) or {}
        except Exception:
            field_filters = {}

    # Map top-level brand param (used by header dropdown links) into
    # field_filters["brand"] so it behaves like a normal field filter.
    brand = q.get("brand")
    if brand:
        if isinstance(brand, str):
            brand_values = [brand]
        else:
            brand_values = list(brand)

        existing = field_filters.get("brand") or []
        # Ensure list form
        if not isinstance(existing, list):
            existing = [existing]

        for b in brand_values:
            if b not in existing:
                existing.append(b)

        field_filters["brand"] = existing

    if field_filters:
        q.field_filters = field_filters

    # Build a safe copy of query args for core webshop function, stripping
    # custom-only keys that the ProductQuery engine is not aware of.
    # These custom filters will be applied post-query on the returned items.
    core_field_filters = dict(field_filters or {})

    # Keys reserved for our custom, post-processing filters.
    custom_only_keys = {"price_from", "price_to", "offers_title", "badges"}
    for key in list(core_field_filters.keys()):
        if key in custom_only_keys:
            core_field_filters.pop(key, None)

    core_query_args = frappe._dict(q)
    if core_field_filters:
        core_query_args.field_filters = core_field_filters

    # Get results from core webshop function
    result = core_get_product_filter_data(core_query_args)

    # If the core layer already failed (it returns a dict with "exc"),
    # propagate as-is so the frontend can show its generic error state.
    if not isinstance(result, dict) or result.get("exc"):
        return result

    # Extract and apply custom filters (price, offers, badges) against the
    # successfully returned items list.
    custom_filters = _extract_custom_filters(field_filters)

    if custom_filters and result.get("items"):
        result["items"] = _apply_custom_filters(result["items"], custom_filters)
        result["total_count"] = len(result["items"])

    return result


def _extract_custom_filters(field_filters: Dict[str, Any]) -> Dict[str, Any]:
    """Extract custom filter values from field_filters."""
    custom = {}

    # Price filters
    if field_filters.get("price_from"):
        values = field_filters["price_from"]
        if isinstance(values, list) and values:
            custom["price_from"] = flt(values[0])
    if field_filters.get("price_to"):
        values = field_filters["price_to"]
        if isinstance(values, list) and values:
            custom["price_to"] = flt(values[0])

    # Offers filter
    if field_filters.get("offers_title"):
        values = field_filters["offers_title"]
        if isinstance(values, list):
            custom["offers_title"] = [str(v) for v in values if v]

    # Badges filter
    if field_filters.get("badges"):
        values = field_filters["badges"]
        if isinstance(values, list):
            custom["badges"] = [str(v) for v in values if v]

    return custom


def _apply_custom_filters(items: List[Dict[str, Any]], custom: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apply custom filters to the items list."""
    filtered = items

    # Get price list for price filtering
    price_list = (
        frappe.db.get_single_value("Webshop Settings", "price_list")
        or frappe.db.get_single_value("Selling Settings", "selling_price_list")
        or "Standard Selling"
    )

    # Apply price filter
    if custom.get("price_from") is not None or custom.get("price_to") is not None:
        price_from = custom.get("price_from")
        price_to = custom.get("price_to")

        # Get item codes from items
        item_codes = [item.get("item_code") for item in filtered if item.get("item_code")]

        if item_codes:
            # Find items with prices in range
            price_filters: List[Any] = [
                ["Item Price", "price_list", "=", price_list],
                ["Item Price", "selling", "=", 1],
                ["Item Price", "item_code", "in", item_codes],
            ]

            if price_from is not None:
                price_filters.append(["Item Price", "price_list_rate", ">=", price_from])
            if price_to is not None:
                price_filters.append(["Item Price", "price_list_rate", "<=", price_to])

            items_with_price = frappe.get_all(
                "Item Price",
                filters=price_filters,
                fields=["item_code"],
                distinct=True,
                pluck="item_code",
            )

            # Filter items to only those with matching prices
            filtered = [item for item in filtered if item.get("item_code") in items_with_price]

    # Apply offers filter
    if custom.get("offers_title"):
        offer_titles = custom["offers_title"]

        # Get Website Item names from filtered items
        website_item_names = [item.get("name") for item in filtered if item.get("name")]

        if website_item_names:
            # Find Website Items that have selected offer titles
            website_items_with_offers = frappe.get_all(
                "Website Offer",
                filters=[
                    ["offer_title", "in", offer_titles],
                    ["parent", "in", website_item_names],
                    ["parenttype", "=", "Website Item"],
                ],
                fields=["parent"],
                distinct=True,
                pluck="parent",
            )

            # Filter items to only those with matching offers
            filtered = [item for item in filtered if item.get("name") in website_items_with_offers]
        else:
            filtered = []

    # Apply badges filter
    if custom.get("badges"):
        badge_types = custom["badges"]

        # Get item_codes from filtered items
        item_codes = [item.get("item_code") for item in filtered if item.get("item_code")]

        if item_codes:
            # Step 1: Find Items that have selected badge types
            items_with_badges = frappe.get_all(
                "Item Badge",
                filters=[
                    ["badge_type", "in", badge_types],
                    ["parent", "in", item_codes],
                ],
                fields=["parent"],
                distinct=True,
                pluck="parent",
            )

            # Step 2: Filter items to only those whose item_code has the badge
            filtered = [item for item in filtered if item.get("item_code") in items_with_badges]
        else:
            filtered = []

    return filtered


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

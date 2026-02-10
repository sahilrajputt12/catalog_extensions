# Copyright (c) 2025, HaramiHost and Contributors
# License: MIT

import frappe


def validate(doc, method):
    # Basic validation: ensure from <= to if both are set
    if doc.from_amount and doc.to_amount:
        if doc.from_amount > doc.to_amount:
            frappe.throw("From Amount cannot be greater than To Amount")

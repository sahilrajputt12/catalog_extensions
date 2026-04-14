from types import SimpleNamespace
from unittest import TestCase

import frappe

from catalog_extensions.stock_guard import _build_stock_guard_metadata


frappe.cache = SimpleNamespace(hget=lambda _key, _field, generator=None: generator() if generator else None)
frappe.logger = lambda *args, **kwargs: SimpleNamespace(error=lambda *a, **k: None)


class StockGuardTestCase(TestCase):
	def test_out_of_stock_blocks_add_and_increase(self):
		result = _build_stock_guard_metadata(
			available_qty=0,
			current_qty=0,
			on_backorder=False,
			is_stock_item=True,
			allow_items_not_in_stock=False,
		)

		self.assertEqual(result["stock_state"], "out_of_stock")
		self.assertEqual(result["stock_message"], "Out of stock")
		self.assertFalse(result["show_stock_qty"])
		self.assertFalse(result["can_add_to_cart"])
		self.assertFalse(result["can_increase_qty"])

	def test_out_of_stock_preserves_existing_cart_qty_without_increase(self):
		result = _build_stock_guard_metadata(
			available_qty=0,
			current_qty=2,
			on_backorder=False,
			is_stock_item=True,
			allow_items_not_in_stock=False,
		)

		self.assertEqual(result["max_orderable_qty"], 2)
		self.assertTrue(result["can_add_to_cart"])
		self.assertFalse(result["can_increase_qty"])

	def test_out_of_stock_allows_ordering_when_setting_enabled(self):
		result = _build_stock_guard_metadata(
			available_qty=0,
			current_qty=0,
			on_backorder=False,
			is_stock_item=True,
			allow_items_not_in_stock=True,
		)

		self.assertEqual(result["stock_state"], "out_of_stock")
		self.assertEqual(result["stock_message"], "Out of stock")
		self.assertIsNone(result["max_orderable_qty"])
		self.assertTrue(result["can_add_to_cart"])
		self.assertTrue(result["can_increase_qty"])

	def test_low_stock_uses_amazon_style_message_when_stock_quantity_enabled(self):
		result = _build_stock_guard_metadata(
			available_qty=3,
			current_qty=1,
			on_backorder=False,
			is_stock_item=True,
			show_stock_qty=True,
		)

		self.assertEqual(result["stock_state"], "low_stock")
		self.assertEqual(result["stock_message"], "Only 3 left in stock")
		self.assertTrue(result["show_stock_qty"])
		self.assertTrue(result["can_add_to_cart"])
		self.assertTrue(result["can_increase_qty"])

	def test_low_stock_hides_quantity_message_when_stock_quantity_disabled(self):
		result = _build_stock_guard_metadata(
			available_qty=3,
			current_qty=1,
			on_backorder=False,
			is_stock_item=True,
			show_stock_qty=False,
		)

		self.assertEqual(result["stock_state"], "low_stock")
		self.assertEqual(result["stock_message"], "")
		self.assertFalse(result["show_stock_qty"])
		self.assertTrue(result["can_add_to_cart"])
		self.assertTrue(result["can_increase_qty"])

	def test_backorder_stays_purchasable(self):
		result = _build_stock_guard_metadata(
			available_qty=0,
			current_qty=0,
			on_backorder=True,
			is_stock_item=True,
			allow_items_not_in_stock=False,
		)

		self.assertEqual(result["stock_state"], "backorder")
		self.assertEqual(result["stock_message"], "Available on backorder")
		self.assertTrue(result["can_add_to_cart"])
		self.assertTrue(result["can_increase_qty"])

	def test_non_stock_item_stays_purchasable(self):
		result = _build_stock_guard_metadata(
			available_qty=0,
			current_qty=0,
			on_backorder=False,
			is_stock_item=False,
			allow_items_not_in_stock=False,
		)

		self.assertEqual(result["stock_state"], "in_stock")
		self.assertTrue(result["can_add_to_cart"])
		self.assertTrue(result["can_increase_qty"])

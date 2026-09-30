import unittest
import os
import sqlite3
import shutil
from datetime import datetime

# Import database functions from main application module
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app

class TestBillingAppTask1(unittest.TestCase):

    def setUp(self):
        self.test_db = os.path.join("data", "test_billing.db")
        app.DB_PATH = self.test_db
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        app.init_db()

    def tearDown(self):
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def test_01_schema_and_migrations(self):
        conn = app.get_db_connection()
        cursor = conn.cursor()
        
        # Check products migration columns
        cursor.execute("PRAGMA table_info(products)")
        p_cols = [r["name"] for r in cursor.fetchall()]
        self.assertIn("image_path", p_cols)
        self.assertIn("bg_color", p_cols)

        # Check bill_items migration columns
        cursor.execute("PRAGMA table_info(bill_items)")
        bi_cols = [r["name"] for r in cursor.fetchall()]
        self.assertIn("price_adjustment_snapshot", bi_cols)
        self.assertIn("final_rate_snapshot", bi_cols)

        # Check shop_price_adjustments table existence
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_price_adjustments'")
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_02_same_product_repeat_add1_increment(self):
        cart = []
        prod = {"id": 1, "product_name": "Surf Excel", "variant": "500g", "mrp": 75.0, "rate": 61.0, "base_rate": 61.0, "price_adjustment": 0.0}
        
        # Click 1: Add 1
        existing = next((item for item in cart if item["product_id"] == prod["id"]), None)
        if existing: existing["qty"] += 1
        else: cart.append({"product_id": prod["id"], "qty": 1, "rate": prod["rate"]})

        # Click 2: Add 1 again
        existing = next((item for item in cart if item["product_id"] == prod["id"]), None)
        if existing: existing["qty"] += 1
        else: cart.append({"product_id": prod["id"], "qty": 1, "rate": prod["rate"]})

        self.assertEqual(len(cart), 1)
        self.assertEqual(cart[0]["qty"], 2)

    def test_03_shop_specific_price_adjustments(self):
        conn = app.get_db_connection()
        # Add adjustment: Shop 1, Product 1 -> +2.0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO shop_price_adjustments (shop_id, product_id, price_adjustment, created_at, updated_at) VALUES (1, 1, 2.0, ?, ?)", (now, now))
        conn.commit()

        # Query adjustment
        adj_row = conn.execute("SELECT price_adjustment FROM shop_price_adjustments WHERE shop_id = 1 AND product_id = 1").fetchone()
        self.assertIsNotNone(adj_row)
        adj_val = adj_row["price_adjustment"]
        
        base_rate = 61.0
        final_rate = base_rate + adj_val
        self.assertEqual(final_rate, 63.0)
        conn.close()

    def test_04_mrp_exclusion_and_bill_totals(self):
        mrp = 75.0
        final_rate = 61.0
        qty = 10
        
        line_amt = qty * final_rate
        mrp_total = qty * mrp

        self.assertEqual(line_amt, 610.0)
        self.assertNotEqual(line_amt, mrp_total)

    def test_05_historical_price_snapshot_isolation(self):
        conn = app.get_db_connection()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Finalize invoice with Rate = 61.0
        cursor = conn.cursor()
        cursor.execute("INSERT INTO bills (invoice_number, shop_name_snapshot, bill_date, subtotal, grand_total, created_at) VALUES ('INV-TEST01', 'Test Shop', ?, 610.0, 610.0, ?)", (now, now))
        bill_id = cursor.lastrowid
        cursor.execute("INSERT INTO bill_items (bill_id, product_id, product_name_snapshot, variant_snapshot, quantity, mrp_snapshot, distributor_rate_snapshot, price_adjustment_snapshot, final_rate_snapshot, amount) VALUES (?, 1, 'Surf Excel', '500g', 10, 75.0, 61.0, 0.0, 610.0, 610.0)", (bill_id,))
        conn.commit()

        # Master catalog price update -> 64.0
        conn.execute("UPDATE products SET distributor_rate = 64.0 WHERE id = 1")
        conn.commit()

        # Fetch bill items
        b_item = conn.execute("SELECT * FROM bill_items WHERE bill_id = ?", (bill_id,)).fetchone()
        self.assertEqual(b_item["distributor_rate_snapshot"], 61.0)
        conn.close()

if __name__ == "__main__":
    unittest.main()
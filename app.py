import os
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import shutil
import uuid
import pandas as pd
import streamlit as st

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image as RLImage
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

st.set_page_config(
    page_title="DistroBill - FMCG Distributor Suite",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = os.path.join("data", "billing.db")
INVOICE_DIR = "invoices"
BACKUP_DIR = "backups"
IMAGE_DIR = os.path.join("data", "product_images")

for directory in ["data", INVOICE_DIR, BACKUP_DIR, IMAGE_DIR]:
    os.makedirs(directory, exist_ok=True)

def get_db_connection():
    """Establishes connection to local SQLite database with Row factory and WAL mode."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes SQLite tables and applies idempotent migrations safely."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL,
                variant TEXT NOT NULL,
                mrp REAL NOT NULL,
                distributor_rate REAL NOT NULL,
                sku TEXT,
                category TEXT,
                image_path TEXT,
                bg_color TEXT DEFAULT '#ffffff',
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_name TEXT NOT NULL,
                contact_person TEXT,
                phone TEXT,
                address TEXT,
                gstin TEXT,
                notes TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shop_price_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                price_adjustment REAL DEFAULT 0.0,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(shop_id, product_id),
                FOREIGN KEY (shop_id) REFERENCES shops (id),
                FOREIGN KEY (product_id) REFERENCES products (id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT UNIQUE NOT NULL,
                shop_id INTEGER,
                shop_name_snapshot TEXT NOT NULL,
                shop_phone_snapshot TEXT,
                shop_address_snapshot TEXT,
                shop_gstin_snapshot TEXT,
                bill_date TEXT NOT NULL,
                subtotal REAL NOT NULL,
                grand_total REAL NOT NULL,
                payment_status TEXT DEFAULT 'Unpaid',
                pdf_path TEXT,
                created_at TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bill_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER NOT NULL,
                product_id INTEGER,
                product_name_snapshot TEXT NOT NULL,
                variant_snapshot TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                mrp_snapshot REAL NOT NULL,
                distributor_rate_snapshot REAL NOT NULL,
                price_adjustment_snapshot REAL DEFAULT 0.0,
                final_rate_snapshot REAL NOT NULL,
                amount REAL NOT NULL,
                FOREIGN KEY (bill_id) REFERENCES bills (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER DEFAULT 0,
                purchase_date TEXT DEFAULT '',
                supplier_name TEXT DEFAULT '',
                supplier_phone TEXT DEFAULT '',
                supplier_address TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                variant TEXT DEFAULT '',
                quantity INTEGER DEFAULT 0,
                purchase_rate REAL DEFAULT 0.0,
                total_amount REAL DEFAULT 0.0,
                invoice_ref TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
        """)
        
        cursor.execute("PRAGMA table_info(purchases)")
        pur_info = cursor.fetchall()
        pur_cols = [row["name"] for row in pur_info]
        
        needs_rebuild = False
        for col in pur_info:
            if col["name"] == "product_id" and col["notnull"] == 1:
                needs_rebuild = True
                break

        if needs_rebuild:
            cursor.execute("""
                CREATE TABLE purchases_temp (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER DEFAULT 0,
                    purchase_date TEXT DEFAULT '',
                    supplier_name TEXT DEFAULT '',
                    supplier_phone TEXT DEFAULT '',
                    supplier_address TEXT DEFAULT '',
                    product_name TEXT DEFAULT '',
                    variant TEXT DEFAULT '',
                    quantity INTEGER DEFAULT 0,
                    purchase_rate REAL DEFAULT 0.0,
                    total_amount REAL DEFAULT 0.0,
                    invoice_ref TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                )
            """)
            valid_cols = ["id", "product_id", "purchase_date", "supplier_name", "supplier_phone", "supplier_address", "product_name", "variant", "quantity", "purchase_rate", "total_amount", "invoice_ref", "notes", "created_at", "updated_at"]
            common_cols = [c for c in pur_cols if c in valid_cols]
            if common_cols:
                col_str = ", ".join(common_cols)
                cursor.execute(f"INSERT INTO purchases_temp ({col_str}) SELECT {col_str} FROM purchases")
            cursor.execute("DROP TABLE purchases")
            cursor.execute("ALTER TABLE purchases_temp RENAME TO purchases")
            cursor.execute("PRAGMA table_info(purchases)")
            pur_cols = [row["name"] for row in cursor.fetchall()]

        needed_cols = {
            "product_id": "INTEGER DEFAULT 0",
            "purchase_date": "TEXT DEFAULT ''",
            "supplier_name": "TEXT DEFAULT ''",
            "supplier_phone": "TEXT DEFAULT ''",
            "supplier_address": "TEXT DEFAULT ''",
            "product_name": "TEXT DEFAULT ''",
            "variant": "TEXT DEFAULT ''",
            "quantity": "INTEGER DEFAULT 0",
            "purchase_rate": "REAL DEFAULT 0.0",
            "total_amount": "REAL DEFAULT 0.0",
            "invoice_ref": "TEXT DEFAULT ''",
            "notes": "TEXT DEFAULT ''",
            "created_at": "TEXT DEFAULT ''",
            "updated_at": "TEXT DEFAULT ''"
        }
        for col_name, col_def in needed_cols.items():
            if col_name not in pur_cols:
                cursor.execute(f"ALTER TABLE purchases ADD COLUMN {col_name} {col_def}")

        cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        
        cursor.execute("PRAGMA table_info(products)")
        prod_cols = [row["name"] for row in cursor.fetchall()]
        if "image_path" not in prod_cols:
            cursor.execute("ALTER TABLE products ADD COLUMN image_path TEXT")
        if "bg_color" not in prod_cols:
            cursor.execute("ALTER TABLE products ADD COLUMN bg_color TEXT DEFAULT '#ffffff'")

        cursor.execute("PRAGMA table_info(bills)")
        bill_cols = [row["name"] for row in cursor.fetchall()]
        if "payment_status" not in bill_cols:
            cursor.execute("ALTER TABLE bills ADD COLUMN payment_status TEXT DEFAULT 'Unpaid'")

        cursor.execute("PRAGMA table_info(bill_items)")
        bi_cols = [row["name"] for row in cursor.fetchall()]
        if "price_adjustment_snapshot" not in bi_cols:
            cursor.execute("ALTER TABLE bill_items ADD COLUMN price_adjustment_snapshot REAL DEFAULT 0.0")
        if "final_rate_snapshot" not in bi_cols:
            cursor.execute("ALTER TABLE bill_items ADD COLUMN final_rate_snapshot REAL DEFAULT 0.0")
            cursor.execute("UPDATE bill_items SET final_rate_snapshot = distributor_rate_snapshot WHERE final_rate_snapshot = 0.0")

        default_settings = {
            "business_name": "Apex Distribution Private Limited",
            "tagline": "Wholesale FMCG & Grocery Distributors",
            "business_address": "Plot 45, Industrial Area Phase II, New Delhi - 110020",
            "phone": "+91 98100 99887 / 011-45678900",
            "email": "billing@apexdistro.com",
            "gstin": "07AAAAA1234A1Z9",
            "invoice_prefix": "INV",
            "next_invoice_no": "101",
            "currency": "₹",
            "invoice_footer": "Thank you for your business! All goods received in good condition.",
            "logo_path": ""
        }
        
        for key, val in default_settings.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
            
        cursor.execute("SELECT COUNT(*) FROM products")
        if cursor.fetchone()[0] == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sample_prods = [
                ("Surf Excel", "250g", 40.0, 34.0, "SE-250", "Detergents", None, "#f0fdf4", 1, now, now),
                ("Surf Excel", "500g", 75.0, 61.0, "SE-500", "Detergents", None, "#f0fdf4", 1, now, now),
                ("Surf Excel", "1kg", 140.0, 118.0, "SE-1000", "Detergents", None, "#f0fdf4", 1, now, now),
                ("Surf Excel", "5kg", 650.0, 550.0, "SE-5000", "Detergents", None, "#f0fdf4", 1, now, now),
                ("Vim Dishwash", "250ml", 45.0, 38.0, "VIM-250", "Cleaning", None, "#eff6ff", 1, now, now),
                ("Vim Dishwash", "500ml", 85.0, 62.0, "VIM-500", "Cleaning", None, "#eff6ff", 1, now, now),
                ("Tide Detergent", "1kg", 150.0, 120.0, "TD-1000", "Detergents", None, "#fff7ed", 1, now, now),
            ]
            cursor.executemany("""
                INSERT INTO products (product_name, variant, mrp, distributor_rate, sku, category, image_path, bg_color, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, sample_prods)

        cursor.execute("SELECT COUNT(*) FROM shops")
        if cursor.fetchone()[0] == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sample_shops = [
                ("Sharma Store", "Rajesh Sharma", "9876543210", "Main Market, Block A", "07AAAAA0000A1Z5", "Regular Client", 1, now, now),
                ("Gupta Provision Store", "Suresh Gupta", "9811223344", "Shop #12, Station Road", "07BBBBB1111B1Z2", "Key Retailer", 1, now, now),
                ("Verma General Store", "Amit Verma", "9900112233", "Near Bus Stand, Sector 4", "", "Cash Only", 1, now, now),
            ]
            cursor.executemany("""
                INSERT INTO shops (shop_name, contact_person, phone, address, gstin, notes, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, sample_shops)

        conn.commit()
    finally:
        conn.close()

init_db()

def get_settings():
    """Retrieves settings dictionary from database."""
    conn = get_db_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}

def update_settings(settings_dict):
    """Updates settings key-value pairs in database."""
    conn = get_db_connection()
    for k, v in settings_dict.items():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()

def format_currency(val):
    """Formats numeric value as INR currency string."""
    d = Decimal(str(val or 0.0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return f"₹{d:,.2f}"

def save_uploaded_image(uploaded_file):
    """Saves uploaded pack image locally and returns relative path."""
    if uploaded_file is None:
        return None
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
        st.error("Unsupported file type. Upload JPG, JPEG, PNG, or WebP.")
        return None
    filename = f"prod_{uuid.uuid4().hex[:10]}{ext}"
    filepath = os.path.join(IMAGE_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return filepath

def delete_bill_fully(bill_id):
    """Permanently removes bill record, line items, and associated PDF file."""
    conn = get_db_connection()
    b_row = conn.execute("SELECT pdf_path FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if b_row and b_row["pdf_path"] and os.path.exists(b_row["pdf_path"]):
        try:
            os.remove(b_row["pdf_path"])
        except Exception as e:
            st.error(f"Could not remove PDF file: {e}")
    
    conn.execute("DELETE FROM bill_items WHERE bill_id = ?", (bill_id,))
    conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
    conn.commit()
    conn.close()

def delete_product_fully(product_id):
    """Permanently removes product and associated shop pricing adjustments."""
    conn = get_db_connection()
    prod = conn.execute("SELECT image_path FROM products WHERE id = ?", (product_id,)).fetchone()
    if prod and prod["image_path"] and os.path.exists(prod["image_path"]):
        try:
            os.remove(prod["image_path"])
        except Exception:
            pass
    conn.execute("DELETE FROM shop_price_adjustments WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()

def delete_shop_fully(shop_id):
    """Permanently removes shop and associated pricing adjustments."""
    conn = get_db_connection()
    conn.execute("DELETE FROM shop_price_adjustments WHERE shop_id = ?", (shop_id,))
    conn.execute("DELETE FROM shops WHERE id = ?", (shop_id,))
    conn.commit()
    conn.close()

def get_latest_purchase_rate(conn, prod_name, variant):
    """Returns the most recent purchase rate per piece for a given product and variant."""
    row = conn.execute(
        "SELECT purchase_rate FROM purchases WHERE LOWER(product_name) = LOWER(?) AND LOWER(variant) = LOWER(?) ORDER BY purchase_date DESC, id DESC LIMIT 1",
        (prod_name.strip(), variant.strip())
    ).fetchone()
    return float(row["purchase_rate"]) if row else 0.0

st.markdown("""
    <style>
    .stApp { background-color: #f8fafc; font-family: 'Inter', system-ui, -apple-system, sans-serif; }
    .app-header { background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: white; padding: 1.25rem 1.75rem; border-radius: 12px; margin-bottom: 1.5rem; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
    .app-title { font-size: 1.6rem; font-weight: 700; margin: 0; }
    .app-subtitle { font-size: 0.85rem; color: #94a3b8; margin-top: 0.2rem; }
    .css-card { background-color: #ffffff; border-radius: 12px; padding: 1.25rem; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05); margin-bottom: 1rem; }
    .metric-box { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 10px; padding: 1rem; display: flex; align-items: center; gap: 1rem; }
    .metric-icon-box { width: 48px; height: 48px; border-radius: 8px; display: flex; align-items: center; justify-content: center; background: #eff6ff; color: #2563eb; font-weight: bold; }
    .product-card { border: 1px solid #cbd5e1; border-radius: 10px; padding: 0.75rem; transition: all 0.2s ease; height: 100%; display: flex; flex-direction: column; justify-content: space-between; }
    .product-title { font-size: 0.95rem; font-weight: 700; color: #0f172a; margin-top: 0.4rem; }
    .product-variant { font-size: 0.75rem; color: #475569; font-weight: 600; background: rgba(0,0,0,0.05); padding: 2px 6px; border-radius: 4px; display: inline-block; margin-bottom: 0.4rem; }
    .mrp-tag { font-size: 0.75rem; color: #94a3b8; text-decoration: line-through; }
    .rate-badge { background-color: #eff6ff; border: 1px solid #bfdbfe; color: #1d4ed8; font-size: 0.95rem; font-weight: 800; padding: 0.25rem 0.5rem; border-radius: 6px; display: inline-block; margin-top: 0.25rem; }
    .cart-container { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 12px; padding: 1.25rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
    .cart-total-box { background: #0f172a; color: white; padding: 1.25rem; border-radius: 8px; margin-top: 1rem; text-align: right; }
    .cart-total-amount { font-size: 1.8rem; font-weight: 800; color: #38bdf8; }
    .invoice-box { background: #ffffff; border: 1px solid #cbd5e1; padding: 2.5rem; border-radius: 8px; color: #1e293b; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); max-width: 800px; margin: 0 auto; }
    .img-placeholder { height: 110px; background: #f1f5f9; border-radius: 6px; display: flex; align-items: center; justify-content: center; color: #94a3b8; font-size: 0.8rem; font-weight: 600; }

    /* Zywra / Finnova Modern Card Styling */
    .zy-kpi-card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 1.25rem; box-shadow: 0 4px 16px -2px rgba(15, 23, 42, 0.04); position: relative; overflow: hidden; }
    .zy-kpi-title { font-size: 0.82rem; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
    .zy-kpi-val { font-size: 1.75rem; font-weight: 800; color: #0f172a; margin: 0.3rem 0; }
    .zy-kpi-sub { font-size: 0.78rem; font-weight: 600; display: inline-block; padding: 2px 8px; border-radius: 12px; }
    .zy-sub-green { background: #dcfce7; color: #15803d; }
    .zy-sub-blue { background: #dbeafe; color: #1e40af; }
    .zy-sub-amber { background: #fef3c7; color: #b45309; }
    .zy-kpi-badge { position: absolute; top: 1.25rem; right: 1.25rem; width: 36px; height: 36px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 1.1rem; }
    .zy-dark-container { background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); color: #f8fafc; border-radius: 20px; padding: 1.5rem; box-shadow: 0 12px 24px -6px rgba(15, 23, 42, 0.25); margin-top: 1.5rem; }
    .zy-filter-bar { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 0.75rem 1.25rem; margin-bottom: 1.25rem; box-shadow: 0 2px 8px rgba(0,0,0,0.02); }
    </style>
""", unsafe_allow_html=True)

if "active_page" not in st.session_state:
    st.session_state.active_page = "Dashboard"
if "cart" not in st.session_state:
    st.session_state.cart = []
if "show_review_modal" not in st.session_state:
    st.session_state.show_review_modal = False
if "view_bill_id" not in st.session_state:
    st.session_state.view_bill_id = None
if "edit_bill_id" not in st.session_state:
    st.session_state.edit_bill_id = None

def add_to_cart_cb(p_id, p_name, p_var, p_mrp, p_base_rate, p_shop_adj):
    """Callback function that safely updates cart quantity on + Add 1 clicks."""
    for item in st.session_state.cart:
        if item["product_id"] == p_id:
            item["qty"] += 1
            return
    st.session_state.cart.append({
        "product_id": p_id,
        "name": p_name,
        "variant": p_var,
        "mrp": p_mrp,
        "base_rate": p_base_rate,
        "price_adjustment": p_shop_adj,
        "qty": 1
    })

def generate_pdf_invoice(bill_id):
    """Generates an A4 Tax Invoice PDF using ReportLab flowables."""
    if not REPORTLAB_AVAILABLE:
        return None
    
    conn = get_db_connection()
    bill_row = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if not bill_row:
        conn.close()
        return None
    
    bill = dict(bill_row)
    items = [dict(r) for r in conn.execute("SELECT * FROM bill_items WHERE bill_id = ?", (bill_id,)).fetchall()]
    settings = get_settings()
    conn.close()
    
    pdf_filename = f"{bill['invoice_number']}.pdf"
    pdf_path = os.path.join(INVOICE_DIR, pdf_filename)
    
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=36,
        bottomMargin=36
    )
    
    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle(
        'DocNormal',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#334155')
    )
    
    story = []
    
    header_data = [
        [
            Paragraph(f"<b>{settings.get('business_name', 'Apex Distribution')}</b><br/>"
                      f"{settings.get('business_address', '')}<br/>"
                      f"Phone: {settings.get('phone', '')} | GSTIN: {settings.get('gstin', '')}", normal_style),
            Paragraph(f"<font size=16 color='#2563eb'><b>TAX INVOICE</b></font><br/>"
                      f"<b>Invoice #:</b> {bill['invoice_number']}<br/>"
                      f"<b>Date:</b> {bill['bill_date']}", ParagraphStyle('RightText', parent=normal_style, alignment=2))
        ]
    ]
    
    header_table = Table(header_data, colWidths=[310, 220])
    header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    story.append(header_table)
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0'), spaceAfter=12))
    
    bill_to_text = f"<b>BILL TO:</b><br/>" \
                   f"<b>{bill['shop_name_snapshot']}</b><br/>" \
                   f"Phone: {bill['shop_phone_snapshot'] or 'N/A'}<br/>" \
                   f"Address: {bill['shop_address_snapshot'] or 'N/A'}<br/>" \
                   f"GSTIN: {bill['shop_gstin_snapshot'] or 'N/A'}"
    
    story.append(Paragraph(bill_to_text, normal_style))
    story.append(Spacer(1, 12))
    
    table_data = [["#", "Product", "Qty", "MRP", "Base", "Adj", "Final", "Amount"]]
    for idx, item in enumerate(items, 1):
        f_rate = item['final_rate_snapshot'] if item.get('final_rate_snapshot') else item['distributor_rate_snapshot']
        adj_v = item.get('price_adjustment_snapshot', 0.0)
        table_data.append([
            str(idx),
            f"{item['product_name_snapshot']} ({item['variant_snapshot']})",
            str(item['quantity']),
            f"{item['mrp_snapshot']:,.2f}",
            f"{item['distributor_rate_snapshot']:,.2f}",
            f"{adj_v:,.2f}",
            f"{f_rate:,.2f}",
            f"{item['amount']:,.2f}"
        ])
        
    col_w = [25, 145, 30, 50, 50, 45, 55, 65]
    item_table = Table(table_data, colWidths=col_w)
    item_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f8fafc')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#475569')),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('ALIGN', (2,0), (2,-1), 'CENTER'),
        ('ALIGN', (3,0), (-1,-1), 'RIGHT'),
        ('LINEBELOW', (0,0), (-1,0), 1.5, colors.HexColor('#cbd5e1')),
        ('LINEBELOW', (0,1), (-1,-1), 0.5, colors.HexColor('#f1f5f9')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0'))
    ]))
    story.append(item_table)
    story.append(Spacer(1, 12))
    
    totals_data = [
        ["", Paragraph("<b>GRAND TOTAL:</b>", ParagraphStyle('RightAlign', parent=normal_style, alignment=2)), f"₹{bill['grand_total']:,.2f}"]
    ]
    totals_table = Table(totals_data, colWidths=[280, 140, 110])
    totals_table.setStyle(TableStyle([
        ('FONTNAME', (2,0), (2,0), 'Helvetica-Bold'),
        ('FONTSIZE', (2,0), (2,0), 12),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('TEXTCOLOR', (2,0), (2,0), colors.HexColor('#0f172a')),
        ('LINEABOVE', (1,0), (2,0), 1, colors.HexColor('#0f172a')),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 15))
    
    footer_text = settings.get("invoice_footer", "")
    if footer_text:
        story.append(Paragraph(f"<b>Terms & Notes:</b> {footer_text}", normal_style))
        
    doc.build(story)
    
    conn = get_db_connection()
    conn.execute("UPDATE bills SET pdf_path = ? WHERE id = ?", (pdf_path, bill_id))
    conn.commit()
    conn.close()
    
    return pdf_path

def generate_profit_pdf(bill_id):
    """Generates an internal owner-only Profit Analysis PDF for a given bill."""
    if not REPORTLAB_AVAILABLE:
        return None

    conn = get_db_connection()
    bill_row = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if not bill_row:
        conn.close()
        return None

    bill = dict(bill_row)
    items = [dict(r) for r in conn.execute("SELECT * FROM bill_items WHERE bill_id = ?", (bill_id,)).fetchall()]
    settings = get_settings()

    pdf_filename = f"profit_{bill['invoice_number']}.pdf"
    pdf_path = os.path.join(INVOICE_DIR, pdf_filename)

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle(
        'ProfitNormal',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1e293b')
    )

    story = []

    header_data = [
        [
            Paragraph(f"<b>{settings.get('business_name', 'Apex Distribution')}</b><br/>"
                      f"CONFIDENTIAL OWNER PROFIT REPORT", normal_style),
            Paragraph(f"<font size=14 color='#16a34a'><b>PROFIT ANALYSIS</b></font><br/>"
                      f"<b>Invoice #:</b> {bill['invoice_number']}<br/>"
                      f"<b>Date:</b> {bill['bill_date']}", ParagraphStyle('RightText', parent=normal_style, alignment=2))
        ]
    ]

    header_table = Table(header_data, colWidths=[310, 220])
    header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    story.append(header_table)
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#16a34a'), spaceAfter=10))

    story.append(Paragraph(f"<b>Shop / Customer:</b> {bill['shop_name_snapshot']}", normal_style))
    story.append(Spacer(1, 10))

    table_data = [["Product", "Variant", "Qty", "Selling Rate", "Purchase Rate", "Revenue", "Cost", "Profit"]]

    tot_rev = 0.0
    tot_cost = 0.0

    for item in items:
        p_name = item['product_name_snapshot']
        variant = item['variant_snapshot']
        qty = item['quantity']
        sell_rate = item['final_rate_snapshot'] if item.get('final_rate_snapshot') else item['distributor_rate_snapshot']
        purch_rate = get_latest_purchase_rate(conn, p_name, variant)

        rev = sell_rate * qty
        cost = purch_rate * qty
        profit = rev - cost

        tot_rev += rev
        tot_cost += cost

        table_data.append([
            p_name,
            variant,
            str(qty),
            f"₹{sell_rate:,.2f}",
            f"₹{purch_rate:,.2f}",
            f"₹{rev:,.2f}",
            f"₹{cost:,.2f}",
            f"₹{profit:,.2f}"
        ])

    conn.close()

    tot_profit = tot_rev - tot_cost
    margin_pct = (tot_profit / tot_rev * 100) if tot_rev > 0 else 0.0

    col_w = [110, 60, 30, 65, 65, 65, 65, 70]
    p_table = Table(table_data, colWidths=col_w)
    p_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f0fdf4')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#166534')),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('ALIGN', (2,0), (2,-1), 'CENTER'),
        ('ALIGN', (3,0), (-1,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1'))
    ]))
    story.append(p_table)
    story.append(Spacer(1, 15))

    summary_data = [
        ["Total Revenue:", f"₹{tot_rev:,.2f}"],
        ["Total Purchase Cost:", f"₹{tot_cost:,.2f}"],
        ["Total Net Profit:", f"₹{tot_profit:,.2f}"],
        ["Profit Margin %:", f"{margin_pct:.2f}%"]
    ]
    s_table = Table(summary_data, colWidths=[200, 120])
    s_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('TEXTCOLOR', (0,2), (1,2), colors.HexColor('#15803d')),
        ('LINEABOVE', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
    ]))
    story.append(s_table)

    doc.build(story)
    return pdf_path

settings = get_settings()

with st.sidebar:
    st.markdown("""
        <div style="text-align: center; padding: 0.5rem 0 1rem 0;">
            <h2 style="margin: 0; color: #1e293b; font-size: 1.4rem; font-weight: 800;">DISTROBILL</h2>
            <span style="font-size: 0.75rem; color: #64748b; font-weight: 600;">FMCG DISTRIBUTION SUITE</span>
        </div>
    """, unsafe_allow_html=True)
    
    nav_options = [
        "Dashboard", 
        "New Bill", 
        "Products", 
        "Shops / Customers", 
        "📦 Purchases", 
        "📊 Profit Analysis", 
        "Bill History", 
        "Settings & Backup"
    ]
    page_map = {
        "Dashboard": "Dashboard", 
        "New Bill": "New Bill", 
        "Products": "Products", 
        "Shops / Customers": "Shops", 
        "📦 Purchases": "Purchases",
        "📊 Profit Analysis": "Profit Analysis",
        "Bill History": "Bill History", 
        "Settings & Backup": "Settings"
    }
    
    current_index = 0
    for i, opt in enumerate(nav_options):
        if page_map[opt] == st.session_state.active_page:
            current_index = i
            break

    selected_nav = st.radio("MAIN MENU", nav_options, index=current_index, key="nav_radio")
    selected_page = page_map[selected_nav]
    
    if selected_page != st.session_state.active_page:
        st.session_state.active_page = selected_page
        st.session_state.view_bill_id = None
        st.session_state.edit_bill_id = None
        st.rerun()

    st.divider()

    cart_count = sum(item["qty"] for item in st.session_state.cart)
    cart_total = sum(item["qty"] * (item["base_rate"] + item.get("price_adjustment", 0.0)) for item in st.session_state.cart)
    st.markdown("**Current Cart Summary**")
    st.caption(f"Items in cart: **{cart_count} units**")
    st.caption(f"Cart Total: **{format_currency(cart_total)}**")
    if cart_count > 0:
        if st.button("Go to Billing Cart ➔", use_container_width=True, type="primary"):
            st.session_state.active_page = "New Bill"
            st.rerun()

    st.markdown("""
        <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 1.5rem;">
            DistroBill v2.5 (SQLite Persistent)<br>
            Database: Local SQLite Active
        </div>
    """, unsafe_allow_html=True)

st.markdown(f"""
    <div class="app-header">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <h1 class="app-title">{settings.get('business_name', 'Apex Distribution')}</h1>
                <div class="app-subtitle">{settings.get('tagline', '')} | GSTIN: {settings.get('gstin', '')}</div>
            </div>
            <div style="text-align: right; background: rgba(255,255,255,0.1); padding: 8px 16px; border-radius: 8px;">
                <span style="font-size: 0.8rem; color: #cbd5e1;">SYSTEM MODE</span><br>
                <strong style="font-size: 1rem; color: #38bdf8;">ACTIVE DB SESSION</strong>
            </div>
        </div>
    </div>
""", unsafe_allow_html=True)

if st.session_state.active_page == "Dashboard":
    st.subheader("Business Overview")
    
    conn = get_db_connection()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    today_bills = conn.execute("SELECT * FROM bills WHERE bill_date LIKE ?", (f"{today_str}%",)).fetchall()
    today_sales = sum(b["grand_total"] for b in today_bills)
    
    today_profit = 0.0
    for b in today_bills:
        items = conn.execute("SELECT * FROM bill_items WHERE bill_id = ?", (b["id"],)).fetchall()
        for it_row in items:
            it = dict(it_row)
            p_name = it["product_name_snapshot"]
            variant = it["variant_snapshot"]
            qty = it["quantity"]
            sell_rate = it["final_rate_snapshot"] if it.get("final_rate_snapshot") else it["distributor_rate_snapshot"]
            purch_rate = get_latest_purchase_rate(conn, p_name, variant)
            today_profit += (sell_rate - purch_rate) * qty

    active_prods = conn.execute("SELECT COUNT(*) FROM products WHERE is_active = 1").fetchone()[0]
    active_shops = conn.execute("SELECT COUNT(*) FROM shops WHERE is_active = 1").fetchone()[0]
    recent_bills = conn.execute("SELECT * FROM bills ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.markdown(f"""
            <div class="metric-box">
                <div class="metric-icon-box">📄</div>
                <div>
                    <div style="font-size:0.8rem; color:#64748b;">Today's Bills</div>
                    <div style="font-size:1.4rem; font-weight:700;">{len(today_bills)}</div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
            <div class="metric-box">
                <div class="metric-icon-box">💰</div>
                <div>
                    <div style="font-size:0.8rem; color:#64748b;">Today's Sales Total</div>
                    <div style="font-size:1.4rem; font-weight:700;">{format_currency(today_sales)}</div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
            <div class="metric-box">
                <div class="metric-icon-box">📈</div>
                <div>
                    <div style="font-size:0.8rem; color:#64748b;">Today's Profit</div>
                    <div style="font-size:1.4rem; font-weight:700; color:#16a34a;">{format_currency(today_profit)}</div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    with col4:
        st.markdown(f"""
            <div class="metric-box">
                <div class="metric-icon-box">📦</div>
                <div>
                    <div style="font-size:0.8rem; color:#64748b;">Active Products</div>
                    <div style="font-size:1.4rem; font-weight:700;">{active_prods}</div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    with col5:
        st.markdown(f"""
            <div class="metric-box">
                <div class="metric-icon-box">🏪</div>
                <div>
                    <div style="font-size:0.8rem; color:#64748b;">Active Shops</div>
                    <div style="font-size:1.4rem; font-weight:700;">{active_shops}</div>
                </div>
            </div>
        """, unsafe_allow_html=True)

    st.write("")

    if st.button("+ CREATE NEW BILL NOW", type="primary", use_container_width=True, key="dash_new_bill_btn"):
        st.session_state.active_page = "New Bill"
        st.rerun()

    st.subheader("Recent Bills")
    if recent_bills:
        df_data = []
        for b in recent_bills:
            df_data.append({
                "Invoice No": b["invoice_number"],
                "Date & Time": b["bill_date"],
                "Shop / Customer": b["shop_name_snapshot"],
                "Grand Total": format_currency(b["grand_total"])
            })
        st.dataframe(pd.DataFrame(df_data), use_container_width=True, hide_index=True)
    else:
        st.info("No bills recorded yet in SQLite database.")

elif st.session_state.active_page == "New Bill":
    st.subheader("Fast Billing Terminal")
    
    conn = get_db_connection()
    active_shops = conn.execute("SELECT * FROM shops WHERE is_active = 1 ORDER BY shop_name").fetchall()
    active_products = conn.execute("SELECT * FROM products WHERE is_active = 1 ORDER BY product_name, variant").fetchall()
    
    col_shop, col_new_shop = st.columns([3, 1])
    shop_options = ["Walk-in Customer / Cash Sale"] + [f"{s['shop_name']} ({s['phone'] or 'No Phone'})" for s in active_shops]
    
    with col_shop:
        selected_shop_str = st.selectbox("Select Shop / Customer:", shop_options, index=0)
        
    with col_new_shop:
        st.write("")
        st.write("")
        with st.popover("➕ Quick Add Shop"):
            st.markdown("#### Add New Retailer")
            q_name = st.text_input("Shop Name*")
            q_phone = st.text_input("Phone Number")
            q_address = st.text_input("Address")
            if st.button("Save Shop", type="primary"):
                if q_name.strip():
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn_q = get_db_connection()
                    conn_q.execute("""
                        INSERT INTO shops (shop_name, contact_person, phone, address, gstin, notes, is_active, created_at, updated_at)
                        VALUES (?, 'Quick Added', ?, ?, '', '', 1, ?, ?)
                    """, (q_name.strip(), q_phone.strip(), q_address.strip(), now, now))
                    conn_q.commit()
                    conn_q.close()
                    st.success(f"Shop '{q_name}' added!")
                    st.rerun()
                else:
                    st.error("Shop name is required.")

    selected_shop_obj = None
    adjustments_map = {}
    if selected_shop_str != "Walk-in Customer / Cash Sale":
        for s in active_shops:
            if f"{s['shop_name']} ({s['phone'] or 'No Phone'})" == selected_shop_str:
                selected_shop_obj = s
                break
        if selected_shop_obj:
            adj_rows = conn.execute("SELECT product_id, price_adjustment FROM shop_price_adjustments WHERE shop_id = ?", (selected_shop_obj["id"],)).fetchall()
            adjustments_map = {r["product_id"]: r["price_adjustment"] for r in adj_rows}

    conn.close()

    left_col, right_col = st.columns([1.2, 1.8])

    with left_col:
        st.markdown("### 1. Select Products")
        search_query = st.text_input("Search products...", placeholder="e.g. Surf Excel, 500g")
        
        filtered_products = [
            p for p in active_products 
            if search_query.lower() in p["product_name"].lower() or 
               search_query.lower() in p["variant"].lower() or 
               search_query.lower() in (p["sku"] or "").lower()
        ]

        if not filtered_products:
            st.warning("No matching active products found.")
        else:
            grid_cols = st.columns(2)
            for idx, prod in enumerate(filtered_products):
                col_target = grid_cols[idx % 2]
                base_rate = prod["distributor_rate"]
                default_shop_adj = adjustments_map.get(prod["id"], 0.0)
                card_bg = prod["bg_color"] if prod["bg_color"] else "#ffffff"

                with col_target:
                    st.markdown(f'<div class="product-card" style="background-color: {card_bg};">', unsafe_allow_html=True)
                    
                    img_p = prod["image_path"]
                    if img_p and os.path.exists(img_p):
                        st.image(img_p, use_container_width=True)
                    else:
                        st.markdown('<div class="img-placeholder">NO PACK IMAGE</div>', unsafe_allow_html=True)

                    st.markdown(f"""
                        <div class="product-title">{prod['product_name']}</div>
                        <span class="product-variant">Pack: {prod['variant']}</span>
                        <div style="margin-top: 0.2rem;">
                            <span class="mrp-tag">MRP {format_currency(prod['mrp'])}</span> | Base: <b>{format_currency(base_rate)}</b>
                        </div>
                    """, unsafe_allow_html=True)

                    st.button(
                        f"➕ Add 1", 
                        key=f"add1_{prod['id']}", 
                        on_click=add_to_cart_cb, 
                        args=(prod['id'], prod['product_name'], prod['variant'], prod['mrp'], base_rate, default_shop_adj),
                        use_container_width=True
                    )
                        
                    st.markdown("</div>", unsafe_allow_html=True)

    with right_col:
        st.markdown("### 2. Billing Cart")
        st.markdown("<div class='cart-container'>", unsafe_allow_html=True)
        
        if not st.session_state.cart:
            st.info("Cart is empty. Select products from left panel.")
        else:
            subtotal = 0.0
            
            h1, h2, h3, h4, h5 = st.columns([2.5, 1.5, 1.5, 2, 0.6])
            h1.markdown("**Product & Variant**")
            h2.markdown("**Qty**")
            h3.markdown("**Adj (₹)**")
            h4.markdown("**Amount**")
            h5.markdown("**Del**")
            st.divider()

            for i, item in enumerate(st.session_state.cart):
                c1, c2, c3, c4, c5 = st.columns([2.5, 1.5, 1.5, 2, 0.6])
                
                with c1:
                    st.markdown(f"**{item['name']}**<br><small>{item['variant']} | MRP: {format_currency(item['mrp'])} | Base: {format_currency(item['base_rate'])}</small>", unsafe_allow_html=True)
                
                with c2:
                    item['qty'] = st.number_input("Qty", min_value=1, value=int(item['qty']), key=f"q_{item['product_id']}", label_visibility="collapsed")
                    
                with c3:
                    item['price_adjustment'] = st.number_input("Adj", value=float(item.get('price_adjustment', 0.0)), step=1.0, key=f"adj_{item['product_id']}", label_visibility="collapsed")
                    
                final_rate = item['base_rate'] + item['price_adjustment']
                line_amt = final_rate * item['qty']
                subtotal += line_amt
                
                with c4:
                    st.markdown(f"**{format_currency(line_amt)}**")
                    st.caption(f"Rate: {format_currency(final_rate)}")
                    
                with c5:
                    if st.button("❌", key=f"del_{item['product_id']}"):
                        st.session_state.cart.pop(i)
                        st.rerun()

            st.divider()
            st.markdown(f"""
                <div class="cart-total-box">
                    <div style="font-size: 0.8rem; text-transform: uppercase; color: #94a3b8;">GRAND TOTAL DUE</div>
                    <div class="cart-total-amount">{format_currency(subtotal)}</div>
                </div>
            """, unsafe_allow_html=True)

            st.write("")
            col_gen, col_clr = st.columns([2, 1])
            with col_gen:
                if st.button("REVIEW & GENERATE BILL", type="primary", use_container_width=True):
                    st.session_state.show_review_modal = True
            with col_clr:
                if st.button("Clear Cart", use_container_width=True):
                    st.session_state.cart = []
                    st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state.show_review_modal and st.session_state.cart:
        st.divider()
        st.markdown("## Pre-Bill Review & Confirmation")
        
        prefix = settings.get("invoice_prefix", "INV")
        next_no = int(settings.get("next_invoice_no", "101"))
        inv_no = f"{prefix}-{next_no:06d}"
        curr_time = datetime.now().strftime("%Y-%m-%d %I:%M %p")

        st.markdown(f"**Invoice Number:** `{inv_no}` | **Date:** {curr_time}")
        if selected_shop_obj:
            st.markdown(f"**Bill To:** {selected_shop_obj['shop_name']} ({selected_shop_obj['phone']})")
        else:
            st.markdown("**Bill To:** Walk-in Customer / Cash Sale")

        review_rows = []
        for idx, item in enumerate(st.session_state.cart, 1):
            f_rate = item["base_rate"] + item["price_adjustment"]
            review_rows.append({
                "#": idx,
                "Product": item["name"],
                "Variant": item["variant"],
                "Qty": item["qty"],
                "MRP": format_currency(item["mrp"]),
                "Base Rate": format_currency(item["base_rate"]),
                "Price Adj": format_currency(item["price_adjustment"]),
                "Final Rate": format_currency(f_rate),
                "Amount": format_currency(item["qty"] * f_rate)
            })
        st.table(pd.DataFrame(review_rows))

        c_back, c_confirm = st.columns([1, 2])
        with c_back:
            if st.button("Back to Editing Cart", use_container_width=True):
                st.session_state.show_review_modal = False
                st.rerun()
        with c_confirm:
            if st.button("SAVE & GENERATE INVOICE", type="primary", use_container_width=True):
                conn = get_db_connection()
                try:
                    conn.execute("BEGIN TRANSACTION")
                    subtotal = sum(item["qty"] * (item["base_rate"] + item["price_adjustment"]) for item in st.session_state.cart)
                    
                    shop_name_snap = selected_shop_obj["shop_name"] if selected_shop_obj else "Walk-in Customer"
                    shop_phone_snap = selected_shop_obj["phone"] if selected_shop_obj else "N/A"
                    shop_addr_snap = selected_shop_obj["address"] if selected_shop_obj else "N/A"
                    shop_gstin_snap = selected_shop_obj["gstin"] if selected_shop_obj else "N/A"
                    shop_id_val = selected_shop_obj["id"] if selected_shop_obj else None

                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO bills (invoice_number, shop_id, shop_name_snapshot, shop_phone_snapshot, shop_address_snapshot, shop_gstin_snapshot, bill_date, subtotal, grand_total, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (inv_no, shop_id_val, shop_name_snap, shop_phone_snap, shop_addr_snap, shop_gstin_snap, curr_time, subtotal, subtotal, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    
                    bill_id = cursor.lastrowid

                    for item in st.session_state.cart:
                        f_rate = item["base_rate"] + item["price_adjustment"]
                        line_amt = item["qty"] * f_rate
                        cursor.execute("""
                            INSERT INTO bill_items (bill_id, product_id, product_name_snapshot, variant_snapshot, quantity, mrp_snapshot, distributor_rate_snapshot, price_adjustment_snapshot, final_rate_snapshot, amount)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (bill_id, item["product_id"], item["name"], item["variant"], item["qty"], item["mrp"], item["base_rate"], item["price_adjustment"], f_rate, line_amt))

                    cursor.execute("UPDATE settings SET value = ? WHERE key = 'next_invoice_no'", (str(next_no + 1),))

                    conn.commit()
                    
                    generate_pdf_invoice(bill_id)

                    st.session_state.cart = []
                    st.session_state.show_review_modal = False
                    st.session_state.view_bill_id = bill_id
                    st.session_state.active_page = "Bill History"
                    st.toast("Bill finalized and saved to SQLite!", icon="🎉")
                    st.rerun()

                except Exception as e:
                    conn.rollback()
                    st.error(f"Transaction failed: {str(e)}")
                finally:
                    conn.close()

elif st.session_state.active_page == "Products":
    st.subheader("Product & Pricing Catalog")
    
    tab_list, tab_add = st.tabs(["Catalog", "Add Product / Variant"])

    with tab_list:
        p_search = st.text_input("Filter catalog...", placeholder="Search product or category")
        conn = get_db_connection()
        products = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
        conn.close()

        filtered_p = [p for p in products if p_search.lower() in p["product_name"].lower() or p_search.lower() in p["variant"].lower()]

        for prod in filtered_p:
            status_symbol = "🟢" if prod["is_active"] else "🔴"
            with st.expander(f"{status_symbol} **{prod['product_name']}** ({prod['variant']}) — Rate: {format_currency(prod['distributor_rate'])} | MRP: {format_currency(prod['mrp'])}"):
                with st.form(key=f"edit_p_{prod['id']}"):
                    ec1, ec2, ec3 = st.columns(3)
                    with ec1:
                        ename = st.text_input("Name", value=prod["product_name"])
                        evariant = st.text_input("Variant", value=prod["variant"])
                        ecolor = st.color_picker("Card Background Color", value=prod["bg_color"] or "#ffffff")
                    with ec2:
                        emrp = st.number_input("MRP (₹)", value=float(prod["mrp"]), min_value=0.0, step=1.0)
                        erate = st.number_input("Base Distributor Rate (₹)", value=float(prod["distributor_rate"]), min_value=0.0, step=1.0)
                        esku = st.text_input("SKU", value=prod["sku"] or "")
                    with ec3:
                        eactive = st.checkbox("Active Status", value=bool(prod["is_active"]))
                        uploaded_img = st.file_uploader("Replace Pack Image", type=['jpg', 'jpeg', 'png', 'webp'], key=f"img_up_{prod['id']}")
                        remove_img = st.checkbox("Remove Current Image", key=f"rm_img_{prod['id']}")

                    if erate > emrp:
                        st.warning("Warning: Base rate exceeds MRP.")

                    if st.form_submit_button("Update Details"):
                        if ename.strip() and evariant.strip():
                            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            new_img_path = prod["image_path"]
                            
                            if remove_img:
                                if new_img_path and os.path.exists(new_img_path):
                                    try: os.remove(new_img_path)
                                    except: pass
                                new_img_path = None
                            elif uploaded_img is not None:
                                saved_p = save_uploaded_image(uploaded_img)
                                if saved_p: new_img_path = saved_p

                            conn = get_db_connection()
                            conn.execute("""
                                UPDATE products 
                                SET product_name = ?, variant = ?, mrp = ?, distributor_rate = ?, sku = ?, bg_color = ?, image_path = ?, is_active = ?, updated_at = ?
                                WHERE id = ?
                            """, (ename.strip(), evariant.strip(), emrp, erate, esku.strip(), ecolor, new_img_path, 1 if eactive else 0, now, prod["id"]))
                            conn.commit()
                            conn.close()
                            st.success("Product updated successfully!")
                            st.rerun()

                st.divider()
                cp1, cp2 = st.columns([2, 1])
                with cp1:
                    conf_p = st.checkbox("Confirm Product Deletion", key=f"chk_del_p_{prod['id']}")
                with cp2:
                    if st.button("🗑️ Delete Product", key=f"btn_del_p_{prod['id']}", use_container_width=True):
                        if conf_p:
                            delete_product_fully(prod["id"])
                            st.success(f"Product '{prod['product_name']}' deleted!")
                            st.rerun()
                        else:
                            st.warning("Check 'Confirm' box first")

    with tab_add:
        with st.form("add_product_main"):
            a_name = st.text_input("Product Name*")
            a_variant = st.text_input("Variant / Pack Size*")
            a_mrp = st.number_input("MRP (₹)*", min_value=0.0, value=100.0)
            a_rate = st.number_input("Base Distributor Rate (₹)*", min_value=0.0, value=80.0)
            a_sku = st.text_input("SKU Code")
            a_cat = st.text_input("Category", value="General")
            a_color = st.color_picker("Card Background Color", value="#ffffff")
            a_img = st.file_uploader("Upload Pack Image", type=['jpg', 'jpeg', 'png', 'webp'])

            if st.form_submit_button("Save Product to Catalog", type="primary"):
                if a_name.strip() and a_variant.strip():
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    img_path_saved = save_uploaded_image(a_img) if a_img else None
                    
                    conn = get_db_connection()
                    conn.execute("""
                        INSERT INTO products (product_name, variant, mrp, distributor_rate, sku, category, bg_color, image_path, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """, (a_name.strip(), a_variant.strip(), a_mrp, a_rate, a_sku.strip(), a_cat.strip(), a_color, img_path_saved, now, now))
                    conn.commit()
                    conn.close()
                    st.success("Product added successfully!")
                    st.rerun()
                else:
                    st.error("Name and Variant are required.")

elif st.session_state.active_page == "Shops":
    st.subheader("Retailer / Shop Directory & Pricing Rules")
    
    stab_list, stab_add = st.tabs(["Shops List", "Register New Shop"])

    with stab_list:
        conn = get_db_connection()
        shops = conn.execute("SELECT * FROM shops ORDER BY id DESC").fetchall()
        products = conn.execute("SELECT * FROM products WHERE is_active = 1 ORDER BY product_name").fetchall()

        for shop in shops:
            status_icon = "🟢" if shop["is_active"] else "🔴"
            with st.expander(f"{status_icon} **{shop['shop_name']}** — Owner: {shop['contact_person'] or 'N/A'} | 📱 {shop['phone'] or 'N/A'}"):
                with st.form(f"edit_shop_{shop['id']}"):
                    sc1, sc2 = st.columns(2)
                    with sc1:
                        es_name = st.text_input("Shop Name", value=shop["shop_name"])
                        es_owner = st.text_input("Contact Person", value=shop["contact_person"] or "")
                        es_phone = st.text_input("Phone", value=shop["phone"] or "")
                    with sc2:
                        es_addr = st.text_area("Address", value=shop["address"] or "")
                        es_gstin = st.text_input("GSTIN", value=shop["gstin"] or "")
                        es_active = st.checkbox("Active Account", value=bool(shop["is_active"]))

                    st.markdown("#### Shop-Specific Default Price Adjustments (₹ Fixed Amount)")
                    adj_rows = conn.execute("SELECT product_id, price_adjustment FROM shop_price_adjustments WHERE shop_id = ?", (shop["id"],)).fetchall()
                    adj_dict = {r["product_id"]: r["price_adjustment"] for r in adj_rows}

                    new_adj_values = {}
                    if products:
                        adj_cols = st.columns(2)
                        for idx, p in enumerate(products):
                            with adj_cols[idx % 2]:
                                curr_adj = adj_dict.get(p["id"], 0.0)
                                new_adj_values[p["id"]] = st.number_input(
                                    f"Adj for {p['product_name']} ({p['variant']}) Base: ₹{p['distributor_rate']}",
                                    value=float(curr_adj),
                                    step=1.0,
                                    key=f"adj_s{shop['id']}_p{p['id']}"
                                )

                    if st.form_submit_button("Save Shop Details & Price Rules"):
                        if es_name.strip():
                            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            conn.execute("""
                                UPDATE shops 
                                SET shop_name = ?, contact_person = ?, phone = ?, address = ?, gstin = ?, is_active = ?, updated_at = ?
                                WHERE id = ?
                            """, (es_name.strip(), es_owner.strip(), es_phone.strip(), es_addr.strip(), es_gstin.strip(), 1 if es_active else 0, now, shop["id"]))
                            
                            for p_id, adj_val in new_adj_values.items():
                                conn.execute("""
                                    INSERT INTO shop_price_adjustments (shop_id, product_id, price_adjustment, created_at, updated_at)
                                    VALUES (?, ?, ?, ?, ?)
                                    ON CONFLICT(shop_id, product_id) DO UPDATE SET price_adjustment = ?, updated_at = ?
                                """, (shop["id"], p_id, adj_val, now, now, adj_val, now))

                            conn.commit()
                            st.success("Shop details & custom pricing rules saved!")
                            st.rerun()

                st.divider()
                cs1, cs2 = st.columns([2, 1])
                with cs1:
                    conf_s = st.checkbox("Confirm Shop Deletion", key=f"chk_del_s_{shop['id']}")
                with cs2:
                    if st.button("🗑️ Delete Shop", key=f"btn_del_s_{shop['id']}", use_container_width=True):
                        if conf_s:
                            delete_shop_fully(shop["id"])
                            st.success(f"Shop '{shop['shop_name']}' deleted!")
                            st.rerun()
                        else:
                            st.warning("Check 'Confirm' box first")
        conn.close()

    with stab_add:
        with st.form("add_shop_main"):
            ns_name = st.text_input("Shop Name*")
            ns_owner = st.text_input("Owner Name")
            ns_phone = st.text_input("Phone Number")
            ns_addr = st.text_area("Address")
            ns_gstin = st.text_input("GSTIN")

            if st.form_submit_button("Register Shop", type="primary"):
                if ns_name.strip():
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn = get_db_connection()
                    conn.execute("""
                        INSERT INTO shops (shop_name, contact_person, phone, address, gstin, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """, (ns_name.strip(), ns_owner.strip(), ns_phone.strip(), ns_addr.strip(), ns_gstin.strip(), now, now))
                    conn.commit()
                    conn.close()
                    st.success("Shop registered successfully!")
                    st.rerun()

elif st.session_state.active_page == "Purchases":
    st.subheader("📦 Supplier Purchases (Owner Only)")
    
    ptab_list, ptab_add = st.tabs(["Purchase History", "+ Add Purchase Record"])

    with ptab_add:
        st.markdown("### Record Supplier Purchase")
        conn = get_db_connection()
        prod_rows = conn.execute("SELECT DISTINCT product_name, variant FROM products ORDER BY product_name, variant").fetchall()
        conn.close()

        prod_options = ["-- Select Product --"] + [f"{r['product_name']} - {r['variant']}" for r in prod_rows] + ["Custom / Other"]

        with st.form("add_purchase_form"):
            c1, c2 = st.columns(2)
            with c1:
                p_date = st.date_input("Purchase Date", value=datetime.now())
                s_name = st.text_input("Supplier Name*")
                s_phone = st.text_input("Supplier Phone (optional)")
                s_addr = st.text_area("Supplier Address (optional)")
            with c2:
                sel_prod = st.selectbox("Select Product & Variant", prod_options)
                p_name_val = ""
                p_var_val = ""
                if sel_prod == "Custom / Other":
                    p_name_val = st.text_input("Product Name*")
                    p_var_val = st.text_input("Variant / Pack Size*")
                elif sel_prod != "-- Select Product --":
                    parts = sel_prod.split(" - ")
                    p_name_val = parts[0]
                    p_var_val = parts[1]
                    st.info(f"Selected: **{p_name_val}** ({p_var_val})")

                qty_val = st.number_input("Quantity / Number of Pieces*", min_value=1, value=100)
                rate_val = st.number_input("Purchase Rate per Piece (₹)*", min_value=0.0, value=50.0, step=1.0)
                inv_ref = st.text_input("Invoice Number / Reference Number")
                notes_val = st.text_area("Notes (optional)")

            tot_calc = qty_val * rate_val
            st.markdown(f"### Total Purchase Amount: **{format_currency(tot_calc)}**")

            if st.form_submit_button("Save Purchase Record", type="primary"):
                if sel_prod == "-- Select Product --":
                    st.error("Select valid product or choose 'Custom / Other'")
                elif s_name.strip() and p_name_val.strip() and p_var_val.strip():
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    date_str = p_date.strftime("%Y-%m-%d")
                    
                    conn = get_db_connection()
                    prod_match = conn.execute(
                        "SELECT id FROM products WHERE LOWER(product_name) = LOWER(?) AND LOWER(variant) = LOWER(?)",
                        (p_name_val.strip(), p_var_val.strip())
                    ).fetchone()
                    p_id_val = prod_match["id"] if prod_match else 0

                    conn.execute("""
                        INSERT INTO purchases (product_id, purchase_date, supplier_name, supplier_phone, supplier_address, product_name, variant, quantity, purchase_rate, total_amount, invoice_ref, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (p_id_val, date_str, s_name.strip(), s_phone.strip(), s_addr.strip(), p_name_val.strip(), p_var_val.strip(), qty_val, rate_val, tot_calc, inv_ref.strip(), notes_val.strip(), now, now))

                    conn.commit()
                    conn.close()
                    st.success("Purchase record created successfully!")
                    st.rerun()
                else:
                    st.error("Supplier Name, Product Name, and Variant are required.")

    with ptab_list:
        conn = get_db_connection()
        purchases = conn.execute("SELECT * FROM purchases ORDER BY id DESC").fetchall()
        conn.close()

        if not purchases:
            st.info("No purchase records found.")
        else:
            p_search = st.text_input("Filter purchases...", placeholder="Search supplier, product or invoice ref")
            
            filtered_purch = []
            for p in purchases:
                row = dict(p)
                s_name = str(row.get("supplier_name", ""))
                p_name = str(row.get("product_name", ""))
                i_ref = str(row.get("invoice_ref", ""))
                
                if (p_search.lower() in s_name.lower() or 
                    p_search.lower() in p_name.lower() or 
                    p_search.lower() in i_ref.lower()):
                    filtered_purch.append(row)

            for pur in filtered_purch:
                with st.expander(f"📦 **{pur.get('product_name', '')}** ({pur.get('variant', '')}) — Qty: {pur.get('quantity', 0)} @ {format_currency(pur.get('purchase_rate', 0))} = {format_currency(pur.get('total_amount', 0))} | Supplier: {pur.get('supplier_name', '')} ({pur.get('purchase_date', '')})"):
                    with st.form(f"edit_pur_{pur.get('id')}"):
                        ec1, ec2 = st.columns(2)
                        with ec1:
                            ep_date = st.text_input("Purchase Date (YYYY-MM-DD)", value=str(pur.get("purchase_date", "")))
                            es_name = st.text_input("Supplier Name", value=str(pur.get("supplier_name", "")))
                            es_phone = st.text_input("Supplier Phone", value=str(pur.get("supplier_phone", "")))
                            es_addr = st.text_area("Supplier Address", value=str(pur.get("supplier_address", "")))
                        with ec2:
                            ep_name = st.text_input("Product Name", value=str(pur.get("product_name", "")))
                            ep_var = st.text_input("Variant", value=str(pur.get("variant", "")))
                            eqty = st.number_input("Quantity", min_value=1, value=int(pur.get("quantity", 1)))
                            erate = st.number_input("Purchase Rate (₹)", min_value=0.0, value=float(pur.get("purchase_rate", 0.0)))
                            einv_ref = st.text_input("Invoice Ref", value=str(pur.get("invoice_ref", "")))
                            enotes = st.text_area("Notes", value=str(pur.get("notes", "")))

                        etot = eqty * erate
                        st.caption(f"Recalculated Total: {format_currency(etot)}")

                        if st.form_submit_button("Update Purchase Record"):
                            if es_name.strip() and ep_name.strip() and ep_var.strip():
                                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                conn = get_db_connection()
                                prod_match = conn.execute(
                                    "SELECT id FROM products WHERE LOWER(product_name) = LOWER(?) AND LOWER(variant) = LOWER(?)",
                                    (ep_name.strip(), ep_var.strip())
                                ).fetchone()
                                p_id_val = prod_match["id"] if prod_match else 0
                                conn.execute("""
                                    UPDATE purchases
                                    SET product_id = ?, purchase_date = ?, supplier_name = ?, supplier_phone = ?, supplier_address = ?, product_name = ?, variant = ?, quantity = ?, purchase_rate = ?, total_amount = ?, invoice_ref = ?, notes = ?, updated_at = ?
                                    WHERE id = ?
                                """, (p_id_val, ep_date.strip(), es_name.strip(), es_phone.strip(), es_addr.strip(), ep_name.strip(), ep_var.strip(), eqty, erate, etot, einv_ref.strip(), enotes.strip(), now, pur["id"]))
                                conn.commit()
                                conn.close()
                                st.success("Purchase record updated!")
                                st.rerun()

                    st.divider()
                    cp1, cp2 = st.columns([2, 1])
                    with cp1:
                        chk_del = st.checkbox("Confirm Deletion", key=f"chk_pur_{pur['id']}")
                    with cp2:
                        if st.button("🗑️ Delete Purchase", key=f"del_pur_{pur['id']}", use_container_width=True):
                            if chk_del:
                                conn = get_db_connection()
                                conn.execute("DELETE FROM purchases WHERE id = ?", (pur["id"],))
                                conn.commit()
                                conn.close()
                                st.success("Purchase record deleted.")
                                st.rerun()
                            else:
                                st.warning("Check 'Confirm Deletion' box first.")

elif st.session_state.active_page == "Profit Analysis":
    st.subheader("📊 Profit Analysis & Revenue Intelligence")

    conn = get_db_connection()
    all_bills = conn.execute("SELECT * FROM bills ORDER BY id DESC").fetchall()

    if not all_bills:
        st.info("No generated bills available for profit analysis.")
        conn.close()
    else:
        bill_records = []
        for b in all_bills:
            b_dict = dict(b)
            items = conn.execute("SELECT * FROM bill_items WHERE bill_id = ?", (b_dict["id"],)).fetchall()
            b_rev = 0.0
            b_cost = 0.0
            for it_row in items:
                it = dict(it_row)
                p_name = it["product_name_snapshot"]
                variant = it["variant_snapshot"]
                qty = it["quantity"]
                sell_rate = it["final_rate_snapshot"] if it.get("final_rate_snapshot") else it["distributor_rate_snapshot"]
                purch_rate = get_latest_purchase_rate(conn, p_name, variant)
                b_rev += sell_rate * qty
                b_cost += purch_rate * qty
            b_profit = b_rev - b_cost
            
            dt_obj = None
            date_str = str(b_dict["bill_date"]).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %I:%M %p", "%Y-%m-%d"):
                try:
                    dt_obj = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    pass
            if not dt_obj:
                dt_obj = datetime.now()
                
            bill_records.append({
                "id": b_dict["id"],
                "invoice_number": b_dict["invoice_number"],
                "bill_date": dt_obj,
                "revenue": b_rev if b_rev > 0 else float(b_dict["grand_total"]),
                "cost": b_cost,
                "profit": b_profit
            })
            
        df_analytics = pd.DataFrame(bill_records)

        # 1. Top Finnova-Style KPI Summary Row
        total_rev_all = df_analytics["revenue"].sum()
        total_cost_all = df_analytics["cost"].sum()
        total_prof_all = df_analytics["profit"].sum()
        overall_margin = (total_prof_all / total_rev_all * 100) if total_rev_all > 0 else 0.0

        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(f"""
                <div class="zy-kpi-card">
                    <div class="zy-kpi-badge" style="background:#eff6ff; color:#2563eb;">💰</div>
                    <div class="zy-kpi-title">Total Revenue</div>
                    <div class="zy-kpi-val">{format_currency(total_rev_all)}</div>
                    <span class="zy-kpi-sub zy-sub-blue">Gross Billing</span>
                </div>
            """, unsafe_allow_html=True)
        with k2:
            st.markdown(f"""
                <div class="zy-kpi-card">
                    <div class="zy-kpi-badge" style="background:#fff7ed; color:#ea580c;">📦</div>
                    <div class="zy-kpi-title">Purchase Cost</div>
                    <div class="zy-kpi-val">{format_currency(total_cost_all)}</div>
                    <span class="zy-kpi-sub zy-sub-amber">Supplier Goods Outlay</span>
                </div>
            """, unsafe_allow_html=True)
        with k3:
            st.markdown(f"""
                <div class="zy-kpi-card">
                    <div class="zy-kpi-badge" style="background:#f0fdf4; color:#16a34a;">📈</div>
                    <div class="zy-kpi-title">Net Profit</div>
                    <div class="zy-kpi-val" style="color:#15803d;">{format_currency(total_prof_all)}</div>
                    <span class="zy-kpi-sub zy-sub-green">↑ Realized Earnings</span>
                </div>
            """, unsafe_allow_html=True)
        with k4:
            st.markdown(f"""
                <div class="zy-kpi-card">
                    <div class="zy-kpi-badge" style="background:#faf5ff; color:#9333ea;">📊</div>
                    <div class="zy-kpi-title">Profit Margin</div>
                    <div class="zy-kpi-val" style="color:#7e22ce;">{overall_margin:.1f}%</div>
                    <span class="zy-kpi-sub zy-sub-green">Target Margin ROI</span>
                </div>
            """, unsafe_allow_html=True)

        st.write("")

        # 2. Sleek Interactive Filter & Chart Section
        st.markdown('<div class="zy-filter-bar">', unsafe_allow_html=True)
        f_title, f_col1, f_col2 = st.columns([2, 1, 1])
        with f_title:
            st.markdown("#### 📈 Analytics & Trend Performance")
            st.caption("Filter breakdown by metric and time granularity")
        with f_col1:
            metric_choice = st.selectbox("Select Metric", ["Total Sales", "Total Profit"], key="profit_chart_metric")
        with f_col2:
            time_view = st.selectbox("View Granularity", ["Monthly", "Weekly", "Yearly"], index=0, key="profit_chart_granularity")
        st.markdown('</div>', unsafe_allow_html=True)

        if time_view == "Yearly":
            df_analytics["Period"] = df_analytics["bill_date"].dt.strftime("%Y")
        elif time_view == "Weekly":
            df_analytics["Period"] = df_analytics["bill_date"].dt.strftime("%Y-W%W")
        else:
            df_analytics["Period"] = df_analytics["bill_date"].dt.strftime("%Y-%m (%b)")

        grouped_df = df_analytics.groupby("Period", as_index=False)[["revenue", "cost", "profit"]].sum().sort_values("Period")
        y_col = "revenue" if metric_choice == "Total Sales" else "profit"
        
        chart_data = grouped_df.set_index("Period")[[y_col]]
        chart_data.columns = [metric_choice]
        
        if len(grouped_df) < 2:
            st.info("ℹ️ Only 1 time period recorded. Trend line requires at least 2 periods.")
            st.line_chart(chart_data, height=280, use_container_width=True)
        else:
            st.line_chart(chart_data, height=300, use_container_width=True)

        st.write("")

        # 3. Finnova Dark-Themed Invoice Profit Deep-Dive Section
        st.markdown('<div class="zy-dark-container">', unsafe_allow_html=True)
        st.markdown("<h3 style='color:#38bdf8; margin-bottom:0.2rem;'>🧾 Invoice-Level Profit Audit</h3>", unsafe_allow_html=True)
        st.markdown("<p style='color:#94a3b8; font-size:0.85rem;'>Inspect itemized purchase costs vs realized profit per invoice.</p>", unsafe_allow_html=True)

        bill_options = [f"{b['invoice_number']} — {b['shop_name_snapshot']} ({b['bill_date']}) - Total: ₹{b['grand_total']:,.2f}" for b in all_bills]
        selected_b_str = st.selectbox("Select Generated Bill to Analyze:", bill_options)
        
        sel_bill = None
        for b in all_bills:
            if f"{b['invoice_number']} — {b['shop_name_snapshot']} ({b['bill_date']}) - Total: ₹{b['grand_total']:,.2f}" == selected_b_str:
                sel_bill = b
                break

        if sel_bill:
            items = conn.execute("SELECT * FROM bill_items WHERE bill_id = ?", (sel_bill["id"],)).fetchall()
            
            st.markdown(f"#### Invoice: `<span style='color:#38bdf8;'>{sel_bill['invoice_number']}</span>` | Shop: **{sel_bill['shop_name_snapshot']}** | Date: **{sel_bill['bill_date']}**", unsafe_allow_html=True)

            analysis_data = []
            total_revenue = 0.0
            total_cost = 0.0

            for it_row in items:
                it = dict(it_row)
                p_name = it["product_name_snapshot"]
                variant = it["variant_snapshot"]
                qty = it["quantity"]
                sell_rate = it["final_rate_snapshot"] if it.get("final_rate_snapshot") else it["distributor_rate_snapshot"]
                
                purch_rate = get_latest_purchase_rate(conn, p_name, variant)
                
                revenue = sell_rate * qty
                cost = purch_rate * qty
                profit = revenue - cost

                total_revenue += revenue
                total_cost += cost

                analysis_data.append({
                    "Product": p_name,
                    "Variant": variant,
                    "Quantity": qty,
                    "Actual Selling Rate": format_currency(sell_rate),
                    "Purchase Rate": format_currency(purch_rate),
                    "Revenue": format_currency(revenue),
                    "Purchase Cost": format_currency(cost),
                    "Profit": format_currency(profit)
                })

            total_profit = total_revenue - total_cost
            margin_pct = (total_profit / total_revenue * 100) if total_revenue > 0 else 0.0

            st.dataframe(pd.DataFrame(analysis_data), use_container_width=True, hide_index=True)

            dm1, dm2, dm3, dm4 = st.columns(4)
            with dm1:
                st.metric("Revenue", format_currency(total_revenue))
            with dm2:
                st.metric("Purchase Cost", format_currency(total_cost))
            with dm3:
                st.metric("Net Profit", format_currency(total_profit))
            with dm4:
                st.metric("Margin %", f"{margin_pct:.2f}%")

            st.write("")

            profit_pdf_path = generate_profit_pdf(sel_bill["id"])
            if profit_pdf_path and os.path.exists(profit_pdf_path):
                with open(profit_pdf_path, "rb") as pdf_file:
                    st.download_button(
                        label="📥 Download Profit Report PDF (Owner Only)",
                        data=pdf_file,
                        file_name=os.path.basename(profit_pdf_path),
                        mime="application/pdf",
                        type="primary"
                    )

        st.markdown('</div>', unsafe_allow_html=True)

        conn.close()

elif st.session_state.active_page == "Bill History":
    st.subheader("Bill History & Invoice Archives")

    conn = get_db_connection()

    if st.session_state.edit_bill_id:
        bill_id = st.session_state.edit_bill_id
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        items = conn.execute("SELECT * FROM bill_items WHERE bill_id = ?", (bill_id,)).fetchall()
        
        if bill:
            st.markdown(f"### ✏️️ Editing Invoice: `{bill['invoice_number']}` ({bill['shop_name_snapshot']})")
            
            if "edit_cart" not in st.session_state or st.session_state.get("edit_bill_loaded") != bill_id:
                st.session_state.edit_cart = []
                for it_row in items:
                    it = dict(it_row)
                    rate_v = it["final_rate_snapshot"] if it.get("final_rate_snapshot") else it["distributor_rate_snapshot"]
                    st.session_state.edit_cart.append({
                        "product_id": it["product_id"],
                        "name": it["product_name_snapshot"],
                        "variant": it["variant_snapshot"],
                        "qty": it["quantity"],
                        "mrp": it["mrp_snapshot"],
                        "base_rate": it["distributor_rate_snapshot"],
                        "price_adjustment": it.get("price_adjustment_snapshot", 0.0),
                        "rate": rate_v
                    })
                st.session_state.edit_bill_loaded = bill_id

            st.markdown("#### Line Items")
            for i, it in enumerate(st.session_state.edit_cart):
                ec1, ec2, ec3, ec4, ec5 = st.columns([2, 1, 1, 1, 0.5])
                with ec1:
                    st.write(f"**{it['name']} ({it['variant']})**")
                with ec2:
                    it["qty"] = st.number_input("Qty", min_value=1, value=int(it["qty"]), key=f"eb_qty_{i}")
                with ec3:
                    it["rate"] = st.number_input("Rate (₹)", min_value=0.0, value=float(it["rate"]), key=f"eb_rate_{i}")
                with ec4:
                    st.write(f"**{format_currency(it['qty'] * it['rate'])}**")
                with ec5:
                    if st.button("❌", key=f"eb_del_{i}"):
                        st.session_state.edit_cart.pop(i)
                        st.rerun()

            e_subtotal = sum(it["qty"] * it["rate"] for it in st.session_state.edit_cart)
            st.markdown(f"### Updated Total: {format_currency(e_subtotal)}")

            col_esave, col_ecancel = st.columns([2, 1])
            with col_esave:
                if st.button("Save Edited Bill & Regenerate PDF", type="primary", use_container_width=True):
                    try:
                        conn.execute("BEGIN TRANSACTION")
                        conn.execute("UPDATE bills SET subtotal = ?, grand_total = ? WHERE id = ?", (e_subtotal, e_subtotal, bill_id))
                        conn.execute("DELETE FROM bill_items WHERE bill_id = ?", (bill_id,))
                        
                        for it in st.session_state.edit_cart:
                            conn.execute("""
                                INSERT INTO bill_items (bill_id, product_id, product_name_snapshot, variant_snapshot, quantity, mrp_snapshot, distributor_rate_snapshot, price_adjustment_snapshot, final_rate_snapshot, amount)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (bill_id, it["product_id"], it["name"], it["variant"], it["qty"], it["mrp"], it["base_rate"], it["price_adjustment"], it["rate"], it["qty"] * it["rate"]))
                            
                        conn.commit()
                        generate_pdf_invoice(bill_id)
                        st.session_state.edit_bill_id = None
                        st.session_state.view_bill_id = bill_id
                        st.success("Bill updated and PDF regenerated!")
                        st.rerun()
                    except Exception as ex:
                        conn.rollback()
                        st.error(f"Failed to edit bill: {str(ex)}")

            with col_ecancel:
                if st.button("Cancel Editing", use_container_width=True):
                    st.session_state.edit_bill_id = None
                    st.rerun()

        conn.close()

    elif st.session_state.view_bill_id:
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (st.session_state.view_bill_id,)).fetchone()
        items = conn.execute("SELECT * FROM bill_items WHERE bill_id = ?", (st.session_state.view_bill_id,)).fetchall()
        conn.close()

        if bill:
            col_b_back, col_b_edit = st.columns([2, 1])
            with col_b_back:
                if st.button("⬅️ Back to All Bills History"):
                    st.session_state.view_bill_id = None
                    st.rerun()
            with col_b_edit:
                if st.button("✏️ Edit This Bill", use_container_width=True):
                    st.session_state.edit_bill_id = bill['id']
                    st.rerun()

            st.divider()
            st.markdown(f"""
                <div class="invoice-box">
                    <div style="display: flex; justify-content: space-between; border-bottom: 2px solid #e2e8f0; padding-bottom: 1rem;">
                        <div>
                            <div style="font-size: 1.5rem; font-weight: 800;">{settings.get('business_name')}</div>
                            <div style="font-size: 0.85rem; color: #64748b;">{settings.get('business_address')}</div>
                        </div>
                        <div style="text-align: right;">
                            <div style="font-size: 1.2rem; font-weight: 800; color: #2563eb;">TAX INVOICE</div>
                            <div><strong>{bill['invoice_number']}</strong></div>
                            <div style="font-size: 0.85rem; color: #64748b;">Date: {bill['bill_date']}</div>
                        </div>
                    </div>
                    <div style="margin-top: 1rem;">
                        <strong>Billed To:</strong> {bill['shop_name_snapshot']}<br>
                        Phone: {bill['shop_phone_snapshot']} | Address: {bill['shop_address_snapshot']}
                    </div>
            """, unsafe_allow_html=True)

            item_data = []
            for idx, it_row in enumerate(items, 1):
                it = dict(it_row)
                rate_val = it["final_rate_snapshot"] if it.get("final_rate_snapshot") else it["distributor_rate_snapshot"]
                adj_v = it.get("price_adjustment_snapshot", 0.0)
                item_data.append({
                    "#": idx,
                    "Product & Pack": f"{it['product_name_snapshot']} ({it['variant_snapshot']})",
                    "Qty": it['quantity'],
                    "MRP": format_currency(it['mrp_snapshot']),
                    "Base": format_currency(it['distributor_rate_snapshot']),
                    "Adj": format_currency(adj_v),
                    "Final Rate": format_currency(rate_val),
                    "Amount": format_currency(it['amount'])
                })
            st.table(pd.DataFrame(item_data))

            st.markdown(f"""
                <div style="text-align: right; font-size: 1.4rem; font-weight: 800; color: #0f172a;">
                    Grand Total: {format_currency(bill['grand_total'])}
                </div>
                </div>
            """, unsafe_allow_html=True)

            st.write("")
            c_pdf, c_regen = st.columns(2)
            with c_pdf:
                if bill['pdf_path'] and os.path.exists(bill['pdf_path']):
                    with open(bill['pdf_path'], "rb") as pdf_file:
                        st.download_button(
                            label="📥 Download PDF Invoice",
                            data=pdf_file,
                            file_name=os.path.basename(bill['pdf_path']),
                            mime="application/pdf",
                            use_container_width=True
                        )
                else:
                    st.info("PDF file not generated yet.")
            with c_regen:
                if st.button("🔄 Regenerate PDF Snapshot", use_container_width=True):
                    pdf_path = generate_pdf_invoice(bill['id'])
                    if pdf_path:
                        st.success("PDF regenerated from historical snapshots!")
                        st.rerun()

    else:
        b_search = st.text_input("🔍 Search invoice number or shop name...", key="b_search")
        all_bills = conn.execute("SELECT * FROM bills ORDER BY id DESC").fetchall()
        conn.close()

        filtered = [
            b for b in all_bills 
            if b_search.lower() in b["invoice_number"].lower() or b_search.lower() in b["shop_name_snapshot"].lower()
        ]

        if not filtered:
            st.info("No matching invoice history records found.")
        else:
            for b in filtered:
                st.markdown("<div class='css-card'>", unsafe_allow_html=True)
                c1, c2, c3, c4, c5 = st.columns([1.5, 2, 1.5, 0.8, 1.2])
                with c1:
                    st.markdown(f"**{b['invoice_number']}**")
                    st.caption(b["bill_date"])
                with c2:
                    st.markdown(f"**{b['shop_name_snapshot']}**")
                with c3:
                    st.markdown(f"### {format_currency(b['grand_total'])}")
                with c4:
                    if st.button("👁️ View", key=f"v_{b['id']}"):
                        st.session_state.view_bill_id = b["id"]
                        st.rerun()
                with c5:
                    conf = st.checkbox("Confirm", key=f"chk_del_{b['id']}")
                    if st.button("🗑️ Delete", key=f"del_b_{b['id']}"):
                        if conf:
                            delete_bill_fully(b['id'])
                            st.success(f"Deleted {b['invoice_number']}")
                            st.rerun()
                        else:
                            st.warning("Check 'Confirm' box first")
                st.markdown("</div>", unsafe_allow_html=True)

elif st.session_state.active_page == "Settings":
    st.subheader("Business Settings & Database Backup")

    tab_set, tab_bak = st.tabs(["Business Profile", "Database Backup"])

    with tab_set:
        with st.form("settings_form_main"):
            sc1, sc2 = st.columns(2)
            with sc1:
                b_name = st.text_input("Business Name", value=settings.get("business_name", ""))
                b_tagline = st.text_input("Tagline", value=settings.get("tagline", ""))
                b_phone = st.text_input("Phone", value=settings.get("phone", ""))
                b_email = st.text_input("Email", value=settings.get("email", ""))
            with sc2:
                b_gstin = st.text_input("GSTIN", value=settings.get("gstin", ""))
                b_addr = st.text_area("Address", value=settings.get("business_address", ""))
                b_prefix = st.text_input("Invoice Prefix", value=settings.get("invoice_prefix", "INV"))
                b_footer = st.text_area("Invoice Footer Note", value=settings.get("invoice_footer", ""))

            if st.form_submit_button("Save Settings", type="primary"):
                update_settings({
                    "business_name": b_name.strip(),
                    "tagline": b_tagline.strip(),
                    "phone": b_phone.strip(),
                    "email": b_email.strip(),
                    "gstin": b_gstin.strip(),
                    "business_address": b_addr.strip(),
                    "invoice_prefix": b_prefix.strip(),
                    "invoice_footer": b_footer.strip()
                })
                st.success("Settings saved successfully!")
                st.rerun()

    with tab_bak:
        st.markdown("### Local SQLite Database Operations")
        st.info("Click below to create a timestamped backup copy of your database.")

        if st.button("CREATE DATABASE BACKUP NOW", type="primary"):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"backup_{timestamp}.db"
            backup_filepath = os.path.join(BACKUP_DIR, backup_filename)
            shutil.copy2(DB_PATH, backup_filepath)
            st.success(f"Database backup created: `{backup_filepath}`")

        st.divider()
        st.markdown("#### Available Backups")
        backup_files = [f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")]
        if backup_files:
            for bf in sorted(backup_files, reverse=True):
                bf_path = os.path.join(BACKUP_DIR, bf)
                with open(bf_path, "rb") as file_data:
                    st.download_button(
                        label=f"💾 Download {bf}",
                        data=file_data,
                        file_name=bf,
                        mime="application/x-sqlite3"
                    )
        else:
            st.caption("No backup files created yet.")
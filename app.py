from flask import Flask, render_template, request, jsonify
import sqlite3
import pandas as pd
import re
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "sales.db"
DATA_PATH = BASE_DIR / "data"
DATA_PATH.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_date TEXT NOT NULL,
    product TEXT NOT NULL,
    region TEXT NOT NULL,
    revenue REAL NOT NULL CHECK(revenue >= 0)
)
"""

SAMPLE_ROWS = [
    ("2025-01-05", "Laptop", "North", 1200.00),
    ("2025-01-12", "Phone", "South", 800.00),
    ("2025-01-18", "Headphones", "East", 150.00),
    ("2025-02-03", "Laptop", "West", 2400.00),
    ("2025-02-11", "Phone", "North", 1600.00),
    ("2025-02-21", "Monitor", "South", 450.00),
    ("2025-03-02", "Laptop", "East", 1200.00),
    ("2025-03-09", "Headphones", "West", 300.00),
    ("2025-03-19", "Phone", "South", 800.00),
    ("2025-03-25", "Monitor", "North", 900.00),
    ("2025-04-04", "Laptop", "North", 3600.00),
    ("2025-04-15", "Phone", "West", 1600.00),
]

COLUMN_ALIASES = {
     "date": ["date", "order date", "transaction date", "sales date", "order_date", "transaction_date"],
    "product": ["product", "product name", "item", "item name", "product_name"],
    "region": ["region", "sales region", "territory", "area", "sales_region"],
    "revenue": ["revenue", "sales", "amount", "total", "total sales", "sales amount", "revenue amount", "price"]
}

def get_connection(readonly=False):
    if readonly:
        return sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute(SCHEMA)
    count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    conn.close()
    if count == 0:
        source = BASE_DIR / "ai_bi_assistant_sales_data - GET.csv"
        if source.exists():
            with source.open("rb") as f:
                load_csv(f)
        else:
            conn = get_connection()
            conn.executemany(
                "INSERT INTO sales(order_date, product, region, revenue) VALUES (?, ?, ?, ?)",
                SAMPLE_ROWS
            )
            conn.commit()
            conn.close()

def normalize_header(value):
    return re.sub(r"\s+", " ", str(value).strip().lower().replace("_", " "))


def load_csv(file_storage):
    """Validate, clean and replace sales using either revenue or quantity × unit price − discount."""
    df = pd.read_csv(
    BASE_DIR / "ai_bi_assistant_sales_data - GET.csv"
)
    if df.empty:
        raise ValueError("The CSV has no data rows.")
    original_columns = {normalize_header(c): c for c in df.columns}

    def find_column(names):
        for name in names:
            if normalize_header(name) in original_columns:
                return original_columns[normalize_header(name)]
        return None

    date_col = find_column(["date", "order date", "transaction date", "sales date"])
    product_col = find_column(["product", "product name", "item"])
    region_col = find_column(["region", "sales region", "territory", "area"])
    revenue_col = find_column(["revenue", "sales", "amount", "total sales", "sales amount"])
    quantity_col = find_column(["quantity", "qty"])
    price_col = find_column(["unit price", "unit_price", "price per unit"])
    discount_col = find_column(["discount amount", "discount_amount", "discount"])
    status_col = find_column(["status", "order status"])

    if not all([date_col, product_col, region_col]):
        raise ValueError("CSV must contain date/order_date, product and region columns.")
    if revenue_col is None and not (quantity_col and price_col):
        raise ValueError("CSV must contain a revenue/sales column, or both Quantity and Unit_price.")

    dates = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
    product = df[product_col].astype("string").str.strip()
    region = df[region_col].astype("string").str.strip()

    if revenue_col:
        revenue = pd.to_numeric(df[revenue_col].astype(str).str.replace(r"[$,₹]", "", regex=True).str.strip(), errors="coerce")
    else:
        qty = pd.to_numeric(df[quantity_col], errors="coerce")
        unit_price = pd.to_numeric(df[price_col].astype(str).str.replace(r"[$,₹]", "", regex=True).str.strip(), errors="coerce")
        discount = pd.to_numeric(df[discount_col], errors="coerce").fillna(0) if discount_col else 0
        revenue = qty * unit_price - discount
    valid = dates.notna() & product.notna() & region.notna() & revenue.notna()
    valid &= (product.str.len() > 0) & (region.str.len() > 0) & (revenue >= 0)
    if quantity_col and not revenue_col:
        valid &= pd.to_numeric(df[quantity_col], errors="coerce") > 0
    if status_col:
        # Returned/cancelled transactions are not counted as realized sales.
        valid &= df[status_col].astype(str).str.strip().str.lower().isin(["completed", "complete", "delivered"])
    before = len(df)
    cleaned = pd.DataFrame({
        "order_date": dates[valid].dt.strftime("%Y-%m-%d"),
        "product": product[valid],
        "region": region[valid],
        "revenue": revenue[valid].astype(float)
    })
    removed = before - len(cleaned)
    if cleaned.empty:
        raise ValueError("No valid completed sales remained after cleaning. Check the CSV values.")
    conn = get_connection()
    conn.execute(SCHEMA)
    conn.execute("DELETE FROM sales")
    conn.executemany(
        "INSERT INTO sales(order_date, product, region, revenue) VALUES (?, ?, ?, ?)",
        list(cleaned[["order_date", "product", "region", "revenue"]].itertuples(index=False, name=None))
    )
    conn.commit()
    conn.close()
    return {"loaded": len(cleaned), "removed": removed}

def query_rows(sql, params=()):
    # Only developer-authored SELECT statements are sent to this read-only connection.
    conn = get_connection(readonly=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

def money(value):
    return f"{float(value or 0):,.2f}"

def month_candidates(question):
    """Recognize YYYY-MM, month names, or month-name plus year."""
    q = question.lower()
    found = re.findall(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])\b", q)
    months = [f"{int(y):04d}-{int(m):02d}" for y, m in found]
    month_names = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
        "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12
    }
    year_matches = re.findall(r"\b(20\d{2})\b", q)
    year = int(year_matches[0]) if year_matches else None
    for name, number in month_names.items():
        if re.search(rf"\b{name}\b", q):
            if year is not None:
                candidate = f"{year:04d}-{number:02d}"
                if candidate not in months:
                    months.append(candidate)
            else:
                rows = query_rows(
                    "SELECT DISTINCT substr(order_date,1,4) AS year FROM sales "
                    "WHERE CAST(substr(order_date,6,2) AS INTEGER)=? ORDER BY year",
                    (number,)
                )
                if len(rows) == 1:
                    candidate = f"{rows[0]['year']}-{number:02d}"
                    if candidate not in months:
                        months.append(candidate)
    return months

def answer_question(question):
    if not isinstance(question, str) or not question.strip():
        return {"error": "Please enter a question."}, 400
    if len(question) > 500:
        return {"error": "Question is too long. Keep it under 500 characters."}, 400

    q = question.lower().strip()
    if any(word in q for word in ["drop table", "delete from", "truncate", "update sales", "insert into", "alter table"]):
        return {"error": "Database-changing requests are not allowed. Ask a sales analytics question instead."}, 400

    if re.search(r"\b(total|overall|sum)\b", q) and re.search(r"\b(revenue|sales|income)\b", q):
        rows = query_rows("SELECT ROUND(SUM(revenue), 2) AS total_revenue, COUNT(*) AS transactions FROM sales")
        total = rows[0]["total_revenue"] or 0
        return {"answer": f"Total revenue is {money(total)} across {rows[0]['transactions']} valid transactions.",
                "intent": "total_revenue", "evidence": rows, "chart": None, "sql_label": "SUM(revenue), COUNT(*)"}, 200

    if re.search(r"\b(highest|top|best|most)\b", q) and "product" in q and re.search(r"\b(revenue|sales|selling|generated)\b", q):
        rows = query_rows("""SELECT product, ROUND(SUM(revenue),2) AS revenue
                             FROM sales GROUP BY product ORDER BY revenue DESC, product ASC LIMIT 10""")
        if not rows:
            return {"error": "No sales data is available."}, 400
        first = rows[0]
        return {"answer": f"{first['product']} generated the highest revenue: {money(first['revenue'])}.",
                "intent": "top_product", "evidence": rows, "chart": {"type":"bar","label":"Product revenue","x":"product","y":"revenue"}, "sql_label": "GROUP BY product, ORDER BY revenue DESC"}, 200

    if re.search(r"\b(region|territory|area)\b", q) and re.search(r"\b(highest|top|most|sales|revenue|generated)\b", q):
        rows = query_rows("""SELECT region, ROUND(SUM(revenue),2) AS revenue
                             FROM sales GROUP BY region ORDER BY revenue DESC, region ASC""")
        if not rows:
            return {"error": "No sales data is available."}, 400
        first = rows[0]
        return {"answer": f"{first['region']} generated the highest sales at {money(first['revenue'])}.",
                "intent": "top_region", "evidence": rows, "chart": {"type":"bar","label":"Revenue by region","x":"region","y":"revenue"}, "sql_label": "GROUP BY region, ORDER BY revenue DESC"}, 200

    if re.search(r"\b(change|changed|difference|compare|comparison|growth|increase|decrease)\b", q) and re.search(r"\b(month|revenue|sales|between|from)\b", q):
        months = month_candidates(q)
        if len(months) < 2:
            return {"error": "Please specify two months, for example: How did revenue change between 2025-01 and 2025-02?"}, 400
        months = months[:2]
        rows = query_rows("""SELECT substr(order_date,1,7) AS month, ROUND(SUM(revenue),2) AS revenue
                             FROM sales WHERE substr(order_date,1,7) IN (?,?)
                             GROUP BY substr(order_date,1,7)""", tuple(months))
        values = {r["month"]: float(r["revenue"] or 0) for r in rows}
        a, b = months
        first, second = values.get(a, 0.0), values.get(b, 0.0)
        difference = round(second - first, 2)
        pct = round((difference / first) * 100, 2) if first else None
        direction = "increased" if difference > 0 else ("decreased" if difference < 0 else "did not change")
        pct_text = f" ({abs(pct):.2f}%)" if pct is not None else " (percentage change unavailable because the first month's revenue is zero)"
        evidence = [{"month": a, "revenue": first}, {"month": b, "revenue": second},
                    {"change": difference, "percentage_change": pct}]
        return {"answer": f"Revenue {direction} by {money(abs(difference))} from {a} to {b}{pct_text}.",
                "intent": "month_comparison", "evidence": evidence,
                "chart": {"type":"bar","label":"Monthly revenue","x":"month","y":"revenue"},
                "sql_label": "Monthly SUM(revenue) filtered to the two requested months"}, 200

    if re.search(r"\b(monthly|month|trend|over time)\b", q) and re.search(r"\b(revenue|sales|trend|show|what|how)\b", q):
        rows = query_rows("""SELECT substr(order_date,1,7) AS month, ROUND(SUM(revenue),2) AS revenue
                             FROM sales GROUP BY substr(order_date,1,7) ORDER BY month""")
        if not rows:
            return {"error": "No sales data is available."}, 400
        return {"answer": f"Monthly revenue trend calculated for {len(rows)} month(s) from the database.",
                "intent": "monthly_trend", "evidence": rows,
                "chart": {"type":"line","label":"Monthly revenue","x":"month","y":"revenue"},
                "sql_label": "GROUP BY substr(order_date,1,7), ordered chronologically"}, 200

    return {"error": "I couldn't map that question to a supported sales analysis. Try total revenue, top product, monthly revenue trend, top region, or compare revenue between two months."}, 400

@app.route("/")
def index():
    return render_template("index.html")

@app.get("/api/summary")
def summary():
    try:
        total = query_rows("SELECT ROUND(SUM(revenue),2) AS revenue, COUNT(*) AS transactions FROM sales")[0]
        products = query_rows("SELECT COUNT(DISTINCT product) AS count FROM sales")[0]["count"]
        regions = query_rows("SELECT COUNT(DISTINCT region) AS count FROM sales")[0]["count"]
        trend = query_rows("""SELECT substr(order_date,1,7) AS month, ROUND(SUM(revenue),2) AS revenue
                              FROM sales GROUP BY substr(order_date,1,7) ORDER BY month""")
        return jsonify({"revenue": total["revenue"] or 0, "transactions": total["transactions"],
                        "products": products, "regions": regions, "trend": trend})
    except sqlite3.Error:
        app.logger.exception("Unable to read summary")
        return jsonify({"error": "Database is not ready. Restart the app and try again."}), 500

@app.post("/api/ask")
def ask():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Send a JSON object with a 'question' field."}), 400
    result, status = answer_question(body.get("question"))
    return jsonify(result), status

@app.post("/api/upload")
def upload():
    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "Choose a .csv file."}), 400
    try:
        result = load_csv(file)
        return jsonify({"message": "CSV imported successfully.", **result})
    except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("CSV upload failed")
        return jsonify({"error": "Import failed. Check the CSV format and try again."}), 500

init_db()


if __name__ == "__main__":
    app.run(debug=True, port=5000)

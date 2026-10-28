import io
import pytest
import app as sales_app

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(sales_app, "DB_PATH", tmp_path / "test_sales.db")
    sales_app.init_db()
    # Keep unit tests deterministic and independent from the company CSV.
    conn = sales_app.get_connection()
    conn.execute("DELETE FROM sales")
    conn.executemany(
        "INSERT INTO sales(order_date, product, region, revenue) VALUES (?, ?, ?, ?)",
        sales_app.SAMPLE_ROWS
    )
    conn.commit()
    conn.close()
    sales_app.app.config["TESTING"] = True
    with sales_app.app.test_client() as client:
        yield client

def test_total_revenue_returns_evidence(client):
    response = client.post("/api/ask", json={"question": "What is the total revenue?"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["intent"] == "total_revenue"
    assert data["evidence"][0]["transactions"] == len(sales_app.SAMPLE_ROWS)
    assert data["evidence"][0]["total_revenue"] > 0

def test_top_product(client):
    response = client.post("/api/ask", json={"question": "Which product generated the highest revenue?"})
    assert response.status_code == 200
    assert response.get_json()["intent"] == "top_product"
    assert response.get_json()["evidence"][0]["product"] == "Laptop"

def test_monthly_trend(client):
    response = client.post("/api/ask", json={"question": "What is the monthly revenue trend?"})
    assert response.status_code == 200
    assert response.get_json()["intent"] == "monthly_trend"
    assert len(response.get_json()["evidence"]) == 4

def test_invalid_question_is_rejected(client):
    response = client.post("/api/ask", json={"question": "What is the weather tomorrow?"})
    assert response.status_code == 400
    assert "error" in response.get_json()

def test_empty_question_is_rejected(client):
    response = client.post("/api/ask", json={"question": "   "})
    assert response.status_code == 400

def test_destructive_request_is_rejected(client):
    response = client.post("/api/ask", json={"question": "DROP TABLE sales"})
    assert response.status_code == 400

def test_month_comparison(client):
    response = client.post("/api/ask", json={"question": "How did revenue change between 2025-01 and 2025-02?"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["intent"] == "month_comparison"
    assert len(data["evidence"]) == 3

def test_csv_upload_drops_invalid_rows(client):
    csv_text = "Order Date,Product,Region,Sales\n2025-01-01,Book,North,100\nnot-a-date,Pen,South,20\n2025-01-03,Paper,East,not-money\n"
    response = client.post("/api/upload", data={"file": (io.BytesIO(csv_text.encode()), "sales.csv")},
                           content_type="multipart/form-data")
    assert response.status_code == 200
    assert response.get_json()["loaded"] == 1
    assert response.get_json()["removed"] == 2

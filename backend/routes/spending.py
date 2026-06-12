from fastapi import APIRouter, Query
from backend.database import connect
from typing import Optional
from datetime import date

router = APIRouter(prefix="/spending", tags=["spending"])

@router.get("/")
def get_spending(
    month: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    category: Optional[str] = None,
    source: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
):
    filters, params = ["amount < 0"], []

    if month and not start and not end:
        start = f"{month}-01"
        # last day of month: use strftime trick in SQL
        filters.append("strftime('%Y-%m', date) = ?")
        params.append(month)
    else:
        if start:
            filters.append("date >= ?")
            params.append(start)
        if end:
            filters.append("date <= ?")
            params.append(end)

    if category:
        filters.append("category = ?")
        params.append(category)
    if source:
        filters.append("source = ?")
        params.append(source)
    if min_amount is not None:
        filters.append("ABS(amount) >= ?")
        params.append(min_amount)
    if max_amount is not None:
        filters.append("ABS(amount) <= ?")
        params.append(max_amount)

    where = " AND ".join(filters)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM transactions WHERE {where} ORDER BY date DESC LIMIT 500",
            params,
        ).fetchall()
    return [dict(r) for r in rows]

@router.get("/summary")
def spending_summary(month: Optional[str] = None):
    if not month:
        month = date.today().strftime("%Y-%m")
    with connect() as conn:
        rows = conn.execute("""
            SELECT category, SUM(ABS(amount)) as total
            FROM transactions
            WHERE amount < 0 AND strftime('%Y-%m', date) = ?
            GROUP BY category
            ORDER BY total DESC
        """, [month]).fetchall()
    return [dict(r) for r in rows]

@router.get("/top-merchants")
def top_merchants(month: Optional[str] = None, limit: int = Query(10, ge=1, le=100)):
    if not month:
        month = date.today().strftime("%Y-%m")
    with connect() as conn:
        rows = conn.execute("""
            SELECT description, SUM(ABS(amount)) as total, COUNT(*) as count
            FROM transactions
            WHERE amount < 0 AND strftime('%Y-%m', date) = ?
            GROUP BY description
            ORDER BY total DESC
            LIMIT ?
        """, [month, limit]).fetchall()
    return [dict(r) for r in rows]

@router.get("/daily")
def daily_trend(month: Optional[str] = None):
    if not month:
        month = date.today().strftime("%Y-%m")
    with connect() as conn:
        rows = conn.execute("""
            SELECT date, SUM(ABS(amount)) as total
            FROM transactions
            WHERE amount < 0 AND strftime('%Y-%m', date) = ?
            GROUP BY date
            ORDER BY date
        """, [month]).fetchall()
    return [dict(r) for r in rows]

@router.get("/categories")
def get_categories():
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM transactions WHERE category IS NOT NULL ORDER BY category"
        ).fetchall()
    return [r["category"] for r in rows]

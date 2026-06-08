from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from datetime import date
from backend.database import connect

router = APIRouter(prefix="/limits", tags=["limits"])

class LimitIn(BaseModel):
    category: Optional[str] = None
    amount: float
    period: str = "monthly"

@router.get("/")
def get_limits():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM limits").fetchall()
    return [dict(r) for r in rows]

@router.post("/")
def set_limit(limit: LimitIn):
    with connect() as conn:
        conn.execute("""
            INSERT INTO limits (category, amount, period)
            VALUES (?, ?, ?)
            ON CONFLICT(category) DO UPDATE SET amount=excluded.amount, period=excluded.period
        """, [limit.category, limit.amount, limit.period])
    return {"status": "ok"}

@router.delete("/{limit_id}")
def delete_limit(limit_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM limits WHERE id = ?", [limit_id])
    return {"status": "ok"}

@router.get("/status")
def limits_status():
    month = date.today().strftime("%Y-%m")
    with connect() as conn:
        limits = conn.execute("SELECT * FROM limits").fetchall()
        result = []
        for lim in limits:
            lim = dict(lim)
            if lim["category"]:
                spent = conn.execute("""
                    SELECT COALESCE(SUM(ABS(amount)), 0) as total
                    FROM transactions
                    WHERE amount < 0 AND category = ? AND strftime('%Y-%m', date) = ?
                """, [lim["category"], month]).fetchone()["total"]
            else:
                spent = conn.execute("""
                    SELECT COALESCE(SUM(ABS(amount)), 0) as total
                    FROM transactions
                    WHERE amount < 0 AND strftime('%Y-%m', date) = ?
                """, [month]).fetchone()["total"]
            result.append({
                **lim,
                "spent": spent,
                "pct": round(spent / lim["amount"] * 100, 1) if lim["amount"] else 0,
            })
    return result

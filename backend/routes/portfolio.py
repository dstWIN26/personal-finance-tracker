from fastapi import APIRouter
from backend.database import connect

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

@router.get("/")
def get_portfolio():
    with connect() as conn:
        rows = conn.execute("""
            SELECT p.*
            FROM positions p
            INNER JOIN (
                SELECT isin, MAX(fetched_at) as latest
                FROM positions GROUP BY isin
            ) l ON p.isin = l.isin AND p.fetched_at = l.latest
        """).fetchall()
    positions = [dict(r) for r in rows]
    total_value = sum(p["quantity"] * (p["current_price"] or 0) for p in positions)
    total_pl = sum(p["pl_eur"] or 0 for p in positions)
    return {"positions": positions, "total_value": total_value, "total_pl": total_pl}

@router.get("/top")
def top_performers(n: int = 5):
    with connect() as conn:
        rows = conn.execute("""
            SELECT p.* FROM positions p
            INNER JOIN (
                SELECT isin, MAX(fetched_at) as latest
                FROM positions GROUP BY isin
            ) l ON p.isin = l.isin AND p.fetched_at = l.latest
        """).fetchall()
    positions = [dict(r) for r in rows]
    best = sorted(positions, key=lambda p: p["pl_pct"] or 0, reverse=True)[:n]
    worst = sorted(positions, key=lambda p: p["pl_pct"] or 0)[:n]
    return {"best": best, "worst": worst}

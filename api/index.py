"""
Fortune 500 Earnings Calendar -- FastAPI backend.

Serves JSON for the FullCalendar frontend.
On Vercel: static files served by Vercel CDN; only /api/* routes hit this function.
Locally:   StaticFiles mount at "/" makes `python run.py` self-contained.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from .database import companies_col, earnings_col, ensure_seeded

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fortune 500 Earnings Calendar",
    description="Earnings dates for Fortune 500 companies",
    version="2.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    ensure_seeded()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INDUSTRY_COLORS: dict[str, str] = {
    "Technology":         "#6366f1",
    "Healthcare":         "#10b981",
    "Financials":         "#f59e0b",
    "Energy":             "#ef4444",
    "Retail":             "#8b5cf6",
    "Consumer":           "#ec4899",
    "Automotive":         "#14b8a6",
    "Telecommunications": "#3b82f6",
    "Media":              "#f97316",
    "Industrial":         "#64748b",
    "Aerospace":          "#0ea5e9",
    "Logistics":          "#a855f7",
    "Real Estate":        "#84cc16",
    "default":            "#6b7280",
}


def _color_for(industry: str | None) -> str:
    for key, color in _INDUSTRY_COLORS.items():
        if key.lower() in (industry or "").lower():
            return color
    return _INDUSTRY_COLORS["default"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/api/companies")
def list_companies():
    """All companies -- used for ticker dropdown and industry filter."""
    tickers_with_earnings = set(earnings_col().distinct("ticker"))
    rows = companies_col().find(
        {},
        {"_id": 0, "ticker": 1, "name": 1, "industry": 1, "rank": 1},
    ).sort("rank", 1)
    return [
        {**doc, "has_earnings": doc["ticker"] in tickers_with_earnings}
        for doc in rows
    ]


@app.get("/api/earnings")
def list_earnings(
    start: Optional[str] = Query(None, description="ISO date, default today"),
    end: Optional[str] = Query(None, description="ISO date, default today+365"),
    ticker: Optional[str] = Query(None, description="Comma-separated tickers"),
    year: Optional[int] = Query(None, description="Fiscal year"),
    quarter: Optional[str] = Query(None, description="Q1 / Q2 / Q3 / Q4"),
    industry: Optional[str] = Query(None, description="Partial match on industry"),
):
    """
    Return earnings events formatted for FullCalendar.

    FullCalendar calls this with start/end on each calendar navigation.
    Additional filters narrow results for the sidebar filters.
    """
    today = date.today()
    start_date = start or today.isoformat()
    end_date = end or (today + timedelta(days=365)).isoformat()

    query: dict = {
        "date": {"$gte": start_date, "$lte": end_date},
    }

    if ticker:
        tickers = [t.strip().upper() for t in ticker.split(",") if t.strip()]
        if tickers:
            query["ticker"] = {"$in": tickers}

    if year:
        query["fiscal_year"] = year

    if quarter:
        query["quarter"] = quarter.upper()

    if industry:
        query["industry"] = {"$regex": industry, "$options": "i"}

    rows = earnings_col().find(query, {"_id": 0}).sort("date", 1)

    events = []
    for r in rows:
        title = r["ticker"]
        if r.get("quarter"):
            title += f" {r['quarter']}"

        color = _color_for(r.get("industry"))
        events.append({
            "id":              r["ticker"] + r["date"],
            "title":           title,
            "start":           r["date"],
            "backgroundColor": color,
            "borderColor":     color,
            "textColor":       "#ffffff",
            "extendedProps": {
                "ticker":       r["ticker"],
                "company":      r.get("company_name") or r["ticker"],
                "quarter":      r.get("quarter"),
                "fiscal_year":  r.get("fiscal_year"),
                "eps_estimate": r.get("eps_estimate"),
                "actual_eps":   r.get("actual_eps"),
                "industry":     r.get("industry"),
                "rank":         r.get("company_rank"),
                "source":       r.get("source"),
            },
        })

    return events


@app.get("/api/industries")
def list_industries():
    """Distinct industries for the filter dropdown."""
    return sorted(i for i in companies_col().distinct("industry") if i)


# ---------------------------------------------------------------------------
# Static files (local dev only -- Vercel CDN handles this in production)
# ---------------------------------------------------------------------------

_public_dir = Path(__file__).parent.parent / "public"
if _public_dir.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_public_dir), html=True), name="static")

# Vercel serverless entry-point
handler = Mangum(app, lifespan="off")

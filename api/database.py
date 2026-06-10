"""
MongoDB helpers for Fortune 500 Earnings Calendar.
Replaces the SQLite implementation.

Collections:
  companies  -- one doc per Fortune 500 company
  earnings   -- one doc per (ticker, report date); TTL index auto-purges past dates
"""

import csv
import hashlib
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

try:
    from pymongo import ASCENDING, MongoClient, UpdateOne
    from pymongo.collection import Collection
    from pymongo.database import Database
except ImportError as exc:
    raise ImportError("pymongo is required: pip install 'pymongo[srv]'") from exc

logger = logging.getLogger(__name__)

SEED_CSV = Path(__file__).parent.parent / "data" / "fortune500_seed.csv"

# ---------------------------------------------------------------------------
# Industry timing + EPS seed data (unchanged from SQLite version)
# ---------------------------------------------------------------------------

_INDUSTRY_OFFSETS = {
    "Financials":               30,
    "Technology":               42,
    "Healthcare":               44,
    "Healthcare Distribution":  40,
    "Energy":                   38,
    "Retail":                   46,
    "Consumer Staples":         43,
    "Consumer Discretionary":   45,
    "Automotive":               42,
    "Telecommunications":       41,
    "Media & Telecom":          42,
    "Media & Entertainment":    43,
    "Industrial":               44,
    "Industrial & Agriculture": 47,
    "Industrial Conglomerate":  44,
    "Aerospace & Defense":      45,
    "Logistics":                46,
    "Real Estate":              50,
    "default":                  42,
}

_QUARTER_ENDS = {
    "Q1_2025": date(2025, 3, 31),
    "Q2_2025": date(2025, 6, 30),
    "Q3_2025": date(2025, 9, 30),
    "Q4_2025": date(2025, 12, 31),
    "Q1_2026": date(2026, 3, 31),
    "Q2_2026": date(2026, 6, 30),
    "Q3_2026": date(2026, 9, 30),
    "Q4_2026": date(2026, 12, 31),
}

_QUARTER_LABELS = {
    "Q1_2025": ("Q1", 2025),
    "Q2_2025": ("Q2", 2025),
    "Q3_2025": ("Q3", 2025),
    "Q4_2025": ("Q4", 2025),
    "Q1_2026": ("Q1", 2026),
    "Q2_2026": ("Q2", 2026),
    "Q3_2026": ("Q3", 2026),
    "Q4_2026": ("Q4", 2026),
}

_INDUSTRY_EPS = {
    "Technology":  (1.20, 8.50),
    "Financials":  (2.50, 5.00),
    "Healthcare":  (0.80, 4.50),
    "Energy":      (1.50, 6.00),
    "Retail":      (0.40, 3.50),
    "default":     (0.50, 3.00),
}

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_client: Optional[MongoClient] = None


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        uri = os.environ.get("MONGODB_URI")
        if not uri:
            raise EnvironmentError(
                "MONGODB_URI is not set. "
                "Add it to your .env file locally or to Vercel Environment Variables in production."
            )
        _client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    return _client


def _get_db() -> Database:
    db_name = os.environ.get("MONGODB_DB", "fortune500")
    return _get_client()[db_name]


def companies_col() -> Collection:
    return _get_db()["companies"]


def earnings_col() -> Collection:
    return _get_db()["earnings"]

# ---------------------------------------------------------------------------
# Schema / indexes
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Create indexes (idempotent -- safe to call on every startup)."""
    col_c = companies_col()
    col_c.create_index("ticker", unique=True, name="unique_ticker")
    col_c.create_index("rank", name="idx_rank")

    col_e = earnings_col()
    # Prevent duplicate (ticker, date) pairs
    col_e.create_index(
        [("ticker", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="unique_ticker_date",
    )
    col_e.create_index("date", name="idx_date")
    col_e.create_index("fiscal_year", name="idx_fiscal_year")
    col_e.create_index("industry", name="idx_industry")
    # TTL: MongoDB auto-deletes documents once date_dt is in the past
    col_e.create_index(
        "date_dt",
        expireAfterSeconds=0,
        name="ttl_date_dt",
    )
    # Track when Yahoo Finance was last queried per company (used to skip re-fetches)
    col_c.create_index("last_yahoo_fetch", sparse=True, name="idx_last_yahoo_fetch")
    logger.info("MongoDB indexes ensured.")


def is_empty() -> bool:
    try:
        return companies_col().count_documents({}) == 0
    except Exception:
        return True

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _next_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _company_hash_offset(ticker: str, spread: int = 10) -> int:
    h = int(hashlib.md5(ticker.encode()).hexdigest()[:4], 16)
    return h % (spread + 1)


def _make_earnings_for(ticker: str, industry: str) -> list[dict]:
    base_offset = _INDUSTRY_OFFSETS.get(industry, _INDUSTRY_OFFSETS["default"])
    total_offset = base_offset + _company_hash_offset(ticker)

    lo, hi = _INDUSTRY_EPS["default"]
    for key, (lo_v, hi_v) in _INDUSTRY_EPS.items():
        if key.lower() in (industry or "").lower():
            lo, hi = lo_v, hi_v
            break

    eps = (_company_hash_offset(ticker + "eps", spread=20) / 20) * (hi - lo) + lo

    rows = []
    for qkey, qend in _QUARTER_ENDS.items():
        announce = _next_weekday(qend + timedelta(days=total_offset))
        label, fy = _QUARTER_LABELS[qkey]
        rows.append({
            "ticker":       ticker,
            "date":         announce.isoformat(),
            # UTC midnight datetime used by the TTL index
            "date_dt":      datetime(announce.year, announce.month, announce.day, tzinfo=timezone.utc),
            "quarter":      label,
            "fiscal_year":  fy,
            "eps_estimate": round(eps, 2),
            "actual_eps":   None,
            "source":       "seed",
        })
    return rows


def seed_from_csv() -> None:
    """Load Fortune 500 companies and generate synthetic earnings from the seed CSV."""
    if not SEED_CSV.exists():
        logger.warning("Seed CSV not found at %s", SEED_CSV)
        return

    companies = []
    with open(SEED_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            companies.append(row)

    now = datetime.now(timezone.utc)

    # Upsert companies
    company_ops = [
        UpdateOne(
            {"ticker": c["ticker"]},
            {"$set": {
                "ticker":           c["ticker"],
                "name":             c["name"],
                "rank":             int(c["rank"]),
                "industry":         c.get("industry"),
                "revenue_billions": float(c.get("revenue_billions") or 0),
                "last_updated":     now,
            }},
            upsert=True,
        )
        for c in companies
    ]
    if company_ops:
        companies_col().bulk_write(company_ops, ordered=False)

    # Upsert earnings (denormalized with company fields for fast querying)
    earnings_ops = []
    for c in companies:
        for row in _make_earnings_for(c["ticker"], c.get("industry", "")):
            doc = {
                **row,
                "company_name":     c["name"],
                "company_rank":     int(c["rank"]),
                "industry":         c.get("industry"),
                "revenue_billions": float(c.get("revenue_billions") or 0),
                "last_fetched":     now,
            }
            earnings_ops.append(
                UpdateOne(
                    {"ticker": row["ticker"], "date": row["date"]},
                    {"$setOnInsert": doc},
                    upsert=True,
                )
            )

    if earnings_ops:
        earnings_col().bulk_write(earnings_ops, ordered=False)

    logger.info("Seeded %d companies with synthetic earnings.", len(companies))


def ensure_seeded() -> None:
    """Initialize indexes and seed if the DB is empty (called on every startup)."""
    init_db()
    if is_empty() or os.environ.get("FORCE_RESEED") == "true":
        logger.info("DB is empty — attempting live S&P 500 population from slickcharts...")
        try:
            from scripts.update_companies import update_companies
            update_companies()
            logger.info("Live population complete.")
        except Exception as exc:
            logger.warning("Live population failed (%s); falling back to seed CSV.", exc)
            seed_from_csv()

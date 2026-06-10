"""
MongoDB helpers for S&P 500 Earnings Calendar.

Collections:
  companies  -- one doc per S&P 500 company
  earnings   -- one doc per (ticker, report date) from Yahoo Finance only
"""

import csv
import logging
import os
from datetime import datetime, timezone
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

SEED_CSV = Path(__file__).parent.parent / "data" / "sp500_seed.csv"

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
    db_name = os.environ.get("MONGODB_DB", "sp500")
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
    col_c.create_index("last_yahoo_fetch", sparse=True, name="idx_last_yahoo_fetch")

    col_e = earnings_col()
    col_e.create_index(
        [("ticker", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="unique_ticker_date",
    )
    col_e.create_index("date", name="idx_date")
    col_e.create_index("fiscal_year", name="idx_fiscal_year")
    col_e.create_index("industry", name="idx_industry")
    col_e.create_index(
        "date_dt",
        expireAfterSeconds=0,
        name="ttl_date_dt",
    )
    logger.info("MongoDB indexes ensured.")


def is_empty() -> bool:
    try:
        return companies_col().count_documents({}) == 0
    except Exception:
        return True

# ---------------------------------------------------------------------------
# Company seeding (companies only — earnings come exclusively from Yahoo Finance)
# ---------------------------------------------------------------------------


def seed_from_csv() -> None:
    """Upsert companies from the seed CSV. No earnings are created."""
    if not SEED_CSV.exists():
        logger.warning("Seed CSV not found at %s", SEED_CSV)
        return

    companies = []
    with open(SEED_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            companies.append(row)

    now = datetime.now(timezone.utc)
    ops = [
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
    if ops:
        companies_col().bulk_write(ops, ordered=False)

    logger.info("Seeded %d companies from CSV (no synthetic earnings).", len(companies))


def ensure_seeded() -> None:
    """Initialize indexes and populate companies if the DB is empty."""
    init_db()
    if is_empty() or os.environ.get("FORCE_RESEED") == "true":
        logger.info("DB is empty — attempting live S&P 500 population from slickcharts...")
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent.parent))
            from scripts.update_companies import update_companies
            update_companies()
            logger.info("Live population complete.")
        except Exception as exc:
            logger.warning("Live population failed (%s); falling back to seed CSV.", exc)
            seed_from_csv()

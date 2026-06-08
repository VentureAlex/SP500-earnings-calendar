"""
MongoDB client for Fortune 500 earnings calendar dates.

Collection: earnings
TTL index on report_date (expireAfterSeconds=0) automatically purges
documents the moment their report_date passes midnight UTC.
"""

import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from pymongo import MongoClient, ASCENDING, UpdateOne
    from pymongo.collection import Collection
    from pymongo.errors import ConnectionFailure, OperationFailure
except ImportError as exc:
    raise ImportError("pymongo is required: pip install 'pymongo[srv]'") from exc

_client: Optional[MongoClient] = None


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        uri = os.environ.get("MONGODB_URI")
        if not uri:
            raise EnvironmentError(
                "MONGODB_URI environment variable is not set. "
                "Add it to your .env file (never commit the actual URI)."
            )
        _client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    return _client


def get_collection() -> Collection:
    client = _get_client()
    db_name = os.environ.get("MONGODB_DB", "fortune500")
    return client[db_name]["earnings"]


def ensure_indexes() -> None:
    """Create indexes once on startup. Safe to call repeatedly (idempotent)."""
    col = get_collection()

    # TTL index: MongoDB auto-deletes documents when report_date < now
    col.create_index(
        [("report_date", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_report_date",
    )

    # Unique compound index prevents duplicate (ticker, report_date) entries
    col.create_index(
        [("ticker", ASCENDING), ("report_date", ASCENDING)],
        unique=True,
        name="unique_ticker_report_date",
    )

    print("MongoDB indexes ensured.")


def upsert_earnings(rows: List[Dict[str, Any]]) -> int:
    """
    Upsert a list of earnings records. Returns the number of upserted/modified docs.

    Each row must contain:
      ticker        str     e.g. "AAPL"
      report_date   str     YYYY-MM-DD  (converted to UTC datetime for TTL)
      quarter       str     "Q1" | "Q2" | "Q3" | "Q4"
      fiscal_year   int
    Optional:
      company_name  str
      company_rank  int
      industry      str
      eps_estimate  float
      actual_eps    float
      source        str
    """
    if not rows:
        return 0

    col = get_collection()
    ops = []
    now = datetime.now(timezone.utc)

    for row in rows:
        ticker = row.get("ticker", "").upper()
        report_date_str = row.get("report_date", "")
        if not ticker or not report_date_str:
            continue

        # Store as UTC midnight datetime so MongoDB TTL index can act on it
        try:
            report_dt = datetime.fromisoformat(report_date_str[:10]).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue

        doc = {
            "ticker": ticker,
            "report_date": report_dt,
            "quarter": row.get("quarter"),
            "fiscal_year": row.get("fiscal_year"),
            "company_name": row.get("company_name"),
            "company_rank": row.get("company_rank"),
            "industry": row.get("industry"),
            "eps_estimate": row.get("eps_estimate"),
            "actual_eps": row.get("actual_eps"),
            "source": row.get("source", "unknown"),
            "updated_at": now,
        }

        ops.append(
            UpdateOne(
                {"ticker": ticker, "report_date": report_dt},
                {"$set": doc},
                upsert=True,
            )
        )

    if not ops:
        return 0

    result = col.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


def get_upcoming_earnings() -> List[Dict[str, Any]]:
    """Return earnings dates from today onward, sorted by report_date."""
    col = get_collection()
    now = datetime.now(timezone.utc)
    cursor = col.find(
        {"report_date": {"$gte": now}},
        {"_id": 0},
    ).sort("report_date", ASCENDING)
    results = []
    for doc in cursor:
        doc["report_date"] = doc["report_date"].strftime("%Y-%m-%d")
        results.append(doc)
    return results


def prune_past_earnings() -> int:
    """
    Manually delete earnings whose report_date is in the past.
    Belt-and-suspenders: the TTL index handles this automatically,
    but the TTL background task runs only every ~60 seconds.
    """
    col = get_collection()
    now = datetime.now(timezone.utc)
    result = col.delete_many({"report_date": {"$lt": now}})
    return result.deleted_count


def stats() -> Dict[str, Any]:
    col = get_collection()
    total = col.count_documents({})
    upcoming = col.count_documents({"report_date": {"$gte": datetime.now(timezone.utc)}})
    return {"total": total, "upcoming": upcoming, "past_pending_ttl": total - upcoming}

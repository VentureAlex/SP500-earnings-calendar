#!/usr/bin/env python3
"""
Sync earnings calendar dates from SQLite to MongoDB.

Run after the database has been seeded (via api/database.py ensure_seeded),
or after scripts/fetch_earnings.py has pulled live data.

Usage:
    python scripts/sync_to_mongo.py          # sync all upcoming earnings
    python scripts/sync_to_mongo.py --prune  # also manually trigger prune
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

# Allow importing api/ from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.database import get_db
from scripts.mongo_db import ensure_indexes, upsert_earnings, prune_past_earnings, stats


def load_earnings_from_sqlite() -> list:
    """Read upcoming earnings from SQLite joined with company metadata."""
    today = datetime.now(timezone.utc).date().isoformat()
    rows = []

    with get_db() as conn:
        cur = conn.execute(
            """
            SELECT e.ticker, e.date AS report_date, e.quarter, e.fiscal_year,
                   e.eps_estimate, e.actual_eps, e.source,
                   c.name AS company_name, c.rank AS company_rank, c.industry
            FROM earnings e
            JOIN companies c ON c.id = e.company_id
            WHERE e.date >= ?
            ORDER BY e.date
            """,
            (today,),
        )
        for row in cur.fetchall():
            rows.append(
                {
                    "ticker": row["ticker"],
                    "report_date": row["report_date"],
                    "quarter": row["quarter"],
                    "fiscal_year": row["fiscal_year"],
                    "eps_estimate": row["eps_estimate"],
                    "actual_eps": row["actual_eps"],
                    "source": row["source"],
                    "company_name": row["company_name"],
                    "company_rank": row["company_rank"],
                    "industry": row["industry"],
                }
            )
    return rows


def main():
    parser = argparse.ArgumentParser(description="Sync earnings to MongoDB")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Manually prune past earnings (TTL index handles this automatically)",
    )
    args = parser.parse_args()

    print("=== Syncing earnings to MongoDB ===")

    print("Ensuring MongoDB indexes...")
    ensure_indexes()

    if args.prune:
        deleted = prune_past_earnings()
        print(f"Pruned {deleted} past earnings documents.")

    print("Loading upcoming earnings from SQLite...")
    rows = load_earnings_from_sqlite()
    print(f"  Found {len(rows)} upcoming earnings rows.")

    if rows:
        changed = upsert_earnings(rows)
        print(f"  Upserted/updated {changed} documents in MongoDB.")
    else:
        print("  Nothing to sync.")

    s = stats()
    print(
        f"\nMongoDB earnings collection: "
        f"{s['upcoming']} upcoming, "
        f"{s['past_pending_ttl']} past (pending TTL cleanup), "
        f"{s['total']} total."
    )
    print("Done.")


if __name__ == "__main__":
    main()

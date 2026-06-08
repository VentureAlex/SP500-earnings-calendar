#!/usr/bin/env python3
"""
MongoDB maintenance script for Fortune 500 Earnings Calendar.

Seeds the database from fortune500_seed.csv (if empty) and optionally
prunes stale past-earnings documents ahead of the automatic TTL cleanup.

Usage:
    python scripts/sync_to_mongo.py           # seed if empty, show stats
    python scripts/sync_to_mongo.py --prune   # also delete past earnings now
    python scripts/sync_to_mongo.py --reseed  # force full re-seed
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow importing api/ from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.database import earnings_col, ensure_seeded, is_empty, seed_from_csv


def prune_past_earnings() -> int:
    now = datetime.now(timezone.utc)
    result = earnings_col().delete_many({"date_dt": {"$lt": now}})
    return result.deleted_count


def show_stats() -> None:
    col = earnings_col()
    now = datetime.now(timezone.utc)
    total = col.count_documents({})
    upcoming = col.count_documents({"date_dt": {"$gte": now}})
    past = total - upcoming
    print(f"\nMongoDB earnings: {upcoming} upcoming, {past} past (pending TTL), {total} total.")


def main():
    parser = argparse.ArgumentParser(description="MongoDB maintenance for earnings calendar")
    parser.add_argument("--prune", action="store_true", help="Delete past earnings immediately")
    parser.add_argument("--reseed", action="store_true", help="Force full re-seed from CSV")
    args = parser.parse_args()

    if args.reseed:
        os.environ["FORCE_RESEED"] = "true"

    print("=== Fortune 500 Earnings -- MongoDB maintenance ===")
    ensure_seeded()

    if args.prune:
        deleted = prune_past_earnings()
        print(f"Pruned {deleted} past earnings documents.")

    show_stats()
    print("Done.")


if __name__ == "__main__":
    main()

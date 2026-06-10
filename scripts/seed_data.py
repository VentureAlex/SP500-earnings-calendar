#!/usr/bin/env python3
"""
Seed the database with S&P 500 companies.

Tries slickcharts.com first; falls back to the local sp500_seed.csv
if the network is unavailable.

Earnings come exclusively from Yahoo Finance (run fetch_earnings.py after this).

Run: python scripts/seed_data.py [--force]
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Seed the companies collection")
    parser.add_argument("--force", action="store_true", help="Re-seed even if data exists")
    args = parser.parse_args()

    from api.database import init_db, is_empty, companies_col, earnings_col

    init_db()

    if not is_empty() and not args.force:
        print("Database already has companies. Use --force to re-seed.")
        return

    if args.force:
        earnings_col().delete_many({})
        companies_col().delete_many({})
        print("Cleared existing data.")

    try:
        from scripts.update_companies import update_companies
        update_companies()
        print("Seeded from slickcharts.com (live).")
    except Exception as exc:
        print(f"Live source failed ({exc}); falling back to seed CSV.")
        from api.database import seed_from_csv
        seed_from_csv()

    count = companies_col().count_documents({})
    print(f"  {count} companies in DB. Run fetch_earnings.py to populate real earnings.")


if __name__ == "__main__":
    main()

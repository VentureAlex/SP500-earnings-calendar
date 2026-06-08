#!/usr/bin/env python3
"""
Fetch real earnings dates from Yahoo Finance and write to MongoDB.

yfinance handles Yahoo's session/cookie requirements automatically
(no API key needed).

Data written per ticker:
  - Next earnings date
  - EPS estimate (analyst consensus average)

Run:     python scripts/fetch_earnings.py
Options: --dry-run               print results without writing to DB
         --ticker AAPL MSFT ...  fetch specific tickers only
         --delay 0.5             seconds between requests (default 0.5)
         --no-prune              skip pruning past unconfirmed records
"""

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


def fetch_yahoo_earnings(ticker: str) -> dict | None:
    """
    Fetch the next earnings date + EPS estimate for a single ticker via yfinance.

    Returns:
      {"ticker": "AAPL", "date": "2026-07-30", "eps_estimate": 1.90, "source": "yahoo"}
    or None if no upcoming data is available.
    """
    import yfinance as yf

    try:
        cal = yf.Ticker(ticker).calendar
    except Exception as e:
        logger.warning("  %s: yfinance error - %s", ticker, e)
        return None

    if not cal or not cal.get("Earnings Date"):
        return None

    today = date.today()
    upcoming = [d for d in cal["Earnings Date"] if hasattr(d, "isoformat") and d >= today]
    if not upcoming:
        return None

    next_date = min(upcoming)
    return {
        "ticker":       ticker,
        "date":         next_date.isoformat(),
        "eps_estimate": cal.get("Earnings Average"),
        "source":       "yahoo",
    }


def upsert_earning(row: dict, dry_run: bool = False) -> None:
    from api.database import companies_col, earnings_col

    ticker = row["ticker"]
    report_date = row["date"]

    if dry_run:
        eps = f"${row['eps_estimate']:.2f}" if row.get("eps_estimate") else "n/a"
        logger.info("  [dry-run] %-8s -> %s  EPS est: %s", ticker, report_date, eps)
        return

    company = companies_col().find_one(
        {"ticker": ticker},
        {"_id": 0, "name": 1, "rank": 1, "industry": 1},
    )
    if not company:
        logger.debug("  %s: not in companies collection, skipping", ticker)
        return

    date_dt = datetime.fromisoformat(report_date).replace(tzinfo=timezone.utc)

    earnings_col().update_one(
        {"ticker": ticker, "date": report_date},
        {"$set": {
            "ticker":        ticker,
            "date":          report_date,
            "date_dt":       date_dt,
            "quarter":       _quarter_from_date(report_date),
            "fiscal_year":   int(report_date[:4]),
            "eps_estimate":  row.get("eps_estimate"),
            "source":        "yahoo",
            "company_name":  company.get("name"),
            "company_rank":  company.get("rank"),
            "industry":      company.get("industry"),
            "last_fetched":  datetime.now(timezone.utc),
        }},
        upsert=True,
    )


def prune_past_earnings(dry_run: bool = False) -> None:
    """
    Remove unconfirmed Yahoo earnings whose date has passed.
    The MongoDB TTL index handles this automatically, but this runs
    immediately rather than waiting for the 60-second TTL cycle.
    """
    from api.database import earnings_col

    now = datetime.now(timezone.utc)
    query = {"date_dt": {"$lt": now}, "actual_eps": None, "source": "yahoo"}

    if dry_run:
        count = earnings_col().count_documents(query)
        logger.info("[dry-run] would prune %d stale Yahoo earnings records", count)
        return

    result = earnings_col().delete_many(query)
    if result.deleted_count:
        logger.info("Pruned %d stale Yahoo earnings records", result.deleted_count)


def _quarter_from_date(date_str: str) -> str:
    month = int(date_str[5:7])
    return ["Q1", "Q1", "Q1", "Q2", "Q2", "Q2", "Q3", "Q3", "Q3", "Q4", "Q4", "Q4"][month - 1]


def main():
    parser = argparse.ArgumentParser(description="Fetch earnings from Yahoo Finance into MongoDB")
    parser.add_argument("--ticker", nargs="+", metavar="TICK",
                        help="Fetch specific tickers (default: all companies in DB)")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between requests (default: 0.5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print results without writing to DB")
    parser.add_argument("--no-prune", action="store_true",
                        help="Skip pruning past unconfirmed records")
    args = parser.parse_args()

    from api.database import companies_col, ensure_seeded

    # Ensure companies exist before fetching earnings for them
    ensure_seeded()

    if not args.no_prune:
        prune_past_earnings(dry_run=args.dry_run)

    if args.ticker:
        tickers = [t.upper() for t in args.ticker]
    else:
        tickers = [
            c["ticker"]
            for c in companies_col().find({}, {"ticker": 1, "_id": 0}).sort("rank", 1)
        ]

    logger.info("Fetching Yahoo Finance earnings for %d tickers...", len(tickers))

    saved = skipped = 0
    for i, ticker in enumerate(tickers, 1):
        result = fetch_yahoo_earnings(ticker)

        if result:
            upsert_earning(result, dry_run=args.dry_run)
            eps = f"${result['eps_estimate']:.2f}" if result.get("eps_estimate") else "n/a"
            logger.info("  [%d/%d] %-8s -> %s  (EPS est: %s)",
                        i, len(tickers), ticker, result["date"], eps)
            saved += 1
        else:
            logger.info("  [%d/%d] %-8s -> no upcoming data", i, len(tickers), ticker)
            skipped += 1

        if i < len(tickers):
            time.sleep(args.delay)

    logger.info("Done. %d saved, %d skipped.", saved, skipped)


if __name__ == "__main__":
    main()

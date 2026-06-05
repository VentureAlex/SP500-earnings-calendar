#!/usr/bin/env python3
"""
Fetch real earnings dates from Yahoo Finance via the yfinance library.

yfinance handles Yahoo's session/cookie requirements automatically and is
well-maintained by the open-source community (no API key needed).

Data returned per ticker:
  - Next earnings date
  - EPS estimate (average / low / high consensus)
  - Revenue estimate

Run:      python scripts/fetch_earnings.py
Options:  --dry-run               print results without writing to DB
          --ticker AAPL MSFT ...  fetch specific tickers only
          --delay 0.5             seconds between requests (default 0.5)

Future:   A Claude agent will augment this by parsing earnings announcement
          pages for richer data (actual EPS, guidance, segment breakdowns).
"""

import sys
import os
import time
import logging
import argparse
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


def fetch_yahoo_earnings(ticker: str) -> dict | None:
    """
    Fetch the next earnings date + EPS estimates for a single ticker via yfinance.

    Returns:
      {
        "ticker":       "AAPL",
        "date":         "2026-07-30",
        "eps_estimate": 1.90,
        "source":       "yahoo",
      }
    or None if no upcoming data.
    """
    import yfinance as yf

    try:
        cal = yf.Ticker(ticker).calendar
    except Exception as e:
        logger.warning("  %s: yfinance error — %s", ticker, e)
        return None

    if not cal or not cal.get("Earnings Date"):
        return None

    # calendar["Earnings Date"] is a list of date objects; take the first future one
    today = date.today()
    earnings_dates = [
        d for d in cal["Earnings Date"]
        if hasattr(d, "isoformat") and d >= today
    ]
    if not earnings_dates:
        return None

    next_date = min(earnings_dates)

    return {
        "ticker":       ticker,
        "date":         next_date.isoformat(),
        "eps_estimate": cal.get("Earnings Average"),
        "source":       "yahoo",
    }


def upsert_earning(row: dict, dry_run: bool = False):
    from api.database import get_db

    if dry_run:
        eps = f"${row['eps_estimate']:.2f}" if row.get("eps_estimate") else "n/a"
        logger.info("  [dry-run] %-8s → %s  EPS est: %s", row["ticker"], row["date"], eps)
        return

    with get_db() as conn:
        company = conn.execute(
            "SELECT id FROM companies WHERE ticker = ?", (row["ticker"],)
        ).fetchone()
        if not company:
            return

        conn.execute(
            """INSERT INTO earnings (company_id, ticker, date, eps_estimate, source)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ticker, date) DO UPDATE SET
                 eps_estimate = COALESCE(excluded.eps_estimate, earnings.eps_estimate),
                 source       = excluded.source,
                 last_fetched = datetime('now')""",
            (company[0], row["ticker"], row["date"], row.get("eps_estimate"), row["source"]),
        )


def prune_past_earnings(dry_run: bool = False):
    """Remove unconfirmed Yahoo earnings rows whose date has passed."""
    from api.database import get_db
    cutoff = date.today().isoformat()
    if dry_run:
        logger.info("[dry-run] would prune unconfirmed Yahoo earnings before %s", cutoff)
        return
    with get_db() as conn:
        n = conn.execute(
            "DELETE FROM earnings WHERE date < ? AND actual_eps IS NULL AND source = 'yahoo'",
            (cutoff,),
        ).rowcount
    if n:
        logger.info("Pruned %d stale Yahoo earnings records", n)


def main():
    parser = argparse.ArgumentParser(description="Fetch earnings from Yahoo Finance")
    parser.add_argument("--ticker", nargs="+", metavar="TICK",
                        help="Fetch specific tickers (default: all companies in DB)")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between requests (default 0.5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print results without writing to DB")
    parser.add_argument("--no-prune", action="store_true",
                        help="Skip pruning past unconfirmed records")
    args = parser.parse_args()

    from api.database import init_db, get_db

    init_db()

    if not args.no_prune:
        prune_past_earnings(dry_run=args.dry_run)

    if args.ticker:
        tickers = [t.upper() for t in args.ticker]
    else:
        with get_db() as conn:
            tickers = [r[0] for r in conn.execute(
                "SELECT ticker FROM companies ORDER BY rank"
            ).fetchall()]

    logger.info("Fetching Yahoo Finance earnings for %d tickers…", len(tickers))

    saved = skipped = 0
    for i, ticker in enumerate(tickers, 1):
        result = fetch_yahoo_earnings(ticker)

        if result:
            upsert_earning(result, dry_run=args.dry_run)
            eps = f"${result['eps_estimate']:.2f}" if result.get("eps_estimate") else "n/a"
            logger.info("  [%d/%d] %-8s → %s  (EPS est: %s)",
                        i, len(tickers), ticker, result["date"], eps)
            saved += 1
        else:
            logger.info("  [%d/%d] %-8s → no upcoming data", i, len(tickers), ticker)
            skipped += 1

        if i < len(tickers):
            time.sleep(args.delay)

    logger.info("\nDone. %d saved, %d skipped (no upcoming data)", saved, skipped)


if __name__ == "__main__":
    main()

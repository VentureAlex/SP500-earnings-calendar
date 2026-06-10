#!/usr/bin/env python3
"""
Refresh the S&P 500 company list from slickcharts.com and upsert into MongoDB.
Industry (GICS Sector) is pulled from the Wikipedia S&P 500 constituents table
and merged in before writing to the DB.

Run:      python scripts/update_companies.py
Schedule: 1st of each month via .github/workflows/update_companies.yml

Companies no longer in the S&P 500 are removed along with their earnings records.
"""

import logging
import sys
import os
import time

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

SLICKCHARTS_URL = "https://www.slickcharts.com/sp500"
WIKIPEDIA_URL   = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SP500EarningsCalendar/1.0)"}


def fetch_from_slickcharts() -> list[dict]:
    """Scrape rank-ordered S&P 500 list from slickcharts.com."""
    logger.info("Fetching S&P 500 list from slickcharts.com...")
    resp = requests.get(SLICKCHARTS_URL, headers=_HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table")
    if not table:
        raise ValueError("Could not find table on slickcharts.com")

    tbody = table.find("tbody")
    if not tbody:
        raise ValueError("Table has no tbody on slickcharts.com")

    rows = []
    for i, tr in enumerate(tbody.find_all("tr"), start=1):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue
        # Columns: #, Company, Symbol, Weight, Price, Chg, % Chg
        ticker = cells[2].upper().replace(".", "-")  # BRK.B -> BRK-B
        name   = cells[1]
        if not ticker or not name:
            continue
        rows.append({"rank": i, "name": name, "ticker": ticker})

    logger.info("Parsed %d companies from slickcharts.com", len(rows))
    if len(rows) < 400:
        raise ValueError(f"Only got {len(rows)} rows — page structure may have changed")

    return rows


def fetch_sector_map_from_wikipedia() -> dict[str, str]:
    """
    Scrape the Wikipedia S&P 500 constituents table and return a
    dict mapping ticker -> GICS Sector.

    Wikipedia table columns (as of 2024):
      0: Symbol  1: Security  2: GICS Sector  3: GICS Sub-Industry  4+: other
    """
    logger.info("Fetching GICS sector data from Wikipedia...")
    resp = requests.get(WIKIPEDIA_URL, headers=_HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    # The constituents table has id="constituents"
    table = soup.find("table", {"id": "constituents"})
    if not table:
        # Fallback: first wikitable on the page
        table = soup.find("table", {"class": "wikitable"})
    if not table:
        raise ValueError("Could not find constituents table on Wikipedia")

    sector_map: dict[str, str] = {}
    for tr in table.find("tbody").find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue
        raw_ticker = cells[0].upper().replace(".", "-")  # normalize BRK.B -> BRK-B
        sector     = cells[2].strip()
        if raw_ticker and sector:
            sector_map[raw_ticker] = sector

    logger.info("Got GICS sectors for %d tickers from Wikipedia", len(sector_map))
    return sector_map


def update_companies() -> None:
    from api.database import init_db, companies_col, earnings_col
    from pymongo import UpdateOne

    init_db()

    # --- Fetch company list (with retries) ---
    companies = None
    for attempt in range(3):
        try:
            companies = fetch_from_slickcharts()
            break
        except Exception as exc:
            logger.warning("slickcharts attempt %d failed: %s", attempt + 1, exc)
            if attempt == 2:
                logger.error("All slickcharts attempts failed. Aborting.")
                sys.exit(1)
            time.sleep(5 * (attempt + 1))

    # --- Fetch sector data (best-effort; non-fatal if Wikipedia is down) ---
    sector_map: dict[str, str] = {}
    try:
        sector_map = fetch_sector_map_from_wikipedia()
    except Exception as exc:
        logger.warning("Wikipedia sector fetch failed (%s) — industry field will be unchanged", exc)

    # Merge sector into company records
    for c in companies:
        if c["ticker"] in sector_map:
            c["industry"] = sector_map[c["ticker"]]

    new_tickers = {c["ticker"] for c in companies}

    # --- Purge companies (and their earnings) no longer in the S&P 500 ---
    existing_tickers = {
        doc["ticker"]
        for doc in companies_col().find({}, {"ticker": 1, "_id": 0})
    }
    removed = existing_tickers - new_tickers
    if removed:
        earnings_col().delete_many({"ticker": {"$in": list(removed)}})
        companies_col().delete_many({"ticker": {"$in": list(removed)}})
        logger.info("Removed %d companies no longer in S&P 500: %s", len(removed), removed)

    # --- Upsert all current companies ---
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    ops = [
        UpdateOne(
            {"ticker": c["ticker"]},
            {"$set": {
                "ticker":       c["ticker"],
                "name":         c["name"],
                "rank":         c["rank"],
                "industry":     c.get("industry"),
                "last_updated": now,
            }},
            upsert=True,
        )
        for c in companies
    ]
    result = companies_col().bulk_write(ops, ordered=False)
    logger.info(
        "Company list updated — %d upserted, %d matched. %d with industry data.",
        result.upserted_count,
        result.matched_count,
        len(sector_map),
    )


if __name__ == "__main__":
    update_companies()

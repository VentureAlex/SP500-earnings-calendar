# S&P 500 Earnings Calendar

A responsive single-page web app displaying real earnings announcement dates for S&P 500 companies, built on FastAPI + MongoDB + FullCalendar.js.

---

## Features

- Interactive month / week / list / year calendar views (FullCalendar 6)
- Filter by year, fiscal quarter, industry (GICS sector), and ticker
- Alphabetical ticker dropdown — greyed out when no earnings data available
- Color-coded events by industry with a legend
- Event detail modal with EPS estimates and Yahoo Finance link
- Dark / light mode (persisted in localStorage)
- Mobile-responsive with collapsible sidebar

---

## Architecture

```
.
├── public/
│   └── index.html          # Single-page frontend (Tailwind + FullCalendar CDN)
├── api/
│   ├── index.py            # FastAPI app — serves /api/* routes
│   └── database.py         # MongoDB helpers, indexes, seeding logic
├── scripts/
│   ├── update_companies.py # Refresh S&P 500 list (slickcharts + Wikipedia GICS sectors)
│   ├── fetch_earnings.py   # Fetch real earnings dates from Yahoo Finance
│   ├── seed_data.py        # One-shot DB bootstrap
│   └── sync_to_mongo.py    # MongoDB maintenance (prune, stats)
├── data/
│   └── sp500_seed.csv      # Fallback seed dataset (used if slickcharts is unreachable)
├── .github/workflows/
│   ├── sync_earnings.yml       # Weekly: refresh companies + fetch earnings (Monday 6 AM UTC)
│   └── update_companies.yml    # Monthly: full company list refresh (1st of month, 5 AM UTC)
├── run.py                  # Local dev server (uvicorn)
├── vercel.json             # Vercel routing config
└── requirements.txt
```

**Data flow:**
1. `update_companies.py` scrapes slickcharts.com for the S&P 500 list and Wikipedia for GICS sector data, then upserts into MongoDB.
2. `fetch_earnings.py` calls Yahoo Finance (via yfinance) for each company and stores real upcoming earnings dates. A 7-day skip gate prevents re-fetching companies updated within the last week.
3. The frontend fetches `/api/earnings?start=…&end=…&[filters]` on every calendar navigation.
4. GitHub Actions keeps the data fresh automatically (weekly + monthly).

---

## Quick Start

### Prerequisites

- Python 3.11+
- MongoDB Atlas cluster (free M0 tier is sufficient)

### 1. Clone & install

```bash
git clone https://github.com/VentureAlex/sp500-earnings-calendar.git
cd sp500-earnings-calendar
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Fill in MONGODB_URI with your Atlas connection string
# MONGODB_DB defaults to "sp500"
```

### 3. Populate the database

```bash
python scripts/update_companies.py   # ~500 companies + GICS sectors
python scripts/fetch_earnings.py     # real Yahoo Finance earnings dates
```

### 4. Run

```bash
python run.py
```

Open **http://localhost:8000**

---

## Scheduled Refresh (GitHub Actions)

Two workflows keep data current automatically. Add these secrets to your GitHub repo:

| Secret | Value |
|--------|-------|
| `MONGODB_URI` | Your Atlas connection string |
| `MONGODB_DB` | `sp500` |

| Workflow | Schedule | What it does |
|----------|----------|-------------|
| `sync_earnings.yml` | Every Monday 6 AM UTC | Updates company list + fetches Yahoo Finance earnings |
| `update_companies.yml` | 1st of every month 5 AM UTC | Full company list refresh + earnings fetch |

Both workflows can also be triggered manually from the GitHub Actions UI.

---

## Deploy to Vercel

1. Push this repo to GitHub
2. Go to https://vercel.com/new → Import your GitHub repo
3. Add environment variables in Vercel project settings:
   - `MONGODB_URI` — your Atlas connection string
   - `MONGODB_DB` — `sp500`
4. Click **Deploy**

---

## License

MIT

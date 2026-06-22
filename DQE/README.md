# CubeLogic DQE — Data Quality Engine

Interactive Streamlit dashboard for validating intraday energy trading data injected into **CubeWatch STG** tables (`CLIENT_ORDERS` and `CLIENT_TRADES`).

Built for CubeLogic by Or Even.

---

## Features

### 🏠 Overview
- Cross-exchange snapshot of all registered data sources
- File sizes and freshness indicators loaded instantly
- **Load Full Stats** button scans parquet files on demand (cached after first run)
- Date filter defaults to dates available in **all** exchanges for apples-to-apples comparison
- Side-by-side bar charts: orders volume, trades volume, public/private split

### 📊 Data Stats
- Volume breakdown by country, market area, instrument, status, data type (public/private)
- 5 KPI metrics: total records, total volume, weighted avg price, unique IDs, cancel rate
- 6 tabs: Status breakdown, Country breakdown, Instrument breakdown, Hourly activity, Party breakdown, Raw records drill-down
- Exchange + date selectors (shared state across pages)

### 🧪 Test Cases
15 automated data quality checks across both `CLIENT_ORDERS` and `CLIENT_TRADES`:

| TC | Name | Scope |
|---|---|---|
| TC1 | NULL Values | Both |
| TC2 | SORT_ID Incremental | Both |
| TC3 | Lifecycle Start | Both |
| TC4 | Lifecycle End | CLIENT_ORDERS |
| TC5 | Double V | Both |
| TC6 | Duplicate Records | Both |
| TC7 | Auto-Closing Timestamp | CLIENT_ORDERS |
| TC8 | Negative Volume | CLIENT_ORDERS |
| TC9 | Delivery Period Consistency | CLIENT_ORDERS |
| TC10 | Day Timeframe | Both |
| TC11 | DE Market Harmonization | CLIENT_ORDERS |
| TC12 | DE Synthetic Cancel | CLIENT_ORDERS (Public) |
| TC13 | Valid TRAN_STATUS Values | CLIENT_ORDERS |
| TC14 | Private Cancel Logic | CLIENT_ORDERS (Private) |
| TC15 | Private INS_TYPE Coverage | Both |

- Summary table with Status, Violations, **Success Rate %**, and **Exec Time (s)** per TC
- Full drill-down into violation records for each failed TC
- Lifecycle view per ORIG_TRAN_ID
- Dataset size banner (orders public/private split + trades count)

---

## Tech Stack

| Component | Technology |
|---|---|
| Dashboard | [Streamlit](https://streamlit.io) |
| Query engine | [DuckDB](https://duckdb.org) — direct `parquet_scan()` |
| Charts | [Plotly Express](https://plotly.com/python/plotly-express/) |
| Data format | Apache Parquet |

---

## Project Structure

```
DQE/
├── dashboard/
│   ├── app.py                  # Navigation entry point
│   ├── utils.py                # Exchange discovery (EXCHANGE_DIRS registry)
│   └── pages/
│       ├── 0_Overview.py       # Combined cross-exchange overview
│       ├── 1_Data_Stats.py     # Volume & stats drill-down
│       └── 2_Test_Cases.py     # 15 automated DQ test cases
├── Nordpool/
│   ├── Specification/          # Interface specification documents
│   └── extracted/              # ⚠️ Parquet files (gitignored — add locally)
│       └── CLIENT_ORDERS_YYYY-MM-DD-<uuid>.parquet
│       └── CLIENT_TRADES_YYYY-MM-DD-<uuid>.parquet
└── EPEX/
    └── extracted/              # ⚠️ Parquet files (gitignored — add locally)
        └── YYYY-MM-DD/
            └── CLIENT_ORDERS_*.parquet
            └── CLIENT_TRADES_*.parquet
```

---

## Setup

### 1. Install dependencies
```bash
pip install streamlit duckdb pandas plotly pytz
```

### 2. Add parquet data files
Place extracted parquet files in the correct folders (see structure above). Files are excluded from git due to size.

**Nordpool** — flat layout:
```
DQE/Nordpool/extracted/CLIENT_ORDERS_YYYY-MM-DD-<uuid>.parquet
DQE/Nordpool/extracted/CLIENT_TRADES_YYYY-MM-DD-<uuid>.parquet
```

**EPEX** — dated subfolder layout:
```
DQE/EPEX/extracted/YYYY-MM-DD/CLIENT_ORDERS_<uuid>.parquet
DQE/EPEX/extracted/YYYY-MM-DD/CLIENT_TRADES_<uuid>.parquet
```

### 3. Add a new exchange
Open `dashboard/utils.py` and add one line to `EXCHANGE_DIRS`:
```python
EXCHANGE_DIRS: dict[str, Path] = {
    "Nordpool": BASE_DIR / "Nordpool" / "extracted",
    "EPEX":     BASE_DIR / "EPEX"     / "extracted",
    "ICE":      BASE_DIR / "ICE"      / "extracted",   # ← add like this
}
```

### 4. Run the dashboard
```bash
cd DQE
streamlit run dashboard/app.py
```

Open [http://localhost:8501](http://localhost:8501)

---

## Data path configuration

All data paths are resolved relative to `BASE_DIR` in `utils.py`:
```python
BASE_DIR = Path(r"C:\Users\or.even\DQE")
```
Update this if running on a different machine.

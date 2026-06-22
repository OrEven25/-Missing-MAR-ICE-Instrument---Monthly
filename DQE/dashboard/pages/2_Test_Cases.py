"""
Nordpool DQE — Test Cases Engine
18 test cases validating CLIENT_ORDERS and CLIENT_TRADES data integrity.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px
import re
import pytz
import time
from datetime import datetime, timedelta, date
from utils import parquet_scan_expr,\
     discover_all_data_files

st.set_page_config(
    page_title="Test Cases | DQE",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# DATE SELECTION — must happen before constants are set
# ─────────────────────────────────────────────────────────────────────────────
all_data  = discover_all_data_files()
EXCHANGES = list(all_data.keys())

with st.sidebar:
    st.markdown("## 🧪 DQE — Test Cases")
    st.divider()

    # Exchange selector — shared with Data Stats via session state
    _ex_idx = EXCHANGES.index(st.session_state.get("sel_exchange", EXCHANGES[0])) if st.session_state.get("sel_exchange") in EXCHANGES else 0
    sel_exchange = st.radio("📡 Exchange", EXCHANGES, index=_ex_idx, horizontal=True, key="tc_exchange")

    data_files      = all_data[sel_exchange]
    available_dates = list(data_files.keys())
    _prev_date      = st.session_state.get("sel_date")
    _default_date   = _prev_date if _prev_date in available_dates else available_dates[0]
    sel_date = st.selectbox("📅 Trading Date", available_dates,
                            index=available_dates.index(_default_date), key="tc_date")

# Detect exchange or date change — NO longer clears results.
# Results are stored per (exchange, date) key so switching selectors just changes view.
st.session_state["sel_exchange"] = sel_exchange
st.session_state["sel_date"]     = sel_date

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
ORDERS_PATH = data_files[sel_date]["orders"]
TRADES_PATH = data_files[sel_date]["trades"]
DATA_DATE   = date.fromisoformat(sel_date)
CET         = pytz.timezone("Europe/Berlin")

MANDATORY_ORDERS = [
    "TRAN_STATUS", "ORIG_TRAN_ID", "SORT_ID", "PRICE", "VOLUME",
    "TRAN_DATETIME", "INS_TYPE", "DELIVERY_PERIOD", "CURRENCY",
    "COUNTRY", "COMMODITY", "MARKET_PLACE", "ORIGIN", "TRAN_INS_TYPE", "UNIT",
]
MANDATORY_TRADES = MANDATORY_ORDERS + ["BUY_SELL"]  # DELIVERY_HOURS excluded (nullable)


@st.cache_data(show_spinner=False, persist="disk")
def get_record_counts(orders_path: str, trades_path: str) -> dict:
    """Return total row counts for ORDERS and TRADES — single scan each."""
    o = duckdb.query(f"""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE PARTY IS NULL)     AS public,
               COUNT(*) FILTER (WHERE PARTY IS NOT NULL) AS private
        FROM {parquet_scan_expr(orders_path)}
    """).df().iloc[0]
    t = duckdb.query(f"SELECT COUNT(*) AS n FROM {parquet_scan_expr(trades_path)}").df()["n"][0]
    return {"ORDERS": int(o["total"]), "TRADES": int(t),
            "PUBLIC": int(o["public"]), "PRIVATE": int(o["private"])}


@st.cache_data(show_spinner=False, persist="disk")
def get_parties(orders_path: str) -> list:
    """Return distinct client names from non-null PARTY values only."""
    df = duckdb.query(f"""
        SELECT DISTINCT PARTY AS client
        FROM {parquet_scan_expr(orders_path)}
        WHERE PARTY IS NOT NULL
        ORDER BY client
    """).df()
    return df["client"].tolist()

# Pre-load counts early so banner renders before TC execution
_counts_early = get_record_counts(ORDERS_PATH, TRADES_PATH)

TC_REGISTRY = [
    ("TC1",  "NULL Values",                    "Both",             "Mandatory fields null or blank"),
    ("TC2",  "SORT_ID Incremental",            "Both",             "SORT_ID not increasing within ORIG_TRAN_ID"),
    ("TC3",  "Lifecycle Start",                "Both",             "First record per ORIG_TRAN_ID not TRAN_STATUS=V"),
    ("TC4",  "Lifecycle End",                  "CLIENT_ORDERS",    "Last record per ORIG_TRAN_ID not E or C"),
    ("TC5",  "Double V",                       "Both",             "TRAN_STATUS=V appears >1 within same ORIG_TRAN_ID"),
    ("TC6",  "Duplicate Records",              "Both",             "Exact duplicates on key field combination"),
    ("TC7",  "Auto-Closing Timestamp",         "CLIENT_ORDERS",    "Auto-close event TRAN_DATETIME falls after end of trading day"),
    ("TC8",  "Negative Volume",                "CLIENT_ORDERS",    "VOLUME < 0"),
    ("TC9",  "Delivery Period Consistency",    "CLIENT_ORDERS",    "DELIVERY_PERIOD changes within an ORIG_TRAN_ID"),
    ("TC10", "Day Timeframe",                  "Both",             "Min / Max TRAN_DATETIME in CET/CEST for the trading day"),
    ("TC11", "DE Market Harmonization",        "CLIENT_ORDERS",    "DE MARKET_AREA wrong relative to split timing"),
    ("TC12", "DE Synthetic Cancel",            "CLIENT_ORDERS (Public)", "DE public order missing synthetic cancel"),
    ("TC13", "Valid TRAN_STATUS Values",       "CLIENT_ORDERS",    "TRAN_STATUS not in {V, A, E, C, P}"),
    ("TC14", "Private Cancel Logic",           "CLIENT_ORDERS (Private)", "Private order cancel time incorrect"),
    ("TC15", "Private INS_TYPE Coverage",      "Both",             "Private INS_TYPE has no matching public INS_TYPE"),
    ("TC16", "Public-Only Instruments",        "Both",             "INS_TYPEs traded in public data with no private orders or trades"),
    ("TC17", "Orderbook Inversion",            "CLIENT_ORDERS",    "Bid price > Ask price on same INS_TYPE at same minute (crossed book)"),
    ("TC18", "Executed Orders vs Trades",      "Both (Private)",   "Private E/P orders matched to trades via ORDER_REF — count & volume reconciliation"),
]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def q(sql: str) -> pd.DataFrame:
    return duckdb.query(sql).df()


def delivery_start_utc(dp: str, trade_date: date):
    """Parse DELIVERY_PERIOD → delivery start as UTC-naive datetime. Returns None on failure."""
    if not dp or len(dp) < 4:
        return None
    is_next_day = dp[0] == "N"
    base = trade_date + timedelta(days=1) if is_next_day else trade_date
    rest = dp[1:]
    hour, minute = None, None
    try:
        if m := re.match(r"^Q(\d{2})q([1-4])$", rest):
            hour, minute = int(m.group(1)), (int(m.group(2)) - 1) * 15
        elif m := re.match(r"^H(\d{2})$", rest):
            hour, minute = int(m.group(1)), 0
        elif m := re.match(r"^HB(\d{2})", rest):
            hour, minute = int(m.group(1)), 0
        elif m := re.match(r"^QB(\d{2})q([1-4])", rest):
            hour, minute = int(m.group(1)), (int(m.group(2)) - 1) * 15
        if hour is None:
            return None
        naive = datetime(base.year, base.month, base.day, hour, minute)
        return CET.localize(naive, is_dst=True).astimezone(pytz.UTC).replace(tzinfo=None)
    except Exception:
        return None


def end_of_day_utc(d: date) -> datetime:
    """Returns 23:59:59.999 CET/CEST as UTC-naive datetime."""
    eod = CET.localize(datetime(d.year, d.month, d.day, 23, 59, 59, 999000), is_dst=True)
    return eod.astimezone(pytz.UTC).replace(tzinfo=None)


def badge(status: str) -> str:
    return {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "WARN": "⚠️ WARN", "INFO": "ℹ️ INFO"}.get(status, "⏳")


def result_status(df: pd.DataFrame) -> str:
    if df is None:
        return "PENDING"
    if len(df) == 0:
        return "PASS"
    return "FAIL"


# ─────────────────────────────────────────────────────────────────────────────
# TEST CASE FUNCTIONS  (each returns a violations DataFrame)
# ─────────────────────────────────────────────────────────────────────────────

def run_tc1() -> pd.DataFrame:
    results = []
    for label, path, fields in [
        ("ORDERS", ORDERS_PATH, MANDATORY_ORDERS),
        ("TRADES", TRADES_PATH, MANDATORY_TRADES),
    ]:
        where_parts = [f"({f} IS NULL OR TRIM(CAST({f} AS VARCHAR)) = '')" for f in fields]
        flag_parts  = [
            f"CASE WHEN {f} IS NULL OR TRIM(CAST({f} AS VARCHAR)) = '' THEN '{f}' ELSE NULL END AS chk_{i}"
            for i, f in enumerate(fields)
        ]
        df = q(f"""
            SELECT '{label}' AS source, ORIG_TRAN_ID, SORT_ID,
                   TRAN_DATETIME, TRAN_STATUS,
                   {', '.join(flag_parts)}
            FROM {parquet_scan_expr(path)}
            WHERE {' OR '.join(where_parts)}
            LIMIT 10000
        """)
        chk_cols = [c for c in df.columns if c.startswith("chk_")]
        field_map = {f"chk_{i}": f for i, f in enumerate(fields)}
        melted = df.melt(
            id_vars=["source", "ORIG_TRAN_ID", "SORT_ID", "TRAN_DATETIME", "TRAN_STATUS"],
            value_vars=chk_cols, var_name="_col", value_name="null_field"
        ).dropna(subset=["null_field"])
        melted["null_field"] = melted["null_field"]
        melted = melted.drop(columns=["_col"])
        results.append(melted)
    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def run_tc2() -> pd.DataFrame:
    """Detect duplicate SORT_IDs within the same ORIG_TRAN_ID (no sort needed)."""
    results = []
    for label, path in [("ORDERS", ORDERS_PATH), ("TRADES", TRADES_PATH)]:
        df = q(f"""
            WITH dup_sort AS (
                SELECT ORIG_TRAN_ID, SORT_ID, COUNT(*) AS cnt
                FROM {parquet_scan_expr(path)}
                GROUP BY ORIG_TRAN_ID, SORT_ID
                HAVING cnt > 1
            )
            SELECT '{label}' AS source, o.ORIG_TRAN_ID, o.SORT_ID,
                   o.TRAN_DATETIME, o.TRAN_STATUS, d.cnt AS dup_count,
                   'SORT_ID appears ' || d.cnt || ' times in lifecycle' AS error_detail
            FROM {parquet_scan_expr(path)} o
            INNER JOIN dup_sort d ON o.ORIG_TRAN_ID = d.ORIG_TRAN_ID AND o.SORT_ID = d.SORT_ID
            ORDER BY o.ORIG_TRAN_ID, o.SORT_ID
            LIMIT 5000
        """)
        results.append(df)
    filtered = [d for d in results if len(d) > 0]
    return pd.concat(filtered, ignore_index=True) if filtered else pd.DataFrame()


def run_tc3() -> pd.DataFrame:
    """First record per ORIG_TRAN_ID (lowest SORT_ID) must be TRAN_STATUS = V."""
    results = []
    for label, path in [("ORDERS", ORDERS_PATH), ("TRADES", TRADES_PATH)]:
        df = q(f"""
            WITH first_sort AS (
                SELECT ORIG_TRAN_ID, MIN(SORT_ID) AS first_sort_id
                FROM {parquet_scan_expr(path)} GROUP BY ORIG_TRAN_ID
            )
            SELECT '{label}' AS source, o.ORIG_TRAN_ID, o.SORT_ID,
                   o.TRAN_STATUS, o.TRAN_DATETIME,
                   'First record (lowest SORT_ID) is not V' AS error_detail
            FROM {parquet_scan_expr(path)} o
            INNER JOIN first_sort f
                ON o.ORIG_TRAN_ID = f.ORIG_TRAN_ID AND o.SORT_ID = f.first_sort_id
            WHERE o.TRAN_STATUS != 'V'
            LIMIT 5000
        """)
        results.append(df)
    filtered = [d for d in results if len(d) > 0]
    return pd.concat(filtered, ignore_index=True) if filtered else pd.DataFrame()


def run_tc4() -> pd.DataFrame:
    """Last record per ORIG_TRAN_ID (highest SORT_ID) must be E or C."""
    return q(f"""
        WITH last_sort AS (
            SELECT ORIG_TRAN_ID, MAX(SORT_ID) AS last_sort_id
            FROM {parquet_scan_expr(ORDERS_PATH)} GROUP BY ORIG_TRAN_ID
        )
        SELECT 'ORDERS' AS source, o.ORIG_TRAN_ID, o.SORT_ID,
               o.TRAN_STATUS, o.TRAN_DATETIME,
               'Last record (highest SORT_ID) is not E or C' AS error_detail
        FROM {parquet_scan_expr(ORDERS_PATH)} o
        INNER JOIN last_sort l
            ON o.ORIG_TRAN_ID = l.ORIG_TRAN_ID AND o.SORT_ID = l.last_sort_id
        WHERE o.TRAN_STATUS NOT IN ('E', 'C')
        LIMIT 5000
    """)


def run_tc5() -> pd.DataFrame:
    results = []
    for label, path in [("ORDERS", ORDERS_PATH), ("TRADES", TRADES_PATH)]:
        df = q(f"""
            WITH v_counts AS (
                SELECT ORIG_TRAN_ID, COUNT(*) AS v_count FROM {parquet_scan_expr(path)}
                WHERE TRAN_STATUS = 'V' GROUP BY ORIG_TRAN_ID HAVING v_count > 1
            )
            SELECT '{label}' AS source, o.ORIG_TRAN_ID, o.SORT_ID,
                   o.TRAN_STATUS, o.TRAN_DATETIME, v.v_count,
                   'TRAN_STATUS=V appears ' || v.v_count || ' times' AS error_detail
            FROM {parquet_scan_expr(path)} o INNER JOIN v_counts v ON o.ORIG_TRAN_ID = v.ORIG_TRAN_ID
            WHERE o.TRAN_STATUS = 'V'
            ORDER BY o.ORIG_TRAN_ID, o.SORT_ID LIMIT 5000
        """)
        results.append(df)
    filtered = [d for d in results if len(d) > 0]
    return pd.concat(filtered, ignore_index=True) if filtered else pd.DataFrame()


def run_tc6() -> pd.DataFrame:
    key_cols = "INS_TYPE, ORIG_TRAN_ID, SORT_ID, ORIG_INS_TYPE, PRICE, VOLUME, TRAN_DATETIME, TRAN_STATUS"
    join_on  = " AND ".join(f"o.{c.strip()} = d.{c.strip()}" for c in key_cols.split(","))
    results  = []
    for label, path in [("ORDERS", ORDERS_PATH), ("TRADES", TRADES_PATH)]:
        df = q(f"""
            WITH dup_keys AS (
                SELECT {key_cols}, COUNT(*) AS dup_count
                FROM {parquet_scan_expr(path)}
                GROUP BY {key_cols}
                HAVING dup_count > 1
            )
            SELECT '{label}' AS source, o.ORIG_TRAN_ID, o.SORT_ID,
                   o.INS_TYPE, o.PRICE, o.VOLUME,
                   o.TRAN_DATETIME, o.TRAN_STATUS, d.dup_count,
                   'Duplicate record (×' || d.dup_count || ')' AS error_detail
            FROM {parquet_scan_expr(path)} o
            INNER JOIN dup_keys d ON {join_on}
            ORDER BY o.ORIG_TRAN_ID, o.SORT_ID
            LIMIT 5000
        """)
        results.append(df)
    filtered = [d for d in results if len(d) > 0]
    return pd.concat(filtered, ignore_index=True) if filtered else pd.DataFrame()


def run_tc7() -> pd.DataFrame:
    """TC7 — All auto-close events must occur before end of the trading day (CET/CEST)."""
    eod_utc = end_of_day_utc(DATA_DATE)
    df = q(f"""
        SELECT 'ORDERS' AS source, ORIG_TRAN_ID, SORT_ID, TRAN_DATETIME,
               DELIVERY_PERIOD, TRAN_STATUS, SOURCE,
               'Auto-close TRAN_DATETIME is after end of trading day' AS error_detail
        FROM {parquet_scan_expr(ORDERS_PATH)}
        WHERE lower(SOURCE) LIKE '%auto%clos%'
          AND TRAN_DATETIME > TIMESTAMPTZ '{eod_utc}'
        LIMIT 50000
    """)
    return df


def run_tc8() -> pd.DataFrame:
    return q(f"""
        SELECT 'ORDERS' AS source, ORIG_TRAN_ID, SORT_ID,
               TRAN_DATETIME, TRAN_STATUS, VOLUME,
               COUNTRY, MARKET_AREA, DELIVERY_PERIOD,
               'VOLUME=' || ROUND(VOLUME,4) || ' is negative' AS error_detail
        FROM {parquet_scan_expr(ORDERS_PATH)}
        WHERE VOLUME < 0
        LIMIT 5000
    """)


def run_tc9() -> pd.DataFrame:
    """TC9 — DELIVERY_PERIOD changes within a lifecycle. Returns violated ORIG_TRAN_IDs."""
    return q(f"""
        WITH dp_check AS (
            SELECT ORIG_TRAN_ID
            FROM {parquet_scan_expr(ORDERS_PATH)}
            GROUP BY ORIG_TRAN_ID
            HAVING COUNT(DISTINCT DELIVERY_PERIOD) > 1
        )
        SELECT 'ORDERS' AS source, o.ORIG_TRAN_ID, o.SORT_ID, o.TRAN_STATUS,
               o.TRAN_DATETIME, o.DELIVERY_PERIOD, o.PRICE, o.VOLUME,
               o.PARTY, o.COUNTRY, o.MARKET_AREA,
               'DELIVERY_PERIOD changes across lifecycle' AS error_detail
        FROM {parquet_scan_expr(ORDERS_PATH)} o
        INNER JOIN dp_check d ON o.ORIG_TRAN_ID = d.ORIG_TRAN_ID
        ORDER BY o.ORIG_TRAN_ID, o.SORT_ID
        LIMIT 10000
    """)


def run_tc10() -> pd.DataFrame:
    """TC10 — Show min/max TRAN_DATETIME per table, displayed in CET or CEST (DST-aware)."""
    rows = []
    for label, path in [("ORDERS", ORDERS_PATH), ("TRADES", TRADES_PATH)]:
        stats = q(f"""
            SELECT MIN(TRAN_DATETIME) AS min_utc, MAX(TRAN_DATETIME) AS max_utc
            FROM {parquet_scan_expr(path)}
        """).iloc[0]
        if pd.isna(stats["min_utc"]):
            rows.append({"Table": label, "Min TRAN_DATETIME": "—", "Max TRAN_DATETIME": "—",
                         "Timezone": "—", "Records": 0})
            continue
        min_utc = pd.Timestamp(stats["min_utc"]).tz_localize("UTC")
        max_utc = pd.Timestamp(stats["max_utc"]).tz_localize("UTC")
        min_cet = min_utc.tz_convert("Europe/Berlin")
        max_cet = max_utc.tz_convert("Europe/Berlin")
        tz_label = min_cet.strftime("%Z")   # CEST in summer, CET in winter
        rows.append({
            "Table":            label,
            "Timezone":         tz_label,
            f"Min ({tz_label})": min_cet.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + f" {tz_label}",
            f"Max ({tz_label})": max_cet.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + f" {tz_label}",
            "Min (UTC)":        min_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "Max (UTC)":        max_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        })
    return pd.DataFrame(rows)


def run_tc11() -> pd.DataFrame:
    df = q(f"""
        SELECT ORIG_TRAN_ID, SORT_ID, TRAN_DATETIME, DELIVERY_PERIOD,
               MARKET_AREA, TRAN_STATUS, PARTY
        FROM {parquet_scan_expr(ORDERS_PATH)}
        WHERE COUNTRY = 'DE'
        LIMIT 200000
    """)
    if df.empty:
        return pd.DataFrame(columns=[
            "source", "ORIG_TRAN_ID", "SORT_ID", "TRAN_DATETIME",
            "DELIVERY_PERIOD", "MARKET_AREA", "expected_market_area", "error_detail"
        ])
    VALID_POST_SPLIT = {"50HzT", "TTG", "TNG", "AMP"}
    violations = []
    for _, row in df.iterrows():
        dp = str(row["DELIVERY_PERIOD"])
        tran_dt = pd.Timestamp(row["TRAN_DATETIME"]).to_pydatetime().replace(tzinfo=None)
        ds = delivery_start_utc(dp, DATA_DATE)
        if ds is None:
            continue
        split_t = ds - timedelta(minutes=30)
        actual_ma = row["MARKET_AREA"]
        if tran_dt < split_t:
            # Before split window — expect NULL
            if pd.notna(actual_ma) and str(actual_ma).strip():
                violations.append({**row.to_dict(), "source": "ORDERS",
                    "expected_market_area": "NULL",
                    "error_detail": f"Before split: MARKET_AREA='{actual_ma}', expected NULL"})
        else:
            # After / at split — expect one of 4 zones
            if pd.isna(actual_ma) or str(actual_ma).strip() not in VALID_POST_SPLIT:
                violations.append({**row.to_dict(), "source": "ORDERS",
                    "expected_market_area": "50HzT / TTG / TNG / AMP",
                    "error_detail": f"After split: MARKET_AREA='{actual_ma}', expected DE zone"})
    return pd.DataFrame(violations) if violations else pd.DataFrame()


def run_tc12() -> pd.DataFrame:
    df = q(f"""
        WITH de_pub AS (
            SELECT ORIG_TRAN_ID, SORT_ID, TRAN_DATETIME, DELIVERY_PERIOD,
                   TRAN_STATUS,
                   LAST_VALUE(TRAN_STATUS) OVER (
                       PARTITION BY ORIG_TRAN_ID
                       ORDER BY SORT_ID, TRAN_DATETIME
                       ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                   ) AS last_status,
                   MAX(SORT_ID) OVER (PARTITION BY ORIG_TRAN_ID) AS max_sort
            FROM {parquet_scan_expr(ORDERS_PATH)}
            WHERE COUNTRY = 'DE' AND PARTY IS NULL
        )
        SELECT 'ORDERS (Public DE)' AS source, ORIG_TRAN_ID,
               DELIVERY_PERIOD, last_status,
               'No synthetic cancel found before delivery start' AS error_detail
        FROM de_pub
        WHERE SORT_ID = max_sort AND last_status NOT IN ('C', 'E')
        LIMIT 5000
    """)
    return df


def run_tc13() -> pd.DataFrame:
    df = q(f"""
        SELECT 'ORDERS' AS source, ORIG_TRAN_ID, SORT_ID,
               TRAN_STATUS, TRAN_DATETIME, COUNTRY, MARKET_AREA,
               'TRAN_STATUS=' || TRAN_STATUS || ' is not in (V,A,E,C,P)' AS error_detail
        FROM {parquet_scan_expr(ORDERS_PATH)}
        WHERE TRAN_STATUS NOT IN ('V', 'A', 'E', 'C', 'P')
        LIMIT 5000
    """)
    return df


def run_tc14() -> pd.DataFrame:
    df = q(f"""
        WITH priv AS (
            SELECT ORIG_TRAN_ID, DELIVERY_PERIOD, COUNTRY, TRAN_STATUS,
                   TRAN_DATETIME, SORT_ID,
                   LAST_VALUE(TRAN_STATUS) OVER (
                       PARTITION BY ORIG_TRAN_ID
                       ORDER BY SORT_ID, TRAN_DATETIME
                       ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                   ) AS last_status,
                   MAX(CASE WHEN TRAN_STATUS = 'C' THEN TRAN_DATETIME END)
                       OVER (PARTITION BY ORIG_TRAN_ID) AS cancel_dt,
                   MAX(SORT_ID) OVER (PARTITION BY ORIG_TRAN_ID) AS max_sort
            FROM {parquet_scan_expr(ORDERS_PATH)}
            WHERE PARTY IS NOT NULL
        )
        SELECT ORIG_TRAN_ID, DELIVERY_PERIOD, COUNTRY, last_status, cancel_dt
        FROM priv
        WHERE SORT_ID = max_sort
        LIMIT 100000
    """)
    if df.empty:
        return df

    eod_utc    = end_of_day_utc(DATA_DATE)
    TOLERANCE  = 5  # seconds
    violations = []

    for _, row in df.iterrows():
        dp          = str(row["DELIVERY_PERIOD"])
        last_status = row["last_status"]
        cancel_raw  = row["cancel_dt"]
        is_de       = str(row.get("COUNTRY", "")).upper() == "DE"

        if last_status not in ("C", "E"):
            violations.append({
                "ORIG_TRAN_ID": row["ORIG_TRAN_ID"], "DELIVERY_PERIOD": dp,
                "COUNTRY": row.get("COUNTRY"), "last_status": last_status,
                "cancel_datetime": None, "expected_cancel": None,
                "error_detail": "Lifecycle not closed — no C or E record",
            })
            continue

        if last_status == "C" and pd.notna(cancel_raw):
            cancel_dt = pd.Timestamp(cancel_raw).to_pydatetime().replace(tzinfo=None)
            ds = delivery_start_utc(dp, DATA_DATE)

            # Rule 3 — DE market split: cancel at 30 min before delivery
            if is_de and ds:
                expected = ds - timedelta(minutes=30)
                if abs((cancel_dt - expected).total_seconds()) > TOLERANCE:
                    violations.append({
                        "ORIG_TRAN_ID": row["ORIG_TRAN_ID"], "DELIVERY_PERIOD": dp,
                        "COUNTRY": row.get("COUNTRY"), "last_status": last_status,
                        "cancel_datetime": cancel_dt, "expected_cancel": expected,
                        "error_detail": "DE: cancel not at T-30min before delivery",
                    })
                continue

            # Rule 2 — CQ same-day: cancel at delivery start (contract expiry)
            if dp.startswith("C") and ds and ds < eod_utc:
                if abs((cancel_dt - ds).total_seconds()) > TOLERANCE:
                    violations.append({
                        "ORIG_TRAN_ID": row["ORIG_TRAN_ID"], "DELIVERY_PERIOD": dp,
                        "COUNTRY": row.get("COUNTRY"), "last_status": last_status,
                        "cancel_datetime": cancel_dt, "expected_cancel": ds,
                        "error_detail": "CQ: cancel time ≠ delivery start (contract expiry)",
                    })
                continue

            # Rule 1 — NQ or late CQ: cancel at end of CET/CEST day
            if abs((cancel_dt - eod_utc).total_seconds()) > TOLERANCE:
                violations.append({
                    "ORIG_TRAN_ID": row["ORIG_TRAN_ID"], "DELIVERY_PERIOD": dp,
                    "COUNTRY": row.get("COUNTRY"), "last_status": last_status,
                    "cancel_datetime": cancel_dt, "expected_cancel": eod_utc,
                    "error_detail": "NQ/overnight: cancel ≠ end of CET/CEST day",
                })

    return pd.DataFrame(violations) if violations else pd.DataFrame()


def run_tc15() -> pd.DataFrame:
    """TC15 — Every private INS_TYPE must have a corresponding public INS_TYPE."""
    results = []
    for label, path in [("ORDERS", ORDERS_PATH), ("TRADES", TRADES_PATH)]:
        df = q(f"""
            WITH private_ins AS (
                SELECT DISTINCT INS_TYPE, ORIG_INS_TYPE
                FROM {parquet_scan_expr(path)}
                WHERE PARTY IS NOT NULL
            ),
            public_ins AS (
                SELECT DISTINCT INS_TYPE
                FROM {parquet_scan_expr(path)}
                WHERE PARTY IS NULL
            ),
            missing AS (
                SELECT pr.INS_TYPE, pr.ORIG_INS_TYPE
                FROM private_ins pr
                LEFT JOIN public_ins pub ON pr.INS_TYPE = pub.INS_TYPE
                WHERE pub.INS_TYPE IS NULL
            )
            SELECT '{label}'   AS source,
                   m.INS_TYPE,
                   m.ORIG_INS_TYPE,
                   COUNT(o.ORIG_TRAN_ID)          AS private_record_count,
                   COUNT(DISTINCT o.ORIG_TRAN_ID) AS private_unique_ids,
                   'Private INS_TYPE has no public counterpart' AS error_detail
            FROM {parquet_scan_expr(path)} o
            INNER JOIN missing m ON o.INS_TYPE = m.INS_TYPE
            WHERE o.PARTY IS NOT NULL
            GROUP BY 1,2,3,6
            ORDER BY private_record_count DESC
        """)
        results.append(df)
    filtered = [d for d in results if len(d) > 0]
    return pd.concat(filtered, ignore_index=True) if filtered else pd.DataFrame()


def run_tc16() -> pd.DataFrame:
    """TC16 — Public Instruments: INS_TYPEs that exist only in public data (no private orders or trades).
    Returns one row per INS_TYPE with counts from ORDERS and TRADES combined."""
    results = []
    for label, path in [("ORDERS", ORDERS_PATH), ("TRADES", TRADES_PATH)]:
        df = q(f"""
            WITH public_ins AS (
                SELECT DISTINCT INS_TYPE
                FROM {parquet_scan_expr(path)}
                WHERE PARTY IS NULL
            ),
            private_ins AS (
                SELECT DISTINCT INS_TYPE
                FROM {parquet_scan_expr(path)}
                WHERE PARTY IS NOT NULL
            ),
            public_only AS (
                SELECT pub.INS_TYPE
                FROM public_ins pub
                LEFT JOIN private_ins priv ON pub.INS_TYPE = priv.INS_TYPE
                WHERE priv.INS_TYPE IS NULL
            )
            SELECT
                po.INS_TYPE,
                '{label}'                              AS source,
                COUNT(*)                               AS public_record_count,
                COUNT(DISTINCT o.ORIG_TRAN_ID)         AS public_unique_ids,
                'INS_TYPE traded in public data only — no private orders/trades' AS error_detail
            FROM {parquet_scan_expr(path)} o
            INNER JOIN public_only po ON o.INS_TYPE = po.INS_TYPE
            WHERE o.PARTY IS NULL
            GROUP BY po.INS_TYPE, source, error_detail
            ORDER BY public_record_count DESC
        """)
        results.append(df)
    filtered = [d for d in results if len(d) > 0]
    if not filtered:
        return pd.DataFrame()
    combined = pd.concat(filtered, ignore_index=True)
    # Pivot: one row per INS_TYPE with ORDERS and TRADES counts side by side
    pivot = combined.pivot_table(
        index="INS_TYPE",
        columns="source",
        values=["public_record_count", "public_unique_ids"],
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    pivot.columns = [
        "INS_TYPE" if c[1] == "" else f"{c[1]}_{c[0]}"
        for c in pivot.columns
    ]
    pivot["error_detail"] = "INS_TYPE traded in public data only — no private orders/trades"
    return pivot.sort_values("ORDERS_public_record_count", ascending=False, ignore_index=True)


def run_tc17() -> pd.DataFrame:
    """TC17 — Orderbook Inversion: bid price > ask price at the exact same millisecond per INS_TYPE.
    Returns individual order records involved in each inversion."""
    return q(f"""
        WITH crossing_ts AS (
            SELECT
                TRAN_DATETIME,
                INS_TYPE,
                MAX(CASE WHEN BID_ASK = 'B' THEN PRICE END) AS best_bid,
                MIN(CASE WHEN BID_ASK = 'A' THEN PRICE END) AS best_ask
            FROM {parquet_scan_expr(ORDERS_PATH)}
            WHERE BID_ASK IN ('B', 'A') AND PRICE IS NOT NULL
            GROUP BY TRAN_DATETIME, INS_TYPE
            HAVING best_bid IS NOT NULL AND best_ask IS NOT NULL AND best_bid > best_ask
        )
        SELECT
            o.ORIG_TRAN_ID,
            o.INS_TYPE,
            o.TRAN_DATETIME,
            o.PARTY,
            o.BID_ASK,
            ROUND(o.PRICE, 4)                     AS PRICE,
            o.VOLUME,
            ROUND(c.best_bid, 4)                  AS best_bid,
            ROUND(c.best_ask, 4)                  AS best_ask,
            ROUND(c.best_bid - c.best_ask, 4)     AS inversion_spread,
            'Bid price exceeds Ask price at same millisecond (inverted orderbook)' AS error_detail
        FROM {parquet_scan_expr(ORDERS_PATH)} o
        INNER JOIN crossing_ts c
            ON o.TRAN_DATETIME = c.TRAN_DATETIME AND o.INS_TYPE = c.INS_TYPE
        WHERE o.BID_ASK IN ('B', 'A') AND o.PRICE IS NOT NULL
        ORDER BY inversion_spread DESC, o.INS_TYPE, o.TRAN_DATETIME
        LIMIT 20000
    """)


def run_tc18() -> pd.DataFrame:
    """TC18 — Per-INS_TYPE reconciliation: private executed/partial orders vs private trades.
    Shows ANALYSIS_DATE, INS_TYPE, ORIG_INS_TYPE, TRAN_INS_TYPE, TradesCount, Exec_orders, DIFFERENCE."""
    return q(f"""
        WITH exec_orders AS (
            SELECT INS_TYPE, ORIG_INS_TYPE, TRAN_INS_TYPE,
                   COUNT(DISTINCT ORIG_TRAN_ID)  AS Exec_orders
            FROM {parquet_scan_expr(ORDERS_PATH)}
            WHERE PARTY IS NOT NULL AND TRAN_STATUS IN ('E', 'P')
            GROUP BY INS_TYPE, ORIG_INS_TYPE, TRAN_INS_TYPE
        ),
        trade_counts AS (
            SELECT INS_TYPE, COUNT(*) AS TradesCount
            FROM {parquet_scan_expr(TRADES_PATH)}
            WHERE PARTY IS NOT NULL
            GROUP BY INS_TYPE
        )
        SELECT
            '{DATA_DATE}'                                         AS ANALYSIS_DATE,
            COALESCE(o.INS_TYPE,     t.INS_TYPE)                 AS INS_TYPE,
            o.ORIG_INS_TYPE,
            o.TRAN_INS_TYPE,
            COALESCE(t.TradesCount,  0)                          AS TradesCount,
            COALESCE(o.Exec_orders,  0)                          AS Exec_orders,
            COALESCE(t.TradesCount, 0) - COALESCE(o.Exec_orders, 0) AS DIFFERENCE
        FROM exec_orders o
        FULL OUTER JOIN trade_counts t ON o.INS_TYPE = t.INS_TYPE
        ORDER BY ABS(COALESCE(t.TradesCount, 0) - COALESCE(o.Exec_orders, 0)) DESC, INS_TYPE
    """)


TC_RUNNERS = {
    "TC1":  run_tc1,  "TC2":  run_tc2,  "TC3":  run_tc3,  "TC4":  run_tc4,
    "TC5":  run_tc5,  "TC6":  run_tc6,  "TC7":  run_tc7,  "TC8":  run_tc8,
    "TC9":  run_tc9,  "TC10": run_tc10, "TC11": run_tc11, "TC12": run_tc12,
    "TC13": run_tc13, "TC14": run_tc14, "TC15": run_tc15,
    "TC16": run_tc16, "TC17": run_tc17, "TC18": run_tc18,
}


def fetch_full_lifecycle(orig_tran_ids: list, source: str) -> pd.DataFrame:
    """Fetch ALL records for the given ORIG_TRAN_IDs, sorted by ORIG_TRAN_ID, SORT_ID."""
    if not orig_tran_ids:
        return pd.DataFrame()
    path = ORDERS_PATH if source == "ORDERS" else TRADES_PATH
    ids_limited = orig_tran_ids[:100]
    id_list = ", ".join(f"'{i}'" for i in ids_limited)
    return q(f"""
        SELECT ORIG_TRAN_ID, SORT_ID, TRAN_STATUS, TRAN_DATETIME,
               COUNTRY, MARKET_AREA, DELIVERY_PERIOD, PRICE, VOLUME,
               INS_TYPE, ORIG_INS_TYPE, PARTY,
               CASE WHEN PARTY IS NULL THEN 'Public' ELSE PARTY END AS data_type
        FROM {parquet_scan_expr(path)}
        WHERE ORIG_TRAN_ID IN ({id_list})
        ORDER BY ORIG_TRAN_ID, SORT_ID
    """)


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"## 🧪 {sel_exchange} DQE — Test Cases")
st.caption(f"Trading Date: **{sel_date}** | CLIENT_ORDERS + CLIENT_TRADES")

# Dataset size banner
_o = _counts_early["ORDERS"]; _t = _counts_early["TRADES"]
_pub = _counts_early["PUBLIC"]; _priv = _counts_early["PRIVATE"]
st.info(
    f"📦 **Dataset size** — "
    f"Orders: **{_o:,}** ({_pub:,} public · {_priv:,} private)  |  "
    f"Trades: **{_t:,}**",
    icon=None,
)
st.divider()

# ── Sidebar controls ─────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🧪 Test Cases")
    st.divider()

    # Client filter
    _all_parties = get_parties(ORDERS_PATH)
    _party_opts  = ["All clients"] + _all_parties
    _prev_party  = st.session_state.get("sel_party", "All clients")
    _party_idx   = _party_opts.index(_prev_party) if _prev_party in _party_opts else 0
    sel_party    = st.selectbox("👤 Client", _party_opts, index=_party_idx, key="tc_party")
    st.session_state["sel_party"] = sel_party
    st.divider()

    run_all   = st.button("▶️  Run All Tests", type="primary", use_container_width=True)
    clear_all = st.button("🗑️  Clear Current Results", use_container_width=True)
    st.divider()
    selected_tcs = st.multiselect(
        "Run specific TCs only",
        options=[t[0] for t in TC_REGISTRY],
        default=[],
        placeholder="Leave blank = run all",
    )
    run_sel = st.button("▶️  Run Selected", use_container_width=True)

# ── Session state ─────────────────────────────────────────────────────────────
if "tc_results" not in st.session_state:
    st.session_state["tc_results"] = {}   # keyed by (exchange, date)

_run_key = (sel_exchange, sel_date)

if clear_all:
    st.session_state["tc_results"].pop(_run_key, None)
    st.rerun()

# ── Trigger execution ─────────────────────────────────────────────────────────
tcs_to_run = []
if run_all:
    tcs_to_run = [t[0] for t in TC_REGISTRY]
elif run_sel and selected_tcs:
    tcs_to_run = selected_tcs

if tcs_to_run:
    if _run_key not in st.session_state["tc_results"]:
        st.session_state["tc_results"][_run_key] = {}
    progress = st.progress(0, text="Starting…")
    for idx, tc_id in enumerate(tcs_to_run):
        progress.progress((idx) / len(tcs_to_run), text=f"Running {tc_id}…")
        try:
            t0      = time.perf_counter()
            result  = TC_RUNNERS[tc_id]()
            elapsed = round(time.perf_counter() - t0, 1)
            st.session_state["tc_results"][_run_key][tc_id] = {"data": result, "error": None, "elapsed": elapsed}
        except Exception as e:
            st.session_state["tc_results"][_run_key][tc_id] = {"data": pd.DataFrame(), "error": str(e), "elapsed": None}
    progress.progress(1.0, text="✅ All tests complete")

results = st.session_state["tc_results"].get(_run_key, {})
counts  = get_record_counts(ORDERS_PATH, TRADES_PATH)

# ── Apply client filter to violation DataFrames (display only — stored results unchanged) ──
def apply_client_filter(df: pd.DataFrame) -> pd.DataFrame:
    if sel_party == "All clients" or df.empty:
        return df
    if "PARTY" in df.columns:
        # Include private records for this client + all public (PARTY IS NULL) records
        return df[df["PARTY"].isna() | (df["PARTY"] == sel_party)]
    return df

# Scope → denominator mapping (adjusted for client filter)
if sel_party == "All clients":
    SCOPE_TOTAL = {
        "Both":                    counts["ORDERS"] + counts["TRADES"],
        "CLIENT_ORDERS":           counts["ORDERS"],
        "CLIENT_ORDERS (Public)":  counts["PUBLIC"],
        "CLIENT_ORDERS (Private)": counts["PRIVATE"],
        "Both (Private)":          counts["PRIVATE"],
    }
else:
    _client_total = counts["PRIVATE"] + counts["PUBLIC"]
    SCOPE_TOTAL = {k: _client_total for k in [
        "Both", "CLIENT_ORDERS", "CLIENT_ORDERS (Public)",
        "CLIENT_ORDERS (Private)", "Both (Private)",
    ]}

# ── Summary table ─────────────────────────────────────────────────────────────
st.markdown("### 📊 Test Case Summary")

summary_rows = []
for tc_id, name, scope, description in TC_REGISTRY:
    res = results.get(tc_id)
    if res is None:
        status, violations, success_rate, exec_time = "⏳ PENDING", "—", "—", "—"
    elif res["error"]:
        status, violations, success_rate, exec_time = "💥 ERROR", res["error"][:60], "—", "—"
    else:
        df    = apply_client_filter(res["data"])
        total = SCOPE_TOTAL.get(scope, counts["ORDERS"])
        e     = res.get("elapsed")
        exec_time = f"{e:.1f}s" if e is not None else "—"
        # TC10 is informational — always PASS, show min/max timestamps
        if tc_id == "TC10":
            status       = badge("PASS")
            violations   = "INFO"
            n            = 0
            success_rate = "100.00%"
        # TC18 — violations = INS_TYPEs with non-zero DIFFERENCE
        elif tc_id == "TC18":
            n          = int((df["DIFFERENCE"] != 0).sum()) if "DIFFERENCE" in df.columns else 0
            status     = badge("PASS" if n == 0 else "FAIL")
            violations = f"{n:,}" if n > 0 else "0"
            if total > 0:
                pct = max(0.0, (total - n) / total * 100)
                if n > 0:
                    pct = min(pct, 99.99)
                success_rate = f"{pct:.2f}%"
            else:
                success_rate = "—"
        else:
            n          = len(df)
            status     = badge("PASS" if n == 0 else "FAIL")
            violations = f"{n:,}" if n > 0 else "0"
        if total > 0:
            pct = max(0.0, (total - n) / total * 100)
            # Cap at 99.99% for any FAIL — prevents rounding 99.999…% → 100.00%
            if n > 0:
                pct = min(pct, 99.99)
            success_rate = f"{pct:.2f}%"
        else:
            success_rate = "—"
    summary_rows.append({
        "TC":           tc_id,
        "Name":         name,
        "Scope":        scope,
        "Description":  description,
        "Status":       status,
        "Violations":   violations,
        "Success Rate": success_rate,
        "Time":         exec_time,
    })

summary_df = pd.DataFrame(summary_rows)

def color_row(df):
    """Row-level styler: status column drives both Status and Success Rate colors."""
    styles = pd.DataFrame("", index=df.index, columns=df.columns)
    for i, row in df.iterrows():
        s = str(row.get("Status", ""))
        if "PASS" in s:
            styles.at[i, "Status"]       = "color:#2ca02c;font-weight:bold"
            styles.at[i, "Success Rate"] = "color:#2ca02c;font-weight:bold"
        elif "FAIL" in s:
            styles.at[i, "Status"]       = "color:#d62728;font-weight:bold"
            styles.at[i, "Success Rate"] = "color:#d62728;font-weight:bold"
        elif "PENDING" in s:
            styles.at[i, "Status"] = "color:#888"
        elif "ERROR" in s:
            styles.at[i, "Status"]       = "color:#ff7f0e;font-weight:bold"
            styles.at[i, "Success Rate"] = "color:#ff7f0e;font-weight:bold"
    return styles

st.dataframe(
    summary_df.style.apply(color_row, axis=None),
    use_container_width=True, hide_index=True, height=640,
    column_order=["TC", "Name", "Scope", "Description", "Status", "Violations", "Success Rate", "Time"],
)

if not results:
    st.info("👆 Click **Run All Tests** in the sidebar to execute all test cases.")
    st.stop()

st.divider()

# ── Per-TC detail panels ──────────────────────────────────────────────────────
st.markdown("### 🔍 Drill-Down by Test Case")

for tc_id, name, scope, description in TC_REGISTRY:
    res = results.get(tc_id)
    if res is None:
        continue

    df    = apply_client_filter(res.get("data", pd.DataFrame()))
    err   = res.get("error")

    if tc_id == "TC10":
        n_violations = int(df["records_outside_CET_day"].astype(int).sum()) if "records_outside_CET_day" in df.columns else 0
    else:
        n_violations = len(df)

    client_tag = f"  |  Client: `{sel_party}`" if sel_party != "All clients" else ""
    status_str = badge("PASS" if n_violations == 0 else "FAIL") if not err else "💥 ERROR"
    label      = f"{status_str}  **{tc_id} — {name}**  |  Scope: `{scope}`{client_tag}  |  Violations: **{n_violations:,}**"

    with st.expander(label, expanded=(n_violations > 0 and n_violations < 50000)):
        st.caption(f"_{description}_")

        # TC-specific notes
        if tc_id == "TC7" and n_violations == 0:
            if df.empty:
                st.info("ℹ️ No auto-close events found in this day's data (SOURCE does not contain 'auto clos'). PASS.")
            else:
                st.success("✅ All auto-close events occurred before end of trading day.")

        if err:
            st.error(f"Error running {tc_id}: {err}")
            continue

        if n_violations == 0:
            st.success(f"✅ No violations found.")
            # Always show info tables even on PASS
            if tc_id == "TC10":
                st.dataframe(df, use_container_width=True, hide_index=True)
            if tc_id == "TC18":
                st.dataframe(df, use_container_width=True, hide_index=True)
            continue

        # TC10 — informational summary (never has violations)
        if tc_id == "TC10":
            st.dataframe(df, use_container_width=True, hide_index=True)
            continue

        # TC18 — show only rows with non-zero DIFFERENCE; highlight them red
        if tc_id == "TC18":
            diff_df = df[df["DIFFERENCE"] != 0].copy() if "DIFFERENCE" in df.columns else df
            def _style_tc18(row):
                return ["background-color:#4a1a1a;color:#ff9999;font-weight:600"] * len(row)
            if diff_df.empty:
                st.success("✅ All INS_TYPEs are balanced — TradesCount equals Exec_orders.")
            else:
                st.dataframe(
                    diff_df.style.apply(_style_tc18, axis=1),
                    use_container_width=True, hide_index=True,
                )
                st.error(f"❌ {len(diff_df):,} INS_TYPE(s) have a mismatch between trades count and executed orders.")
            csv18 = diff_df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Export TC18 reconciliation CSV", csv18,
                               file_name=f"nordpool_dqe_TC18_{DATA_DATE}.csv",
                               mime="text/csv", key="export_TC18")
            continue

        # Stats row
        col1, col2, col3 = st.columns(3)
        col1.metric("Violations", f"{n_violations:,}")
        if "source" in df.columns:
            col2.metric("Orders", f"{(df['source']=='ORDERS').sum():,}")
            col3.metric("Trades", f"{(df['source']=='TRADES').sum():,}")

        # Optional chart for high-volume TCs (skip TC17 — breakdown not useful there)
        if n_violations > 10 and "COUNTRY" in df.columns and tc_id != "TC17":
            country_agg = df["COUNTRY"].value_counts().reset_index()
            country_agg.columns = ["COUNTRY", "count"]
            fig = px.bar(country_agg.head(15), x="COUNTRY", y="count",
                         title=f"{tc_id} — Violations by Country",
                         color="count", color_continuous_scale="Reds")
            fig.update_layout(height=280, margin=dict(t=40, b=20))
            st.plotly_chart(fig, use_container_width=True)

        if n_violations > 10 and "error_detail" in df.columns and tc_id != "TC17":
            err_agg = df["error_detail"].value_counts().reset_index()
            err_agg.columns = ["Error Type", "Count"]
            fig2 = px.bar(err_agg.head(10), x="Count", y="Error Type", orientation="h",
                          title=f"{tc_id} — Error Type Breakdown",
                          color="Count", color_continuous_scale="Oranges")
            fig2.update_layout(height=max(200, len(err_agg) * 30 + 60), margin=dict(t=40, b=20))
            st.plotly_chart(fig2, use_container_width=True)

        # Filter within violations
        if n_violations > 20:
            # TC17: filter by INS_TYPE, show buy-side party, full TRAN_DATETIME
            if tc_id == "TC17":
                # Keep only bid (buy) side rows for display; rename PARTY to Buy_Party
                disp17 = df[df["BID_ASK"] == "B"].copy() if "BID_ASK" in df.columns else df.copy()
                if "PARTY" in disp17.columns:
                    disp17 = disp17.rename(columns={"PARTY": "Buy_Party"})
                show17 = [c for c in ["ORIG_TRAN_ID", "INS_TYPE", "TRAN_DATETIME",
                                       "Buy_Party", "PRICE", "VOLUME",
                                       "best_bid", "best_ask", "inversion_spread"]
                           if c in disp17.columns]
                ins_filter = st.selectbox(
                    f"🔎 Filter TC17 by INS_TYPE",
                    ["All"] + sorted(df["INS_TYPE"].dropna().unique().tolist()),
                    key=f"filter_{tc_id}",
                )
                view_df = disp17[show17] if show17 else disp17
                if ins_filter != "All":
                    view_df = view_df[view_df["INS_TYPE"] == ins_filter]
                # Ensure full TRAN_DATETIME string is shown
                if "TRAN_DATETIME" in view_df.columns:
                    view_df = view_df.copy()
                    view_df["TRAN_DATETIME"] = view_df["TRAN_DATETIME"].astype(str)
                st.dataframe(view_df.head(2000), use_container_width=True, hide_index=True, height=400)
                st.caption(f"Showing {min(len(view_df), 2000):,} of {len(disp17):,} bid-side violations")
            else:
                show_cols = [c for c in ["ORIG_TRAN_ID", "SORT_ID", "TRAN_STATUS",
                                          "TRAN_DATETIME", "COUNTRY", "MARKET_AREA",
                                          "DELIVERY_PERIOD", "error_detail", "source"]
                             if c in df.columns]
                search = st.text_input(f"🔎 Filter {tc_id} by ORIG_TRAN_ID",
                                       key=f"filter_{tc_id}", placeholder="Type to filter…")
                view_df = df[show_cols] if show_cols else df
                if search:
                    mask = view_df.apply(lambda r: r.astype(str).str.contains(search, case=False).any(), axis=1)
                    view_df = view_df[mask]
                st.dataframe(view_df.head(2000), use_container_width=True, hide_index=True, height=400)
                st.caption(f"Showing {min(len(view_df), 2000):,} of {n_violations:,} violations")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)

        # ── TC2, TC3, TC9: full lifecycle drill-down ─────────────────────────
        if tc_id in ("TC2", "TC3", "TC9") and n_violations > 0 and "ORIG_TRAN_ID" in df.columns:
            st.markdown("---")
            st.markdown("#### 🔬 Full Lifecycle for Violated ORIG_TRAN_IDs")
            st.caption(
                "All records belonging to each violated ORIG_TRAN_ID, sorted by ORIG_TRAN_ID → SORT_ID. "
                "Capped at 100 unique IDs per source."
            )

            # Collect unique violated IDs per source
            sources_in_df = df["source"].unique() if "source" in df.columns else ["ORDERS"]
            for src in sorted(sources_in_df):
                src_violations = df[df["source"] == src] if "source" in df.columns else df
                violated_ids   = src_violations["ORIG_TRAN_ID"].dropna().unique().tolist()
                total_violated = len(violated_ids)

                st.markdown(f"**{src}** — {total_violated:,} violated ORIG_TRAN_IDs "
                            f"{'(showing first 100)' if total_violated > 100 else ''}")

                with st.spinner(f"Fetching full lifecycle for {min(total_violated, 100)} IDs…"):
                    lifecycle_df = fetch_full_lifecycle(violated_ids, src)

                if lifecycle_df.empty:
                    st.info("No lifecycle records found.")
                    continue

                # Colour rows by status — saturated colours with white text for dark-mode visibility
                STATUS_STYLE = {
                    "V": "background-color: #1a6b2a; color: #ffffff; font-weight: 600",   # dark green
                    "A": "background-color: #1a4f8a; color: #ffffff; font-weight: 600",   # dark blue
                    "P": "background-color: #8a5a00; color: #ffffff; font-weight: 600",   # dark amber
                    "E": "background-color: #0a5c4a; color: #ffffff; font-weight: 600",   # dark teal
                    "C": "background-color: #8a1a1a; color: #ffffff; font-weight: 600",   # dark red
                }

                def style_lifecycle(row):
                    s = STATUS_STYLE.get(str(row.get("TRAN_STATUS", "")), "")
                    base = [s] * len(row) if s else [""] * len(row)
                    col_idx = {c: i for i, c in enumerate(row.index)}
                    if "VOLUME" in col_idx and pd.notna(row["VOLUME"]) and row["VOLUME"] == 0:
                        base[col_idx["VOLUME"]] = "background-color: #cc0000; color: white; font-weight: bold"
                    if "PRICE" in col_idx and pd.notna(row["PRICE"]) and row["PRICE"] < 0:
                        base[col_idx["PRICE"]] = "background-color: #b45309; color: white; font-weight: bold"
                    return base

                # Optional: filter to a specific ORIG_TRAN_ID (display only — CSV always exports full set)
                id_filter = st.text_input(
                    "🔎 Jump to specific ORIG_TRAN_ID",
                    key=f"lifecycle_filter_{tc_id}_{src}",
                    placeholder="Paste an ORIG_TRAN_ID to focus…"
                )
                display_df = lifecycle_df
                if id_filter:
                    display_df = lifecycle_df[lifecycle_df["ORIG_TRAN_ID"].str.contains(id_filter, case=False, na=False)]

                st.dataframe(
                    display_df.style.apply(style_lifecycle, axis=1),
                    use_container_width=True, hide_index=True, height=500,
                )
                st.caption(
                    f"Showing {len(display_df):,} of {len(lifecycle_df):,} records  |  "
                    f"{lifecycle_df['ORIG_TRAN_ID'].nunique():,} unique IDs  |  "
                    "🟢 V=Valid  🔵 A=Amend  🟠 P=Partial  🟩 E=Executed  🔴 C=Cancel"
                )
                # Export always uses the FULL lifecycle (unfiltered) so it's never empty
                lc_csv = lifecycle_df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label=f"⬇️  Export {tc_id} full lifecycle ({src}) CSV ({len(lifecycle_df):,} rows)",
                    data=lc_csv,
                    file_name=f"nordpool_dqe_{tc_id}_{src}_lifecycle_{DATA_DATE}.csv",
                    mime="text/csv",
                    key=f"export_lc_{tc_id}_{src}",
                )

        # Export violations CSV (skip for TC3/TC9 where lifecycle export above is preferred)
        if tc_id not in ("TC3", "TC9"):
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label=f"⬇️  Export {tc_id} violations CSV",
                data=csv,
                file_name=f"nordpool_dqe_{tc_id}_{DATA_DATE}.csv",
                mime="text/csv",
                key=f"export_{tc_id}",
            )

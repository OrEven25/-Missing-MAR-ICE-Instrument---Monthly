"""
DQE — Trading Activity
TradingView-style single-instrument activity chart.
TRAN_DATETIME shown in CET/CEST.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import duckdb
import pandas as pd
import plotly.graph_objects as go
import re
from datetime import datetime, timedelta
from utils import parquet_scan_expr, discover_all_data_files

UTC_OFFSET = 2   # CEST (UTC+2) for June; change to 1 for CET (Nov–Mar)

# ─────────────────────────────────────────────────────────────────────────────
# DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────
all_data      = discover_all_data_files()
all_exchanges = sorted(all_data.keys())
all_dates_map = {d: d[8:10] + "/" + d[5:7] + "/" + d[:4]
                 for ex in all_data.values() for d in ex}

st.markdown("## 📈 Trading Activity")
st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# CASCADE FILTERS  (Exchange → Date → INS_TYPE)
# ─────────────────────────────────────────────────────────────────────────────
f1, f2, f3 = st.columns([1, 1, 2])

with f1:
    st.markdown('<p style="font-size:0.78rem;font-weight:600;color:#a0aec0;margin-bottom:2px">🏦 Exchange</p>', unsafe_allow_html=True)
    sel_exchange = st.selectbox("Exchange", all_exchanges, label_visibility="collapsed")

available_dates = sorted(all_data.get(sel_exchange, {}).keys(), reverse=True)

with f2:
    st.markdown('<p style="font-size:0.78rem;font-weight:600;color:#a0aec0;margin-bottom:2px">📅 Trading Date</p>', unsafe_allow_html=True)
    date_opts = [all_dates_map[d] for d in available_dates]
    sel_date_lbl = st.selectbox("Date", date_opts, label_visibility="collapsed")
    sel_date = available_dates[date_opts.index(sel_date_lbl)]

paths = all_data[sel_exchange][sel_date]

@st.cache_data(show_spinner=False, persist="disk")
def get_ins_types(orders_path) -> pd.DataFrame:
    return duckdb.query(f"""
        SELECT DISTINCT
            INS_TYPE,
            REGEXP_EXTRACT(INS_TYPE, '(Quarter_Hour|Half_Hour|Hour|Block)') AS ins_cat
        FROM {parquet_scan_expr(orders_path)}
        WHERE INS_TYPE IS NOT NULL
        ORDER BY INS_TYPE
    """).df()

ins_df     = get_ins_types(paths["orders"])
ins_type_list = ins_df["INS_TYPE"].tolist()

# Category badge colours
_CAT_COLORS = {
    "Quarter_Hour": "#9b59b6",
    "Half_Hour":    "#2980b9",
    "Hour":         "#27ae60",
    "Block":        "#e67e22",
}

def _cat_badge(cat: str) -> str:
    color = _CAT_COLORS.get(cat, "#555")
    return (f'<span style="background:{color};color:#fff;padding:2px 8px;'
            f'border-radius:4px;font-size:0.72rem;font-weight:600">{cat}</span>')

with f3:
    st.markdown('<p style="font-size:0.78rem;font-weight:600;color:#a0aec0;margin-bottom:2px">🎯 Instrument (INS_TYPE)</p>', unsafe_allow_html=True)
    sel_ins = st.selectbox("INS_TYPE", ins_type_list, label_visibility="collapsed")

# Auto-derive category from selected INS_TYPE and show as badge
sel_cat_row = ins_df[ins_df["INS_TYPE"] == sel_ins]
sel_cat     = sel_cat_row["ins_cat"].iloc[0] if not sel_cat_row.empty else ""
if sel_cat:
    st.markdown(_cat_badge(sel_cat), unsafe_allow_html=True)

st.markdown("---")

if not sel_ins:
    st.info("Select an instrument to view activity.")
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# QUERY — 1-minute buckets, split by Public/Private
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, persist="disk")
def get_activity(orders_path, trades_path, ins_type: str, utc_offset: int) -> tuple:
    safe = ins_type.replace("'", "''")

    # Best bid (max price) and best ask (min price) per minute
    orders = duckdb.query(f"""
        SELECT
            DATE_TRUNC('minute', TRAN_DATETIME + INTERVAL '{utc_offset} hours') AS ts_cet,
            BID_ASK,
            CASE WHEN BID_ASK = 'B' THEN MAX(PRICE) ELSE MIN(PRICE) END         AS best_price,
            COUNT(*)                                                              AS n,
            MAX(CASE WHEN PARTY IS NOT NULL THEN 1 ELSE 0 END)                   AS has_private
        FROM {parquet_scan_expr(orders_path)}
        WHERE INS_TYPE = '{safe}' AND BID_ASK IN ('B','A') AND PRICE IS NOT NULL
        GROUP BY ts_cet, BID_ASK
        ORDER BY ts_cet
    """).df()

    # Individual trades with ORIG_TRAN_ID in customdata
    trades = duckdb.query(f"""
        SELECT
            TRAN_DATETIME + INTERVAL '{utc_offset} hours'                        AS ts_cet,
            BUY_SELL,
            PRICE,
            VOLUME,
            ORIG_TRAN_ID,
            CASE WHEN PARTY IS NOT NULL THEN 1 ELSE 0 END                        AS is_private
        FROM {parquet_scan_expr(trades_path)}
        WHERE INS_TYPE = '{safe}' AND PRICE IS NOT NULL
        ORDER BY ts_cet
    """).df()

    return orders, trades


@st.cache_data(show_spinner=False, persist="disk")
def get_raw_orders(orders_path, ins_type: str, utc_offset: int) -> pd.DataFrame:
    """Individual order records (private only) for ORIG_TRAN_ID hover and TC17 focus."""
    safe = ins_type.replace("'", "''")
    return duckdb.query(f"""
        SELECT
            TRAN_DATETIME + INTERVAL '{utc_offset} hours'  AS ts_cet,
            BID_ASK,
            PRICE,
            VOLUME,
            ORIG_TRAN_ID,
            PARTY
        FROM {parquet_scan_expr(orders_path)}
        WHERE INS_TYPE = '{safe}' AND PARTY IS NOT NULL
          AND BID_ASK IN ('B','A') AND PRICE IS NOT NULL
        ORDER BY ts_cet
        LIMIT 100000
    """).df()


@st.cache_data(show_spinner=False, persist="disk")
def get_delivery_period(orders_path, ins_type: str) -> str | None:
    """Return the most common DELIVERY_PERIOD for this INS_TYPE."""
    safe = ins_type.replace("'", "''")
    df = duckdb.query(f"""
        SELECT DELIVERY_PERIOD, COUNT(*) AS n
        FROM {parquet_scan_expr(orders_path)}
        WHERE INS_TYPE = '{safe}' AND DELIVERY_PERIOD IS NOT NULL
        GROUP BY DELIVERY_PERIOD ORDER BY n DESC LIMIT 1
    """).df()
    return df.iloc[0]["DELIVERY_PERIOD"] if not df.empty else None


def parse_delivery_start(delivery_period: str, trading_date: str, utc_offset: int) -> datetime | None:
    """
    Parse delivery start time (CET) from DELIVERY_PERIOD code.

    Supported patterns:
    - CQ{HH}q{Q}  (Nordpool, OMIE) : hour HH, quarter Q (1-based) → HH:{(Q-1)*15}
    - CH{HH}       (EPEX hourly)    : hour HH → HH:00
    - NQ{HH}q{Q}  (EPEX Q-hour)   : same as CQ format
    """
    base = datetime.strptime(trading_date, "%Y-%m-%d")

    # CQ20q4 or NQ20q4 pattern
    m = re.match(r"[CcNn][Qq](\d{1,2})q(\d)", delivery_period)
    if m:
        hour    = int(m.group(1))
        quarter = int(m.group(2))
        minute  = (quarter - 1) * 15
        return base.replace(hour=hour, minute=minute)

    # CH21 pattern (EPEX hourly)
    m = re.match(r"[Cc][Hh](\d{1,2})", delivery_period)
    if m:
        hour = int(m.group(1))
        return base.replace(hour=hour, minute=0)

    return None

with st.spinner(f"Loading activity for {sel_ins}…"):
    orders_raw, trades_raw = get_activity(paths["orders"], paths["trades"], sel_ins, UTC_OFFSET)
    raw_orders = get_raw_orders(paths["orders"], sel_ins, UTC_OFFSET)

if orders_raw.empty and trades_raw.empty:
    st.warning(f"No data found for **{sel_ins}** on {sel_date_lbl}.")
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# INFO BAR + PRIVATE TOGGLE
# ─────────────────────────────────────────────────────────────────────────────
total_orders = len(orders_raw)
total_trades = len(trades_raw)
priv_orders  = int(orders_raw["has_private"].sum()) if not orders_raw.empty else 0
priv_trades  = int(trades_raw["is_private"].sum())  if not trades_raw.empty else 0

bid_orders = int(orders_raw[orders_raw["BID_ASK"] == "B"]["n"].sum()) if not orders_raw.empty else 0
ask_orders = int(orders_raw[orders_raw["BID_ASK"] == "A"]["n"].sum()) if not orders_raw.empty else 0

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Bid Orders",        f"{bid_orders:,}")
m2.metric("Ask Orders",        f"{ask_orders:,}")
m3.metric("🔒 Private Orders", f"{priv_orders:,}")
m4.metric("Total Trades",      f"{total_trades:,}")
m5.metric("🔒 Priv Trades",    f"{priv_trades:,}")
with m6:
    private_only = st.toggle("🔒 Private only", value=False, key="priv_toggle")

st.caption(f"**{sel_ins}**  ·  {sel_exchange}  ·  {sel_date_lbl}  ·  Time in CET/CEST (UTC+{UTC_OFFSET})")

# ─────────────────────────────────────────────────────────────────────────────
# DELIVERY START LINE
# ─────────────────────────────────────────────────────────────────────────────
delivery_period = get_delivery_period(paths["orders"], sel_ins)
delivery_start  = parse_delivery_start(delivery_period, sel_date, UTC_OFFSET) if delivery_period else None

# ─────────────────────────────────────────────────────────────────────────────
# SPLIT ORDERS INTO BID / ASK  &  TRADES INTO BUY / SELL + private
# ─────────────────────────────────────────────────────────────────────────────
bid_df = orders_raw[orders_raw["BID_ASK"] == "B"].copy() if not orders_raw.empty else pd.DataFrame()
ask_df = orders_raw[orders_raw["BID_ASK"] == "A"].copy() if not orders_raw.empty else pd.DataFrame()

buy_trades  = trades_raw[trades_raw["BUY_SELL"] == "B"].copy() if not trades_raw.empty else pd.DataFrame()
sell_trades = trades_raw[trades_raw["BUY_SELL"] == "S"].copy() if not trades_raw.empty else pd.DataFrame()

priv_buy_trades  = buy_trades[buy_trades["is_private"]  == 1] if not buy_trades.empty  else pd.DataFrame()
pub_buy_trades   = buy_trades[buy_trades["is_private"]  == 0] if not buy_trades.empty  else pd.DataFrame()
priv_sell_trades = sell_trades[sell_trades["is_private"] == 1] if not sell_trades.empty else pd.DataFrame()
pub_sell_trades  = sell_trades[sell_trades["is_private"] == 0] if not sell_trades.empty else pd.DataFrame()

priv_bid_orders  = raw_orders[raw_orders["BID_ASK"] == "B"] if not raw_orders.empty else pd.DataFrame()
priv_ask_orders  = raw_orders[raw_orders["BID_ASK"] == "A"] if not raw_orders.empty else pd.DataFrame()

# ─────────────────────────────────────────────────────────────────────────────
# TRADINGVIEW-STYLE CHART — price on Y axis
# ─────────────────────────────────────────────────────────────────────────────
DARK_BG        = "#0d1117"
GRID_COLOR     = "#1c2333"
AXIS_COLOR     = "#8b949e"
COL_BID        = "#26a69a"   # green  (public bid / buy)
COL_ASK        = "#ef5350"   # red    (public ask / sell)
COL_PRIV_BUY   = "#F58518"   # orange (private buy trade)
COL_PRIV_SELL  = "#FFD700"   # yellow (private sell trade)
COL_DELIVERY   = "#f0e130"
COL_INVERSION  = "#ff9900"

fig = go.Figure()

# ── Public bid/ask lines (hidden in private-only mode) ───────────────────────
if not private_only:
    if not bid_df.empty:
        fig.add_trace(go.Scatter(
            x=bid_df["ts_cet"], y=bid_df["best_price"],
            name="Bid (best)",
            mode="lines",
            line=dict(color=COL_BID, width=1.8, shape="hv"),
            hovertemplate="%{x|%H:%M}  Best Bid: %{y:.2f}<extra></extra>",
        ))
    if not ask_df.empty:
        fig.add_trace(go.Scatter(
            x=ask_df["ts_cet"], y=ask_df["best_price"],
            name="Ask (best)",
            mode="lines",
            line=dict(color=COL_ASK, width=1.8, shape="hv"),
            hovertemplate="%{x|%H:%M}  Best Ask: %{y:.2f}<extra></extra>",
        ))

# ── Private bid/ask lines (shown in private-only mode) ───────────────────────
if private_only and not raw_orders.empty:
    for side, color, label in [("B", COL_PRIV_BUY, "🔒 Priv Bid"), ("A", COL_PRIV_SELL, "🔒 Priv Ask")]:
        side_df = raw_orders[raw_orders["BID_ASK"] == side].copy()
        if not side_df.empty:
            # Aggregate to minute for line; keep ORIG_TRAN_ID count
            agg = side_df.groupby(
                side_df["ts_cet"].dt.floor("min")
            )["PRICE"].agg(lambda x: x.max() if side == "B" else x.min()).reset_index()
            agg.columns = ["ts_cet", "best_price"]
            fig.add_trace(go.Scatter(
                x=agg["ts_cet"], y=agg["best_price"],
                name=label,
                mode="lines",
                line=dict(color=color, width=1.8, shape="hv"),
                hovertemplate="%{x|%H:%M}  " + label + ": %{y:.2f}<extra></extra>",
            ))

# ── Public trades (hidden in private-only mode) ──────────────────────────────
if not private_only:
    if not pub_buy_trades.empty:
        fig.add_trace(go.Scatter(
            x=pub_buy_trades["ts_cet"], y=pub_buy_trades["PRICE"],
            name="Buy Trade",
            mode="markers",
            marker=dict(color=COL_BID, size=5, symbol="circle",
                        opacity=0.7, line=dict(color=COL_BID, width=0.5)),
            customdata=pub_buy_trades[["VOLUME", "ORIG_TRAN_ID"]].values,
            hovertemplate="%{x|%H:%M:%S}  Buy @ %{y:.2f}  Vol: %{customdata[0]:.1f}<br>ID: %{customdata[1]}<extra></extra>",
        ))
    if not pub_sell_trades.empty:
        fig.add_trace(go.Scatter(
            x=pub_sell_trades["ts_cet"], y=pub_sell_trades["PRICE"],
            name="Sell Trade",
            mode="markers",
            marker=dict(color=COL_ASK, size=5, symbol="circle",
                        opacity=0.7, line=dict(color=COL_ASK, width=0.5)),
            customdata=pub_sell_trades[["VOLUME", "ORIG_TRAN_ID"]].values,
            hovertemplate="%{x|%H:%M:%S}  Sell @ %{y:.2f}  Vol: %{customdata[0]:.1f}<br>ID: %{customdata[1]}<extra></extra>",
        ))

# ── Private buy trades — orange diamonds ─────────────────────────────────────
if not priv_buy_trades.empty:
    fig.add_trace(go.Scatter(
        x=priv_buy_trades["ts_cet"], y=priv_buy_trades["PRICE"],
        name="🔒 Private Buy",
        mode="markers",
        marker=dict(color=COL_PRIV_BUY, size=13, symbol="diamond",
                    line=dict(color="#ffffff", width=1.5)),
        customdata=priv_buy_trades[["VOLUME", "ORIG_TRAN_ID"]].values,
        hovertemplate="%{x|%H:%M:%S}  🔒 Buy @ %{y:.2f}  Vol: %{customdata[0]:.1f}<br>ID: %{customdata[1]}<extra></extra>",
    ))

# ── Private sell trades — yellow diamonds ────────────────────────────────────
if not priv_sell_trades.empty:
    fig.add_trace(go.Scatter(
        x=priv_sell_trades["ts_cet"], y=priv_sell_trades["PRICE"],
        name="🔒 Private Sell",
        mode="markers",
        marker=dict(color=COL_PRIV_SELL, size=13, symbol="diamond",
                    line=dict(color="#ffffff", width=1.5)),
        customdata=priv_sell_trades[["VOLUME", "ORIG_TRAN_ID"]].values,
        hovertemplate="%{x|%H:%M:%S}  🔒 Sell @ %{y:.2f}  Vol: %{customdata[0]:.1f}<br>ID: %{customdata[1]}<extra></extra>",
    ))

# ── Private orders — tiny markers for ORIG_TRAN_ID hover when zoomed in ──────
if not priv_bid_orders.empty:
    fig.add_trace(go.Scatter(
        x=priv_bid_orders["ts_cet"], y=priv_bid_orders["PRICE"],
        name="🔒 Priv Bid Order",
        mode="markers",
        marker=dict(color=COL_PRIV_BUY, size=4, symbol="circle", opacity=0.5),
        customdata=priv_bid_orders["ORIG_TRAN_ID"].values,
        hovertemplate="%{x|%H:%M:%S.%3f}  Bid @ %{y:.4f}<br>ID: %{customdata}<extra>🔒 Order</extra>",
        visible="legendonly",
    ))
if not priv_ask_orders.empty:
    fig.add_trace(go.Scatter(
        x=priv_ask_orders["ts_cet"], y=priv_ask_orders["PRICE"],
        name="🔒 Priv Ask Order",
        mode="markers",
        marker=dict(color=COL_PRIV_SELL, size=4, symbol="circle", opacity=0.5),
        customdata=priv_ask_orders["ORIG_TRAN_ID"].values,
        hovertemplate="%{x|%H:%M:%S.%3f}  Ask @ %{y:.4f}<br>ID: %{customdata}<extra>🔒 Order</extra>",
        visible="legendonly",
    ))

# ── Delivery start vertical line ─────────────────────────────────────────────
if delivery_start:
    fig.add_vline(
        x=delivery_start.isoformat(),
        line=dict(color=COL_DELIVERY, width=2, dash="dash"),
        annotation_text=f"⚡ Delivery {delivery_start.strftime('%H:%M')} CET",
        annotation_position="top right",
        annotation_font=dict(color=COL_DELIVERY, size=12),
    )

# ── TC17 Inversion overlay — pull from Test Cases session state ───────────────
COL_INVERSION = "#ff9900"
_tc_results  = st.session_state.get("tc_results", {})
_tc17_key    = (sel_exchange, sel_date)
_tc17_res    = _tc_results.get(_tc17_key, {}).get("TC17")
_inv_df      = pd.DataFrame()

if _tc17_res and _tc17_res.get("data") is not None:
    _raw = _tc17_res["data"]
    if not _raw.empty and "INS_TYPE" in _raw.columns:
        _inv_df = _raw[
            (_raw["INS_TYPE"] == sel_ins) & (_raw["BID_ASK"] == "B")
        ].copy()
        if not _inv_df.empty:
            _inv_df["ts_cet"] = pd.to_datetime(
                _inv_df["TRAN_DATETIME"]
            ) + pd.Timedelta(hours=UTC_OFFSET)
            fig.add_trace(go.Scatter(
                x=_inv_df["ts_cet"],
                y=_inv_df["PRICE"],
                name="⚠️ Inversion (TC17)",
                mode="markers",
                marker=dict(color=COL_INVERSION, size=14, symbol="x",
                            line=dict(color="#ffffff", width=1.5)),
                customdata=_inv_df[["ORIG_TRAN_ID", "inversion_spread"]].values,
                hovertemplate=(
                    "%{x|%H:%M:%S.%3f}  ⚠️ Inversion<br>"
                    "ORIG_TRAN_ID: %{customdata[0]}<br>"
                    "Bid: %{y:.4f}  Spread: %{customdata[1]:.4f}"
                    "<extra>TC17</extra>"
                ),
            ))

# ── TC17 focus: highlight specific ORIG_TRAN_ID sent from Test Cases page ────
_focus_id  = st.session_state.get("tc17_focus_id")
_focus_ins = st.session_state.get("tc17_focus_ins")
if _focus_id and _focus_ins == sel_ins and not raw_orders.empty:
    _focus_rows = raw_orders[raw_orders["ORIG_TRAN_ID"] == _focus_id]
    if not _focus_rows.empty:
        fig.add_trace(go.Scatter(
            x=_focus_rows["ts_cet"],
            y=_focus_rows["PRICE"],
            name=f"🎯 {_focus_id[:22]}…",
            mode="markers",
            marker=dict(color="#ffffff", size=20, symbol="circle-open",
                        line=dict(color="#ff4444", width=3)),
            customdata=_focus_rows[["ORIG_TRAN_ID", "BID_ASK", "VOLUME"]].values,
            hovertemplate=(
                "%{x|%H:%M:%S.%3f}  🎯 TC17 Focus<br>"
                "ID: %{customdata[0]}<br>"
                "Side: %{customdata[1]}  Price: %{y:.4f}  Vol: %{customdata[2]:.2f}"
                "<extra>TC17 Focus</extra>"
            ),
        ))

# ── Layout ────────────────────────────────────────────────────────────────────
fig.update_layout(
    height=580,
    paper_bgcolor=DARK_BG,
    plot_bgcolor=DARK_BG,
    font=dict(color=AXIS_COLOR, family="monospace", size=11),
    hovermode="x unified",
    legend=dict(
        orientation="h", bgcolor="rgba(0,0,0,0)",
        x=0, y=1.05, font=dict(size=12),
    ),
    margin=dict(l=70, r=40, t=60, b=20),
    xaxis=dict(
        showgrid=True, gridcolor=GRID_COLOR, gridwidth=0.5,
        zeroline=False, color=AXIS_COLOR,
        tickformat="%H:%M",
        rangeslider=dict(visible=True, bgcolor="#161b22",
                         bordercolor=GRID_COLOR, thickness=0.06),
        rangeselector=dict(
            bgcolor="#161b22", activecolor="#30363d",
            font=dict(color=AXIS_COLOR),
            buttons=[
                dict(step="hour", stepmode="backward", count=1,  label="1h"),
                dict(step="hour", stepmode="backward", count=3,  label="3h"),
                dict(step="hour", stepmode="backward", count=6,  label="6h"),
                dict(step="all",                                   label="All"),
            ],
        ),
    ),
    yaxis=dict(
        showgrid=True, gridcolor=GRID_COLOR, gridwidth=0.5,
        zeroline=False, color=AXIS_COLOR, title="Price",
    ),
)

st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

if not _inv_df.empty:
    n_inv = len(_inv_df)
    st.caption(
        f"⚠️ **{n_inv:,} TC17 inversion point(s)** overlaid for `{sel_ins}` — "
        "orange ✕ markers show bid-side orders causing spread inversion. "
        "Hover for ORIG_TRAN_ID."
    )
elif _tc17_res is None:
    st.info("💡 Run **TC17** on the Test Cases page for this exchange/date to overlay orderbook inversions on this chart.", icon="ℹ️")

if delivery_start:
    st.caption(f"⚡ Delivery period: **{delivery_period}** → starts **{delivery_start.strftime('%H:%M')} CET** on {sel_date_lbl}")
elif delivery_period:
    st.caption(f"Delivery period: **{delivery_period}** (could not parse delivery time)")


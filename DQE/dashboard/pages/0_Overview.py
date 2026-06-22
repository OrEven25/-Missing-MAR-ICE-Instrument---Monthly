"""
DQE — Combined Overview
High-level snapshot of all data sources and trading dates.
Stats are loaded on demand to keep the page snappy.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px
from datetime import date
from utils import parquet_scan_expr,\
     discover_all_data_files

# ISO-2 → ISO-3 mapping (energy market countries)
ISO2_TO_ISO3 = {
    "AT":"AUT","BE":"BEL","BG":"BGR","CH":"CHE","CY":"CYP","CZ":"CZE",
    "DE":"DEU","DK":"DNK","EE":"EST","ES":"ESP","FI":"FIN","FR":"FRA",
    "GB":"GBR","GR":"GRC","HR":"HRV","HU":"HUN","IE":"IRL","IT":"ITA",
    "LT":"LTU","LU":"LUX","LV":"LVA","ME":"MNE","MK":"MKD","MT":"MLT",
    "NL":"NLD","NO":"NOR","PL":"POL","PT":"PRT","RO":"ROU","RS":"SRB",
    "SE":"SWE","SI":"SVN","SK":"SVK","TR":"TUR","UA":"UKR","AL":"ALB",
    "BA":"BIH","MD":"MDA","XK":"XKX","GE":"GEO","AM":"ARM","AZ":"AZE",
    "US":"USA","CA":"CAN","AU":"AUS","JP":"JPN","CN":"CHN","BR":"BRA",
}


# ─────────────────────────────────────────────────────────────────────────────
# CACHED STAT FETCHERS
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, persist="disk")
def get_source_stats(orders_path: str, trades_path: str) -> dict:
    o = duckdb.query(f"""
        SELECT
            COUNT(*)                                   AS total_orders,
            APPROX_COUNT_DISTINCT(ORIG_TRAN_ID)        AS unique_order_ids,
            COUNT(*) FILTER (WHERE PARTY IS NULL)      AS public_orders,
            COUNT(*) FILTER (WHERE PARTY IS NOT NULL)  AS private_orders,
            COUNT(DISTINCT COUNTRY)                    AS countries,
            COUNT(DISTINCT INS_TYPE)                   AS instruments,
            MIN(TRAN_DATETIME)                         AS earliest,
            MAX(TRAN_DATETIME)                         AS latest
        FROM {parquet_scan_expr(orders_path)}
    """).df().iloc[0]
    t = duckdb.query(f"""
        SELECT COUNT(*) AS total_trades, APPROX_COUNT_DISTINCT(ORIG_TRAN_ID) AS unique_trade_ids
        FROM {parquet_scan_expr(trades_path)}
    """).df().iloc[0]
    return {
        "total_orders":     int(o["total_orders"]),
        "unique_order_ids": int(o["unique_order_ids"]),
        "public_orders":    int(o["public_orders"]),
        "private_orders":   int(o["private_orders"]),
        "countries":        int(o["countries"]),
        "instruments":      int(o["instruments"]),
        "earliest":         str(o["earliest"])[:16],
        "latest":           str(o["latest"])[:16],
        "total_trades":     int(t["total_trades"]),
        "unique_trade_ids": int(t["unique_trade_ids"]),
    }


@st.cache_data(show_spinner=False, persist="disk")
def get_breakdown_stats(orders_path: str, trades_path: str) -> dict:
    schema_cols = duckdb.query(f"SELECT * FROM {parquet_scan_expr(orders_path)} LIMIT 0").df().columns.tolist()
    act_col = "TRAN_ACT_TYPE" if "TRAN_ACT_TYPE" in schema_cols else "TRAN_STATUS"

    ins_df = duckdb.query(f"""
        SELECT COALESCE(INS_TYPE, 'Unknown') AS ins_type, COUNT(*) AS cnt
        FROM {parquet_scan_expr(orders_path)}
        GROUP BY ins_type ORDER BY cnt DESC LIMIT 15
    """).df()

    act_df = duckdb.query(f"""
        SELECT COALESCE({act_col}, 'Unknown') AS action, COUNT(*) AS cnt
        FROM {parquet_scan_expr(orders_path)}
        GROUP BY action ORDER BY cnt DESC
    """).df()
    action_labels = {"V": "New (V)", "A": "Amend (A)", "E": "Cancel (E)", "C": "Change (C)", "P": "Pending (P)"}
    act_df["action"] = act_df["action"].map(lambda x: action_labels.get(x, x))

    hourly_df = duckdb.query(f"""
        SELECT hour(TRAN_DATETIME) AS hour, COUNT(*) AS cnt
        FROM {parquet_scan_expr(orders_path)}
        WHERE TRAN_DATETIME IS NOT NULL
        GROUP BY hour ORDER BY hour
    """).df()

    country_df = duckdb.query(f"""
        SELECT COALESCE(COUNTRY, 'Unknown') AS country, COUNT(*) AS cnt
        FROM {parquet_scan_expr(orders_path)}
        GROUP BY country ORDER BY cnt DESC LIMIT 15
    """).df()

    vol_df = duckdb.query(f"""
        SELECT
            SUM(VOLUME) FILTER (WHERE VOLUME > 0) AS total_volume,
            AVG(PRICE)  FILTER (WHERE PRICE  > 0) AS avg_price,
            AVG(VOLUME) FILTER (WHERE VOLUME > 0) AS avg_volume
        FROM {parquet_scan_expr(orders_path)}
    """).df().iloc[0]

    trades_ins_df = duckdb.query(f"""
        SELECT COALESCE(INS_TYPE, 'Unknown') AS ins_type, COUNT(*) AS cnt
        FROM {parquet_scan_expr(trades_path)}
        GROUP BY ins_type ORDER BY cnt DESC LIMIT 15
    """).df()

    return {
        "ins_type":     ins_df,
        "action_type":  act_df,
        "hourly":       hourly_df,
        "country":      country_df,
        "total_volume": float(vol_df["total_volume"] or 0),
        "avg_price":    float(vol_df["avg_price"]    or 0),
        "avg_volume":   float(vol_df["avg_volume"]   or 0),
        "trades_ins":   trades_ins_df,
    }


@st.cache_data(show_spinner=False, persist="disk")
def get_quick_counts(orders_path: str, trades_path: str, party: str | None) -> dict:
    """Fast count scan — per (path, client). Cached."""
    if party:
        where_o = f"WHERE PARTY = '{party}' OR PARTY IS NULL"
        where_t = f"WHERE PARTY = '{party}' OR PARTY IS NULL"
    else:
        where_o = ""
        where_t = ""
    o = duckdb.query(f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE PARTY IS NULL)     AS pub,
            COUNT(*) FILTER (WHERE PARTY IS NOT NULL) AS priv
        FROM {parquet_scan_expr(orders_path)} {where_o}
    """).df().iloc[0]
    t = duckdb.query(f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE PARTY IS NULL)     AS pub,
            COUNT(*) FILTER (WHERE PARTY IS NOT NULL) AS priv
        FROM {parquet_scan_expr(trades_path)} {where_t}
    """).df().iloc[0]
    return {
        "orders":         int(o["total"]),
        "orders_public":  int(o["pub"]),
        "orders_private": int(o["priv"]),
        "trades":         int(t["total"]),
        "trades_public":  int(t["pub"]),
        "trades_private": int(t["priv"]),
    }


@st.cache_data(show_spinner=False, persist="disk")
def get_all_parties(orders_path: str) -> list:
    df = duckdb.query(f"""
        SELECT DISTINCT PARTY AS p FROM {parquet_scan_expr(orders_path)}
        WHERE PARTY IS NOT NULL ORDER BY p
    """).df()
    return df["p"].tolist()


def file_size_mb(path: str) -> float:
    try:
        return Path(path).stat().st_size / 1_048_576
    except Exception:
        return 0.0


def fmt_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


# ─────────────────────────────────────────────────────────────────────────────
# DISCOVER
# ─────────────────────────────────────────────────────────────────────────────
all_data  = discover_all_data_files()
today     = date.today()
EXCHANGES = list(all_data.keys())
all_dates = sorted({d for dates in all_data.values() for d in dates}, reverse=True)
per_exchange = {ex: set(dates.keys()) for ex, dates in all_data.items()}
comparable_dates = sorted(
    {d for d in all_dates if all(d in per_exchange[ex] for ex in per_exchange)},
    reverse=True,
)

# ─────────────────────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## 🏠 DQE — Data Sources Overview")
st.caption(today.strftime("%d %b %Y"))

if not all_data:
    st.warning("No data sources found. Check EXCHANGE_DIRS in utils.py.")
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# TOP FILTER BAR — Client | Exchange | Date  (inline, not sidebar)
# ─────────────────────────────────────────────────────────────────────────────
# Collect all parties from first available file
_first_orders = next(
    (all_data[ex][d]["orders"] for ex in EXCHANGES for d in all_data[ex]),
    None,
)
all_parties   = get_all_parties(_first_orders) if _first_orders else []

f1, f2, f3 = st.columns(3)
with f1:
    sel_clients = st.multiselect(
        "👤 Client",
        options=all_parties,
        default=all_parties,
        placeholder="All clients",
        key="ov_clients",
    )
    # None = all clients; list of one = specific client
    party_filter = sel_clients[0] if len(sel_clients) == 1 else None
    client_label = sel_clients[0] if len(sel_clients) == 1 else "All Clients"

with f2:
    sel_exchanges = st.multiselect(
        "📡 Exchange",
        options=EXCHANGES,
        default=EXCHANGES,
        placeholder="All exchanges",
        key="ov_exchanges",
    )
    if not sel_exchanges:
        sel_exchanges = EXCHANGES

with f3:
    sel_dates = st.multiselect(
        "📅 Date",
        options=all_dates,
        default=all_dates,
        placeholder="All dates",
        key="ov_dates",
    )
    if not sel_dates:
        sel_dates = all_dates

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# LOAD STATS BUTTON
# ─────────────────────────────────────────────────────────────────────────────
if "loaded_stats" not in st.session_state:
    st.session_state["loaded_stats"] = set()

btn_col, note_col = st.columns([1, 5])
if btn_col.button("📊 Load Full Stats", type="primary", use_container_width=True):
    for ex in sel_exchanges:
        for d in all_data.get(ex, {}):
            if d in sel_dates:
                st.session_state["loaded_stats"].add((ex, d))
    st.rerun()
note_col.caption(
    "Scans parquet files for row counts, fill rates, breakdown stats etc. "
    "**Cached after first run** — subsequent loads are instant."
)

loaded = st.session_state.get("loaded_stats", set())

# ─────────────────────────────────────────────────────────────────────────────
# MATRIX TABLE  (always shown — cells show file size until stats loaded)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"#### 📋 Data Matrix — Client: **{client_label}**")

# Only include dates where at least one selected exchange has data
col_dates = sorted(
    [d for d in sel_dates if any(d in all_data.get(ex, {}) for ex in sel_exchanges)]
)

# Short date labels (MM/DD) to keep columns narrow
short_dates = {d: d[5:] for d in col_dates}   # "2026-06-16" → "06-16"

matrix_rows = []
for table_type in ["CLIENT_ORDERS", "CLIENT_TRADES"]:
    for exchange in sel_exchanges:
        for row_type in ["Total", "🔓 Public", "🔒 Private"]:
            row = {"Table": table_type if row_type == "Total" else "", "Source": exchange if row_type == "Total" else "", "Breakdown": row_type}
            for d in col_dates:
                col = short_dates[d]
                paths = all_data.get(exchange, {}).get(d)
                if paths is None:
                    row[col] = "—"
                elif (exchange, d) not in loaded:
                    sz = file_size_mb(paths["orders"] if table_type == "CLIENT_ORDERS" else paths["trades"])
                    row[col] = f"({sz:.0f}MB)" if row_type == "Total" else ""
                else:
                    counts = get_quick_counts(paths["orders"], paths["trades"], party_filter)
                    if table_type == "CLIENT_ORDERS":
                        n = counts["orders"] if row_type == "Total" else (counts["orders_public"] if "Public" in row_type else counts["orders_private"])
                    else:
                        n = counts["trades"] if row_type == "Total" else (counts["trades_public"] if "Public" in row_type else counts["trades_private"])
                    row[col] = fmt_count(n)
            matrix_rows.append(row)

matrix_df = pd.DataFrame(matrix_rows)
short_col_names = list(short_dates.values())

def style_matrix(df: pd.DataFrame):
    styled = pd.DataFrame("", index=df.index, columns=df.columns)
    for col in short_col_names:
        if col not in df.columns:
            continue
        for idx, val in df[col].items():
            breakdown = df.at[idx, "Breakdown"] if "Breakdown" in df.columns else ""
            if val == "—" or val == "":
                styled.at[idx, col] = "color: #555; font-style: italic"
            elif val.startswith("("):
                styled.at[idx, col] = "color: #888; font-style: italic"
            elif "Public" in str(breakdown):
                styled.at[idx, col] = "color: #4C78A8"
            elif "Private" in str(breakdown):
                styled.at[idx, col] = "color: #F58518; font-weight: bold"
            else:
                styled.at[idx, col] = "font-weight: bold; color: #2ca02c"
    return styled

# Column config: fixed narrow widths for date columns
col_cfg = {
    "Table":      st.column_config.TextColumn("Table",     width="medium"),
    "Source":     st.column_config.TextColumn("Source",    width="small"),
    "Breakdown":  st.column_config.TextColumn("Breakdown", width="small"),
}
for c in short_col_names:
    col_cfg[c] = st.column_config.TextColumn(c, width="small")

n_rows = len(matrix_rows)
st.dataframe(
    matrix_df.style.apply(style_matrix, axis=None),
    use_container_width=True,
    hide_index=True,
    height=45 + n_rows * 35,
    column_config=col_cfg,
)

# ─────────────────────────────────────────────────────────────────────────────
# STOP HERE IF NO STATS LOADED
# ─────────────────────────────────────────────────────────────────────────────
relevant_loaded = {(ex, d) for (ex, d) in loaded if d in sel_dates and ex in sel_exchanges}
if not relevant_loaded:
    st.info("👆 Click **Load Full Stats** to populate the matrix and enable comparison charts.")
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# COLLECT FULL STATS FOR LOADED SOURCES
# ─────────────────────────────────────────────────────────────────────────────
summary_rows = []
with st.spinner("Loading stats…"):
    for exchange in sel_exchanges:
        for trading_date, paths in all_data.get(exchange, {}).items():
            if trading_date not in sel_dates:
                continue
            if (exchange, trading_date) not in loaded:
                continue
            stats = get_source_stats(paths["orders"], paths["trades"])
            bkdn  = get_breakdown_stats(paths["orders"], paths["trades"])
            summary_rows.append({
                "Exchange":         exchange,
                "Date":             trading_date,
                "Orders":           stats["total_orders"],
                "Public Orders":    stats["public_orders"],
                "Private Orders":   stats["private_orders"],
                "Unique IDs":       stats["unique_order_ids"],
                "Trades":           stats["total_trades"],
                "Fill Rate %":      round(stats["total_trades"] / max(stats["total_orders"], 1) * 100, 2),
                "Countries":        stats["countries"],
                "Instruments":      stats["instruments"],
                "Avg Price":        round(bkdn["avg_price"],  2),
                "Avg Volume":       round(bkdn["avg_volume"], 1),
                "Activity From":    stats["earliest"],
                "Activity To":      stats["latest"],
                "_ins_type":        bkdn["ins_type"],
                "_action_type":     bkdn["action_type"],
                "_hourly":          bkdn["hourly"],
                "_country":         bkdn["country"],
                "_trades_ins":      bkdn["trades_ins"],
            })

if not summary_rows:
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL KPI ROW
# ─────────────────────────────────────────────────────────────────────────────
total_orders = sum(r["Orders"] for r in summary_rows)
total_trades = sum(r["Trades"] for r in summary_rows)
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Sources Loaded",  len(summary_rows))
k2.metric("Total Orders",    f"{total_orders:,}")
k3.metric("Total Trades",    f"{total_trades:,}")
k4.metric("Exchanges",       len({r["Exchange"] for r in summary_rows}))
k5.metric("Dates Covered",   len({r["Date"] for r in summary_rows}))
st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# CHART-FRIENDLY DF
# ─────────────────────────────────────────────────────────────────────────────
chart_cols = ["Exchange", "Date", "Orders", "Trades", "Public Orders", "Private Orders",
              "Unique IDs", "Fill Rate %", "Countries", "Instruments", "Avg Price", "Avg Volume"]
summary_df = pd.DataFrame([{c: r[c] for c in chart_cols} for r in summary_rows])
summary_df["Source"] = summary_df["Exchange"] + " · " + summary_df["Date"]

# ─────────────────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────────────────
tab_vol, tab_trend, tab_bkdn, tab_activity, tab_geo, tab_table = st.tabs([
    "📦 Volumes", "📅 Trends", "📐 Breakdown", "⏱ Activity", "🌍 Geography", "📋 Summary Table"
])

# ══ TAB 1 — VOLUMES ══════════════════════════════════════════════════════════
with tab_vol:
    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(summary_df, x="Exchange", y="Orders", color="Date", barmode="group",
                     title="Orders by Exchange & Date", text_auto=".3s",
                     color_discrete_sequence=px.colors.qualitative.Set2)
        fig.update_layout(height=340)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig2 = px.bar(summary_df, x="Exchange", y="Trades", color="Date", barmode="group",
                      title="Trades by Exchange & Date", text_auto=".3s",
                      color_discrete_sequence=px.colors.qualitative.Set2)
        fig2.update_layout(height=340)
        st.plotly_chart(fig2, use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        melt = summary_df[["Source", "Public Orders", "Private Orders"]].melt(
            id_vars="Source", var_name="Type", value_name="Count")
        fig3 = px.bar(melt, x="Source", y="Count", color="Type", barmode="stack",
                      title="Public vs Private Orders",
                      color_discrete_map={"Public Orders": "#4C78A8", "Private Orders": "#F58518"},
                      text_auto=".3s")
        fig3.update_layout(height=340, xaxis_tickangle=-20)
        st.plotly_chart(fig3, use_container_width=True)
    with c4:
        fig4 = px.bar(summary_df, x="Source", y="Fill Rate %", color="Exchange",
                      title="Fill Rate % (Trades ÷ Orders × 100)", text_auto=".2f",
                      color_discrete_sequence=px.colors.qualitative.Pastel)
        fig4.update_layout(height=340, xaxis_tickangle=-20, showlegend=False)
        fig4.add_hline(y=100, line_dash="dot", line_color="grey")
        st.plotly_chart(fig4, use_container_width=True)

# ══ TAB 2 — TRENDS ═══════════════════════════════════════════════════════════
with tab_trend:
    st.markdown("#### 📅 Daily Volume Trends per Exchange")
    if len(summary_df["Date"].unique()) < 2:
        st.info("Trends require at least 2 dates. Expand the date filter and click Load Full Stats.")
    else:
        trend_df = summary_df.sort_values(["Exchange", "Date"])
        t1, t2 = st.columns(2)
        with t1:
            fig_t1 = px.line(trend_df, x="Date", y="Orders", color="Exchange",
                             title="Daily Order Volume", markers=True,
                             color_discrete_sequence=px.colors.qualitative.Bold)
            fig_t1.update_layout(height=360, xaxis_tickangle=-30)
            fig_t1.update_traces(line_width=2)
            st.plotly_chart(fig_t1, use_container_width=True)
        with t2:
            fig_t2 = px.line(trend_df, x="Date", y="Trades", color="Exchange",
                             title="Daily Trade Volume", markers=True,
                             color_discrete_sequence=px.colors.qualitative.Bold)
            fig_t2.update_layout(height=360, xaxis_tickangle=-30)
            fig_t2.update_traces(line_width=2)
            st.plotly_chart(fig_t2, use_container_width=True)

        t3, t4 = st.columns(2)
        with t3:
            fig_t3 = px.line(trend_df, x="Date", y="Fill Rate %", color="Exchange",
                             title="Daily Fill Rate %", markers=True,
                             color_discrete_sequence=px.colors.qualitative.Bold)
            fig_t3.add_hline(y=100, line_dash="dot", line_color="grey")
            fig_t3.update_layout(height=360, xaxis_tickangle=-30)
            st.plotly_chart(fig_t3, use_container_width=True)
        with t4:
            fig_t4 = px.line(trend_df, x="Date", y="Unique IDs", color="Exchange",
                             title="Daily Unique Order IDs", markers=True,
                             color_discrete_sequence=px.colors.qualitative.Bold)
            fig_t4.update_layout(height=360, xaxis_tickangle=-30)
            st.plotly_chart(fig_t4, use_container_width=True)

# ══ TAB 3 — BREAKDOWN ════════════════════════════════════════════════════════
with tab_bkdn:
    ins_frames, ti_frames, act_frames = [], [], []
    for r in summary_rows:
        lbl = r["Exchange"] + " · " + r["Date"]
        df_i  = r["_ins_type"].copy();    df_i["Source"]  = lbl; ins_frames.append(df_i)
        df_ti = r["_trades_ins"].copy();  df_ti["Source"] = lbl; ti_frames.append(df_ti)
        df_a  = r["_action_type"].copy(); df_a["Source"]  = lbl; act_frames.append(df_a)

    if ins_frames:
        ins_all = pd.concat(ins_frames, ignore_index=True)
        b1, b2  = st.columns(2)
        with b1:
            fig_b1 = px.bar(ins_all, x="ins_type", y="cnt", color="Source", barmode="group",
                            title="Orders — Instrument Type", text_auto=".3s",
                            labels={"ins_type": "Instrument", "cnt": "Orders"},
                            color_discrete_sequence=px.colors.qualitative.Bold)
            fig_b1.update_layout(height=360, xaxis_tickangle=-30)
            st.plotly_chart(fig_b1, use_container_width=True)
        with b2:
            ti_all = pd.concat(ti_frames, ignore_index=True)
            fig_b2 = px.bar(ti_all, x="ins_type", y="cnt", color="Source", barmode="group",
                            title="Trades — Instrument Type", text_auto=".3s",
                            labels={"ins_type": "Instrument", "cnt": "Trades"},
                            color_discrete_sequence=px.colors.qualitative.Bold)
            fig_b2.update_layout(height=360, xaxis_tickangle=-30)
            st.plotly_chart(fig_b2, use_container_width=True)

    if act_frames:
        act_all = pd.concat(act_frames, ignore_index=True)
        b3, b4  = st.columns([2, 1])
        with b3:
            fig_b3 = px.bar(act_all, x="action", y="cnt", color="Source", barmode="group",
                            title="Order Action Types", text_auto=".3s",
                            color_discrete_sequence=px.colors.qualitative.Set1)
            fig_b3.update_layout(height=360)
            st.plotly_chart(fig_b3, use_container_width=True)
        with b4:
            first_src = act_all["Source"].iloc[0]
            fig_b4 = px.pie(act_all[act_all["Source"] == first_src],
                            names="action", values="cnt", hole=0.4,
                            title=f"Mix — {first_src}",
                            color_discrete_sequence=px.colors.qualitative.Set1)
            fig_b4.update_layout(height=360)
            st.plotly_chart(fig_b4, use_container_width=True)

# ══ TAB 4 — ACTIVITY ═════════════════════════════════════════════════════════
with tab_activity:
    hourly_frames = []
    for r in summary_rows:
        df_h = r["_hourly"].copy(); df_h["Source"] = r["Exchange"] + " · " + r["Date"]
        hourly_frames.append(df_h)
    if hourly_frames:
        hourly_all = pd.concat(hourly_frames, ignore_index=True)
        fig_h = px.line(hourly_all, x="hour", y="cnt", color="Source",
                        title="Orders Submitted per Hour of Day",
                        labels={"hour": "Hour (UTC)", "cnt": "Orders"}, markers=True,
                        color_discrete_sequence=px.colors.qualitative.Dark2)
        fig_h.update_layout(height=400, xaxis=dict(dtick=1, range=[0, 23]))
        fig_h.update_traces(line_width=2)
        st.plotly_chart(fig_h, use_container_width=True)

# ══ TAB 5 — GEOGRAPHY ════════════════════════════════════════════════════════
with tab_geo:
    country_frames = []
    for r in summary_rows:
        df_c = r["_country"].copy()
        df_c["Source"]   = r["Exchange"] + " · " + r["Date"]
        df_c["Exchange"] = r["Exchange"]
        country_frames.append(df_c)
    if country_frames:
        country_all = pd.concat(country_frames, ignore_index=True)
        # Aggregate across all sources for the choropleth heat layer
        agg = country_all.groupby("country")["cnt"].sum().reset_index()
        agg["iso3"] = agg["country"].map(ISO2_TO_ISO3)
        agg_mapped  = agg.dropna(subset=["iso3"])

        fig_map = px.choropleth(
            agg_mapped,
            locations="iso3",
            color="cnt",
            hover_name="country",
            hover_data={"iso3": False, "cnt": ":,"},
            color_continuous_scale="Blues",
            title="Order Volume by Country",
            labels={"cnt": "Orders"},
        )
        fig_map.update_layout(
            height=500,
            geo=dict(
                showframe=False,
                showcoastlines=True,
                projection_type="natural earth",
                scope="europe",          # zoom on Europe by default; remove for world
            ),
            coloraxis_colorbar=dict(title="Orders"),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_map, use_container_width=True)

        # Toggle world vs Europe scope
        if st.checkbox("🌐 Show world map", key="ov_world_map"):
            fig_world = px.choropleth(
                agg_mapped, locations="iso3", color="cnt",
                hover_name="country", hover_data={"iso3": False, "cnt": ":,"},
                color_continuous_scale="Blues",
                title="Order Volume by Country — World",
                labels={"cnt": "Orders"},
            )
            fig_world.update_layout(
                height=450, geo=dict(showframe=False, projection_type="natural earth"),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_world, use_container_width=True)

        # Bar chart below map for precise comparison
        per_src = country_all.groupby(["country", "Source"])["cnt"].sum().reset_index()
        top_countries = agg.nlargest(15, "cnt")["country"].tolist()
        fig_bar = px.bar(
            per_src[per_src["country"].isin(top_countries)],
            x="cnt", y="country", color="Source", barmode="group", orientation="h",
            title="Top 15 Countries — Orders per Source",
            labels={"country": "Country", "cnt": "Orders"},
            text_auto=".3s",
            color_discrete_sequence=px.colors.qualitative.Vivid,
        )
        fig_bar.update_layout(height=420, yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig_bar, use_container_width=True)

# ══ TAB 6 — SUMMARY TABLE ════════════════════════════════════════════════════
with tab_table:
    disp = summary_df[["Exchange", "Date", "Orders", "Trades", "Fill Rate %",
                        "Public Orders", "Private Orders", "Countries",
                        "Instruments", "Avg Price", "Avg Volume"]].copy()
    disp["Avg Price"]  = disp["Avg Price"].map(lambda x: f"{x:,.2f}")
    disp["Avg Volume"] = disp["Avg Volume"].map(lambda x: f"{x:,.1f}")
    disp = disp.sort_values(["Exchange", "Date"])
    st.dataframe(disp, use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — minimal
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏠 Overview")
    st.divider()
    st.caption("Use the filters at the top of the page to select Client, Exchange and Date.")
    if comparable_dates:
        st.caption(f"🟢 Dates in **all** exchanges: {', '.join(comparable_dates)}")



# ══ TAB 2 — TRENDS ═══════════════════════════════════════════════════════════
with tab_trend:
    st.markdown("#### 📅 Daily Volume Trends per Exchange")
    st.caption("Shows all available dates — useful for exchanges with long history (e.g. OMIE with 16 days).")

    if len(summary_df["Date"].unique()) < 2:
        st.info("Trends require at least 2 dates. Expand the date range in the sidebar and click Load Full Stats.")
    else:
        trend_df = summary_df.sort_values(["Exchange", "Date"])

        t1, t2 = st.columns(2)
        with t1:
            fig_t1 = px.line(trend_df, x="Date", y="Orders", color="Exchange",
                             title="Daily Order Volume per Exchange", markers=True,
                             color_discrete_sequence=px.colors.qualitative.Bold)
            fig_t1.update_layout(height=360, xaxis_tickangle=-30)
            fig_t1.update_traces(line_width=2)
            st.plotly_chart(fig_t1, use_container_width=True)
        with t2:
            fig_t2 = px.line(trend_df, x="Date", y="Trades", color="Exchange",
                             title="Daily Trade Volume per Exchange", markers=True,
                             color_discrete_sequence=px.colors.qualitative.Bold)
            fig_t2.update_layout(height=360, xaxis_tickangle=-30)
            fig_t2.update_traces(line_width=2)
            st.plotly_chart(fig_t2, use_container_width=True)

        t3, t4 = st.columns(2)
        with t3:
            fig_t3 = px.line(trend_df, x="Date", y="Fill Rate %", color="Exchange",
                             title="Daily Fill Rate %", markers=True,
                             color_discrete_sequence=px.colors.qualitative.Bold)
            fig_t3.update_layout(height=360, xaxis_tickangle=-30)
            fig_t3.add_hline(y=100, line_dash="dot", line_color="grey")
            st.plotly_chart(fig_t3, use_container_width=True)
        with t4:
            fig_t4 = px.line(trend_df, x="Date", y="Unique IDs", color="Exchange",
                             title="Daily Unique Order IDs", markers=True,
                             color_discrete_sequence=px.colors.qualitative.Bold)
            fig_t4.update_layout(height=360, xaxis_tickangle=-30)
            st.plotly_chart(fig_t4, use_container_width=True)

# ══ TAB 3 — BREAKDOWN ════════════════════════════════════════════════════════
with tab_bkdn:
    ins_frames = []
    for r in summary_rows:
        df_i = r["_ins_type"].copy(); df_i["Source"] = r["Exchange"] + " · " + r["Date"]
        ins_frames.append(df_i)
    ti_frames = []
    for r in summary_rows:
        df_ti = r["_trades_ins"].copy(); df_ti["Source"] = r["Exchange"] + " · " + r["Date"]
        ti_frames.append(df_ti)
    act_frames = []
    for r in summary_rows:
        df_a = r["_action_type"].copy(); df_a["Source"] = r["Exchange"] + " · " + r["Date"]
        act_frames.append(df_a)

    if ins_frames:
        ins_all = pd.concat(ins_frames, ignore_index=True)
        b1, b2  = st.columns(2)
        with b1:
            fig_b1 = px.bar(ins_all, x="ins_type", y="cnt", color="Source", barmode="group",
                            title="Orders — Instrument Type", text_auto=".3s",
                            labels={"ins_type": "Instrument", "cnt": "Orders"},
                            color_discrete_sequence=px.colors.qualitative.Bold)
            fig_b1.update_layout(height=360, xaxis_tickangle=-30)
            st.plotly_chart(fig_b1, use_container_width=True)
        with b2:
            if ti_frames:
                ti_all = pd.concat(ti_frames, ignore_index=True)
                fig_b2 = px.bar(ti_all, x="ins_type", y="cnt", color="Source", barmode="group",
                                title="Trades — Instrument Type", text_auto=".3s",
                                labels={"ins_type": "Instrument", "cnt": "Trades"},
                                color_discrete_sequence=px.colors.qualitative.Bold)
                fig_b2.update_layout(height=360, xaxis_tickangle=-30)
                st.plotly_chart(fig_b2, use_container_width=True)

    if act_frames:
        act_all = pd.concat(act_frames, ignore_index=True)
        b3, b4  = st.columns([2, 1])
        with b3:
            fig_b3 = px.bar(act_all, x="action", y="cnt", color="Source", barmode="group",
                            title="Order Action Types (New / Amend / Cancel / Change)",
                            labels={"action": "Action", "cnt": "Count"}, text_auto=".3s",
                            color_discrete_sequence=px.colors.qualitative.Set1)
            fig_b3.update_layout(height=360)
            st.plotly_chart(fig_b3, use_container_width=True)
        with b4:
            first_src = act_all["Source"].iloc[0]
            fig_b4 = px.pie(act_all[act_all["Source"] == first_src],
                            names="action", values="cnt", hole=0.4,
                            title=f"Mix — {first_src}",
                            color_discrete_sequence=px.colors.qualitative.Set1)
            fig_b4.update_layout(height=360)
            st.plotly_chart(fig_b4, use_container_width=True)

# ══ TAB 4 — ACTIVITY ═════════════════════════════════════════════════════════
with tab_activity:
    hourly_frames = []
    for r in summary_rows:
        df_h = r["_hourly"].copy(); df_h["Source"] = r["Exchange"] + " · " + r["Date"]
        hourly_frames.append(df_h)
    if hourly_frames:
        hourly_all = pd.concat(hourly_frames, ignore_index=True)
        fig_h = px.line(hourly_all, x="hour", y="cnt", color="Source",
                        title="Orders Submitted per Hour of Day",
                        labels={"hour": "Hour (UTC)", "cnt": "Orders"}, markers=True,
                        color_discrete_sequence=px.colors.qualitative.Dark2)
        fig_h.update_layout(height=400, xaxis=dict(dtick=1, range=[0, 23]))
        fig_h.update_traces(line_width=2)
        st.plotly_chart(fig_h, use_container_width=True)

# ══ TAB 5 — GEOGRAPHY ════════════════════════════════════════════════════════
with tab_geo:
    country_frames = []
    for r in summary_rows:
        df_c = r["_country"].copy(); df_c["Source"] = r["Exchange"] + " · " + r["Date"]
        country_frames.append(df_c)
    if country_frames:
        country_all = pd.concat(country_frames, ignore_index=True)
        fig_g = px.bar(country_all, x="cnt", y="country", color="Source", barmode="group",
                       orientation="h", title="Top Countries — Order Volume",
                       labels={"country": "Country", "cnt": "Orders"},
                       text_auto=".3s",
                       color_discrete_sequence=px.colors.qualitative.Vivid)
        fig_g.update_layout(
            height=max(360, len(country_all["country"].unique()) * 28),
            yaxis=dict(categoryorder="total ascending"),
        )
        st.plotly_chart(fig_g, use_container_width=True)

# ══ TAB 6 — SUMMARY TABLE ════════════════════════════════════════════════════
with tab_table:
    disp = summary_df[["Exchange", "Date", "Orders", "Trades", "Fill Rate %",
                        "Public Orders", "Private Orders", "Countries",
                        "Instruments", "Avg Price", "Avg Volume"]].copy()
    disp["Avg Price"]  = disp["Avg Price"].map(lambda x: f"{x:,.2f}")
    disp["Avg Volume"] = disp["Avg Volume"].map(lambda x: f"{x:,.1f}")
    disp = disp.sort_values(["Exchange", "Date"])
    st.dataframe(disp, use_container_width=True, hide_index=True)



# ─────────────────────────────────────────────────────────────────────────────
# CACHED STAT FETCHER  (expensive — only called when user requests it)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, persist="disk")
def get_source_stats(orders_path: str, trades_path: str) -> dict:
    """Full aggregation scan. Cached per file path — runs once, then instant."""
    o = duckdb.query(f"""
        SELECT
            COUNT(*)                                   AS total_orders,
            APPROX_COUNT_DISTINCT(ORIG_TRAN_ID)        AS unique_order_ids,
            COUNT(*) FILTER (WHERE PARTY IS NULL)      AS public_orders,
            COUNT(*) FILTER (WHERE PARTY IS NOT NULL)  AS private_orders,
            COUNT(DISTINCT COUNTRY)                    AS countries,
            COUNT(DISTINCT INS_TYPE)                   AS instruments,
            MIN(TRAN_DATETIME)                         AS earliest,
            MAX(TRAN_DATETIME)                         AS latest
        FROM {parquet_scan_expr(orders_path)}
    """).df().iloc[0]
    t = duckdb.query(f"""
        SELECT COUNT(*) AS total_trades, APPROX_COUNT_DISTINCT(ORIG_TRAN_ID) AS unique_trade_ids
        FROM {parquet_scan_expr(trades_path)}
    """).df().iloc[0]
    return {
        "total_orders":     int(o["total_orders"]),
        "unique_order_ids": int(o["unique_order_ids"]),
        "public_orders":    int(o["public_orders"]),
        "private_orders":   int(o["private_orders"]),
        "countries":        int(o["countries"]),
        "instruments":      int(o["instruments"]),
        "earliest":         str(o["earliest"])[:16],
        "latest":           str(o["latest"])[:16],
        "total_trades":     int(t["total_trades"]),
        "unique_trade_ids": int(t["unique_trade_ids"]),
    }


@st.cache_data(show_spinner=False, persist="disk")
def get_breakdown_stats(orders_path: str, trades_path: str) -> dict:
    """Breakdown stats for comparison charts. Cached per file path."""
    # Detect action column — Nordpool uses TRAN_ACT_TYPE, EPEX/others use TRAN_STATUS
    schema_cols = duckdb.query(f"SELECT * FROM {parquet_scan_expr(orders_path)} LIMIT 0").df().columns.tolist()
    act_col = "TRAN_ACT_TYPE" if "TRAN_ACT_TYPE" in schema_cols else "TRAN_STATUS"

    ins_df = duckdb.query(f"""
        SELECT COALESCE(INS_TYPE, 'Unknown') AS ins_type, COUNT(*) AS cnt
        FROM {parquet_scan_expr(orders_path)}
        GROUP BY ins_type ORDER BY cnt DESC LIMIT 15
    """).df()

    act_df = duckdb.query(f"""
        SELECT COALESCE({act_col}, 'Unknown') AS action,
               COUNT(*) AS cnt
        FROM {parquet_scan_expr(orders_path)}
        GROUP BY action ORDER BY cnt DESC
    """).df()
    action_labels = {"V": "New (V)", "A": "Amend (A)", "E": "Cancel (E)", "C": "Change (C)", "P": "Pending (P)"}
    act_df["action"] = act_df["action"].map(lambda x: action_labels.get(x, x))

    hourly_df = duckdb.query(f"""
        SELECT hour(TRAN_DATETIME) AS hour, COUNT(*) AS cnt
        FROM {parquet_scan_expr(orders_path)}
        WHERE TRAN_DATETIME IS NOT NULL
        GROUP BY hour ORDER BY hour
    """).df()

    country_df = duckdb.query(f"""
        SELECT COALESCE(COUNTRY, 'Unknown') AS country, COUNT(*) AS cnt
        FROM {parquet_scan_expr(orders_path)}
        GROUP BY country ORDER BY cnt DESC LIMIT 15
    """).df()

    vol_df = duckdb.query(f"""
        SELECT
            SUM(VOLUME) FILTER (WHERE VOLUME > 0) AS total_volume,
            AVG(PRICE)  FILTER (WHERE PRICE  > 0) AS avg_price,
            AVG(VOLUME) FILTER (WHERE VOLUME > 0) AS avg_volume
        FROM {parquet_scan_expr(orders_path)}
    """).df().iloc[0]

    trades_ins_df = duckdb.query(f"""
        SELECT COALESCE(INS_TYPE, 'Unknown') AS ins_type, COUNT(*) AS cnt
        FROM {parquet_scan_expr(trades_path)}
        GROUP BY ins_type ORDER BY cnt DESC LIMIT 15
    """).df()

    return {
        "ins_type":    ins_df,
        "action_type": act_df,
        "hourly":      hourly_df,
        "country":     country_df,
        "total_volume": float(vol_df["total_volume"] or 0),
        "avg_price":    float(vol_df["avg_price"]    or 0),
        "avg_volume":   float(vol_df["avg_volume"]   or 0),
        "trades_ins":  trades_ins_df,
    }


def file_size_mb(path: str) -> float:
    try:
        return Path(path).stat().st_size / 1_048_576
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# DISCOVER  (filesystem only — always instant)
# ─────────────────────────────────────────────────────────────────────────────
all_data = discover_all_data_files()
today    = date.today()

# ─────────────────────────────────────────────────────────────────────────────
# DATE FILTER  (sidebar)
# ─────────────────────────────────────────────────────────────────────────────
all_dates        = sorted({d for dates in all_data.values() for d in dates}, reverse=True)
per_exchange     = {ex: set(dates.keys()) for ex, dates in all_data.items()}
comparable_dates = sorted(
    {d for d in all_dates if all(d in per_exchange[ex] for ex in per_exchange)},
    reverse=True,
)

with st.sidebar:
    st.markdown("## 🏠 Overview")
    st.divider()
    st.markdown("**📅 Trading Date Filter**")
    if comparable_dates:
        st.caption(f"🟢 Dates in **all** exchanges: {', '.join(comparable_dates)}")
    sel_dates = st.multiselect(
        "Select date(s) to display",
        options=all_dates,
        default=all_dates,   # show every exchange by default
        help="🟢 dates exist in every exchange and are ideal for side-by-side comparison. Deselect dates to narrow the view.",
    )
    if not sel_dates:
        sel_dates = all_dates  # fallback: show all

# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## 🏠 DQE — Data Sources Overview")
date_label = ", ".join(sel_dates) if sel_dates != all_dates else "All dates"
st.caption(f"Comparing: **{date_label}**  |  {today.strftime('%d %b %Y')}")

if not all_data:
    st.warning("No data sources found. Check EXCHANGE_DIRS in utils.py.")
    st.stop()

# ── Instant file-size table (no DuckDB) ──────────────────────────────────────
quick_rows = []
for exchange, dates in all_data.items():
    for trading_date, paths in dates.items():
        if trading_date not in sel_dates:
            continue
        days_old  = (today - date.fromisoformat(trading_date)).days
        freshness = "🟢 Today" if days_old == 0 else (f"🟡 {days_old}d old" if days_old <= 3 else f"🔴 {days_old}d old")
        in_all    = "✅" if trading_date in comparable_dates else "⚠️ partial"
        quick_rows.append({
            "Exchange":        exchange,
            "Trading Date":    trading_date,
            "Comparable":      in_all,
            "Orders file":     f"{file_size_mb(paths['orders']):.0f} MB",
            "Trades file":     f"{file_size_mb(paths['trades']):.0f} MB",
            "Freshness":       freshness,
            "Stats loaded":    "✅" if (exchange, trading_date) in st.session_state.get("loaded_stats", set()) else "—",
        })

st.dataframe(pd.DataFrame(quick_rows), use_container_width=True, hide_index=True)
st.divider()

# ── Load stats button ────────────────────────────────────────────────────────
col_btn, col_note = st.columns([1, 4])
load_all = col_btn.button("📊 Load Full Stats", type="primary", use_container_width=True)
col_note.caption(
    "Scans all parquet files to compute row counts, unique IDs, instruments, etc. "
    "Results are **cached** — first run may take a few minutes for large files (e.g. EPEX 122M rows); "
    "subsequent loads are instant."
)

if "loaded_stats" not in st.session_state:
    st.session_state["loaded_stats"] = set()

if load_all:
    for exchange, dates in all_data.items():
        for trading_date in dates:
            if trading_date in sel_dates:
                st.session_state["loaded_stats"].add((exchange, trading_date))
    st.rerun()

# ── Render stats for sources the user has loaded ─────────────────────────────
loaded = st.session_state.get("loaded_stats", set())
# Only consider entries matching the current date filter
relevant_loaded = {k for k in loaded if k[1] in sel_dates}
if not relevant_loaded:
    st.info("👆 Click **Load Full Stats** to run aggregation scans for the selected date(s).")
    st.stop()

st.divider()
summary_rows = []

for exchange, dates in all_data.items():
    for trading_date, paths in dates.items():
        if trading_date not in sel_dates:
            continue
        if (exchange, trading_date) not in loaded:
            continue

        days_old  = (today - date.fromisoformat(trading_date)).days
        freshness = "🟢 Today" if days_old == 0 else (f"🟡 {days_old}d old" if days_old <= 3 else f"🔴 {days_old}d old")

        st.markdown(f"#### 📡 {exchange}  ·  📅 {trading_date}  ·  {freshness}")
        with st.spinner(f"Scanning {exchange} / {trading_date}…"):
            stats = get_source_stats(paths["orders"], paths["trades"])
            bkdn  = get_breakdown_stats(paths["orders"], paths["trades"])

        c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
        c1.metric("Orders",           f"{stats['total_orders']:,}")
        c2.metric("  ↳ Public",       f"{stats['public_orders']:,}")
        c3.metric("  ↳ Private",      f"{stats['private_orders']:,}")
        c4.metric("Unique Order IDs", f"{stats['unique_order_ids']:,}")
        c5.metric("Trades",           f"{stats['total_trades']:,}")
        c6.metric("Countries",        str(stats["countries"]))
        c7.metric("Instruments",      str(stats["instruments"]))

        st.caption(
            f"⏱ Activity window: `{stats['earliest']}` → `{stats['latest']}`"
        )
        st.divider()

        summary_rows.append({
            "Exchange":        exchange,
            "Trading Date":    trading_date,
            "Orders":          stats["total_orders"],
            "Public Orders":   stats["public_orders"],
            "Private Orders":  stats["private_orders"],
            "Unique Order IDs":stats["unique_order_ids"],
            "Trades":          stats["total_trades"],
            "Countries":       stats["countries"],
            "Instruments":     stats["instruments"],
            "Fill Rate %":     round(stats["total_trades"] / max(stats["total_orders"], 1) * 100, 2),
            "Avg Price":       bkdn["avg_price"],
            "Avg Volume":      bkdn["avg_volume"],
            "_ins_type":       bkdn["ins_type"],
            "_action_type":    bkdn["action_type"],
            "_hourly":         bkdn["hourly"],
            "_country":        bkdn["country"],
            "_trades_ins":     bkdn["trades_ins"],
        })

# ── Comparison charts (only when 2+ sources loaded) ──────────────────────────
if len(summary_rows) >= 2:
    st.markdown("### 📊 Cross-Exchange Volume Comparison")

    # Warn if selected dates don't exist in all exchanges
    shown_dates = {r["Trading Date"] for r in summary_rows}
    shown_exchanges = {r["Exchange"] for r in summary_rows}
    for d in shown_dates:
        missing = [ex for ex in all_data if ex not in shown_exchanges or d not in all_data.get(ex, {})]
        if missing:
            st.warning(f"⚠️ `{d}` is not available for: {', '.join(missing)} — comparison may be incomplete.")

    # Chart-friendly df (no private columns)
    chart_cols = ["Exchange", "Trading Date", "Orders", "Trades", "Public Orders",
                  "Private Orders", "Unique Order IDs", "Fill Rate %", "Countries",
                  "Instruments", "Avg Price", "Avg Volume"]
    summary_df = pd.DataFrame([{c: r[c] for c in chart_cols} for r in summary_rows])
    label_col  = summary_df.apply(lambda r: f"{r['Exchange']} ({r['Trading Date']})", axis=1)
    summary_df.insert(0, "Source", label_col)

    # ── Row 1: Orders & Trades volume ────────────────────────────────────────
    st.markdown("#### 📦 Order & Trade Volumes")
    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(summary_df, x="Exchange", y="Orders", color="Trading Date",
                     barmode="group", title="Total Orders by Exchange",
                     text_auto=".3s",
                     color_discrete_sequence=px.colors.qualitative.Set2)
        fig.update_layout(height=340, legend_title_text="Date", showlegend=True)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig2 = px.bar(summary_df, x="Exchange", y="Trades", color="Trading Date",
                      barmode="group", title="Total Trades by Exchange",
                      text_auto=".3s",
                      color_discrete_sequence=px.colors.qualitative.Set2)
        fig2.update_layout(height=340, legend_title_text="Date")
        st.plotly_chart(fig2, use_container_width=True)

    # ── Row 2: Public/Private split + Fill Rate ───────────────────────────────
    st.markdown("#### 🔓 Order Visibility Split & Fill Rate")
    c3, c4 = st.columns(2)
    with c3:
        melt = summary_df[["Source", "Public Orders", "Private Orders"]].melt(
            id_vars="Source", var_name="Type", value_name="Count"
        )
        fig3 = px.bar(melt, x="Source", y="Count", color="Type", barmode="stack",
                      title="Public vs Private Orders per Source",
                      color_discrete_map={"Public Orders": "#4C78A8", "Private Orders": "#F58518"},
                      text_auto=".3s")
        fig3.update_layout(height=340, xaxis_tickangle=-20)
        st.plotly_chart(fig3, use_container_width=True)
    with c4:
        fig4 = px.bar(summary_df, x="Source", y="Fill Rate %", color="Exchange",
                      title="Fill Rate % (Trades ÷ Orders × 100)",
                      text_auto=".2f",
                      color_discrete_sequence=px.colors.qualitative.Pastel)
        fig4.update_layout(height=340, xaxis_tickangle=-20, showlegend=False)
        fig4.add_hline(y=100, line_dash="dot", line_color="grey",
                       annotation_text="100%", annotation_position="top right")
        st.plotly_chart(fig4, use_container_width=True)

    # ── Row 3: Instrument type breakdown ─────────────────────────────────────
    st.markdown("#### 📐 Instrument Type Breakdown")
    ins_frames = []
    for r in summary_rows:
        df_ins = r["_ins_type"].copy()
        df_ins["Source"] = f"{r['Exchange']} ({r['Trading Date']})"
        df_ins["Exchange"] = r["Exchange"]
        ins_frames.append(df_ins)
    if ins_frames:
        ins_all = pd.concat(ins_frames, ignore_index=True)
        c5, c6 = st.columns(2)
        with c5:
            fig5 = px.bar(ins_all, x="ins_type", y="cnt", color="Source",
                          barmode="group", title="Orders — Instrument Type Distribution",
                          labels={"ins_type": "Instrument Type", "cnt": "Order Count"},
                          text_auto=".3s",
                          color_discrete_sequence=px.colors.qualitative.Bold)
            fig5.update_layout(height=360, xaxis_tickangle=-30)
            st.plotly_chart(fig5, use_container_width=True)
        with c6:
            trades_ins_frames = []
            for r in summary_rows:
                df_ti = r["_trades_ins"].copy()
                df_ti["Source"] = f"{r['Exchange']} ({r['Trading Date']})"
                trades_ins_frames.append(df_ti)
            if trades_ins_frames:
                ti_all = pd.concat(trades_ins_frames, ignore_index=True)
                fig6 = px.bar(ti_all, x="ins_type", y="cnt", color="Source",
                              barmode="group", title="Trades — Instrument Type Distribution",
                              labels={"ins_type": "Instrument Type", "cnt": "Trade Count"},
                              text_auto=".3s",
                              color_discrete_sequence=px.colors.qualitative.Bold)
                fig6.update_layout(height=360, xaxis_tickangle=-30)
                st.plotly_chart(fig6, use_container_width=True)

    # ── Row 4: Order action type breakdown ───────────────────────────────────
    st.markdown("#### 🔄 Order Action Type Breakdown (New / Amend / Cancel / Change)")
    act_frames = []
    for r in summary_rows:
        df_act = r["_action_type"].copy()
        df_act["Source"] = f"{r['Exchange']} ({r['Trading Date']})"
        act_frames.append(df_act)
    if act_frames:
        act_all = pd.concat(act_frames, ignore_index=True)
        c7, c8 = st.columns([2, 1])
        with c7:
            fig7 = px.bar(act_all, x="action", y="cnt", color="Source",
                          barmode="group", title="Order Actions by Exchange",
                          labels={"action": "Action Type", "cnt": "Count"},
                          text_auto=".3s",
                          color_discrete_sequence=px.colors.qualitative.Set1)
            fig7.update_layout(height=360)
            st.plotly_chart(fig7, use_container_width=True)
        with c8:
            # Pie for first source as a proportion overview
            first = act_all[act_all["Source"] == act_all["Source"].iloc[0]]
            fig8 = px.pie(first, names="action", values="cnt",
                          title=f"Action Mix — {first['Source'].iloc[0]}",
                          color_discrete_sequence=px.colors.qualitative.Set1,
                          hole=0.4)
            fig8.update_layout(height=360)
            st.plotly_chart(fig8, use_container_width=True)

    # ── Row 5: Hourly activity profile ───────────────────────────────────────
    st.markdown("#### ⏱ Intraday Order Submission Profile")
    hourly_frames = []
    for r in summary_rows:
        df_h = r["_hourly"].copy()
        df_h["Source"] = f"{r['Exchange']} ({r['Trading Date']})"
        hourly_frames.append(df_h)
    if hourly_frames:
        hourly_all = pd.concat(hourly_frames, ignore_index=True)
        fig9 = px.line(hourly_all, x="hour", y="cnt", color="Source",
                       title="Orders Submitted per Hour of Day",
                       labels={"hour": "Hour (UTC)", "cnt": "Order Count"},
                       markers=True,
                       color_discrete_sequence=px.colors.qualitative.Dark2)
        fig9.update_layout(height=360, xaxis=dict(dtick=1, range=[0, 23]))
        fig9.update_traces(line_width=2)
        st.plotly_chart(fig9, use_container_width=True)

    # ── Row 6: Top countries ──────────────────────────────────────────────────
    st.markdown("#### 🌍 Top Countries by Order Count")
    country_frames = []
    for r in summary_rows:
        df_c = r["_country"].copy()
        df_c["Source"] = f"{r['Exchange']} ({r['Trading Date']})"
        country_frames.append(df_c)
    if country_frames:
        country_all = pd.concat(country_frames, ignore_index=True)
        fig10 = px.bar(country_all, x="cnt", y="country", color="Source",
                       barmode="group", orientation="h",
                       title="Top Countries — Order Volume",
                       labels={"country": "Country", "cnt": "Order Count"},
                       text_auto=".3s",
                       color_discrete_sequence=px.colors.qualitative.Vivid)
        fig10.update_layout(height=max(360, len(country_all["country"].unique()) * 28),
                            yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig10, use_container_width=True)

    # ── Row 7: Summary table ─────────────────────────────────────────────────
    st.markdown("#### 📋 Summary Table")
    display_df = summary_df[["Source", "Orders", "Trades", "Fill Rate %",
                              "Public Orders", "Private Orders", "Countries",
                              "Instruments", "Avg Price", "Avg Volume"]].copy()
    display_df["Avg Price"]  = display_df["Avg Price"].map(lambda x: f"{x:,.2f}")
    display_df["Avg Volume"] = display_df["Avg Volume"].map(lambda x: f"{x:,.1f}")
    st.dataframe(display_df, use_container_width=True, hide_index=True)


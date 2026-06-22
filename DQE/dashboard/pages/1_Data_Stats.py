"""
CubeLogic DQE — Data Stats
CubeWatch STG | CLIENT_ORDERS + CLIENT_TRADES
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from utils import parquet_scan_expr,\
     discover_all_data_files

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

STATUS_COLORS = {"V": "#2ca02c", "A": "#1f77b4", "P": "#ff7f0e", "E": "#9467bd", "C": "#d62728"}
TYPE_COLORS   = {"Public": "#4C78A8", "Private": "#F58518"}
BS_COLORS     = {"B": "#2ca02c", "S": "#d62728"}

# ─────────────────────────────────────────────────────────────────────────────
# DISCOVER
# ─────────────────────────────────────────────────────────────────────────────
all_data  = discover_all_data_files()
EXCHANGES = list(all_data.keys())

# ─────────────────────────────────────────────────────────────────────────────
# TOP CONTROL BAR  — Exchange | Date | Client
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## 📊 Data Stats")
ctrl1, ctrl2, ctrl3 = st.columns([2, 2, 3])

with ctrl1:
    ex_opts = ["All Exchanges"] + EXCHANGES
    _prev_ex = st.session_state.get("ds_sel_exchange", "All Exchanges")
    _ex_idx  = ex_opts.index(_prev_ex) if _prev_ex in ex_opts else 0
    sel_ex_choice = st.selectbox("📡 Exchange", ex_opts, index=_ex_idx, key="ds_exchange_ctrl")
    st.session_state["ds_sel_exchange"] = sel_ex_choice
    sel_exchanges = EXCHANGES if sel_ex_choice == "All Exchanges" else [sel_ex_choice]
    st.session_state["sel_exchange"] = sel_exchanges[0]

# Derive available dates from selected exchanges
all_available_dates = sorted(
    {d for ex in sel_exchanges for d in all_data.get(ex, {})},
    reverse=True,
)

with ctrl2:
    date_opts = ["All Dates"] + all_available_dates
    _prev_dt  = st.session_state.get("ds_sel_date", "All Dates")
    _dt_idx   = date_opts.index(_prev_dt) if _prev_dt in date_opts else 0
    sel_dt_choice = st.selectbox("📅 Trading Date", date_opts, index=_dt_idx, key="ds_date_ctrl")
    st.session_state["ds_sel_date"] = sel_dt_choice
    sel_dates = all_available_dates if sel_dt_choice == "All Dates" else [sel_dt_choice]

# Build list of (exchange, date, orders_path, trades_path) to load
selected_sources = [
    (ex, d, all_data[ex][d]["orders"], all_data[ex][d]["trades"])
    for ex in sel_exchanges
    for d in sel_dates
    if d in all_data.get(ex, {})
]

# For client picker — union of parties across first 3 sources (fast, cached)
# ─────────────────────────────────────────────────────────────────────────────
# CACHED FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, persist="disk")
def get_parties(orders_path: str) -> list:
    df = duckdb.query(f"""
        SELECT DISTINCT PARTY AS client
        FROM {parquet_scan_expr(orders_path)}
        WHERE PARTY IS NOT NULL
        ORDER BY client
    """).df()
    return df["client"].tolist()

with ctrl3:
    # Collect parties from all selected sources (use first path that works)
    _all_parties: list = []
    for _, _, op, _ in selected_sources[:3]:
        for p in get_parties(op):
            if p not in _all_parties:
                _all_parties.append(p)
    _all_parties.sort()
    _party_opts  = ["All clients"] + _all_parties
    _prev_party  = st.session_state.get("sel_party", "All clients")
    _party_idx   = _party_opts.index(_prev_party) if _prev_party in _party_opts else 0
    sel_party    = st.selectbox("👤 Client", _party_opts, index=_party_idx, key="ds_party_ctrl")
    st.session_state["sel_party"] = sel_party

# Convenience labels for header
sel_exchange = sel_exchanges[0] if len(sel_exchanges) == 1 else f"{len(sel_exchanges)} exchanges"
DATA_DATE    = sel_dates[0]     if len(sel_dates)     == 1 else f"{len(sel_dates)} dates"
# Keep single paths for raw record explorer (use first source)
ORDERS_PATH = selected_sources[0][2] if selected_sources else ""
TRADES_PATH = selected_sources[0][3] if selected_sources else ""

st.divider()

@st.cache_data(show_spinner="⏳ Aggregating orders…", persist="disk")
def load_orders_summary(orders_path: str) -> pd.DataFrame:
    return duckdb.query(f"""
        SELECT
            COUNTRY                                                       AS country,
            COALESCE(MARKET_AREA, 'Single Zone')                          AS market_area,
            COALESCE(ORIG_INS_TYPE, 'UNKNOWN')                            AS instrument,
            TRAN_STATUS                                                    AS status,
            CASE WHEN PARTY IS NULL THEN 'Public' ELSE 'Private' END      AS data_type,
            COALESCE(PARTY, 'Public')                                      AS client,
            hour(TRAN_DATETIME)                                            AS trade_hour,
            COALESCE(ORDER_TYPE, 'LIM')                                   AS order_type,
            COUNT(*)                                                       AS records,
            SUM(VOLUME)                                                    AS total_volume,
            AVG(PRICE)                                                     AS avg_price,
            MIN(PRICE)                                                     AS min_price,
            MAX(PRICE)                                                     AS max_price,
            COUNT(*) FILTER (WHERE PRICE < 0)                             AS neg_price_count,
            APPROX_COUNT_DISTINCT(ORIG_TRAN_ID)                           AS unique_ids
        FROM {parquet_scan_expr(orders_path)}
        GROUP BY 1,2,3,4,5,6,7,8
    """).df()


@st.cache_data(show_spinner="⏳ Aggregating trades…", persist="disk")
def load_trades_summary(trades_path: str) -> pd.DataFrame:
    return duckdb.query(f"""
        SELECT
            COUNTRY                                                       AS country,
            COALESCE(MARKET_AREA, 'Single Zone')                          AS market_area,
            COALESCE(ORIG_INS_TYPE, 'UNKNOWN')                            AS instrument,
            TRAN_STATUS                                                    AS status,
            BUY_SELL                                                       AS buy_sell,
            CASE WHEN PARTY IS NULL THEN 'Public' ELSE 'Private' END      AS data_type,
            COALESCE(PARTY, 'Public')                                      AS client,
            hour(TRAN_DATETIME)                                            AS trade_hour,
            COUNT(*)                                                       AS records,
            SUM(VOLUME)                                                    AS total_volume,
            AVG(PRICE)                                                     AS avg_price,
            MIN(PRICE)                                                     AS min_price,
            MAX(PRICE)                                                     AS max_price,
            COUNT(*) FILTER (WHERE PRICE < 0)                             AS neg_price_count,
            APPROX_COUNT_DISTINCT(ORIG_TRAN_ID)                           AS unique_ids
        FROM {parquet_scan_expr(trades_path)}
        GROUP BY 1,2,3,4,5,6,7,8
    """).df()


@st.cache_data(show_spinner=False, persist="disk")
def load_trends(exchange_key: str) -> pd.DataFrame:
    """Aggregate daily totals across all dates for this exchange — for the Trends tab."""
    rows = []
    for trading_date, paths in all_data[exchange_key].items():
        df = duckdb.query(f"""
            SELECT
                COUNT(*) AS orders,
                SUM(VOLUME) AS total_volume,
                COUNT(*) FILTER (WHERE PARTY IS NOT NULL) AS private_orders,
                COUNT(*) FILTER (WHERE PARTY IS NULL)     AS public_orders
            FROM {parquet_scan_expr(paths["orders"])}
        """).df().iloc[0]
        tr = duckdb.query(f"""
            SELECT COUNT(*) AS trades FROM {parquet_scan_expr(paths["trades"])}
        """).df().iloc[0]
        rows.append({
            "Date":           trading_date,
            "Orders":         int(df["orders"]),
            "Trades":         int(tr["trades"]),
            "Public Orders":  int(df["public_orders"]),
            "Private Orders": int(df["private_orders"]),
            "Total Volume":   float(df["total_volume"] or 0),
            "Fill Rate %":    round(int(tr["trades"]) / max(int(df["orders"]), 1) * 100, 2),
        })
    return pd.DataFrame(rows).sort_values("Date")


@st.cache_data(show_spinner="Loading records…", ttl=120)
def load_raw_orders(orders_path: str, country, market_area, instrument, limit=1000) -> pd.DataFrame:
    filters = []
    if country:
        filters.append(f"COUNTRY = '{country}'")
    if market_area:
        filters.append("MARKET_AREA IS NULL" if market_area == "Single Zone" else f"MARKET_AREA = '{market_area}'")
    if instrument and instrument != "UNKNOWN":
        filters.append(f"ORIG_INS_TYPE = '{instrument}'")
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    return duckdb.query(f"""
        SELECT TRAN_STATUS, ORIG_TRAN_ID, SORT_ID, TRAN_DATETIME,
               COUNTRY, MARKET_AREA, ORIG_INS_TYPE, DELIVERY_PERIOD,
               BID_ASK, ORDER_TYPE, PRICE, VOLUME, HIDDEN_VOL,
               CURRENCY, PARTY, SUBPARTY, SOURCE
        FROM {parquet_scan_expr(orders_path)}
        {where}
        ORDER BY TRAN_DATETIME DESC
        LIMIT {limit}
    """).df()


@st.cache_data(show_spinner="Loading records…", ttl=120)
def load_raw_trades(trades_path: str, country, market_area, instrument, limit=1000) -> pd.DataFrame:
    filters = []
    if country:
        filters.append(f"COUNTRY = '{country}'")
    if market_area:
        filters.append("MARKET_AREA IS NULL" if market_area == "Single Zone" else f"MARKET_AREA = '{market_area}'")
    if instrument and instrument != "UNKNOWN":
        filters.append(f"ORIG_INS_TYPE = '{instrument}'")
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    return duckdb.query(f"""
        SELECT TRAN_STATUS, ORIG_TRAN_ID, ORDER_REF, SORT_ID, TRAN_DATETIME,
               COUNTRY, MARKET_AREA, ORIG_INS_TYPE, DELIVERY_PERIOD,
               BUY_SELL, PRICE, VOLUME, DELIVERY_HOURS, CURRENCY,
               PARTY, SUBPARTY, SOURCE
        FROM {parquet_scan_expr(trades_path)}
        {where}
        ORDER BY TRAN_DATETIME DESC
        LIMIT {limit}
    """).df()


# ─────────────────────────────────────────────────────────────────────────────
# LOAD SUMMARY DATA — concat across all selected sources
# ─────────────────────────────────────────────────────────────────────────────
if not selected_sources:
    st.warning("No data available for the selected exchange/date combination.")
    st.stop()

ord_chunks, trd_chunks = [], []
for ex, dt, op, tp in selected_sources:
    o = load_orders_summary(op).copy()
    o["exchange"] = ex
    o["date"]     = dt
    ord_chunks.append(o)
    t = load_trades_summary(tp).copy()
    t["exchange"] = ex
    t["date"]     = dt
    trd_chunks.append(t)

ord_df = pd.concat(ord_chunks, ignore_index=True)
trd_df = pd.concat(trd_chunks, ignore_index=True)

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — drill-down only
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 Data Stats")
    st.divider()
    st.markdown("### 🔍 Drill-Down Filters")
    st.caption("Filters apply to the **current date** view. Use the Trends tab for multi-day analysis.")

    dataset = st.radio("Dataset", ["Orders", "Trades"], horizontal=True)
    base_df = ord_df if dataset == "Orders" else trd_df

    # Apply client filter first so drill-downs reflect it
    def apply_client(df: pd.DataFrame) -> pd.DataFrame:
        if sel_party != "All clients":
            return df[df["client"].isin([sel_party, "Public"])]
        return df

    base_df_filtered = apply_client(base_df)

    dtype_opts = ["All"] + sorted(base_df_filtered["data_type"].unique())
    sel_dtype  = st.selectbox("① Data Type", dtype_opts)
    df1        = base_df_filtered if sel_dtype == "All" else base_df_filtered[base_df_filtered["data_type"] == sel_dtype]

    country_opts = ["All"] + sorted(df1["country"].dropna().unique())
    sel_country  = st.selectbox("② Country", country_opts)
    df2          = df1 if sel_country == "All" else df1[df1["country"] == sel_country]

    market_opts = ["All"] + sorted(df2["market_area"].dropna().unique())
    sel_market  = st.selectbox("③ Market Area", market_opts)
    df3         = df2 if sel_market == "All" else df2[df2["market_area"] == sel_market]

    ins_opts = ["All"] + sorted(df3["instrument"].dropna().unique())
    sel_ins  = st.selectbox("④ Instrument", ins_opts)

    st.divider()
    all_statuses = sorted(base_df_filtered["status"].dropna().unique())
    sel_statuses = st.multiselect("Status filter", all_statuses, default=all_statuses)


# ─────────────────────────────────────────────────────────────────────────────
# APPLY ALL FILTERS
# ─────────────────────────────────────────────────────────────────────────────
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    d = apply_client(df)
    if sel_dtype   != "All": d = d[d["data_type"]   == sel_dtype]
    if sel_country != "All": d = d[d["country"]     == sel_country]
    if sel_market  != "All": d = d[d["market_area"] == sel_market]
    if sel_ins     != "All": d = d[d["instrument"]  == sel_ins]
    if sel_statuses:         d = d[d["status"].isin(sel_statuses)]
    return d

filt_df = apply_filters(base_df)

# ─────────────────────────────────────────────────────────────────────────────
# BREADCRUMB & KPIs
# ─────────────────────────────────────────────────────────────────────────────
crumbs = ["All"]
for label in [sel_dtype, sel_country, sel_market, sel_ins]:
    if label != "All":
        crumbs.append(label)

client_label = sel_party if sel_party != "All clients" else "All clients"
st.markdown(f"**{sel_exchange}** · `{DATA_DATE}` · 👤 {client_label} · {dataset}")
st.markdown("📍 " + " › ".join(f"**{c}**" for c in crumbs))
st.caption(f"Showing {int(filt_df['records'].sum()):,} records across {int(filt_df['unique_ids'].sum()):,} unique IDs")

total_records = int(filt_df["records"].sum())
total_volume  = filt_df["total_volume"].sum()
wavg_price    = ((filt_df["avg_price"] * filt_df["records"]).sum() / total_records if total_records > 0 else 0)
unique_ids    = int(filt_df["unique_ids"].sum())
cancel_pct    = (filt_df.loc[filt_df["status"] == "C", "records"].sum() / total_records * 100 if total_records > 0 else 0)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Records",     f"{total_records:,}")
k2.metric("Total Volume (MW)", f"{total_volume:,.0f}")
k3.metric("Wt. Avg Price",     f"€{wavg_price:,.2f}")
k4.metric("Unique IDs",        f"{unique_ids:,}")
k5.metric("Cancel Rate",       f"{cancel_pct:.1f}%")
st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# DRILL-DOWN GROUPING LEVEL
# ─────────────────────────────────────────────────────────────────────────────
if sel_ins != "All":
    group_col, group_label, chart_title = "trade_hour", "Hour of Day (UTC)", f"Hour breakdown — {sel_ins}"
elif sel_market != "All":
    group_col, group_label, chart_title = "instrument", "Instrument", f"Instruments in {sel_market}"
elif sel_country != "All":
    group_col, group_label, chart_title = "market_area", "Market Area", f"Market Areas in {sel_country}"
else:
    group_col, group_label, chart_title = "country", "Country", "All Countries"

# ─────────────────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────────────────
tab_vol, tab_time, tab_heat, tab_price, tab_trends, tab_raw, tab_profile = st.tabs([
    "📊 Volume Breakdown",
    "⏰ Time of Day",
    "🗺️ Heatmap",
    "💰 Price Analysis",
    "📅 Trends",
    "📋 Raw Records",
    "🏷️ Data Profile",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — VOLUME BREAKDOWN
# ══════════════════════════════════════════════════════════════════════════════
with tab_vol:
    col_a, col_b = st.columns(2)

    with col_a:
        agg = (filt_df.groupby([group_col, "status"])["records"]
               .sum().reset_index().sort_values(group_col))
        fig = px.bar(agg, x=group_col, y="records", color="status",
                     color_discrete_map=STATUS_COLORS,
                     title=f"Record Count by Status — {chart_title}",
                     labels={"records": "Count", group_col: group_label, "status": "Status"},
                     barmode="stack")
        fig.update_layout(height=420, xaxis_tickangle=-35)
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        agg2 = (filt_df.groupby([group_col, "data_type"])["total_volume"]
                .sum().reset_index().sort_values(group_col))
        fig2 = px.bar(agg2, x=group_col, y="total_volume", color="data_type",
                      color_discrete_map=TYPE_COLORS,
                      title=f"Volume (MW) — {chart_title}",
                      labels={"total_volume": "Volume (MW)", group_col: group_label, "data_type": "Type"},
                      barmode="stack")
        fig2.update_layout(height=420, xaxis_tickangle=-35)
        st.plotly_chart(fig2, use_container_width=True)

    col_c, col_d, col_e = st.columns(3)
    with col_c:
        s_agg = filt_df.groupby("status")["records"].sum().reset_index()
        fig3 = px.pie(s_agg, names="status", values="records",
                      color="status", color_discrete_map=STATUS_COLORS,
                      title="Record Split by Status", hole=0.45)
        fig3.update_layout(height=320)
        st.plotly_chart(fig3, use_container_width=True)

    with col_d:
        t_agg = filt_df.groupby("data_type")["total_volume"].sum().reset_index()
        fig4 = px.pie(t_agg, names="data_type", values="total_volume",
                      color="data_type", color_discrete_map=TYPE_COLORS,
                      title="Volume: Public vs Private", hole=0.45)
        fig4.update_layout(height=320)
        st.plotly_chart(fig4, use_container_width=True)

    with col_e:
        if dataset == "Trades" and "buy_sell" in filt_df.columns:
            bs_agg = filt_df.groupby("buy_sell")["total_volume"].sum().reset_index()
            fig5 = px.pie(bs_agg, names="buy_sell", values="total_volume",
                          color="buy_sell", color_discrete_map=BS_COLORS,
                          title="Buy vs Sell Volume", hole=0.45)
        else:
            ot_agg = filt_df.groupby("order_type")["records"].sum().reset_index() if "order_type" in filt_df.columns else pd.DataFrame()
            fig5 = px.pie(ot_agg, names="order_type", values="records",
                          title="Order Type Split (LIM vs ICE)", hole=0.45) if not ot_agg.empty else go.Figure()
        fig5.update_layout(height=320)
        st.plotly_chart(fig5, use_container_width=True)

    st.markdown("#### 📋 Volume Summary Table")
    summary = (filt_df.groupby([group_col, "data_type"])
               .agg(records=("records","sum"), total_volume=("total_volume","sum"),
                    avg_price=("avg_price","mean"), min_price=("min_price","min"),
                    max_price=("max_price","max"), unique_ids=("unique_ids","sum"))
               .reset_index().sort_values("total_volume", ascending=False))
    summary.columns = [group_label, "Type", "Records", "Vol (MW)",
                       "Avg Price", "Min Price", "Max Price", "Unique IDs"]
    for col in ["Vol (MW)", "Avg Price", "Min Price", "Max Price"]:
        summary[col] = summary[col].round(3)
    summary["Records"]    = summary["Records"].apply(lambda x: f"{int(x):,}")
    summary["Unique IDs"] = summary["Unique IDs"].apply(lambda x: f"{int(x):,}")
    st.dataframe(summary, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TIME OF DAY
# ══════════════════════════════════════════════════════════════════════════════
with tab_time:
    st.markdown(f"#### 📊 Activity broken into round hours (UTC) — {DATA_DATE}")

    hourly_status = filt_df.groupby(["trade_hour", "status"])["records"].sum().reset_index()
    fig_h1 = px.bar(hourly_status, x="trade_hour", y="records", color="status",
                    color_discrete_map=STATUS_COLORS,
                    title="Order/Trade Events by Hour — coloured by Status",
                    labels={"trade_hour": "Hour (UTC)", "records": "Record Count", "status": "Status"},
                    barmode="stack")
    fig_h1.update_xaxes(tickmode="linear", tick0=0, dtick=1, range=[-0.5, 23.5])
    fig_h1.update_layout(height=380)
    st.plotly_chart(fig_h1, use_container_width=True)

    col_f, col_g = st.columns(2)
    with col_f:
        hourly_vol = filt_df.groupby(["trade_hour", "data_type"])["total_volume"].sum().reset_index()
        fig_h2 = px.bar(hourly_vol, x="trade_hour", y="total_volume", color="data_type",
                        color_discrete_map=TYPE_COLORS,
                        title="Volume (MW) per Hour — Public vs Private",
                        labels={"trade_hour": "Hour (UTC)", "total_volume": "Volume (MW)", "data_type": "Type"},
                        barmode="group")
        fig_h2.update_xaxes(tickmode="linear", tick0=0, dtick=1, range=[-0.5, 23.5])
        fig_h2.update_layout(height=380)
        st.plotly_chart(fig_h2, use_container_width=True)

    with col_g:
        hourly_cancel = filt_df.groupby("trade_hour").apply(
            lambda x: pd.Series({
                "cancel_rate": x.loc[x["status"]=="C","records"].sum() / x["records"].sum() * 100
                               if x["records"].sum() > 0 else 0,
                "total_records": x["records"].sum(),
            })
        ).reset_index()
        fig_h3 = make_subplots(specs=[[{"secondary_y": True}]])
        fig_h3.add_trace(go.Bar(x=hourly_cancel["trade_hour"], y=hourly_cancel["total_records"],
                                name="Total Records", marker_color="#AEC6CF", opacity=0.6), secondary_y=False)
        fig_h3.add_trace(go.Scatter(x=hourly_cancel["trade_hour"], y=hourly_cancel["cancel_rate"],
                                    name="Cancel Rate %", line=dict(color="#d62728", width=2),
                                    mode="lines+markers"), secondary_y=True)
        fig_h3.update_layout(title_text="Volume vs Cancel Rate by Hour", height=380)
        fig_h3.update_xaxes(tickmode="linear", tick0=0, dtick=1, range=[-0.5, 23.5])
        fig_h3.update_yaxes(title_text="Record Count", secondary_y=False)
        fig_h3.update_yaxes(title_text="Cancel Rate (%)", secondary_y=True)
        st.plotly_chart(fig_h3, use_container_width=True)

    if sel_country == "All":
        st.markdown("#### 📈 Volume per Hour — by Country")
        hourly_country = filt_df.groupby(["trade_hour", "country"])["total_volume"].sum().reset_index()
        fig_hc = px.line(hourly_country, x="trade_hour", y="total_volume", color="country", markers=True,
                         title=f"Volume (MW) per Hour — per Country ({DATA_DATE})",
                         labels={"trade_hour": "Hour (UTC)", "total_volume": "Volume (MW)"})
        fig_hc.update_xaxes(tickmode="linear", tick0=0, dtick=1, range=[-0.5, 23.5])
        fig_hc.update_layout(height=380)
        st.plotly_chart(fig_hc, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — HEATMAP
# ══════════════════════════════════════════════════════════════════════════════
with tab_heat:
    st.markdown(f"#### 🗺️ Volume intensity — {DATA_DATE}")

    for grp, title, scale in [
        ("country",     "Country × Hour",      "Blues"),
        ("market_area", "Market Area × Hour",  "Oranges"),
        ("status",      "Status × Hour (records)", "Greens"),
    ]:
        val_col = "records" if grp == "status" else "total_volume"
        pivot = (filt_df.groupby([grp, "trade_hour"])[val_col]
                 .sum().reset_index()
                 .pivot(index=grp, columns="trade_hour", values=val_col)
                 .fillna(0))
        fig_hm = px.imshow(pivot, title=f"Heatmap: {title} ({DATA_DATE})",
                           labels={"x": "Hour (UTC)", "y": grp.replace("_"," ").title(),
                                   "color": "Volume (MW)" if val_col == "total_volume" else "Records"},
                           color_continuous_scale=scale, aspect="auto", text_auto=",")
        fig_hm.update_layout(height=max(220, len(pivot)*50+80))
        st.plotly_chart(fig_hm, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — PRICE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
with tab_price:
    col_p1, col_p2 = st.columns(2)

    with col_p1:
        price_country = (filt_df.groupby("country")
                         .apply(lambda x: pd.Series({
                             "avg_price": (x["avg_price"]*x["records"]).sum()/x["records"].sum(),
                             "min_price": x["min_price"].min(),
                             "max_price": x["max_price"].max(),
                         })).reset_index())
        fig_p1 = go.Figure()
        fig_p1.add_trace(go.Bar(x=price_country["country"],
                                y=price_country["max_price"]-price_country["min_price"],
                                base=price_country["min_price"], name="Price Range",
                                marker_color="#AEC6CF", opacity=0.5))
        fig_p1.add_trace(go.Scatter(x=price_country["country"], y=price_country["avg_price"],
                                    mode="markers", marker=dict(color="#1f77b4", size=10, symbol="diamond"),
                                    name="Weighted Avg Price"))
        fig_p1.update_layout(title="Price Range & Avg by Country", yaxis_title="Price (EUR/MWh)", height=380)
        st.plotly_chart(fig_p1, use_container_width=True)

    with col_p2:
        neg_by_country = (filt_df.groupby("country")
                          .agg(neg=("neg_price_count","sum"), total=("records","sum"))
                          .reset_index())
        neg_by_country["neg_pct"] = neg_by_country["neg"]/neg_by_country["total"]*100
        fig_p2 = px.bar(neg_by_country, x="country", y="neg_pct",
                        title="Negative Price Records — % of Total by Country",
                        labels={"country":"Country","neg_pct":"% Negative Prices"},
                        color="neg_pct", color_continuous_scale="Reds")
        fig_p2.update_layout(height=380)
        st.plotly_chart(fig_p2, use_container_width=True)

    price_hour = (filt_df.groupby(["trade_hour","country"])
                  .apply(lambda x: (x["avg_price"]*x["records"]).sum()/x["records"].sum())
                  .reset_index(name="wavg_price"))
    fig_p3 = px.line(price_hour, x="trade_hour", y="wavg_price", color="country",
                     title=f"Weighted Avg Price by Hour — per Country ({DATA_DATE})",
                     labels={"trade_hour":"Hour (UTC)","wavg_price":"Avg Price (EUR/MWh)"},
                     markers=True)
    fig_p3.update_xaxes(tickmode="linear", tick0=0, dtick=1, range=[-0.5, 23.5])
    fig_p3.update_layout(height=380)
    st.plotly_chart(fig_p3, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — TRENDS (multi-day)
# ══════════════════════════════════════════════════════════════════════════════
with tab_trends:
    st.markdown(f"#### 📅 Multi-Day Trends — {sel_exchange}")
    st.caption("Shows daily aggregates across **all available dates** for selected exchanges. Client filter does not apply here.")

    trend_chunks = []
    for ex in sel_exchanges:
        if len(all_data.get(ex, {})) >= 1:
            df_t = load_trends(ex).copy()
            df_t["Exchange"] = ex
            trend_chunks.append(df_t)

    if not trend_chunks or sum(len(c) for c in trend_chunks) < 2:
        st.info("Select more dates or exchanges to see trends.")
    else:
        trends_df = pd.concat(trend_chunks, ignore_index=True).sort_values(["Exchange", "Date"])

        tr1, tr2 = st.columns(2)
        with tr1:
            fig_tr1 = px.line(trends_df, x="Date", y="Orders", color="Exchange",
                              title="Daily Orders per Exchange", markers=True,
                              color_discrete_sequence=px.colors.qualitative.Bold)
            fig_tr1.update_layout(height=320, xaxis_tickangle=-30)
            fig_tr1.update_traces(line_width=2)
            st.plotly_chart(fig_tr1, use_container_width=True)
        with tr2:
            fig_tr2 = px.line(trends_df, x="Date", y="Trades", color="Exchange",
                              title="Daily Trades per Exchange", markers=True,
                              color_discrete_sequence=px.colors.qualitative.Bold)
            fig_tr2.update_layout(height=320, xaxis_tickangle=-30)
            fig_tr2.update_traces(line_width=2)
            st.plotly_chart(fig_tr2, use_container_width=True)

        tr3, tr4 = st.columns(2)
        with tr3:
            melt = trends_df[["Date", "Exchange", "Public Orders", "Private Orders"]].melt(
                id_vars=["Date", "Exchange"], var_name="Type", value_name="Count")
            fig_tr3 = px.bar(melt, x="Date", y="Count", color="Type", barmode="stack",
                             facet_col="Exchange",
                             title="Daily Orders — Public vs Private",
                             color_discrete_map={"Public Orders": "#4C78A8", "Private Orders": "#F58518"})
            fig_tr3.update_layout(height=320, xaxis_tickangle=-30)
            st.plotly_chart(fig_tr3, use_container_width=True)
        with tr4:
            fig_tr4 = px.line(trends_df, x="Date", y="Fill Rate %", color="Exchange",
                              title="Daily Fill Rate %", markers=True,
                              color_discrete_sequence=px.colors.qualitative.Bold)
            fig_tr4.add_hline(y=100, line_dash="dot", line_color="grey")
            fig_tr4.update_layout(height=320, xaxis_tickangle=-30)
            fig_tr4.update_traces(line_width=2)
            st.plotly_chart(fig_tr4, use_container_width=True)

        st.markdown("#### 📋 Daily Summary Table")
        st.dataframe(trends_df.sort_values("Date", ascending=False), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — RAW RECORDS
# ══════════════════════════════════════════════════════════════════════════════
with tab_raw:
    st.markdown(f"#### 📋 Raw Record Explorer")
    st.caption("Drill down to a specific instrument to load meaningful record samples.")

    # Source picker when multiple selected
    source_labels = [f"{ex} · {dt}" for ex, dt, _, _ in selected_sources]
    if len(selected_sources) > 1:
        raw_src_label = st.selectbox("Source to explore", source_labels, key="raw_src")
        raw_src_idx   = source_labels.index(raw_src_label)
    else:
        raw_src_idx = 0
    _, _, raw_op, raw_tp = selected_sources[raw_src_idx]

    raw_country = sel_country if sel_country != "All" else None
    raw_market  = sel_market  if sel_market  != "All" else None
    raw_ins     = sel_ins     if sel_ins     != "All" else None

    col_btn1, col_btn2, _ = st.columns([1, 1, 4])
    load_data = col_btn1.button("🔍 Load Records")
    row_limit = col_btn2.selectbox("Rows", [100, 250, 500, 1000], index=1, label_visibility="collapsed")

    if load_data:
        with st.spinner("Querying parquet…"):
            raw = (load_raw_orders(raw_op, raw_country, raw_market, raw_ins, limit=row_limit)
                   if dataset == "Orders"
                   else load_raw_trades(raw_tp, raw_country, raw_market, raw_ins, limit=row_limit))

        def style_row(row):
            s = [""] * len(row)
            col_map = {c: i for i, c in enumerate(row.index)}
            status = row.get("TRAN_STATUS", "")
            if status == "C":   s = ["background-color: #ffe6e6"] * len(row)
            elif status == "E": s = ["background-color: #e6ffe6"] * len(row)
            elif status == "P": s = ["background-color: #fff3e0"] * len(row)
            if "VOLUME" in col_map and pd.notna(row["VOLUME"]) and row["VOLUME"] == 0:
                s[col_map["VOLUME"]] = "background-color: #ff4444; color: white; font-weight: bold"
            if "PRICE" in col_map and pd.notna(row["PRICE"]) and row["PRICE"] < 0:
                s[col_map["PRICE"]] = "background-color: #fff3cd; font-weight: bold"
            return s

        st.caption(f"Loaded **{len(raw):,}** records")
        st.dataframe(raw.style.apply(style_row, axis=1), use_container_width=True, height=520)
        st.caption("🔴 Cancelled  🟢 Executed  🟠 Partial  🟡 Negative price  🔴 Zero volume")
        if len(raw) > 0:
            a1, a2, a3 = st.columns(3)
            a1.metric("Zero Volume Rows",    int((raw["VOLUME"]==0).sum()) if "VOLUME" in raw.columns else "N/A")
            a2.metric("Negative Price Rows", int((raw["PRICE"]<0).sum())   if "PRICE"  in raw.columns else "N/A")
            a3.metric("NULL ORDER_REF",      int(raw["ORDER_REF"].isna().sum()) if "ORDER_REF" in raw.columns else "N/A")
    else:
        st.info("👆 Select filters in the sidebar then click **Load Records** to view raw data.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — DATA PROFILE
# ══════════════════════════════════════════════════════════════════════════════
with tab_profile:
    st.markdown(f"#### 🏷️ Data Profile — Trading Date: **{DATA_DATE}**")
    st.divider()

    col_d1, col_d2, col_d3 = st.columns(3)
    col_d1.metric("Trading Date",  DATA_DATE)
    col_d2.metric("Orders File",   "CLIENT_ORDERS")
    col_d3.metric("Trades File",   "CLIENT_TRADES")
    st.markdown("---")

    st.markdown("##### 👤 PARTY Breakdown")
    st.caption("Public = PARTY IS NULL (market data) | Private = PARTY IS NOT NULL (own data)")

    party_rows = []
    for tbl, df_sum in [("CLIENT_ORDERS", ord_df), ("CLIENT_TRADES", trd_df)]:
        total = int(df_sum["records"].sum())
        for dtype in ["Public", "Private"]:
            subset      = df_sum[df_sum["data_type"] == dtype]
            n           = int(subset["records"].sum())
            vol         = subset["total_volume"].sum()
            party_label = "NULL (Public)" if dtype == "Public" else "Axpo (Private)"
            party_rows.append({
                "Table":             tbl,
                "PARTY":             party_label,
                "Data Type":         dtype,
                "Record Count":      f"{n:,}",
                "% of Table":        f"{n/total*100:.2f}%" if total > 0 else "0%",
                "Total Volume (MW)": f"{vol:,.0f}",
                "Unique IDs":        f"{int(subset['unique_ids'].sum()):,}",
            })

    party_df = pd.DataFrame(party_rows)

    def highlight_private(row):
        if row["Data Type"] == "Private":
            return ["background-color: #b45309; color: white; font-weight: bold"] * len(row)
        return [""] * len(row)

    st.dataframe(party_df.style.apply(highlight_private, axis=1),
                 use_container_width=True, hide_index=True)

    col_do1, col_do2 = st.columns(2)
    # Use raw numeric values for charts
    party_chart_df = pd.DataFrame(party_rows).copy()
    party_chart_df["Record Count Num"] = party_chart_df["Record Count"].str.replace(",","").astype(int)

    with col_do1:
        ord_p = party_chart_df[party_chart_df["Table"] == "CLIENT_ORDERS"]
        fig_o = px.pie(ord_p, names="PARTY", values="Record Count Num",
                       title=f"CLIENT_ORDERS — Party Split ({DATA_DATE})",
                       color="Data Type", color_discrete_map=TYPE_COLORS, hole=0.5)
        fig_o.update_traces(textinfo="label+percent+value")
        fig_o.update_layout(height=340)
        st.plotly_chart(fig_o, use_container_width=True)

    with col_do2:
        trd_p = party_chart_df[party_chart_df["Table"] == "CLIENT_TRADES"]
        fig_t = px.pie(trd_p, names="PARTY", values="Record Count Num",
                       title=f"CLIENT_TRADES — Party Split ({DATA_DATE})",
                       color="Data Type", color_discrete_map=TYPE_COLORS, hole=0.5)
        fig_t.update_traces(textinfo="label+percent+value")
        fig_t.update_layout(height=340)
        st.plotly_chart(fig_t, use_container_width=True)

    st.markdown("##### ⏰ Volume by Hour — Public vs Private")
    for tbl, df_sum, title in [("Orders", ord_df, "CLIENT_ORDERS"), ("Trades", trd_df, "CLIENT_TRADES")]:
        hourly_party = df_sum.groupby(["trade_hour","data_type"])["total_volume"].sum().reset_index()
        hourly_party["data_type"] = hourly_party["data_type"].replace({"Private": "Axpo (Private)"})
        fig_hp = px.bar(hourly_party, x="trade_hour", y="total_volume", color="data_type",
                        color_discrete_map={"Public": "#4C78A8", "Axpo (Private)": "#F58518"},
                        title=f"{title} — Volume (MW) per Hour ({DATA_DATE})",
                        labels={"trade_hour":"Hour (UTC)","total_volume":"Volume (MW)","data_type":"Party"},
                        barmode="stack")
        fig_hp.update_xaxes(tickmode="linear", tick0=0, dtick=1, range=[-0.5, 23.5])
        fig_hp.update_layout(height=320)
        st.plotly_chart(fig_hp, use_container_width=True)

"""
DQE — Data Overview (Unified)
High-level data count table with multi-select filters.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px
from utils import parquet_scan_expr, discover_all_data_files

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Filter bar labels */
.filter-label {
    font-size: 0.78rem;
    font-weight: 600;
    color: #a0aec0;
    letter-spacing: 0.05em;
    margin-bottom: 2px;
}
/* Tighten multiselect tags */
div[data-baseweb="select"] span {
    font-size: 0.82rem;
}
/* Table styling */
.count-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
}
.count-table th {
    background: #1e2130;
    color: #e2e8f0;
    font-weight: 700;
    padding: 8px 14px;
    border: 1px solid #2d3748;
    text-align: left;
    white-space: nowrap;
}
.count-table th.date-col {
    text-align: center;
}
.count-table td {
    padding: 6px 14px;
    border: 1px solid #2d3748;
    color: #e2e8f0;
    vertical-align: middle;
}
.count-table td.num {
    text-align: center;
    font-variant-numeric: tabular-nums;
    font-family: 'Courier New', monospace;
}
.count-table td.num-public {
    text-align: center;
    font-variant-numeric: tabular-nums;
    font-family: 'Courier New', monospace;
    color: #63b3ed;
}
.count-table td.num-private {
    text-align: center;
    font-variant-numeric: tabular-nums;
    font-family: 'Courier New', monospace;
    color: #f6ad55;
    font-weight: 600;
}
.count-table td.table-type {
    font-weight: 700;
    color: #90cdf4;
    font-size: 0.85rem;
    vertical-align: middle;
    text-align: center;
    background: #1a202c;
}
.count-table td.source {
    font-weight: 600;
    text-align: center;
    color: #f6e05e;
    background: #1e2130;
}
.count-table td.breakdown-public {
    color: #63b3ed;
    text-align: center;
}
.count-table td.breakdown-private {
    color: #f6ad55;
    font-weight: 600;
    text-align: center;
}
.count-table tr:hover td {
    background: #2a2f45 !important;
}
.count-table td.dash {
    color: #4a5568;
    text-align: center;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# ISO-2 → ISO-3 MAPPING
# ─────────────────────────────────────────────────────────────────────────────
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
# DATA DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────
all_data = discover_all_data_files()
all_exchanges = sorted(all_data.keys())
all_dates = sorted({d for ex in all_data.values() for d in ex.keys()}, reverse=True)
all_parties: list[str] = []
for ex, dates in all_data.items():
    for d, paths in dates.items():
        try:
            rows = duckdb.query(
                f"SELECT DISTINCT PARTY FROM {parquet_scan_expr(paths['orders'])} WHERE PARTY IS NOT NULL LIMIT 50"
            ).df()["PARTY"].dropna().tolist()
            all_parties.extend(rows)
        except Exception:
            pass
all_parties = sorted(set(all_parties))


# ─────────────────────────────────────────────────────────────────────────────
# CACHED COUNT FETCHER
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, persist="disk")
def get_counts(orders_path: str | list, trades_path: str | list, party: str | None) -> dict:
    op = str(orders_path)
    tp = str(trades_path)
    if party:
        where = f"WHERE PARTY = '{party}' OR PARTY IS NULL"
    else:
        where = ""
    o = duckdb.query(f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE PARTY IS NULL)     AS pub,
            COUNT(*) FILTER (WHERE PARTY IS NOT NULL) AS priv
        FROM {parquet_scan_expr(orders_path)} {where}
    """).df().iloc[0]
    t = duckdb.query(f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE PARTY IS NULL)     AS pub,
            COUNT(*) FILTER (WHERE PARTY IS NOT NULL) AS priv
        FROM {parquet_scan_expr(trades_path)} {where}
    """).df().iloc[0]
    return {
        "orders_pub":   int(o["pub"]),
        "orders_priv":  int(o["priv"]),
        "trades_pub":   int(t["pub"]),
        "trades_priv":  int(t["priv"]),
    }


@st.cache_data(show_spinner=False, persist="disk")
def get_source_stats(orders_path, trades_path) -> dict:
    o = duckdb.query(f"""
        SELECT COUNT(*) AS total_orders, APPROX_COUNT_DISTINCT(ORIG_TRAN_ID) AS unique_order_ids,
               COUNT(*) FILTER (WHERE PARTY IS NULL)     AS public_orders,
               COUNT(*) FILTER (WHERE PARTY IS NOT NULL) AS private_orders,
               COUNT(DISTINCT COUNTRY) AS countries, COUNT(DISTINCT INS_TYPE) AS instruments
        FROM {parquet_scan_expr(orders_path)}
    """).df().iloc[0]
    t = duckdb.query(f"""
        SELECT COUNT(*) AS total_trades
        FROM {parquet_scan_expr(trades_path)}
    """).df().iloc[0]
    return {
        "total_orders":   int(o["total_orders"]),
        "public_orders":  int(o["public_orders"]),
        "private_orders": int(o["private_orders"]),
        "total_trades":   int(t["total_trades"]),
        "countries":      int(o["countries"]),
    }


@st.cache_data(show_spinner=False, persist="disk")
def get_breakdown_stats(orders_path, trades_path) -> dict:
    schema_cols = duckdb.query(f"SELECT * FROM {parquet_scan_expr(orders_path)} LIMIT 0").df().columns.tolist()
    act_col = "TRAN_ACT_TYPE" if "TRAN_ACT_TYPE" in schema_cols else "TRAN_STATUS"

    country_df = duckdb.query(f"""
        SELECT COALESCE(COUNTRY,'Unknown') AS country, COUNT(*) AS cnt
        FROM {parquet_scan_expr(orders_path)}
        GROUP BY country ORDER BY cnt DESC
    """).df()

    trades_ins_df = duckdb.query(f"""
        SELECT COALESCE(INS_TYPE,'Unknown') AS ins_type, COUNT(*) AS cnt
        FROM {parquet_scan_expr(trades_path)}
        GROUP BY ins_type ORDER BY cnt DESC LIMIT 15
    """).df()

    return {"country": country_df, "trades_ins": trades_ins_df}


@st.cache_data(show_spinner=False, persist="disk")
def get_lifecycle_stats(orders_path) -> pd.DataFrame:
    """
    Per visibility (Public/Private):
    - total_ids : distinct ORIG_TRAN_IDs
    - executed  : IDs with at least one TRAN_STATUS = 'E'
    - partial   : IDs with at least one TRAN_STATUS = 'P' but NO 'E' (partial fills, ultimately cancelled)
    """
    df = duckdb.query(f"""
        WITH flags AS (
            SELECT
                ORIG_TRAN_ID,
                PARTY,
                MAX(CASE WHEN TRAN_STATUS = 'E' THEN 1 ELSE 0 END) AS has_e,
                MAX(CASE WHEN TRAN_STATUS = 'P' THEN 1 ELSE 0 END) AS has_p
            FROM {parquet_scan_expr(orders_path)}
            GROUP BY ORIG_TRAN_ID, PARTY
        )
        SELECT
            CASE WHEN PARTY IS NULL THEN 'Public' ELSE 'Private' END AS visibility,
            COUNT(*)                                                   AS total_ids,
            SUM(has_e)                                                 AS executed,
            SUM(CASE WHEN has_p = 1 AND has_e = 0 THEN 1 ELSE 0 END) AS partial
        FROM flags
        GROUP BY visibility
    """).df()
    return df


# ─────────────────────────────────────────────────────────────────────────────
# PAGE HEADER
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## 📊 Data Overview")
st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# FILTER BAR — 3-column multi-select dropdowns
# ─────────────────────────────────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown('<div class="filter-label">🏦 Exchange</div>', unsafe_allow_html=True)
    sel_exchanges = st.multiselect(
        label="Exchange",
        options=all_exchanges,
        default=all_exchanges,
        placeholder="All Exchanges",
        label_visibility="collapsed",
    )
    if not sel_exchanges:
        sel_exchanges = all_exchanges

with col2:
    st.markdown('<div class="filter-label">📅 Trading Date</div>', unsafe_allow_html=True)
    date_labels = {d: d[8:10] + "/" + d[5:7] + "/" + d[:4] for d in all_dates}  # DD/MM/YYYY
    sel_date_labels = st.multiselect(
        label="Trading Date",
        options=list(date_labels.values()),
        default=list(date_labels.values()),
        placeholder="All Dates",
        label_visibility="collapsed",
    )
    if not sel_date_labels:
        sel_date_labels = list(date_labels.values())
    # reverse map label → iso date
    label_to_date = {v: k for k, v in date_labels.items()}
    sel_dates = [label_to_date[lbl] for lbl in sel_date_labels]

with col3:
    st.markdown('<div class="filter-label">👤 Client</div>', unsafe_allow_html=True)
    client_options = ["All Clients"] + all_parties
    sel_client = st.multiselect(
        label="Client",
        options=client_options,
        default=["All Clients"],
        placeholder="All Clients",
        label_visibility="collapsed",
    )
    if not sel_client or "All Clients" in sel_client:
        party_filter: str | None = None
        client_label = "All Clients"
    else:
        party_filter = sel_client[0]   # primary client for count filtering
        client_label = ", ".join(sel_client)

st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# BUILD TABLE DATA
# ─────────────────────────────────────────────────────────────────────────────
# Display dates in DD/MM/YYYY as column headers, sorted chronologically
col_dates = sorted(
    [d for d in sel_dates if any(d in all_data.get(ex, {}) for ex in sel_exchanges)]
)
col_headers = [date_labels[d] for d in col_dates]

def fmt(n: int) -> str:
    return f"{n:,}" if n > 0 else "—"

# rows: list of (table_type, exchange, breakdown, {date: count})
table_rows = []
for table_type in ["CLIENT_ORDERS", "CLIENT_TRADES"]:
    for exchange in sel_exchanges:
        for breakdown in ["🔓 Public", "🔒 Private"]:
            row: dict = {
                "table_type": table_type,
                "exchange":   exchange,
                "breakdown":  breakdown,
            }
            has_any = False
            for d in col_dates:
                paths = all_data.get(exchange, {}).get(d)
                if paths is None:
                    row[d] = None
                    continue
                try:
                    counts = get_counts(paths["orders"], paths["trades"], party_filter)
                    key = ("orders_pub" if table_type == "CLIENT_ORDERS" else "trades_pub") if "Public" in breakdown \
                          else ("orders_priv" if table_type == "CLIENT_ORDERS" else "trades_priv")
                    row[d] = counts[key]
                    has_any = True
                except Exception:
                    row[d] = None
            if has_any:
                table_rows.append(row)

# ─────────────────────────────────────────────────────────────────────────────
# RENDER HTML TABLE with merged cells
# ─────────────────────────────────────────────────────────────────────────────
if not table_rows:
    st.info("No data available for the selected filters.")
    st.stop()

# Build HTML
html_rows = []
# Header
date_th = "".join(f'<th class="date-col">{h}</th>' for h in col_headers)
html_rows.append(
    f'<tr><th></th><th>Source</th><th>Breakdown</th>{date_th}</tr>'
)

# Group rows: table_type → exchange → [Public, Private]
from itertools import groupby

# Count rowspans needed
table_type_counts: dict[str, int] = {}
for r in table_rows:
    table_type_counts[r["table_type"]] = table_type_counts.get(r["table_type"], 0) + 1

exchange_counts: dict[tuple, int] = {}
for r in table_rows:
    key = (r["table_type"], r["exchange"])
    exchange_counts[key] = exchange_counts.get(key, 0) + 1

emitted_table_type: set[str] = set()
emitted_exchange: set[tuple] = set()

for r in table_rows:
    tt  = r["table_type"]
    ex  = r["exchange"]
    bd  = r["breakdown"]
    key = (tt, ex)

    cells = ""

    # Table type cell (rowspan across all exchanges × breakdowns)
    if tt not in emitted_table_type:
        span = table_type_counts[tt]
        cells += f'<td class="table-type" rowspan="{span}">{tt}</td>'
        emitted_table_type.add(tt)

    # Exchange cell (rowspan across Public + Private)
    if key not in emitted_exchange:
        span = exchange_counts[key]
        cells += f'<td class="source" rowspan="{span}">{ex}</td>'
        emitted_exchange.add(key)

    # Breakdown cell
    bd_class = "breakdown-public" if "Public" in bd else "breakdown-private"
    num_class = "num-public" if "Public" in bd else "num-private"
    icon = '<span style="filter:hue-rotate(195deg) saturate(5) brightness(1.1)">🔓</span> Public' if "Public" in bd else "🔒 Private"
    cells += f'<td class="{bd_class}">{icon}</td>'

    # Count cells
    for d in col_dates:
        val = r.get(d)
        if val is None:
            cells += '<td class="dash">—</td>'
        else:
            cells += f'<td class="{num_class}">{fmt(val)}</td>'

    html_rows.append(f"<tr>{cells}</tr>")

html = f'<table class="count-table">{"".join(html_rows)}</table>'
st.markdown(html, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# CHARTS — Volumes & Geography
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("---")

# Collect stats for all visible (exchange, date) combinations
chart_rows = []
with st.spinner("Loading chart data…"):
    for exchange in sel_exchanges:
        for d, paths in all_data.get(exchange, {}).items():
            if d not in col_dates:
                continue
            try:
                stats = get_source_stats(paths["orders"], paths["trades"])
                bkdn  = get_breakdown_stats(paths["orders"], paths["trades"])
                chart_rows.append({
                    "Exchange":       exchange,
                    "Date":           d,
                    "Source":         exchange + " · " + date_labels[d],
                    "Orders":         stats["total_orders"],
                    "Public Orders":  stats["public_orders"],
                    "Private Orders": stats["private_orders"],
                    "Trades":         stats["total_trades"],
                    "Fill Rate %":    round(stats["total_trades"] / max(stats["total_orders"], 1) * 100, 2),
                    "_country":       bkdn["country"],
                    "_lifecycle":     get_lifecycle_stats(paths["orders"]),
                })
            except Exception:
                pass

if not chart_rows:
    st.stop()

summary_df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in chart_rows])

tab_vol, tab_geo, tab_life = st.tabs(["📦 Volumes", "🌍 Geography", "🔄 Order Lifecycle"])

# ── VOLUMES TAB ──
with tab_vol:
    c1, c2 = st.columns(2)
    with c1:
        fig1 = px.bar(summary_df, x="Exchange", y="Orders", color="Date", barmode="group",
                      title="Orders by Exchange & Date", text_auto=".3s",
                      color_discrete_sequence=px.colors.qualitative.Set2)
        fig1.update_layout(height=340)
        st.plotly_chart(fig1, use_container_width=True)
    with c2:
        fig2 = px.bar(summary_df, x="Exchange", y="Trades", color="Date", barmode="group",
                      title="Trades by Exchange & Date", text_auto=".3s",
                      color_discrete_sequence=px.colors.qualitative.Set2)
        fig2.update_layout(height=340)
        st.plotly_chart(fig2, use_container_width=True)

# ── GEOGRAPHY TAB ──
with tab_geo:
    country_frames = []
    for r in chart_rows:
        df_c = r["_country"].copy()
        df_c["Source"]   = r["Source"]
        df_c["Exchange"] = r["Exchange"]
        country_frames.append(df_c)

    if country_frames:
        country_all = pd.concat(country_frames, ignore_index=True)
        agg = country_all.groupby("country")["cnt"].sum().reset_index()
        agg["iso3"] = agg["country"].map(ISO2_TO_ISO3)
        agg_mapped  = agg.dropna(subset=["iso3"])

        fig_map = px.choropleth(
            agg_mapped, locations="iso3", color="cnt",
            hover_name="country", hover_data={"iso3": False, "cnt": ":,"},
            color_continuous_scale="Blues",
            title="Order Volume by Country",
            labels={"cnt": "Orders"},
        )
        fig_map.update_layout(
            height=500,
            geo=dict(showframe=False, showcoastlines=True,
                     projection_type="natural earth", scope="europe"),
            coloraxis_colorbar=dict(title="Orders"),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_map, use_container_width=True)

        if st.checkbox("🌐 Show world map", key="dov_world_map"):
            fig_world = px.choropleth(
                agg_mapped, locations="iso3", color="cnt",
                hover_name="country", hover_data={"iso3": False, "cnt": ":,"},
                color_continuous_scale="Blues",
                title="Order Volume by Country — World",
                labels={"cnt": "Orders"},
            )
            fig_world.update_layout(
                height=450,
                geo=dict(showframe=False, projection_type="natural earth"),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_world, use_container_width=True)

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

# ── ORDER LIFECYCLE TAB ──────────────────────────────────────────────────────
with tab_life:
    st.markdown("#### 🔄 Private Order Lifecycle — Execution Analysis")
    st.caption(
        "**Fully Executed** = ORIG_TRAN_IDs with at least one TRAN_STATUS=E.  "
        "**Partially Executed** = ORIG_TRAN_IDs with at least one TRAN_STATUS=P but no E "
        "(partial fills ultimately cancelled)."
    )

    life_frames = []
    for r in chart_rows:
        df_l = r["_lifecycle"].copy()
        df_l["Source"]   = r["Source"]
        df_l["Exchange"] = r["Exchange"]
        df_l["Date"]     = r["Date"]
        life_frames.append(df_l)

    if not life_frames:
        st.info("No lifecycle data available.")
        st.stop()

    life_all = pd.concat(life_frames, ignore_index=True)
    # Private only
    priv = life_all[life_all["visibility"] == "Private"].copy()

    if priv.empty:
        st.info("No private order data found.")
        st.stop()

    # ── Pivot table: Exchange × (Total / Executed / Partial) × Date ───────────
    all_col_dates = sorted(priv["Date"].unique())
    all_exchanges_lc = sorted(priv["Exchange"].unique())

    # Build lookup: (exchange, date) → {total, executed, partial}
    lc_lookup: dict = {}
    for _, row in priv.iterrows():
        lc_lookup[(row["Exchange"], row["Date"])] = {
            "total":    int(row["total_ids"]),
            "executed": int(row["executed"]),
            "partial":  int(row["partial"]),
        }

    # Date column headers as DD/MM/YYYY
    col_hdr = {d: d[8:10] + "/" + d[5:7] + "/" + d[:4] for d in all_col_dates}

    date_ths = "".join(f'<th style="background:#f5e642;color:#111;text-align:center;padding:8px 18px;border:1px solid #ccc;font-weight:700;">{col_hdr[d]}</th>' for d in all_col_dates)
    header = f'''<tr>
        <th style="background:#f5e642;color:#111;text-align:center;padding:8px 16px;border:1px solid #ccc;font-weight:700;">Source</th>
        <th style="background:#f5e642;color:#111;text-align:center;padding:8px 18px;border:1px solid #ccc;font-weight:700;">Breakdown</th>
        {date_ths}
    </tr>'''

    BREAKDOWN_LABELS = [
        ("total",    "Total Unique Private ORIG_TRAN_IDs"),
        ("executed", "Fully Executed"),
        ("partial",  "Partially Executed"),
    ]

    body_rows = []
    for exchange in all_exchanges_lc:
        for i, (key, label) in enumerate(BREAKDOWN_LABELS):
            src_cell = f'<td style="text-align:center;font-weight:600;vertical-align:middle;border:1px solid #333;padding:6px 14px;" rowspan="3">{exchange}</td>' if i == 0 else ""
            bd_style = "font-weight:700;" if key == "total" else ""
            date_cells = ""
            for d in all_col_dates:
                val = lc_lookup.get((exchange, d), {}).get(key)
                txt = f"{val:,}" if val is not None else "—"
                date_cells += f'<td style="text-align:center;padding:6px 14px;border:1px solid #333;{bd_style}">{txt}</td>'
            body_rows.append(
                f'<tr>{src_cell}<td style="text-align:center;padding:6px 14px;border:1px solid #333;{bd_style}">{label}</td>{date_cells}</tr>'
            )

    html_tbl = f'''
    <table style="border-collapse:collapse;width:auto;font-size:0.88rem;margin-bottom:24px;">
        {header}
        {"".join(body_rows)}
    </table>'''
    st.markdown(html_tbl, unsafe_allow_html=True)

    # ── Combined % chart: (Executed + Partial) / Total grouped by Exchange ──────
    st.markdown("##### % Executed Orders")
    chart_data = []
    for _, row in priv.iterrows():
        total = row["total_ids"]
        if total == 0:
            continue
        pct = round((row["executed"] + row["partial"]) / total * 100, 1)
        chart_data.append({
            "Exchange": row["Exchange"],
            "Date":     col_hdr[row["Date"]],   # DD/MM/YYYY label
            "% Executed": pct,
        })

    chart_df = pd.DataFrame(chart_data).sort_values(["Exchange", "Date"])

    fig = px.bar(
        chart_df, x="Exchange", y="% Executed", color="Date", barmode="group",
        title="% Executed Orders",
        text=chart_df["% Executed"].map(lambda v: f"{v:.0f}%"),
        color_discrete_sequence=["#1f6f8b", "#e07b39"],
        labels={"% Executed": "", "Exchange": ""},
    )
    fig.update_traces(
        textposition="outside",
        textfont=dict(size=13, color="white"),
    )
    fig.update_layout(
        height=420,
        yaxis=dict(
            ticksuffix="%",
            range=[0, max(chart_df["% Executed"].max() * 1.3, 10)],
            gridcolor="#2d3748",
        ),
        xaxis=dict(showgrid=False),
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0", size=13),
        title_x=0.5,
        title_font=dict(size=16),
        margin=dict(t=60, b=60),
    )
    st.plotly_chart(fig, use_container_width=True)



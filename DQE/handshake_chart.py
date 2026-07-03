import pandas as pd
import plotly.graph_objects as go
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
CSV_FILE = Path(__file__).parent / "handshake_data.csv"
ITEMS = ["NORDPOOL", "EOD2_START", "EOD2_END", "EOD1_START", "EOD1_END"]
# ─────────────────────────────────────────────────────────────────────────────

df = pd.read_csv(CSV_FILE)

# Melt from wide (pivot) to long format
date_cols = [c for c in df.columns if c != "ITEM"]
df_long = df.melt(id_vars="ITEM", value_vars=date_cols, var_name="DATE", value_name="LAST_UPDATE")

# Filter to selected items only
df_long = df_long[df_long["ITEM"].isin(ITEMS)]

# Parse dates and datetimes
df_long["DATE"] = pd.to_datetime(df_long["DATE"], dayfirst=True)
df_long = df_long.dropna(subset=["LAST_UPDATE"])
df_long["LAST_UPDATE_DT"] = pd.to_datetime(df_long["LAST_UPDATE"], format="mixed", dayfirst=False, errors="coerce")
df_long = df_long.dropna(subset=["LAST_UPDATE_DT"])

# Extract time as decimal hours for y-axis (e.g. 07:30 → 7.5)
df_long["HOUR"] = df_long["LAST_UPDATE_DT"].dt.hour + df_long["LAST_UPDATE_DT"].dt.minute / 60
df_long["TIME_LABEL"] = df_long["LAST_UPDATE_DT"].dt.strftime("%H:%M")

# One value per ITEM per DATE (take max if duplicates)
df_long = df_long.groupby(["ITEM", "DATE"], as_index=False).agg({"HOUR": "max", "TIME_LABEL": "last"})

df_long = df_long.sort_values(["ITEM", "DATE"])

# ── Chart ─────────────────────────────────────────────────────────────────────
fig = go.Figure()

for item in df_long["ITEM"].unique():
    d = df_long[df_long["ITEM"] == item]
    fig.add_trace(go.Scatter(
        x=d["DATE"],
        y=d["HOUR"],
        mode="lines+markers",
        name=item,
        marker=dict(size=6),
        line=dict(width=2),
        connectgaps=False,
        hovertemplate=(
            "<b>" + item + "</b><br>"
            "Date: %{x|%d %b %Y}<br>"
            "Last Update: %{customdata}<extra></extra>"
        ),
        customdata=d["TIME_LABEL"],
    ))

# Y-axis: readable time labels
tick_vals = list(range(0, 25))
tick_text = [f"{h:02d}:00" for h in tick_vals]

fig.update_layout(
    title="Daily Last Update Time by Interface",
    xaxis=dict(
        title="Analysis Date",
        tickformat="%d %b",
        tickangle=-45,
        tickfont=dict(size=12),
        dtick="D1",
    ),
    yaxis_title="Last Update Time",
    yaxis=dict(tickvals=tick_vals, ticktext=tick_text, range=[0, 24]),
    hovermode="x unified",
    template="plotly_white",
    height=600,
    legend=dict(title="Interface"),
)

fig.show()

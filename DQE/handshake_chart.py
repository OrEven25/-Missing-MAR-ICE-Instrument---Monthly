import pandas as pd
import plotly.graph_objects as go
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
CSV_FILE = Path(__file__).parent / "handshake_data.csv"
ITEMS = ["EOD2_START", "EOD2_END"]
# ─────────────────────────────────────────────────────────────────────────────

df = pd.read_csv(CSV_FILE)

# Melt from wide (pivot) to long format
date_cols = [c for c in df.columns if c != "ITEM" and not c.startswith("Unnamed")]
df_long = df.melt(id_vars="ITEM", value_vars=date_cols, var_name="DATE", value_name="LAST_UPDATE")

# Filter to selected items only
df_long = df_long[df_long["ITEM"].isin(ITEMS)]

# Parse dates and datetimes
df_long["DATE"] = pd.to_datetime(df_long["DATE"], dayfirst=True)
df_long = df_long.dropna(subset=["LAST_UPDATE"])
df_long["LAST_UPDATE_DT"] = pd.to_datetime(df_long["LAST_UPDATE"], format="mixed", dayfirst=True, errors="coerce")
df_long = df_long.dropna(subset=["LAST_UPDATE_DT"])

df_long["HOVER_LABEL"] = df_long["LAST_UPDATE_DT"].dt.strftime("%d/%m/%Y %H:%M")

# One value per ITEM per DATE (take max if duplicates)
df_long = df_long.groupby(["ITEM", "DATE"], as_index=False).agg({"LAST_UPDATE_DT": "max", "HOVER_LABEL": "last"})

df_long = df_long.sort_values(["ITEM", "DATE"])

# ── Chart ─────────────────────────────────────────────────────────────────────
fig = go.Figure()

for item in df_long["ITEM"].unique():
    d = df_long[df_long["ITEM"] == item]
    fig.add_trace(go.Scatter(
        x=d["DATE"],
        y=d["LAST_UPDATE_DT"],          # full datetime on y-axis
        mode="lines+markers",
        name=item,
        marker=dict(size=8),
        line=dict(width=2),
        connectgaps=False,
        hovertemplate=(
            "<b>" + item + "</b><br>"
            "Analysis Date: %{x|%d %b %Y}<br>"
            "Completed: %{customdata}<extra></extra>"
        ),
        customdata=d["HOVER_LABEL"],
    ))

fig.update_layout(
    title="EOD2 Start & End — Full Completion Datetime by Analysis Date",
    xaxis=dict(
        title="Analysis Date",
        tickformat="%d %b",
        tickangle=-45,
        tickfont=dict(size=12),
        dtick="D1",
    ),
    yaxis=dict(
        title="Actual Completion Date & Time",
        tickformat="%d %b %H:%M",
        tickfont=dict(size=11),
    ),
    hovermode="x unified",
    template="plotly_white",
    height=600,
    legend=dict(title="Interface"),
)

fig.show()


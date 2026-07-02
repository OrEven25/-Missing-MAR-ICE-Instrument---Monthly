import pandas as pd
import plotly.graph_objects as go
from pathlib import Path

CSV_FILE = Path(__file__).parent / "last_update_data.csv"

df = pd.read_csv(CSV_FILE)
df["ANALYSIS_DATE"] = pd.to_datetime(df["ANALYSIS_DATE"], format="%d/%m/%Y")
df["LAST_UPDATE_TIME"] = pd.to_datetime(df["LAST_UPDATE"], format="%I:%M:%S %p")
df["HOUR_DECIMAL"] = df["LAST_UPDATE_TIME"].dt.hour + df["LAST_UPDATE_TIME"].dt.minute / 60
df = df.sort_values("ANALYSIS_DATE")

# Average line
avg = df["HOUR_DECIMAL"].mean()
avg_h = int(avg)
avg_m = int((avg % 1) * 60)
avg_label = f"Avg: {avg_h:02d}:{avg_m:02d} AM"

fig = go.Figure()

fig.add_trace(go.Scatter(
    x=df["ANALYSIS_DATE"],
    y=df["HOUR_DECIMAL"],
    mode="lines+markers",
    name="Last Update",
    line=dict(color="#1f77b4", width=2),
    marker=dict(size=7),
    hovertemplate="<b>%{x|%d %b %Y}</b><br>Last Update: %{customdata}<extra></extra>",
    customdata=df["LAST_UPDATE"],
))

fig.add_hline(
    y=avg,
    line_dash="dash",
    line_color="orange",
    annotation_text=avg_label,
    annotation_position="top left",
)

# Y-axis tick labels as readable times
tick_vals = list(range(0, 14))
tick_text = [f"{h:02d}:00 {'AM' if h < 12 else 'PM'}" for h in tick_vals]

fig.update_layout(
    title="EPEXSPOT — Daily Last Update Time",
    xaxis_title="Analysis Date",
    yaxis_title="Last Update Time",
    yaxis=dict(
        tickvals=tick_vals,
        ticktext=tick_text,
        range=[0, 13],
    ),
    hovermode="x unified",
    template="plotly_white",
    height=500,
)

fig.show()

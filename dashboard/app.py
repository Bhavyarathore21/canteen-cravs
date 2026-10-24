"""
Canteen Cravs: operations dashboard.

Run it from the project folder (canteen-cravs):

    streamlit run dashboard/app.py

It reads the small CSV files in dashboard/data/, which warehouse/run_analysis.py creates.
"""
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DATA = Path(__file__).resolve().parent / "data"

ORANGE, TEAL, YELLOW, DARK, GREY = "#E4572E", "#17A398", "#F2A541", "#2E282A", "#CFC9C2"
PALETTE = [ORANGE, TEAL, YELLOW, DARK]
TRUE_LOST_REVENUE = 23415  # known only because the data is simulated; used to judge the estimate

st.set_page_config(page_title="Canteen Cravs Dashboard", page_icon="🍽️", layout="wide")


@st.cache_data
def load(name):
    path = DATA / f"{name}.csv"
    if not path.exists():
        st.error(f"Missing {path.name}. Run warehouse/run_analysis.py first.")
        st.stop()
    return pd.read_csv(path)


def tidy(fig, height=380):
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", y=1.12, x=0),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ------------------------------------------------------------------ header
st.title("🍽️ Canteen Cravs")
st.caption(
    "Pre-order app for a college canteen: where the lunch rush hurts and what it costs. "
    "**All data is simulated** (90 days, about 9,800 orders)."
)

k = load("12_kpis").iloc[0]
cards = st.columns(6)
cards[0].metric("Orders", f"{int(k.total_orders):,}")
cards[1].metric("Revenue (completed)", f"₹{int(k.revenue):,}")
cards[2].metric("Avg wait for food", f"{k.avg_wait_min} min")
cards[3].metric("Cancelled", f"{k.cancel_rate_pct}%")
cards[4].metric("No-shows", f"{k.no_show_rate_pct}%")
cards[5].metric("Paid by UPI", f"{k.upi_share_pct}%")

tab_demand, tab_menu, tab_lost, tab_service = st.tabs(
    ["📈 Demand", "🍛 Menu and sell-outs", "💸 Lost sales", "⏱️ Service and payments"]
)

# ------------------------------------------------------------------ demand
with tab_demand:
    daily = load("10_daily_trend")
    daily["date"] = pd.to_datetime(daily["date"])
    daily["7-day average"] = daily["orders"].rolling(7, min_periods=1).mean()

    with st.container(border=True):
        st.subheader("Orders per day")
        fig = go.Figure()
        fig.add_bar(x=daily["date"], y=daily["orders"], name="Orders per day", marker_color=GREY)
        fig.add_scatter(x=daily["date"], y=daily["7-day average"], name="7-day average",
                        line=dict(color=ORANGE, width=3))
        st.plotly_chart(tidy(fig))
        st.caption("Usage grows as more students join the app. Gaps are Sundays and holidays.")

    left, right = st.columns([3, 2])
    with left, st.container(border=True):
        st.subheader("When is the rush?")
        heat = load("11_demand_heatmap")
        days = heat.drop_duplicates("day_name").sort_values("weekday_num")["day_name"].tolist()
        grid = heat.pivot(index="day_name", columns="hour", values="avg_orders").reindex(days)
        fig = px.imshow(grid, aspect="auto", text_auto=".0f",
                        color_continuous_scale=["#FFF3E6", ORANGE],
                        labels=dict(x="Hour of day", y="", color="Orders"))
        st.plotly_chart(tidy(fig, 360))
        st.caption("Average orders per open day, by weekday and hour.")
    with right, st.container(border=True):
        st.subheader("Orders by hour")
        peak = load("02_peak_hours")
        fig = px.bar(peak, x="hour_label", y="avg_orders_per_day", color="day_slot",
                     color_discrete_sequence=PALETTE,
                     labels=dict(hour_label="", avg_orders_per_day="Orders per day", day_slot=""))
        st.plotly_chart(tidy(fig, 360))

# ------------------------------------------------------------------ menu
with tab_menu:
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader("Revenue by item")
        top = load("03_top_items").sort_values("revenue")
        fig = px.bar(top, x="revenue", y="item_name", orientation="h", color="category",
                     color_discrete_sequence=PALETTE,
                     hover_data=["units_sold", "units_wasted", "pct_of_revenue"],
                     labels=dict(revenue="Revenue (₹)", item_name="", category=""))
        st.plotly_chart(tidy(fig, 420))
        st.caption("Meals bring in most of the money. Samosas sell many units but little revenue.")
    with right, st.container(border=True):
        st.subheader("Days each item sold out")
        sell = load("04_sellouts")
        fig = px.bar(sell, x="item_name", y="days_sold_out", color_discrete_sequence=[ORANGE],
                     hover_data=["usual_time", "pct_of_open_days"],
                     labels=dict(item_name="", days_sold_out="Days sold out"))
        st.plotly_chart(tidy(fig, 420))
        st.caption("Hover to see the usual sell-out time.")

    with st.container(border=True):
        st.subheader("Kitchen prep suggestions (last 28 open days)")
        prep = load("09_prep_recommendation").rename(columns={
            "item_name": "Item", "daily_prep": "Daily prep", "avg_units_sold": "Avg sold",
            "days_sold_out": "Days sold out", "pct_days_sold_out": "% days sold out",
            "avg_leftover_other_days": "Avg leftover", "suggestion": "Suggestion"})
        st.dataframe(prep[["Item", "Daily prep", "Avg sold", "Days sold out",
                           "% days sold out", "Avg leftover", "Suggestion"]], hide_index=True)
        st.caption("A rule of thumb, not a forecast: often sold out means prepare more, "
                   "big leftovers means prepare less.")

# ------------------------------------------------------------------ lost sales
with tab_lost:
    est = load("05_lost_sales_estimate")
    total = est[est["item_name"] == "TOTAL"].iloc[0]
    items = est[est["item_name"] != "TOTAL"].sort_values("est_lost_revenue", ascending=False)

    cols = st.columns(3)
    cols[0].metric("Estimated lost revenue", f"₹{int(total.est_lost_revenue):,}")
    cols[1].metric("Estimated lost items", f"{int(total.est_lost_units):,}")
    cols[2].metric("Share of the simulator's true loss",
                   f"{100 * total.est_lost_revenue / TRUE_LOST_REVENUE:.0f}%",
                   help=f"The simulator knows the true loss: ₹{TRUE_LOST_REVENUE:,}.")

    with st.container(border=True):
        st.subheader("Estimated lost revenue by item")
        fig = px.bar(items, x="item_name", y="est_lost_revenue", color_discrete_sequence=[ORANGE],
                     hover_data=["days_sold_out", "est_lost_units"],
                     labels=dict(item_name="", est_lost_revenue="Lost revenue (₹)"))
        st.plotly_chart(tidy(fig))

    with st.expander("How is this estimated?"):
        st.markdown(
            """
Nobody records the students who wanted something that had run out, so the loss has to be
**estimated**:

1. On days an item was still available, measure how many units of it each order contained, hour by hour.
2. On days it sold out, take the orders placed *after* the sell-out and apply the same rate.

The estimate is **conservative**: students who wanted only the sold-out item never placed an
order, so the data cannot see them. Because the data is simulated, the true loss is known
(₹23,415), so the method can be checked. It finds about three quarters of it and ranks the items correctly.
            """
        )

# ------------------------------------------------------------------ service
with tab_service:
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader("How long students wait")
        wait = load("06_wait_times")
        fig = go.Figure()
        fig.add_scatter(x=wait["hour_label"], y=wait["avg_wait_min"], name="Average",
                        mode="lines+markers", line=dict(color=TEAL, width=3))
        fig.add_scatter(x=wait["hour_label"], y=wait["p90_wait_min"], name="9 in 10 wait less than",
                        mode="lines+markers", line=dict(color=ORANGE, width=3, dash="dot"))
        fig.update_yaxes(title="Minutes from order to ready")
        st.plotly_chart(tidy(fig))
        st.caption("The lunch rush adds about 6 minutes to the average wait.")
    with right, st.container(border=True):
        st.subheader("Who causes the no-shows?")
        ns = load("08_repeat_no_shows")
        long = ns.melt(id_vars="group_name", value_vars=["pct_of_students", "pct_of_all_no_shows"],
                       var_name="measure", value_name="percent")
        long["measure"] = long["measure"].map({"pct_of_students": "% of students",
                                               "pct_of_all_no_shows": "% of all no-shows"})
        fig = px.bar(long, x="group_name", y="percent", color="measure", barmode="group",
                     color_discrete_sequence=[GREY, ORANGE],
                     labels=dict(group_name="", percent="Percent", measure=""))
        st.plotly_chart(tidy(fig))
        st.caption("A small group causes half of all no-shows, which supports blocking cash "
                   "orders after 3 no-shows.")

    with st.container(border=True):
        st.subheader("UPI versus cash")
        pay = load("07_payments_and_no_shows").rename(columns={
            "payment_method": "Method", "orders": "Orders", "pct_of_orders": "% of orders",
            "cancel_rate_pct": "Cancelled %", "no_show_rate_pct": "No-show %",
            "food_made_not_collected": "Food made, not collected (₹)",
            "money_never_received": "Money never received (₹)", "refunded": "Refunded (₹)"})
        st.dataframe(pay, hide_index=True)
        st.caption("Cash no-shows cost real money: the food was made and never paid for.")

# ------------------------------------------------------------------ footer
with st.expander("About this project"):
    st.markdown(
        """
**Canteen Cravs** is a pre-ordering system for a college canteen, rebuilt as an end-to-end data project:

`FastAPI app (SQLite)` → `Python pipeline (extract, clean, quality checks)` → `data lake on AWS S3 (raw, cleaned, curated)`
→ `star schema warehouse (DuckDB)` → `SQL analysis` → `this dashboard`.

The data comes from a simulator that models lunch rushes, sell-outs, cancellations and no-shows.
The patterns shown here were built into the simulation, so what this project demonstrates is the
**method**, not real canteen findings.
        """
    )

"""EV charging dashboard.

    streamlit run dashboard.py

Reads charging.db (written by logger.py) - safe to run while the logger is running.
"""
from __future__ import annotations

import base64
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from price_engine import DK_TZ, all_in_prices, cheapest_window, load_settings

HERE = Path(__file__).parent
DB_PATH = HERE / "charging.db"

# Fixed colour per location (colour follows the place, never its rank)
LOC_LABELS = {"home": "Home", "office": "Work", "supercharger": "Tesla Supercharger",
              "public_dc": "Other fast charger", "public_ac": "Public AC"}
LOC_COLORS = {"Home": "#2a78d6", "Work": "#eb6834", "Public AC": "#1baf7a",
              "Other fast charger": "#eda100", "Tesla Supercharger": "#e87ba4"}
BLUE, GREEN = "#2a78d6", "#1baf7a"

# ---------------------------------------------------------------- home screen icon (iPhone "Add to Home Screen")
ICON_B64 = "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"
ICON_FILE = HERE / "static" / "ev-icon-ring.png"
_icon = base64.b64decode(ICON_B64)
if not ICON_FILE.exists() or ICON_FILE.read_bytes() != _icon:  # served at /app/static/ (enableStaticServing)
    ICON_FILE.parent.mkdir(exist_ok=True)
    ICON_FILE.write_bytes(_icon)

st.set_page_config(page_title="EV charging", page_icon=str(ICON_FILE), layout="wide")
components.html("""<script>
  const d = window.parent.document;
  if (!d.querySelector('link[rel="apple-touch-icon"]')) {
    const add = (tag, attrs) => { const e = d.createElement(tag); Object.assign(e, attrs); d.head.appendChild(e); };
    add("link", {rel: "apple-touch-icon", href: "/app/static/ev-icon-ring.png"});
    add("meta", {name: "apple-mobile-web-app-title", content: "EV charging"});
    add("meta", {name: "apple-mobile-web-app-capable", content: "yes"});
  }
</script>""", height=0)
st.markdown("""
<style>
  .block-container {padding-top: 1.6rem; padding-bottom: 3rem;}
  [data-testid="stMetricValue"] {font-size: 1.65rem; font-weight: 600;}
  [data-testid="stMetricLabel"] p {font-size: .85rem; opacity: .75;}
  /* top row: two cards side by side even on phones */
  .st-key-toprow [data-testid="stHorizontalBlock"] {flex-wrap: nowrap !important; gap: .6rem;}
  .st-key-toprow [data-testid="stColumn"] {min-width: 0 !important; width: 50% !important; flex: 1 1 0 !important;}
  .st-key-toprow [data-testid="stHorizontalBlock"] {align-items: stretch;}
  .st-key-pricebox, .st-key-batterybox {min-height: 188px;}
  @media (max-width: 640px) {
    .st-key-pricebox, .st-key-batterybox {min-height: 184px;}
    .st-key-toprow [data-testid="stMetricValue"] {font-size: 1.35rem;}
    .st-key-toprow [data-testid="stMetricDelta"] {font-size: .75rem;}
  }
  [class*="st-key-kcard"] {min-height: 132px;}
  .st-key-planmetrics [data-testid="stHorizontalBlock"] {flex-wrap: nowrap !important; gap: .5rem;}
  .st-key-planmetrics [data-testid="stColumn"] {min-width: 0 !important; width: 33% !important; flex: 1 1 0 !important;}
  @media (max-width: 640px) {.st-key-planmetrics [data-testid="stMetricValue"] {font-size: 1.15rem;}}
  /* phone: live card in one row; summary + driving cards two per row */
  @media (max-width: 640px) {
    .st-key-livebox [data-testid="stHorizontalBlock"] {flex-wrap: nowrap !important; gap: .5rem;}
    .st-key-livebox [data-testid="stColumn"] {min-width: 0 !important; width: 33% !important; flex: 1 1 0 !important;}
    .st-key-livebox [data-testid="stMetricValue"] {font-size: 1.25rem;}
    .st-key-kpis [data-testid="stHorizontalBlock"], .st-key-drive [data-testid="stHorizontalBlock"]
      {flex-wrap: wrap !important; gap: .6rem;}
    .st-key-kpis [data-testid="stColumn"], .st-key-drive [data-testid="stColumn"]
      {flex: 1 1 calc(50% - .6rem) !important; min-width: calc(50% - .6rem) !important; width: calc(50% - .6rem) !important;}
    .st-key-kpis [data-testid="stMetricValue"], .st-key-drive [data-testid="stMetricValue"] {font-size: 1.3rem;}
    .st-key-kpis [data-testid="stMetricDelta"] {font-size: .72rem;}
  }
  .sub {opacity: .65; font-size: .9rem; margin-top: -.6rem; margin-bottom: .8rem;}
  .tag {display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .8rem;
        background: rgba(42,120,214,.12); color: inherit; margin-right: 6px;}
</style>
""", unsafe_allow_html=True)
import setup_page  # noqa: E402

if not setup_page.SETTINGS_FILE.exists():
    st.session_state["show_setup"] = True          # first run: stay in setup until the user leaves it
if st.session_state.get("show_setup"):
    setup_page.render(first_run=not setup_page.SETTINGS_FILE.exists())
    if setup_page.SETTINGS_FILE.exists() and st.button("Go to the dashboard →", use_container_width=True):
        st.session_state["show_setup"] = False
        st.rerun()
    st.stop()
settings = load_settings()
NOW = pd.Timestamp.now(tz=DK_TZ)


# ---------------------------------------------------------------- data
@st.cache_data(ttl=60)
def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not DB_PATH.exists():
        return pd.DataFrame(), pd.DataFrame()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    sessions = pd.read_sql("select * from sessions", con)
    slots = pd.read_sql("select sl.*, se.location from slots sl join sessions se on se.id = sl.session_id", con)
    con.close()
    for col in ("est_start_utc", "est_end_utc", "odo_start", "odo_end",
                "station", "operator", "rate_kr_per_kwh", "cost_source"):  # older databases may lack these
        if col not in sessions:
            sessions[col] = None
    for df, col in ((sessions, "start_utc"), (sessions, "end_utc"), (sessions, "est_start_utc"),
                    (sessions, "est_end_utc"), (slots, "slot_start_utc")):
        df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601").dt.tz_convert(DK_TZ)
    # show the estimated real charging times; fall back to when the logger noticed
    sessions["start"] = sessions["est_start_utc"].fillna(sessions["start_utc"])
    sessions["end"] = sessions["est_end_utc"].where(sessions["end_utc"].notna()).fillna(sessions["end_utc"])
    sessions["Location"] = sessions["location"].map(LOC_LABELS).fillna(sessions["location"])
    slots["Location"] = slots["location"].map(LOC_LABELS).fillna(slots["location"])
    slots["cost"] = slots["kwh_grid"] * slots["price_kr"]
    return sessions, slots


@st.cache_data(ttl=3600)
def day_prices(day: date) -> pd.DataFrame:
    return all_in_prices(day, settings)


def prices_or_none(day: date) -> pd.DataFrame | None:
    try:
        return day_prices(day)
    except Exception:
        return None


def flat_day_average(day: date) -> float | None:
    p = prices_or_none(day)
    return None if p is None else float(p["total"].mean())


# ---------------------------------------------------------------- live car reading (on demand only)
def refresh_car() -> None:
    """Run one logger check right now: read the car, record a reading if it is charging
    (kWh + cost), and save the battery level for everyone. Never wakes the car."""
    try:
        from logger import Prices, load_car_state, tick
        from tesla_client import Tesla
        before = load_car_state()
        tick(Tesla(), settings, Prices(settings))
        after = load_car_state()
        if not after or (before and after["time"] == before["time"]):
            st.session_state["car_msg"] = "The car is asleep – open the Tesla app to wake it, then tap Refresh again."
        else:
            st.session_state.pop("car_msg", None)
        load.clear()  # show the new reading in the charts and tables straight away
    except SystemExit as e:  # e.g. Tesla login expired
        st.session_state["car_msg"] = str(e)
    except Exception as e:
        st.session_state["car_msg"] = f"Couldn't reach the car: {e}"


sessions, slots = load()
from logger import load_car_state  # latest reading saved by Refresh or by the logger
car = load_car_state()
if car:
    car["time"] = pd.Timestamp(car["time"]).tz_convert(DK_TZ)
last_battery = None
if car:
    last_battery = car["battery"]
elif not sessions.empty and sessions["battery_end"].notna().any():
    last_battery = int(sessions.sort_values("start_utc")["battery_end"].dropna().iloc[-1])


def read_ago(t: pd.Timestamp) -> str:
    if t.date() == NOW.date():
        return f"{t:%H:%M}"
    if t.date() == NOW.date() - timedelta(days=1):
        return f"yesterday {t:%H:%M}"
    return f"{t:%d %b %H:%M}"

# ---------------------------------------------------------------- header
car_name = car["name"] if car else (sessions["car"].dropna().iloc[-1] if not sessions.empty else "My EV")
st.title("⚡ EV charging")
st.markdown(f'<div class="sub">{car_name} · {settings["supplier"]["name"]} · '
            f'{settings["area"]["price_area"]} · {NOW:%a %d %b, %H:%M}</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------- live: charging now
@st.fragment(run_every=60)
def charging_now() -> None:
    from logger import load_car_state
    if not DB_PATH.exists():
        return
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sess = con.execute("select * from sessions where end_utc is null order by start_utc desc limit 1").fetchone()
    con.close()
    if not sess:
        return
    live = load_car_state() or {}
    now = pd.Timestamp.now(tz=DK_TZ)
    start = pd.Timestamp(sess["est_start_utc"] if "est_start_utc" in sess.keys() and sess["est_start_utc"]
                         else sess["start_utc"]).tz_convert(DK_TZ)
    where = LOC_LABELS.get(sess["location"], sess["location"])
    with st.container(border=True, key="livebox"):
        st.markdown(f"**⚡ Charging now · {where}** <span style='opacity:.6'>since {start:%H:%M}</span>",
                    unsafe_allow_html=True)
        a1, a2, a3 = st.columns(3)
        a1.metric("Added", f"{sess['kwh_grid']:.1f} kWh")
        a2.metric("Cost so far", f"{sess['cost_kr']:.0f} kr")
        batt = live.get("battery")
        a3.metric("Battery", f"{batt} %" if batt is not None else "–")
        bits = []
        if live.get("power"):
            bits.append(f"{live['power']:.0f} kW")
        if live.get("limit"):
            bits.append(f"charging to {live['limit']} %")
        if live.get("minutes_to_full") and live.get("time"):
            t_full = pd.Timestamp(live["time"]).tz_convert(DK_TZ) + pd.Timedelta(minutes=live["minutes_to_full"])
            bits.append(f"full ≈ {t_full:%H:%M}")
        if live.get("time"):
            bits.append(f"updated {pd.Timestamp(live['time']).tz_convert(DK_TZ):%H:%M}")
        st.caption(" · ".join(bits))
        if st.button("🔄 Refresh now", key="live_refresh", use_container_width=True):
            with st.spinner("Reading the car…"):
                refresh_car()
            st.rerun(scope="app")
        if st.session_state.get("car_msg"):
            st.caption(st.session_state["car_msg"])


charging_now()

# ---------------------------------------------------------------- right now
today_p = prices_or_none(NOW.date())
tomorrow_p = prices_or_none(NOW.date() + timedelta(days=1))

with st.container(key="toprow"):
    r1, r2 = st.columns(2)
with r1, st.container(border=True, key="pricebox"):
    if today_p is not None:
        cur = today_p[today_p["time"] <= NOW]["total"].iloc[-1]
        avg = today_p["total"].mean()
        st.metric("Price right now", f"{cur:.2f} kr", f"{cur - avg:+.2f} vs avg", delta_color="inverse",
                  help="All-in price per kWh incl. VAT, compared with today's average")
        st.caption(f"Today's avg {avg:.2f} kr")
    else:
        st.metric("Price right now", "–")
with r2, st.container(border=True, key="batterybox"):
    if car:
        status = car["charging"] or ""
        st.metric(f"Battery · {car['where']}", f"{car['battery']} %")
        st.caption(f"{status} · {read_ago(car['time'])}")
    else:
        st.metric("Battery", f"{last_battery} %" if last_battery is not None else "–",
                  help="Level after your last logged charge – tap Refresh for a live reading")
    st.button("🔄 Refresh", on_click=refresh_car, use_container_width=True)
    if st.session_state.get("car_msg"):
        st.caption(st.session_state["car_msg"])

# ---------------------------------------------------------------- planner
with st.container(border=True):
    st.subheader("Plan the next charge")
    top = st.container()        # chart + result: drawn first on screen, filled after the settings below
    settings_box = st.container()
    with settings_box:
        st.divider()
        s1, s2 = st.columns(2)
        # tomorrow's prices are published ~13:00, so default to Tomorrow in the afternoon
        when = s1.radio("Day", ["Today", "Tomorrow"], horizontal=True, index=1 if NOW.hour >= 14 else 0)
        mode = s2.radio("Charge by", ["Battery %", "kWh"], horizontal=True)
        if mode == "kWh":
            kwh_needed = st.number_input("kWh to add", 1.0, 150.0, 40.0, step=5.0)
        else:
            capacity = float(settings["charging"].get("battery_kwh", 77))
            pct_now = st.slider("Battery now (%)", 0, 100, last_battery if last_battery is not None else 30,
                                help="From 'Refresh', or the level after your last logged charge")
            pct_target = st.slider("Charge to (%)", 0, 100, max(80, pct_now))
            kwh_needed = max(pct_target - pct_now, 0) / 100 * capacity
            st.caption(f"= {kwh_needed:.1f} kWh into a {capacity:.0f} kWh battery")
    day = NOW.date() + timedelta(days=1 if when == "Tomorrow" else 0)
    prices = today_p if when == "Today" else tomorrow_p
    with top:
        if prices is None:
            st.info(f"No prices for {day:%d %b} yet – tomorrow's arrive around 13:00.")
        else:
            future = prices if when == "Tomorrow" else prices[prices["time"] >= NOW.floor("15min")]
            best = None
            if kwh_needed > 0:
                try:
                    best = cheapest_window(future.reset_index(drop=True),
                                           kwh_needed / settings["charging"]["efficiency"],
                                           settings["charging"]["charger_kw"])
                except ValueError:
                    best = None
            p = prices.assign(t=prices["time"].dt.tz_localize(None))
            area = alt.Chart(p).mark_area(interpolate="step-after", opacity=0.10, color=BLUE).encode(
                x="t:T", y="total:Q")
            line = alt.Chart(p).mark_line(strokeWidth=2, interpolate="step-after", color=BLUE).encode(
                x=alt.X("t:T", title=None, axis=alt.Axis(format="%H:%M", tickCount=8)),
                y=alt.Y("total:Q", title="kr/kWh incl. VAT"),
                tooltip=[alt.Tooltip("t:T", title="Time", format="%H:%M"),
                         alt.Tooltip("total:Q", title="kr/kWh", format=".2f")])
            layers = [area, line]
            if best:
                layers.insert(0, alt.Chart(pd.DataFrame({"s": [best["start"].tz_localize(None)],
                                                         "e": [best["end"].tz_localize(None)]})
                                           ).mark_rect(opacity=0.18, color=GREEN).encode(x="s:T", x2="e:T"))
            if when == "Today":
                layers.append(alt.Chart(pd.DataFrame({"n": [NOW.tz_localize(None)]})).mark_rule(
                    strokeDash=[4, 4], color="#888").encode(x="n:T"))
            st.altair_chart(alt.layer(*layers).properties(height=230), use_container_width=True)
            if kwh_needed <= 0:
                st.info("Set a charge target above the current battery level.")
            elif not best:
                st.info(f"Not enough time left today for {kwh_needed:.0f} kWh – try Tomorrow.")
            else:
                worst = future["total"].rolling(max(1, round((best["end"] - best["start"]) / timedelta(minutes=15)))).mean().max()
                with st.container(key="planmetrics"):
                    m1, m2, m3 = st.columns(3)
                m1.metric("Cheapest", f"{best['start']:%H:%M}–{best['end']:%H:%M}",
                          help="Green band in the chart: cheapest continuous window for this charge")
                m2.metric("Cost", f"{best['cost_kr']:.0f} kr",
                          help=f"Including charging losses · {best['avg_kr_per_kwh']:.2f} kr/kWh on average")
                m3.metric("Saving", f"{worst * kwh_needed / settings['charging']['efficiency'] - best['cost_kr']:.0f} kr",
                          help="Compared with charging the same amount in the most expensive window")
                st.link_button(f"📱 Open Tesla app – schedule start {best['start']:%H:%M}", "tesla://",
                               use_container_width=True)
                st.caption(f"{when} · {kwh_needed:.1f} kWh · in the app: Charging → Schedule → start at "
                           f"{best['start']:%H:%M}" + (" · dashed line: now" if when == "Today" else ""))

if sessions.empty:
    st.info("No charging sessions yet – they appear here after the next charge.")
    st.stop()

months = sorted(sessions["start"].dt.strftime("%Y-%m").unique(), reverse=True)

# ---------------------------------------------------------------- charging summary (by location)
st.subheader("Charging summary")
period = st.selectbox("Period", ["All time"] + months, key="sum_period",
                      format_func=lambda m: m if m == "All time" else pd.Timestamp(m + "-01").strftime("%B %Y"))
src = sessions if period == "All time" else sessions[sessions["start"].dt.strftime("%Y-%m") == period]
if src.empty:
    st.caption("No charging in this period.")
else:
    src = src.assign(hours=((src["end"].fillna(pd.Timestamp.now(tz=DK_TZ)) - src["start"])
                            .dt.total_seconds() / 3600).clip(lower=0))
    g = (src.groupby("Location")
            .agg(Sessions=("id", "count"), kWh=("kwh_grid", "sum"), Cost=("cost_kr", "sum"), Hours=("hours", "sum"))
            .sort_values("kWh", ascending=False))
    total = g.sum().rename("Total")
    g = pd.concat([g, total.to_frame().T])
    g["Avg price"] = g["Cost"] / g["kWh"].where(g["kWh"] > 0)
    g["Avg power"] = g["kWh"] / g["Hours"].where(g["Hours"] > 0)
    g["Share"] = g["Cost"] / total["Cost"] * 100 if total["Cost"] else 0
    g = g.reset_index(names="Location")[["Location", "kWh", "Cost", "Avg price", "Sessions", "Avg power", "Hours", "Share"]]
    styled = (g.style
              .format({"kWh": "{:.1f}", "Cost": "{:.2f}", "Avg price": "{:.2f}", "Sessions": "{:.0f}",
                       "Avg power": "{:.1f}", "Hours": "{:.1f}", "Share": "{:.0f} %"}, na_rep="–")
              .apply(lambda r: ["font-weight: 700; background-color: rgba(42,120,214,.08)"] * len(r)
                     if r["Location"] == "Total" else [""] * len(r), axis=1))
    st.dataframe(styled, hide_index=True, use_container_width=True,
                 column_config={"kWh": st.column_config.Column("Energy (kWh)"),
                                "Cost": st.column_config.Column("Cost (kr)"),
                                "Avg price": st.column_config.Column("kr/kWh", help="Average price paid per kWh"),
                                "Avg power": st.column_config.Column("Avg kW", help="Average charging speed"),
                                "Hours": st.column_config.Column("Hours", help="Total time spent charging"),
                                "Share": st.column_config.Column("Share of cost")})

# ---------------------------------------------------------------- driving & petrol comparison
cmp_ = settings.get("compare", {})
fuel = cmp_.get("fuel", "petrol")
l_per_100 = cmp_.get("fuel_l_per_100km", cmp_.get("petrol_l_per_100km", 6.5))
if cmp_.get("fuel_km_per_litre"):  # e.g. 15 km/l -> 6.67 l/100 km
    l_per_100 = round(100 / cmp_["fuel_km_per_litre"], 2)
kr_per_l = cmp_.get("fuel_kr_per_litre", cmp_.get("petrol_kr_per_litre", 13.5))
odo = sessions.dropna(subset=["odo_end"]).sort_values("start") if "odo_end" in sessions else pd.DataFrame()
km = None
if not odo.empty:
    tracked_from = odo["start"].min()          # charges before this have no odometer reading
    if period == "All time":
        odo_p, base = odo, odo["odo_start"].min()
    else:
        odo_p = odo[odo["start"].dt.strftime("%Y-%m") == period]
        before = odo[odo["start"] < pd.Timestamp(period + "-01", tz=DK_TZ)]
        base = before["odo_end"].iloc[-1] if not before.empty else (odo_p["odo_start"].min() if not odo_p.empty else None)
    if not odo_p.empty and base is not None:
        km = odo_p["odo_end"].max() - base
        drv = sessions[sessions["start"] >= tracked_from]
        if period != "All time":
            drv = drv[drv["start"].dt.strftime("%Y-%m") == period]
with st.container(border=True, key="drive"):
    st.markdown(f"**🚗 Driving** · {period if period == 'All time' else pd.Timestamp(period + '-01').strftime('%B %Y')}")
    if km is None or km < 30:
        st.caption("Appears after a few charges with the new logger – it saves the odometer at each charge.")
    else:
        all_kwh, all_cost = drv["kwh_grid"].sum(), drv["cost_kr"].sum()
        petrol = km / 100 * l_per_100 * kr_per_l
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Driven", f"{km:,.0f} km".replace(",", "."))
        d2.metric("Energy use", f"{all_kwh / km * 100:.1f} kWh/100 km", help="Charged kWh (incl. losses) per km driven")
        d3.metric("Cost per 100 km", f"{all_cost / km * 100:.0f} kr")
        d4.metric(f"{fuel.capitalize()} car would cost", f"{petrol:.0f} kr", f"you saved {petrol - all_cost:.0f} kr",
                  delta_color="normal")
        st.caption(f"Follows the Period above · since odometer tracking began {tracked_from:%d %b %Y}. "
                   f"{fuel.capitalize()} car: {100 / l_per_100:.0f} km/l at {kr_per_l:.2f} kr/l.")

# ---------------------------------------------------------------- where the money goes (own period filter)
with st.container(border=True, key="moneybox"):
    st.markdown("**💰 Where the money goes**")
    this_month = NOW.strftime("%Y-%m")
    wm_opts = ["All time"] + sorted(set(months) | {this_month}, reverse=True)
    wm_period = st.selectbox("Period", wm_opts, index=wm_opts.index(this_month), key="wm_period",
                             format_func=lambda m: m if m == "All time" else pd.Timestamp(m + "-01").strftime("%B %Y"))
    wm = sessions if wm_period == "All time" else sessions[sessions["start"].dt.strftime("%Y-%m") == wm_period]
    by_loc = wm.groupby("Location", as_index=False)[["kwh_grid", "cost_kr"]].sum()
    if by_loc.empty or by_loc["cost_kr"].sum() == 0:
        st.caption("No paid charging in this period.")
    else:
        by_loc["price"] = by_loc["cost_kr"] / by_loc["kwh_grid"].where(by_loc["kwh_grid"] > 0)
        by_loc["label"] = by_loc["cost_kr"].map(lambda v: f"{v:,.0f} kr".replace(",", "."))
        base = alt.Chart(by_loc).encode(
            y=alt.Y("Location:N", title=None, axis=alt.Axis(labelLimit=0, labelPadding=6),
                    sort=by_loc.sort_values("cost_kr", ascending=False)["Location"].tolist()),  # biggest first
            x=alt.X("cost_kr:Q", title=None, axis=None,
                    scale=alt.Scale(domain=[0, by_loc["cost_kr"].max() * 1.3])),  # room for the amounts
            tooltip=["Location:N", alt.Tooltip("kwh_grid:Q", title="kWh", format=".1f"),
                     alt.Tooltip("cost_kr:Q", title="kr", format=".2f"),
                     alt.Tooltip("price:Q", title="kr/kWh", format=".2f")])
        bars = base.mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
            color=alt.Color("Location:N", legend=None,
                            scale=alt.Scale(domain=list(LOC_COLORS), range=list(LOC_COLORS.values()))))
        labels = base.mark_text(align="left", dx=6, fontSize=13, color="#555").encode(text="label:N")
        st.altair_chart((bars + labels).properties(height=alt.Step(34)), use_container_width=True)
        st.caption(f"Total {by_loc['cost_kr'].sum():,.0f} kr · {by_loc['kwh_grid'].sum():,.0f} kWh"
                   .replace(",", "."))

# ---------------------------------------------------------------- month summary
st.subheader("Your charging")
f1, f2 = st.columns([1, 3])
with f1:
    month = st.selectbox("Month", months, format_func=lambda m: pd.Timestamp(m + "-01").strftime("%B %Y"))
with f2:
    locs = st.multiselect("Locations", list(LOC_COLORS), default=list(LOC_COLORS))

in_locs = sessions[sessions["Location"].isin(locs)]
ms = in_locs[in_locs["start"].dt.strftime("%Y-%m") == month]
prev_month = (pd.Timestamp(month + "-01") - pd.DateOffset(months=1)).strftime("%Y-%m")
pm = in_locs[in_locs["start"].dt.strftime("%Y-%m") == prev_month]
msl = slots[slots["session_id"].isin(ms["id"])]

kwh, cost = ms["kwh_grid"].sum(), ms["cost_kr"].sum()
pkwh, pcost = pm["kwh_grid"].sum(), pm["cost_kr"].sum()
home = msl[msl["location"] == "home"].copy()
saved = None
if not home.empty:
    home["day"] = home["slot_start_utc"].dt.date
    home["flat"] = home["day"].map({d: flat_day_average(d) for d in home["day"].unique()})
    if home["flat"].notna().all():
        saved = (home["kwh_grid"] * home["flat"]).sum() - home["cost"].sum()


def delta(now_v, prev_v, fmt):
    return None if pm.empty else fmt.format(now_v - prev_v) + " vs last month"


def forecast_kpi():
    """Current month: projected month total. Past months: number of sessions."""
    if month != NOW.strftime("%Y-%m") or cost == 0:
        return ("Sessions", f"{len(ms)}", None, "off")
    days_in_month = (pd.Timestamp(month + "-01") + pd.offsets.MonthEnd(0)).day
    elapsed = (NOW - pd.Timestamp(month + "-01", tz=DK_TZ)).total_seconds() / 86400
    return ("Month forecast", f"≈ {cost / max(elapsed, 1) * days_in_month:.0f} kr", None, "off")


with st.container(key="kpis"):
    k = st.columns(5)
for i, (col, args) in enumerate(zip(k, [
    ("Energy", f"{kwh:.0f} kWh", delta(kwh, pkwh, "{:+.0f} kWh"), "off"),
    ("Cost", f"{cost:.0f} kr", delta(cost, pcost, "{:+.0f} kr"), "inverse"),
    ("Average price", f"{cost / kwh:.2f} kr/kWh" if kwh else "–",
     delta(cost / kwh if kwh else 0, pcost / pkwh if pkwh else 0, "{:+.2f}") if kwh and pkwh else None, "inverse"),
    ("Saved by timing", f"{saved:.0f} kr" if saved is not None else "–", None, "normal"),
    forecast_kpi(),
]), ):
    with col, st.container(border=True, key=f"kcard{i}"):
        helps = {"Saved by timing": "Home charging vs paying each day's average price",
                 "Month forecast": f"Projected from {len(ms)} sessions so far this month"}
        st.metric(args[0], args[1], args[2], delta_color=args[3], help=helps.get(args[0]))

# ---------------------------------------------------------------- charts
bar = dict(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)


def loc_color(df: pd.DataFrame, legend: bool = False) -> alt.Color:
    """Fixed colour per location. The key is drawn once above the charts (shared), not inside each chart."""
    return alt.Color("Location:N", legend=None,
                     scale=alt.Scale(domain=list(LOC_COLORS), range=list(LOC_COLORS.values())))


_present = [l for l in LOC_COLORS if l in set(in_locs["Location"])]
st.markdown(
    "<div style='display:flex;flex-wrap:wrap;gap:.35rem 1rem;margin:.2rem 0 .6rem;font-size:.85rem'>" +
    "".join(f"<span style='white-space:nowrap'><span style='display:inline-block;width:.7rem;height:.7rem;"
            f"border-radius:2px;background:{LOC_COLORS[l]};margin-right:.35rem;vertical-align:-1px'></span>{l}</span>"
            for l in _present) + "</div>", unsafe_allow_html=True)


daily = (ms.assign(day=ms["start"].dt.tz_localize(None).dt.normalize())
           .groupby(["day", "Location"], as_index=False)[["kwh_grid", "cost_kr"]].sum().sort_values("day"))
daily["label"] = daily["day"].dt.strftime("%d %b")
monthly = (in_locs.assign(month=in_locs["start"].dt.strftime("%Y-%m"))
           .groupby(["month", "Location"], as_index=False)[["kwh_grid", "cost_kr"]].sum())

c1, c2 = st.columns(2)
with c1, st.container(border=True):
    st.markdown("**Cost per day**")
    st.altair_chart(alt.Chart(daily).mark_bar(**bar, stroke="white", strokeWidth=1).encode(
        x=alt.X("label:O", title=None, sort=None, axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("cost_kr:Q", title="kr"), color=loc_color(daily),
        tooltip=[alt.Tooltip("day:T", title="Day", format="%a %d %b"), "Location:N",
                 alt.Tooltip("kwh_grid:Q", title="kWh", format=".1f"),
                 alt.Tooltip("cost_kr:Q", title="kr", format=".2f")],
    ).properties(height=260), use_container_width=True)

with c2, st.container(border=True):
    st.markdown("**Cost per month**")
    st.altair_chart(alt.Chart(monthly).mark_bar(**bar, stroke="white", strokeWidth=1).encode(
        x=alt.X("month:O", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("cost_kr:Q", title="kr"), color=loc_color(monthly),
        tooltip=["month:O", "Location:N", alt.Tooltip("kwh_grid:Q", title="kWh", format=".1f"),
                 alt.Tooltip("cost_kr:Q", title="kr", format=".2f")],
    ).properties(height=260), use_container_width=True)

# ---------------------------------------------------------------- public charging price (editable)
PUBLIC_KEYS = ("supercharger", "public_dc", "public_ac")
pub = sessions[sessions["location"].isin(PUBLIC_KEYS) & sessions["end_utc"].notna()].sort_values("start", ascending=False)
if not pub.empty:
    import public_prices as pp
    last = pub.iloc[0]

    def _txt(v):  # empty database values arrive as NaN, which would otherwise count as "filled in"
        return v if isinstance(v, str) and v.strip() else None
    last_station, last_operator, last_source = _txt(last["station"]), _txt(last["operator"]), _txt(last["cost_source"])
    with st.container(border=True, key="pricefix"):
        st.markdown("**💳 Last public charge**")
        where = last_station or LOC_LABELS.get(last["location"], last["location"])
        op = f" · {last_operator}" if last_operator and last_operator not in where else ""
        rate_now = last["rate_kr_per_kwh"] if pd.notna(last["rate_kr_per_kwh"]) else (
            last["cost_kr"] / last["kwh_grid"] if last["kwh_grid"] else 0)
        src = last_source or "estimate"
        st.caption(f"{where}{op} · {last['start']:%a %d %b %H:%M} · {last['kwh_grid']:.1f} kWh · "
                   f"{last['cost_kr']:.0f} kr · price from: {src}")
        if src == "Tesla bill":
            st.success(f"Exact amount from Tesla: {last['cost_kr']:.2f} kr ({rate_now:.2f} kr/kWh)")
        else:
            e1, e2 = st.columns(2)
            new_rate = e1.number_input("Price (kr/kWh)", min_value=0.0, max_value=20.0, step=0.05,
                                       value=float(round(rate_now, 2)), key=f"rate_{last['id']}")
            new_total = e2.number_input("…or total from receipt (kr)", min_value=0.0, step=1.0, value=0.0,
                                        key=f"total_{last['id']}", help="Leave at 0 to use the price per kWh")
            if st.button("💾 Save price", key=f"save_{last['id']}", use_container_width=True):
                used = pp.set_session_price(int(last["id"]), rate=None if new_total > 0 else new_rate,
                                            total=new_total if new_total > 0 else None)
                load.clear()
                name = last_station or last_operator
                st.session_state["price_msg"] = (f"Saved {used:.2f} kr/kWh." + (
                    f" Next charge at {name} uses this price automatically." if name else ""))
                st.rerun()
        if st.session_state.get("price_msg"):
            st.success(st.session_state.pop("price_msg"))

    with st.expander("✏️ Correct earlier public charges"):
        ed = pub.head(20)[["id", "start", "Location", "station", "kwh_grid", "rate_kr_per_kwh", "cost_kr", "cost_source"]].copy()
        ed["rate_kr_per_kwh"] = ed["rate_kr_per_kwh"].fillna(ed["cost_kr"] / ed["kwh_grid"].where(ed["kwh_grid"] > 0))
        ed["start"] = ed["start"].dt.strftime("%d %b %H:%M")
        edited = st.data_editor(
            ed, hide_index=True, use_container_width=True, key="pub_editor",
            disabled=["id", "start", "Location", "station", "kwh_grid", "cost_kr", "cost_source"],
            column_config={"id": None, "start": "Start", "station": "Station", "kwh_grid": st.column_config.NumberColumn("kWh", format="%.1f"),
                           "rate_kr_per_kwh": st.column_config.NumberColumn("kr/kWh ✏️", format="%.2f", min_value=0.0, step=0.05),
                           "cost_kr": st.column_config.NumberColumn("Cost (kr)", format="%.2f"), "cost_source": "Price from"})
        if st.button("💾 Save changes", key="pub_save"):
            changed = 0
            for (_, old), (_, new) in zip(ed.iterrows(), edited.iterrows()):
                if pd.notna(new["rate_kr_per_kwh"]) and abs((old["rate_kr_per_kwh"] or 0) - new["rate_kr_per_kwh"]) > 0.001:
                    pp.set_session_price(int(old["id"]), rate=float(new["rate_kr_per_kwh"]))
                    changed += 1
            load.clear()
            st.session_state["price_msg"] = f"Updated {changed} charge(s)."
            st.rerun()

# ---------------------------------------------------------------- sessions table
st.subheader("Sessions")
table = ms.sort_values("start", ascending=False).assign(
    Start=lambda d: d["start"].dt.strftime("%a %d %b %H:%M"),
    End=lambda d: d["end"].dt.strftime("%H:%M").fillna("charging…"),
    Battery=lambda d: [f"{int(a)}% → {int(b)}%" if pd.notna(a) and pd.notna(b) else ""
                       for a, b in zip(d["battery_start"], d["battery_end"])],
    kWh=lambda d: d["kwh_grid"].round(1),
    Cost=lambda d: d["cost_kr"].round(2),
    Price=lambda d: (d["cost_kr"] / d["kwh_grid"]).round(2),
    Station=lambda d: d["station"].fillna(""),
    Source=lambda d: d["cost_source"].fillna(""),
)[["Start", "End", "Location", "Station", "kWh", "Cost", "Price", "Source", "Battery"]]
st.dataframe(table, hide_index=True, use_container_width=True,
             column_config={"Cost": st.column_config.NumberColumn("Cost (kr)", format="%.2f"),
                            "Price": st.column_config.NumberColumn("kr/kWh", format="%.2f"),
                            "Source": st.column_config.Column("Price from")})
st.download_button("Download CSV", table.to_csv(index=False).encode(), f"charging-{month}.csv")

with st.expander("📍 Home & Work locations"):
    from logger import load_car_state as _lcs, locations as _locs, set_location
    locs_now = _locs(settings)
    for key, label in (("home", "Home"), ("office", "Work")):
        loc = locs_now.get(key, {})
        st.markdown(f"**{label}:** " + (f"{loc['lat']:.5f}, {loc['lon']:.5f} (within {loc.get('radius_m', 200)} m)"
                                        if loc.get("lat") else "not set yet"))
    st.caption("Park at the place, then tap the matching button. It reads the car's position right now.")

    def save_here(key: str, label: str) -> None:
        refresh_car()
        st_ = _lcs()
        fresh = st_ and st_.get("lat") and (pd.Timestamp.now(tz=DK_TZ) - pd.Timestamp(st_["time"])).total_seconds() < 180
        if fresh:
            set_location(key, st_["lat"], st_["lon"])
            st.session_state["loc_msg"] = f"Saved: {label} = {st_['lat']:.5f}, {st_['lon']:.5f}. New charges there use the {label} price."
        else:
            st.session_state["loc_msg"] = "Couldn't read the car's position – open the Tesla app to wake it, then try again."

    b1, b2 = st.columns(2)
    b1.button("📍 Car is here now – save as Work", on_click=save_here, args=("office", "Work"), use_container_width=True)
    b2.button("🏠 Car is here now – save as Home", on_click=save_here, args=("home", "Home"), use_container_width=True)
    if st.session_state.get("loc_msg"):
        st.info(st.session_state.pop("loc_msg"))

with st.expander("🔑 Tesla login"):
    st.markdown("Log in to Tesla again here – for example to add the **charging permission** "
                "(needed for exact Supercharger bills), or if the logger says the login expired.")
    setup_page.tesla_login_ui()
    st.divider()
    st.markdown("**Supercharger history** – adds past Supercharger visits with the exact amount Tesla billed. "
                "This also runs by itself every 6 hours.")
    if st.button("⬇️ Import Supercharger history", use_container_width=True):
        with st.spinner("Fetching from Tesla…"):
            try:
                import public_prices as pp
                from tesla_client import Tesla
                r = pp.sync_supercharger_costs(settings, Tesla(), force=True)
                st.session_state["imp_msg"] = (f"Imported {r['imported']} visit(s), updated {r['matched']} "
                                               "charge(s) with Tesla's bill.")
            except SystemExit as e:
                st.session_state["imp_msg"] = str(e)
            except Exception as e:
                st.session_state["imp_msg"] = f"Import failed: {e}"
        load.clear()
        st.rerun()
    if st.session_state.get("imp_msg"):
        st.info(st.session_state.pop("imp_msg"))

with st.expander("⚙️ Station lookup (Open Charge Map)"):
    import public_prices as pp
    has_key = bool(pp.ocm_key(settings))
    st.markdown("Recognises **which station and company** you charged at, so each public charge can use that "
                "company's price. Needs a free key: sign up at **openchargemap.org** → My Profile → "
                "*My Apps* → *Register an application*, then paste the key here.")
    st.caption("Key saved ✅" if has_key else "No key yet")
    k1, k2 = st.columns([3, 1])
    new_key = k1.text_input("API key", type="password", label_visibility="collapsed", placeholder="Paste API key")
    if k2.button("Save key", use_container_width=True) and new_key.strip():
        pp.save_ocm_key(new_key)
        st.session_state["ocm_msg"] = "Key saved."
        st.rerun()
    if has_key and st.button("🔎 Look up stations for earlier public charges"):
        with st.spinner("Looking up…"):
            try:
                n = pp.backfill_stations(settings)
                st.session_state["ocm_msg"] = f"Found the station for {n} charge(s)."
            except Exception as e:
                st.session_state["ocm_msg"] = f"Lookup failed: {e}"
        load.clear()
        st.rerun()
    if st.session_state.get("ocm_msg"):
        st.info(st.session_state.pop("ocm_msg"))

with st.expander("🔔 Phone notifications"):
    from logger import get_topic
    topic = get_topic(settings)
    st.markdown(f"""You get a push message when **charging finishes**, when the car starts charging at home at an
**expensive time**, and every afternoon with **tonight's cheapest 3 hours**.

1. Install the free **ntfy** app (App Store / Google Play).
2. Tap **+**, and subscribe to the topic **`{topic}`** (server: ntfy.sh).

Keep the topic name private – anyone who knows it can read these messages.""")
    st.link_button("Open topic in browser", f"https://ntfy.sh/{topic}")

st.divider()
if st.button("⚙️ Settings", use_container_width=True):
    st.session_state["show_setup"] = True
    st.rerun()

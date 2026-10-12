"""Charge session logger: watches the car and records every charge with its cost.

    python logger.py          # run continuously (leave the terminal open)
    python logger.py once     # one check, then exit (for testing)
    python logger.py report   # list recorded sessions

Data goes to charging.db (SQLite) next to this file.
"""
from __future__ import annotations

import math
import json
import secrets as pysecrets
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from price_engine import DK_TZ, all_in_prices, cheapest_window, load_settings
from tesla_client import ChargeSnapshot, Tesla
import public_prices as pp

HERE = Path(__file__).parent
DB_PATH = HERE / "charging.db"
SLOT = timedelta(minutes=15)

SCHEMA = """
create table if not exists sessions(
    id integer primary key,
    vin text, car text,
    start_utc text, end_utc text,
    location text, lat real, lon real, fast_charger integer,
    battery_start integer, battery_end integer,
    kwh_battery real default 0, kwh_grid real default 0, cost_kr real default 0,
    joined_late integer default 0
);
create table if not exists readings(
    session_id integer, time_utc text, energy_added real, power_kw real, battery integer
);
create table if not exists slots(
    session_id integer, slot_start_utc text, kwh_grid real, price_kr real,
    primary key (session_id, slot_start_utc)
);
"""


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # upgrade older databases: estimated real start/end of charging
    cols = {r["name"] for r in con.execute("pragma table_info(sessions)")}
    for col, typ in (("est_start_utc", "text"), ("est_end_utc", "text"), ("odo_start", "real"), ("odo_end", "real")):
        if col not in cols:
            con.execute(f"alter table sessions add column {col} {typ}")
    con.execute("update sessions set est_start_utc = (select min(slot_start_utc) from slots"
                " where session_id = sessions.id) where est_start_utc is null")
    pp.ensure_columns(con)  # station, operator, rate, price source
    return con


def log(msg: str) -> None:
    print(f"{datetime.now(DK_TZ):%Y-%m-%d %H:%M}  {msg}", flush=True)


# ---------------------------------------------------------------- location
def distance_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


LOCATIONS_FILE = HERE / "locations.json"  # set from the dashboard ("car is here now")


def locations(s: dict) -> dict:
    """Locations from settings.toml, with positions saved from the dashboard taking priority."""
    locs = {k: dict(v) for k, v in s.get("locations", {}).items()}
    try:
        for name, pos in json.loads(LOCATIONS_FILE.read_text()).items():
            locs.setdefault(name, {"radius_m": 150 if name == "home" else 300,
                                   "price": "dynamic" if name == "home" else None, "price_kr_per_kwh": 0.0})
            locs[name].update(pos)
    except Exception:
        pass
    return locs


def set_location(name: str, lat: float, lon: float) -> None:
    try:
        data = json.loads(LOCATIONS_FILE.read_text())
    except Exception:
        data = {}
    data[name] = {"lat": round(lat, 6), "lon": round(lon, 6)}
    LOCATIONS_FILE.write_text(json.dumps(data, indent=2))


def is_supercharger(snap: ChargeSnapshot) -> bool:
    brand = (getattr(snap, "fast_charger_brand", None) or "").lower()
    kind = (getattr(snap, "fast_charger_type", None) or "").lower()
    return brand == "tesla" or "supercharger" in kind or kind == "tesla"


def classify(snap: ChargeSnapshot, s: dict) -> str:
    if snap.lat is not None:
        for name, loc in locations(s).items():
            if loc.get("lat") and distance_m(snap.lat, snap.lon, loc["lat"], loc["lon"]) <= loc.get("radius_m", 200):
                return name
    if snap.fast_charger:
        return "supercharger" if is_supercharger(snap) else "public_dc"
    return "public_ac"


# ---------------------------------------------------------------- latest car reading (shared with dashboard)
CAR_STATE = HERE / "car_state.json"
LOC_NAMES = {"home": "Home", "office": "Work", "supercharger": "Tesla Supercharger",
             "public_dc": "Other fast charger", "public_ac": "Public AC"}


def save_car_state(name: str, snap: ChargeSnapshot, s: dict) -> None:
    """Remember the latest battery reading so the dashboard can show it after a reload."""
    import json
    if snap.battery_level is None:
        return
    state = {"name": name, "battery": snap.battery_level, "charging": snap.charging_state,
             "power": snap.charger_power_kw, "where": LOC_NAMES.get(classify(snap, s), "Elsewhere"),
             "limit": getattr(snap, "charge_limit", None), "minutes_to_full": getattr(snap, "minutes_to_full", None),
             "odometer_km": getattr(snap, "odometer_km", None), "lat": snap.lat, "lon": snap.lon,
             "time": snap.time.isoformat()}
    tmp = CAR_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(CAR_STATE)  # atomic: the dashboard never reads a half-written file


def load_car_state() -> dict | None:
    import json
    try:
        state = json.loads(CAR_STATE.read_text())
        state["time"] = datetime.fromisoformat(state["time"])
        return state
    except Exception:
        return None


# ---------------------------------------------------------------- phone notifications (ntfy.sh)
TOPIC_FILE = HERE / "ntfy_topic.txt"
NOTIFY_STATE = HERE / "notify_state.json"


def get_topic(s: dict | None = None) -> str:
    """Secret ntfy topic: from settings [notify] ntfy_topic, else a random one created once."""
    t = ((s or {}).get("notify") or {}).get("ntfy_topic")
    if t:
        return t
    if not TOPIC_FILE.exists():
        TOPIC_FILE.write_text("ev-" + "".join(c for c in pysecrets.token_urlsafe(16).lower() if c.isalnum())[:14])
    return TOPIC_FILE.read_text().strip()


def notify_on(s: dict, what: str) -> bool:
    return bool(((s or {}).get("notify") or {}).get(what, True))


def notify(s: dict, title: str, message: str, tags: str = "zap") -> None:
    try:
        requests.post(f"https://ntfy.sh/{get_topic(s)}", data=message.encode("utf-8"), timeout=15,
                      headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": tags})
        log(f"notified: {title}")
    except Exception as e:
        log(f"notification failed: {e}")


def upcoming_prices(s: dict):
    """All-in prices from now until the end of tomorrow (tomorrow only once published)."""
    import pandas as pd
    now = datetime.now(DK_TZ)
    frames = []
    for d in (now.date(), now.date() + timedelta(days=1)):
        try:
            frames.append(all_in_prices(d, s))
        except Exception:
            pass
    if not frames:
        return None
    df = pd.concat(frames)
    floor = pd.Timestamp(now).floor("15min")
    return df[df["time"] >= floor].reset_index(drop=True)


def best_window(s: dict, hours: float = 3):
    p = upcoming_prices(s)
    if p is None or len(p) < hours * 4:
        return None, None
    kw = s["charging"]["charger_kw"]
    return cheapest_window(p, kw * hours, kw), p


def daily_tip(s: dict) -> None:
    """Once a day after tomorrow's prices are out (~13:00): tonight's cheapest 3 hours."""
    now = datetime.now(DK_TZ)
    if now.hour < 14 or not notify_on(s, "daily_tip"):
        return
    try:
        state = json.loads(NOTIFY_STATE.read_text())
    except Exception:
        state = {}
    if state.get("tip_date") == now.date().isoformat():
        return
    w, p = best_window(s)
    if w is None or p["time"].iloc[-1].date() == now.date():  # tomorrow not published yet
        return
    day = "today" if w["start"].date() == now.date() else "tomorrow" if w["start"].date() == now.date() + timedelta(days=1) else ""
    notify(s, "Cheapest charging time",
           f"Best 3 hours: {w['start']:%H:%M}–{w['end']:%H:%M} {day} at {w['avg_kr_per_kwh']:.2f} kr/kWh "
           f"(average until then: {p['total'].mean():.2f}).", "bulb")
    state["tip_date"] = now.date().isoformat()
    NOTIFY_STATE.write_text(json.dumps(state))


# ---------------------------------------------------------------- pricing
class Prices:
    """All-in home prices per 15-min slot, cached per day."""

    def __init__(self, s: dict):
        self.s, self.cache = s, {}

    def home(self, slot_utc: datetime) -> float:
        day = slot_utc.astimezone(DK_TZ).date()
        if day not in self.cache:
            df = all_in_prices(day, self.s)
            self.cache[day] = {t.astimezone(timezone.utc): p for t, p in zip(df["time"], df["total"])}
        return self.cache[day][slot_utc]

    def price(self, location: str, slot_utc: datetime, rate: float | None = None) -> float:
        if rate is not None and location in pp.PUBLIC:
            return rate  # this session's own price (your price / operator list / Tesla bill)
        loc = locations(self.s).get(location)
        if loc is not None:
            return self.home(slot_utc) if loc.get("price") == "dynamic" else loc.get("price_kr_per_kwh", 0.0)
        pub = self.s.get("public", {})
        if location == "supercharger":
            return pub.get("supercharger_kr_per_kwh", 3.20)
        return pub.get("dc_kr_per_kwh", 3.95) if location == "public_dc" else pub.get("ac_kr_per_kwh", 3.50)


def floor_slot(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)


def spread(t0: datetime, t1: datetime, kwh: float) -> list[tuple[datetime, float]]:
    """Split kwh evenly over [t0, t1] into 15-min slots."""
    if kwh <= 0:
        return []
    if t1 <= t0:
        return [(floor_slot(t1), kwh)]
    total = (t1 - t0).total_seconds()
    out, cur = [], floor_slot(t0)
    while cur < t1:
        overlap = (min(cur + SLOT, t1) - max(cur, t0)).total_seconds()
        if overlap > 0:
            out.append((cur, kwh * overlap / total))
        cur += SLOT
    return out


# ---------------------------------------------------------------- sessions
def open_session(con, vin):
    return con.execute("select * from sessions where vin=? and end_utc is null", (vin,)).fetchone()


def last_reading(con, sid):
    return con.execute("select * from readings where session_id=? order by time_utc desc limit 1",
                       (sid,)).fetchone()


def start_session(con, vin, car, snap, s) -> int:
    location = classify(snap, s)
    station = operator = rate = None
    source = "hourly price" if locations(s).get(location, {}).get("price") == "dynamic" else "fixed price"
    if location in pp.PUBLIC:
        try:
            station, operator = pp.lookup_station(snap.lat, snap.lon, s)
        except Exception as e:
            log(f"station lookup failed: {e}")
        if location == "supercharger":
            operator = operator or "Tesla"
        rate, source = pp.choose_rate(location, station, operator, s)
    cur = con.execute(
        "insert into sessions(vin, car, start_utc, location, lat, lon, fast_charger, battery_start, joined_late,"
        " odo_start, odo_end, station, operator, rate_kr_per_kwh, cost_source)"
        " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (vin, car, snap.time.isoformat(), location, snap.lat, snap.lon,
         int(bool(snap.fast_charger)), snap.battery_level, int((snap.energy_added_kwh or 0) > 0.5),
         getattr(snap, "odometer_km", None), getattr(snap, "odometer_km", None),
         station, operator, rate, source),
    )
    where = f" - {station}" if station else ""
    log(f"{car}: charging started at {location}{where} ({snap.battery_level}%)"
        + (f", {rate:.2f} kr/kWh ({source})" if rate is not None else ""))
    if locations(s).get(location, {}).get("price") == "dynamic" and notify_on(s, "peak_alert"):
        try:
            w, p = best_window(s)
            margin = (s.get("notify") or {}).get("peak_margin_kr", 0.5)
            if w is not None and p["total"].iloc[0] - w["avg_kr_per_kwh"] > margin and w["start"] > p["time"].iloc[0]:
                notify(s, "Charging at an expensive time",
                       f"Now {p['total'].iloc[0]:.2f} kr/kWh. Cheapest window: {w['start']:%H:%M}–{w['end']:%H:%M} "
                       f"at {w['avg_kr_per_kwh']:.2f} kr/kWh. Open the dashboard or Tesla app to schedule.", "warning")
        except Exception as e:
            log(f"peak check failed: {e}")
    return cur.lastrowid


def add_reading(con, sess, snap: ChargeSnapshot, s, prices: Prices, charging: bool) -> None:
    sid, eff = sess["id"], s["charging"]["efficiency"]
    prev = last_reading(con, sid)
    e1 = snap.energy_added_kwh or 0.0
    if prev is None:
        # first reading: energy already added before we noticed -> estimate when it started
        p = max(snap.charger_power_kw or 0, 1.0)
        t0 = snap.time - timedelta(hours=min(e1 / p, 24))
        e0, t_end = 0.0, snap.time
    else:
        t0, e0 = datetime.fromisoformat(prev["time_utc"]), prev["energy_added"]
        t_end = snap.time
        if not charging:  # charge finished somewhere in between: assume it ran at last known power
            p = max(prev["power_kw"] or 0, 1.0)
            t_end = min(snap.time, t0 + timedelta(hours=max(e1 - e0, 0) / p))
    if prev is None:
        con.execute("update sessions set est_start_utc=? where id=?", (t0.isoformat(), sid))
    if not charging:
        con.execute("update sessions set est_end_utc=? where id=?", (t_end.isoformat(), sid))
    con.execute("insert into readings values (?,?,?,?,?)",
                (sid, snap.time.isoformat(), e1, snap.charger_power_kw, snap.battery_level))
    for slot, kwh_batt in spread(t0, t_end, e1 - e0):
        try:
            rate = sess["rate_kr_per_kwh"] if "rate_kr_per_kwh" in sess.keys() else None
            price = prices.price(sess["location"], slot, rate)
        except Exception:
            price = None  # prices unavailable right now - filled in later by reprice()
        con.execute(
            "insert into slots values (?,?,?,?) on conflict(session_id, slot_start_utc)"
            " do update set kwh_grid = kwh_grid + excluded.kwh_grid",
            (sid, slot.isoformat(), kwh_batt / eff, price),
        )
    if getattr(snap, "odometer_km", None) is not None:
        con.execute("update sessions set odo_end=?, odo_start=coalesce(odo_start, ?) where id=?",
                    (snap.odometer_km, snap.odometer_km, sid))
    update_totals(con, sid, e1, snap.battery_level)


def update_totals(con, sid, kwh_battery=None, battery=None):
    row = con.execute("select coalesce(sum(kwh_grid),0) k, coalesce(sum(kwh_grid*price_kr),0) c"
                      " from slots where session_id=?", (sid,)).fetchone()
    con.execute("update sessions set kwh_grid=?, cost_kr=?,"
                " kwh_battery=coalesce(?, kwh_battery), battery_end=coalesce(?, battery_end) where id=?",
                (row["k"], row["c"], kwh_battery, battery, sid))


def close_session(con, sess, end: datetime, s: dict | None = None) -> None:
    # if we never saw the charge finish (car fell asleep), the last charging reading is the best estimate
    con.execute("update sessions set end_utc=?, est_end_utc=coalesce(est_end_utc,"
                " (select max(time_utc) from readings where session_id=?)) where id=?",
                (end.isoformat(), sess["id"], sess["id"]))
    s2 = con.execute("select * from sessions where id=?", (sess["id"],)).fetchone()
    avg = s2["cost_kr"] / s2["kwh_grid"] if s2["kwh_grid"] else 0
    log(f"{s2['car']}: charging ended at {s2['location']} - {s2['kwh_grid']:.1f} kWh, "
        f"{s2['cost_kr']:.2f} kr ({avg:.2f} kr/kWh)")
    if s is not None and s2["kwh_grid"] >= 0.5 and notify_on(s, "charge_finished"):
        where = LOC_NAMES.get(s2["location"], s2["location"])
        batt = f", battery {s2['battery_end']}%" if s2["battery_end"] is not None else ""
        src = s2["cost_source"] if "cost_source" in s2.keys() else None
        hint = ""
        if s2["location"] == "supercharger" and src != "Tesla bill":
            hint = " Estimated - the exact amount from Tesla follows."
        elif src in ("estimate", "operator list"):
            hint = " Price is an estimate - correct it in the dashboard."
        place = f" ({s2['station']})" if "station" in s2.keys() and s2["station"] else ""
        notify(s, f"Charging finished - {where}",
               f"{s2['kwh_grid']:.1f} kWh for {s2['cost_kr']:.0f} kr ({avg:.2f} kr/kWh){batt}{place}.{hint}",
               "white_check_mark")


def reprice(con, prices: Prices) -> None:
    rows = con.execute("select sl.session_id, sl.slot_start_utc, se.location, se.rate_kr_per_kwh from slots sl"
                       " join sessions se on se.id = sl.session_id where sl.price_kr is null").fetchall()
    for r in rows:
        try:
            p = prices.price(r["location"], datetime.fromisoformat(r["slot_start_utc"]), r["rate_kr_per_kwh"])
        except Exception:
            continue
        con.execute("update slots set price_kr=? where session_id=? and slot_start_utc=?",
                    (p, r["session_id"], r["slot_start_utc"]))
        update_totals(con, r["session_id"])


# ---------------------------------------------------------------- main loop
def tick(tesla: Tesla, s: dict, prices: Prices) -> bool:
    """One check of all cars. Returns True if any car is charging."""
    any_charging = False
    with db() as con:
        reprice(con, prices)
        for v in tesla.vehicles():
            vin, car = v["vin"], v.get("display_name") or v["vin"]
            sess = open_session(con, vin)
            if v["state"] != "online":
                if sess:  # car went to sleep -> charging is over
                    close_session(con, sess, datetime.now(timezone.utc), s)
                continue
            snap = tesla.snapshot(vin, v["state"])
            if snap.charging_state is None:
                continue
            save_car_state(car, snap, s)
            charging = snap.charging_state == "Charging"
            if sess and charging:
                prev = last_reading(con, sess["id"])
                if prev and (snap.energy_added_kwh or 0) < prev["energy_added"] - 0.05:
                    close_session(con, sess, snap.time, s)  # counter reset -> a new session began
                    sess = None
            if charging:
                any_charging = True
                if sess is None:
                    sid = start_session(con, vin, car, snap, s)
                    sess = con.execute("select * from sessions where id=?", (sid,)).fetchone()
                add_reading(con, sess, snap, s, prices, charging=True)
            elif sess:
                add_reading(con, sess, snap, s, prices, charging=False)  # pick up the final kWh
                close_session(con, sess, snap.time, s)
    return any_charging


def setup_missing() -> str | None:
    """What still has to be done on the dashboard's setup page before the logger can work."""
    from tesla_client import TOKENS_FILE
    if not (HERE / "settings.toml").exists():
        return "settings (setup page, steps 1-2)"
    if not (HERE / "tesla_secrets.toml").exists():
        return "Tesla details (setup page, step 3)"
    if not TOKENS_FILE.exists():
        return "Tesla login (setup page, step 3E)"
    return None


def run(once: bool = False) -> None:
    missing = setup_missing()
    while missing:  # fresh install: wait quietly instead of crashing and restarting
        log(f"Waiting for setup: {missing}. Open the dashboard to finish it.")
        if once:
            return
        time.sleep(120)
        missing = setup_missing()
    settings_file = HERE / "settings.toml"
    s, mtime = load_settings(), settings_file.stat().st_mtime
    tesla, prices = Tesla(), Prices(s)
    log("Logger started. Ctrl+C to stop.")
    while True:
        if settings_file.stat().st_mtime != mtime:  # changed on the settings page: use the new values
            s, mtime = load_settings(), settings_file.stat().st_mtime
            prices = Prices(s)
            log("Settings changed - reloaded.")
        try:
            charging = tick(tesla, s, prices)
        except Exception:
            traceback.print_exc()
            charging = False
        try:
            daily_tip(s)
        except Exception:
            traceback.print_exc()
        try:
            pp.sync_supercharger_costs(s, tesla)
        except Exception:
            traceback.print_exc()
        if once:
            return
        time.sleep(seconds_to_next_check(s, charging))


def seconds_to_next_check(s: dict, charging: bool, now: datetime | None = None) -> float:
    """Check on fixed clock times: every quarter hour (:00 :15 :30 :45) + a small offset,
    so charges started on the quarter are caught within seconds and readings line up with
    the 15-minute price slots. Set poll_align = false in [charging] for plain intervals."""
    c = s["charging"]
    if not c.get("poll_align", True):
        return c["poll_charging_min" if charging else "poll_idle_min"] * 60
    step = int(c.get("poll_step_min", 15)) * 60
    offset = int(c.get("poll_offset_s", 30))
    now = now or datetime.now(DK_TZ)
    secs = now.minute * 60 + now.second + now.microsecond / 1e6
    nxt = (secs - offset) // step * step + step + offset
    return max(nxt - secs, 1.0)


def report() -> None:
    with db() as con:
        rows = con.execute("select * from sessions order by start_utc").fetchall()
    if not rows:
        print("No sessions recorded yet.")
        return
    print(f"{'start':16}  {'location':10} {'kWh':>6} {'cost kr':>8} {'kr/kWh':>6}  {'battery':9}")
    for r in rows:
        start = datetime.fromisoformat(r["est_start_utc"] or r["start_utc"]).astimezone(DK_TZ)
        avg = r["cost_kr"] / r["kwh_grid"] if r["kwh_grid"] else 0
        status = "" if r["end_utc"] else "  (charging)"
        print(f"{start:%Y-%m-%d %H:%M}  {r['location']:10} {r['kwh_grid']:6.1f} {r['cost_kr']:8.2f}"
              f" {avg:6.2f}  {r['battery_start']}->{r['battery_end']}%{status}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"run": run, "once": lambda: run(once=True), "report": report}.get(arg, lambda: print(__doc__))()

"""Prices for public charging: station lookup, your remembered prices, operator list, Tesla bills.

Which price a public charge gets (first match wins):
  1. Tesla bill        - exact amount from Tesla's charging history (Superchargers)
  2. your price        - what you entered in the dashboard for this station / operator
  3. operator list     - [operator_prices] in settings.toml, e.g.  Clever = 4.39
  4. estimate          - [public] defaults in settings.toml
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).parent
DB_PATH = HERE / "charging.db"
OCM_KEY_FILE = HERE / "ocm_key.txt"       # Open Charge Map API key (set from the dashboard)
MEMORY_FILE = HERE / "public_prices.json"  # prices you entered, per station and operator
SYNC_FILE = HERE / "tesla_sync.json"

DEFAULTS = {"supercharger": ("supercharger_kr_per_kwh", 3.20),
            "public_dc": ("dc_kr_per_kwh", 3.95),
            "public_ac": ("ac_kr_per_kwh", 3.50)}
PUBLIC = set(DEFAULTS)


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M}  {msg}", flush=True)


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    ensure_columns(con)
    return con


def ensure_columns(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("pragma table_info(sessions)")}
    for col, typ in (("station", "text"), ("operator", "text"), ("rate_kr_per_kwh", "real"), ("cost_source", "text"),
                     ("tesla_session_id", "text")):
        if cols and col not in cols:
            con.execute(f"alter table sessions add column {col} {typ}")


# ---------------------------------------------------------------- station lookup (Open Charge Map)
def ocm_key(s: dict) -> str | None:
    key = (s.get("ocm") or {}).get("api_key")
    if key:
        return key
    return OCM_KEY_FILE.read_text().strip() if OCM_KEY_FILE.exists() else None


def save_ocm_key(key: str) -> None:
    OCM_KEY_FILE.write_text(key.strip())


def lookup_station(lat, lon, s: dict) -> tuple[str | None, str | None]:
    """Nearest charging station within 250 m: (station name, operator)."""
    key = ocm_key(s)
    if not key or lat is None:
        return None, None
    r = requests.get("https://api.openchargemap.io/v3/poi/", timeout=10,
                     params={"output": "json", "latitude": lat, "longitude": lon, "distance": 0.25,
                             "distanceunit": "KM", "maxresults": 1, "compact": "false", "verbose": "false"},
                     headers={"X-API-Key": key, "User-Agent": "ev-cost-tracker (personal use)"})
    r.raise_for_status()
    pois = r.json()
    if not pois:
        return None, None
    p = pois[0]
    addr = p.get("AddressInfo") or {}
    station = addr.get("Title") or None
    if station and addr.get("Town") and addr["Town"].lower() not in station.lower():
        station = f"{station}, {addr['Town']}"
    operator = (p.get("OperatorInfo") or {}).get("Title") or None
    if operator and (operator.startswith("(") or "unknown" in operator.lower()):
        operator = None
    if operator and " (" in operator:  # "Clever (Denmark)" -> "Clever"
        operator = operator.split(" (")[0]
    return station, operator


# ---------------------------------------------------------------- remembered + listed prices
def _norm(x: str | None) -> str:
    return (x or "").strip().lower()


def load_memory() -> dict:
    try:
        m = json.loads(MEMORY_FILE.read_text())
    except Exception:
        m = {}
    m.setdefault("station", {})
    m.setdefault("operator", {})
    return m


def remember(station: str | None, operator: str | None, rate: float) -> None:
    m = load_memory()
    if station:
        m["station"][_norm(station)] = round(rate, 3)
    if operator:
        m["operator"][_norm(operator)] = round(rate, 3)
    MEMORY_FILE.write_text(json.dumps(m, indent=2, ensure_ascii=False))


def operator_list_price(operator: str | None, s: dict) -> float | None:
    if not operator:
        return None
    for name, price in (s.get("operator_prices") or {}).items():
        if _norm(name) in _norm(operator) or _norm(operator) in _norm(name):
            return float(price)
    return None


def choose_rate(location: str, station: str | None, operator: str | None, s: dict) -> tuple[float, str]:
    m = load_memory()
    if station and _norm(station) in m["station"]:
        return m["station"][_norm(station)], "your price"
    if operator and _norm(operator) in m["operator"]:
        return m["operator"][_norm(operator)], "your price"
    listed = operator_list_price(operator, s)
    if listed:
        return listed, "operator list"
    key, default = DEFAULTS.get(location, ("ac_kr_per_kwh", 3.50))
    return float((s.get("public") or {}).get(key, default)), "estimate"


def apply_rate(con: sqlite3.Connection, sid: int, rate: float, source: str) -> None:
    """Price every slot of a session at one rate and update its total."""
    con.execute("update slots set price_kr=? where session_id=?", (rate, sid))
    con.execute("update sessions set rate_kr_per_kwh=?, cost_source=?,"
                " cost_kr=(select coalesce(sum(kwh_grid), 0) from slots where session_id=?) * ? where id=?",
                (rate, source, sid, rate, sid))


def set_session_price(sid: int, rate: float | None = None, total: float | None = None) -> float:
    """Your correction from the dashboard: a price per kWh, or the total from the receipt.
    It is remembered for this station and operator, so the next charge there uses it."""
    with connect() as con:
        row = con.execute("select * from sessions where id=?", (sid,)).fetchone()
        if total is not None:
            if not row["kwh_grid"]:
                raise ValueError("This charge has no kWh recorded yet.")
            rate = total / row["kwh_grid"]
        apply_rate(con, sid, rate, "your price")
        remember(row["station"], row["operator"], rate)
    return rate


def backfill_stations(s: dict, limit: int = 50) -> int:
    """Look up station + operator for earlier public charges, and re-price estimates."""
    n = 0
    with connect() as con:
        rows = con.execute("select * from sessions where location in ('supercharger','public_dc','public_ac')"
                           " and station is null and lat is not null order by start_utc desc limit ?",
                           (limit,)).fetchall()
        for r in rows:
            station, operator = lookup_station(r["lat"], r["lon"], s)
            if not station and not operator:
                continue
            con.execute("update sessions set station=?, operator=? where id=?", (station, operator, r["id"]))
            if (r["cost_source"] or "estimate") == "estimate":
                rate, source = choose_rate(r["location"], station, operator, s)
                apply_rate(con, r["id"], rate, source)
            n += 1
    return n


# ---------------------------------------------------------------- Tesla Supercharger bills
def _parse_time(v) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def event_kwh(ev: dict) -> float | None:
    """kWh charged in one Tesla history event (from the charging fee's usage), if Tesla states it."""
    kwh = 0.0
    for f in ev.get("fees") or []:
        kind = str(f.get("feeType") or "").upper()
        uom = str(f.get("uom") or "").lower()
        if "kwh" in uom or (kind.startswith("CHARG") and f.get("usageBase") is not None):
            kwh += float(f.get("usageBase") or 0) + float(f.get("usageTier2") or 0) \
                   + float(f.get("usageTier3") or 0) + float(f.get("usageTier4") or 0)
    return kwh or None


def _slots(t0: datetime, t1: datetime, kwh: float):
    """Split kWh evenly over 15-minute slots between t0 and t1."""
    if t1 <= t0:
        t1 = t0 + timedelta(minutes=15)
    total = (t1 - t0).total_seconds()
    cur = t0.replace(minute=t0.minute - t0.minute % 15, second=0, microsecond=0)
    while cur < t1:
        overlap = (min(cur + timedelta(minutes=15), t1) - max(cur, t0)).total_seconds()
        if overlap > 0:
            yield cur, kwh * overlap / total
        cur += timedelta(minutes=15)


def sync_supercharger_costs(s: dict, tesla, force: bool = False) -> dict:
    """Bring in Tesla's Supercharger bills (at most every 6 hours, or on demand):
    - a charge the logger recorded gets the exact amount instead of the estimate
    - a visit the logger never saw (older charges, or the car fell asleep) is added as a new session"""
    now = datetime.now(timezone.utc)
    result = {"matched": 0, "imported": 0}
    try:
        state = json.loads(SYNC_FILE.read_text())
    except Exception:
        state = {}
    last = _parse_time(state.get("last"))
    if not force and last and now - last < timedelta(hours=6):
        return result
    state["last"] = now.isoformat()
    SYNC_FILE.write_text(json.dumps(state))
    try:
        events = tesla.charging_history()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        log(f"Tesla charging history not available (HTTP {code}) - log in again from the dashboard "
            "(Tesla login) to get exact Supercharger costs")
        return result
    from tesla_client import fees_total_dkk
    fallback_rate = float((s.get("public") or {}).get("supercharger_kr_per_kwh", 3.20))
    with connect() as con:
        known = {r[0] for r in con.execute("select tesla_session_id from sessions where tesla_session_id is not null")}
        sessions = con.execute("select * from sessions where end_utc is not null").fetchall()
        for ev in events:
            tid = str(ev.get("sessionId") or "") or None
            start = _parse_time(ev.get("chargeStartDateTime") or ev.get("startTime"))
            stop = _parse_time(ev.get("chargeStopDateTime") or ev.get("unlatchDateTime")) or start
            total, _cur = fees_total_dkk(ev)
            if not start or total <= 0 or (tid and tid in known):
                continue
            site = ev.get("siteLocationName")

            def ours(r):
                return _parse_time(r["est_start_utc"] if "est_start_utc" in r.keys() and r["est_start_utc"]
                                   else r["start_utc"])
            near = [r for r in sessions if ours(r) and abs((ours(r) - start).total_seconds()) < 90 * 60]
            match = next((r for r in near if r["location"] == "supercharger" and r["kwh_grid"]), None)
            if match:  # the logger saw this charge: replace the estimate with the bill
                apply_rate(con, match["id"], total / match["kwh_grid"], "Tesla bill")
                con.execute("update sessions set station=coalesce(?, station), operator='Tesla', tesla_session_id=?"
                            " where id=?", (site, tid, match["id"]))
                log(f"Supercharger {site or ''}: Tesla billed {total:.2f} kr")
                result["matched"] += 1
                continue
            if near:  # some other session at that time (e.g. wrongly classified) - leave it alone
                continue
            kwh = event_kwh(ev)
            source = "Tesla bill" if kwh else "Tesla bill (kWh estimated)"
            kwh = kwh or total / fallback_rate
            vin = ev.get("vin")
            car = con.execute("select car from sessions where vin=? and car is not null limit 1", (vin,)).fetchone()
            cur = con.execute(
                "insert into sessions(vin, car, start_utc, end_utc, est_start_utc, est_end_utc, location, fast_charger,"
                " kwh_battery, kwh_grid, cost_kr, station, operator, rate_kr_per_kwh, cost_source, tesla_session_id)"
                " values (?,?,?,?,?,?,'supercharger',1,?,?,?,?,'Tesla',?,?,?)",
                (vin, car[0] if car else "Tesla", start.isoformat(), stop.isoformat(), start.isoformat(),
                 stop.isoformat(), kwh, kwh, total, site, total / kwh, source, tid))
            for slot, k in _slots(start, stop, kwh):
                con.execute("insert or ignore into slots values (?,?,?,?)",
                            (cur.lastrowid, slot.isoformat(), k, total / kwh))
            log(f"Imported Supercharger visit {start:%Y-%m-%d} {site or ''}: {kwh:.1f} kWh, {total:.2f} kr")
            result["imported"] += 1
    return result

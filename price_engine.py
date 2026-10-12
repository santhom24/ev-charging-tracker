"""Price engine: all-in Danish electricity price per 15-minute slot.

total = spot + grid tariff + Energinet tariffs + supplier markup + elafgift, then + VAT

Data source: Energi Data Service (free, no API key)
  - DayAheadPrices   -> spot price, 15-min resolution, DKK/MWh
  - DatahubPricelist -> grid tariffs, Price1..Price24 = hour 0..23 (local time)
"""
from __future__ import annotations

import json
import math
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

API = "https://api.energidataservice.dk/dataset"
DK_TZ = ZoneInfo("Europe/Copenhagen")
HERE = Path(__file__).parent


# ---------------------------------------------------------------- helpers
def load_settings(path: Path | str = HERE / "settings.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _get(dataset: str, params: dict) -> list[dict]:
    r = requests.get(f"{API}/{dataset}", params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("records", [])


# ---------------------------------------------------------------- spot price
def fetch_spot(day: date, area: str = "DK1") -> pd.DataFrame:
    """Spot prices for one Danish calendar day, in kr/kWh (ex VAT)."""
    params = {
        "start": day.isoformat(),
        "end": (day + timedelta(days=1)).isoformat(),
        "filter": json.dumps({"PriceArea": [area]}),
        "sort": "TimeUTC asc",
        "limit": 0,
    }
    recs = _get("DayAheadPrices", params)
    if not recs:
        raise RuntimeError(
            f"No spot prices for {day} yet. Next-day prices are usually "
            "published around 13:00."
        )
    return spot_records_to_df(recs)


def spot_records_to_df(recs: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(recs)
    df["time"] = pd.to_datetime(df["TimeUTC"]).dt.tz_localize("UTC").dt.tz_convert(DK_TZ)
    df["spot"] = df["DayAheadPriceDKK"] / 1000  # DKK/MWh -> kr/kWh
    return df[["time", "spot"]].sort_values("time").reset_index(drop=True)


# ---------------------------------------------------------------- tariffs
def fetch_tariff_records(filter_: dict, limit: int = 0) -> list[dict]:
    # limit=0 fetches all versions: some grid companies (e.g. N1) publish
    # many short holiday periods ahead, so a small limit can miss today.
    """Latest tariff versions matching the filter (newest first)."""
    params = {"filter": json.dumps(filter_), "sort": "ValidFrom desc", "limit": limit}
    return _get("DatahubPricelist", params)


def tariff_at(ts: datetime, records: list[dict]) -> float | None:
    """Tariff (kr/kWh ex VAT) valid at local time ts, or None if none matches."""
    naive = ts.astimezone(DK_TZ).replace(tzinfo=None)
    for rec in records:
        valid_from = datetime.fromisoformat(rec["ValidFrom"])
        valid_to = datetime.fromisoformat(rec["ValidTo"]) if rec.get("ValidTo") else None
        if valid_from <= naive and (valid_to is None or naive < valid_to):
            hourly = rec.get(f"Price{naive.hour + 1}")
            return float(hourly if hourly is not None else rec["Price1"])
    return None


def _grid_records(s: dict) -> list[dict]:
    g = s["grid"]
    if not (g.get("gln_number") and g.get("charge_type_code")):
        return []
    return fetch_tariff_records(
        {"GLN_Number": [g["gln_number"]], "ChargeTypeCode": [g["charge_type_code"]]}
    )


def _energinet_records(s: dict) -> list[list[dict]]:
    e = s["energinet"]
    out = []
    for note in e.get("notes", []):
        recs = fetch_tariff_records({"GLN_Number": [e["gln_number"]], "Note": [note]})
        if recs:
            out.append(recs)
        else:
            print(f"  ! No Energinet tariff named '{note}' - check with discover_tariffs.py")
    return out


# ---------------------------------------------------------------- all-in price
def all_in_prices(day: date, s: dict | None = None) -> pd.DataFrame:
    """15-min all-in prices for a day. Columns are kr/kWh."""
    s = s or load_settings()
    spot = fetch_spot(day, s["area"]["price_area"])
    return build_prices(spot, _grid_records(s), _energinet_records(s), s)


def build_prices(
    spot: pd.DataFrame,
    grid_recs: list[dict],
    energinet_recs: list[list[dict]],
    s: dict,
) -> pd.DataFrame:
    """Pure calculation (no network) - easy to test."""
    df = spot.copy()

    def grid(ts):
        v = tariff_at(ts, grid_recs) if grid_recs else None
        return v if v is not None else s["grid"]["manual_kr_per_kwh_ex_vat"]

    def energinet(ts):
        if not energinet_recs:
            return s["energinet"]["manual_kr_per_kwh_ex_vat"]
        return sum(tariff_at(ts, recs) or 0.0 for recs in energinet_recs)

    df["grid"] = df["time"].map(grid)
    df["energinet"] = df["time"].map(energinet)
    df["supplier"] = s["supplier"]["markup_kr_per_kwh_ex_vat"]
    df["elafgift"] = s["taxes"]["elafgift_kr_per_kwh_ex_vat"]
    parts = ["spot", "grid", "energinet", "supplier", "elafgift"]
    df["total_ex_vat"] = df[parts].sum(axis=1)
    df["total"] = df["total_ex_vat"] * (1 + s["taxes"]["vat"])
    return df


# ---------------------------------------------------------------- planning
def cheapest_window(prices: pd.DataFrame, kwh: float, charger_kw: float) -> dict:
    """Cheapest continuous charging window for `kwh` at `charger_kw`."""
    slot_kwh = charger_kw * 0.25  # energy per 15-min slot
    n = math.ceil(kwh / slot_kwh)
    if n > len(prices):
        raise ValueError("Charge doesn't fit in the available price slots.")
    # energy per slot: full slots, last slot partial
    energy = [slot_kwh] * (n - 1) + [kwh - slot_kwh * (n - 1)]
    best = None
    for i in range(len(prices) - n + 1):
        cost = sum(p * e for p, e in zip(prices["total"].iloc[i : i + n], energy))
        if best is None or cost < best["cost_kr"]:
            best = {
                "start": prices["time"].iloc[i],
                "end": prices["time"].iloc[i + n - 1] + timedelta(minutes=15),
                "cost_kr": cost,
            }
    best["avg_kr_per_kwh"] = best["cost_kr"] / kwh
    return best

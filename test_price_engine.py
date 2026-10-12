"""Offline tests - run with:  python test_price_engine.py"""
from datetime import datetime

import pandas as pd

from price_engine import DK_TZ, build_prices, cheapest_window, spot_records_to_df, tariff_at

S = {
    "grid": {"manual_kr_per_kwh_ex_vat": 0.30},
    "energinet": {"manual_kr_per_kwh_ex_vat": 0.14},
    "supplier": {"markup_kr_per_kwh_ex_vat": 0.08},
    "taxes": {"elafgift_kr_per_kwh_ex_vat": 0.008, "vat": 0.25},
}

# 24 h of 15-min slots, spot 1000 DKK/MWh except cheap 02:00-04:00
recs = []
for i in range(96):
    t = pd.Timestamp("2026-10-08T22:00:00") + pd.Timedelta(minutes=15 * i)  # UTC
    local_hour = (t.hour + 2) % 24
    recs.append({"TimeUTC": t.isoformat(), "DayAheadPriceDKK": 200.0 if 2 <= local_hour < 4 else 1000.0})
spot = spot_records_to_df(recs)

# Grid tariff: peak 17-21 = 0.90, else 0.20
grid_rec = {"ValidFrom": "2026-01-01T00:00:00", "ValidTo": None, "Price1": 0.20,
            **{f"Price{h+1}": (0.90 if 17 <= h < 21 else 0.20) for h in range(24)}}


def test_spot_conversion():
    assert spot["time"].iloc[0] == pd.Timestamp("2026-10-09 00:00", tz=DK_TZ)
    assert abs(spot["spot"].iloc[0] - 1.0) < 1e-9


def test_tariff_lookup():
    assert tariff_at(datetime(2026, 10, 9, 18, tzinfo=DK_TZ), [grid_rec]) == 0.90
    assert tariff_at(datetime(2026, 10, 9, 3, tzinfo=DK_TZ), [grid_rec]) == 0.20
    assert tariff_at(datetime(2025, 1, 1, tzinfo=DK_TZ), [grid_rec]) is None


def test_all_in_and_window():
    df = build_prices(spot, [grid_rec], [], S)
    at3 = df[df["time"] == pd.Timestamp("2026-10-09 03:00", tz=DK_TZ)].iloc[0]
    expected = (0.2 + 0.20 + 0.14 + 0.08 + 0.008) * 1.25
    assert abs(at3["total"] - expected) < 1e-9
    best = cheapest_window(df, kwh=22, charger_kw=11)  # 2 hours
    assert best["start"].hour == 2 and best["end"].hour == 4
    assert abs(best["cost_kr"] - 22 * expected) < 1e-6


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)

"""Show today's (or tomorrow's) all-in price and the cheapest charging window.

Usage:
    python run_today.py            # today, 40 kWh charge
    python run_today.py 30 tomorrow
"""
import sys
from datetime import date, timedelta

from price_engine import all_in_prices, cheapest_window, load_settings


def main() -> None:
    kwh = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
    day = date.today() + (timedelta(days=1) if "tomorrow" in sys.argv else timedelta(0))
    s = load_settings()

    prices = all_in_prices(day, s)
    hourly = prices.set_index("time").resample("1h").mean(numeric_only=True)

    print(f"\nAll-in price {day} ({s['area']['price_area']}, kr/kWh incl. VAT)\n")
    for ts, row in hourly.iterrows():
        bar = "#" * int(row["total"] * 20)
        print(f"{ts:%H:%M}  {row['total']:5.2f}  {bar}")

    print(f"\nAverage: {prices['total'].mean():.2f} kr/kWh  "
          f"| min {prices['total'].min():.2f}  max {prices['total'].max():.2f}")

    best = cheapest_window(prices, kwh, s["charging"]["charger_kw"])
    print(f"\nCheapest window for {kwh:.0f} kWh at {s['charging']['charger_kw']} kW:")
    print(f"  {best['start']:%H:%M} - {best['end']:%H:%M}  ->  "
          f"{best['cost_kr']:.2f} kr  ({best['avg_kr_per_kwh']:.2f} kr/kWh)")


if __name__ == "__main__":
    main()

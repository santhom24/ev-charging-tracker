"""Find tariff codes for your grid company (or Energinet).

Usage:
    python discover_tariffs.py trefor
    python discover_tariffs.py energinet

Copy GLN_Number and ChargeTypeCode of your "Nettarif C time" line into settings.toml.
"""
import json
import sys

import requests

API = "https://api.energidataservice.dk/dataset/DatahubPricelist"


def main(term: str) -> None:
    params = {
        "filter": json.dumps({"ChargeType": ["D03"]}),  # D03 = tariffs
        "sort": "ValidFrom desc",
        "limit": 0,  # 0 = all records
    }
    recs = requests.get(API, params=params, timeout=60).json()["records"]
    seen = set()
    term = term.lower()
    for r in recs:
        if term not in (r.get("ChargeOwner") or "").lower():
            continue
        key = (r["ChargeOwner"], r["GLN_Number"], r["ChargeTypeCode"], r["Note"])
        if key in seen:
            continue
        seen.add(key)
        print(f"{r['ChargeOwner']}\n  GLN_Number={r['GLN_Number']}  "
              f"ChargeTypeCode={r['ChargeTypeCode']}  Note={r['Note']}  "
              f"ValidFrom={r['ValidFrom']}  Price1={r['Price1']}\n")
    if not seen:
        owners = sorted({r.get("ChargeOwner") or "" for r in recs} - {""})
        print(f"No tariffs found for '{term}'.")
        print("Note: OK, Norlys etc. are suppliers - search for your GRID company instead.\n")
        print("Grid companies available:")
        for o in owners:
            print("  ", o)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    else:
        main(" ".join(sys.argv[1:]))

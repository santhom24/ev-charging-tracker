"""One-time Tesla setup and a quick test.

    python tesla_auth.py check      # is your public key reachable?
    python tesla_auth.py register   # register your domain with Tesla (once)
    python tesla_auth.py login      # log in with your Tesla account (copy/paste flow)
    python tesla_auth.py login-url  # phone-friendly login, step 1: print the login link
    python tesla_auth.py login-code # phone-friendly login, step 2: paste the address you landed on
    python tesla_auth.py test       # list your cars and read charge state
    python tesla_auth.py history    # check access to Tesla's Supercharger billing history
    python tesla_auth.py import-history  # add past Supercharger visits with their exact cost
"""
import secrets as pysecrets
import json
import sys
from pathlib import Path
import urllib.parse
import webbrowser

import requests

from tesla_client import AUTH_URL, SCOPES, Tesla, exchange_code, load_secrets, partner_token

KEY_PATH = "/.well-known/appspecific/com.tesla.3p.public-key.pem"


def check(s):
    url = f"https://{s['domain']}{KEY_PATH}"
    r = requests.get(url, timeout=20)
    ok = r.ok and "BEGIN PUBLIC KEY" in r.text
    print(("OK  " if ok else "FAIL") + f" {url}  (HTTP {r.status_code})")
    if not ok:
        print("Upload the public_site folder contents, incl. the .nojekyll file.")


def register(s):
    tok = partner_token(s)
    r = requests.post(
        s["api_base"] + "/api/1/partner_accounts",
        headers={"Authorization": f"Bearer {tok}"},
        json={"domain": s["domain"]}, timeout=30,
    )
    print(r.status_code, r.text[:500])


LOGIN_STATE = Path(__file__).parent / "login_state.json"


def _auth_url(s, state):
    params = {
        "response_type": "code",
        "client_id": s["client_id"],
        "redirect_uri": s["redirect_uri"],
        "scope": SCOPES,
        "state": state,
        "prompt_missing_scopes": "true",  # ask again for permissions added since the last login
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def login_url(s):
    """Step 1 (works on a phone): print the link and remember the login attempt in a file,
    so it survives the SSH page reloading while you're in the browser."""
    state = pysecrets.token_urlsafe(16)
    LOGIN_STATE.write_text(json.dumps({"state": state}))
    print("\nOpen this link in your phone's browser and log in with your Tesla account:\n")
    print(_auth_url(s, state), "\n")
    print("After approving, the browser shows an error page for localhost:8080 - that's expected.")
    print("Copy the FULL address from the address bar, then run:\n")
    print("    .venv/bin/python tesla_auth.py login-code\n")
    print("and paste the address when asked. Do it within a few minutes - the code expires.")


def login_code(s):
    """Step 2: exchange the pasted callback address for tokens."""
    if not LOGIN_STATE.exists():
        raise SystemExit("Run  tesla_auth.py login-url  first.")
    pasted = sys.argv[2] if len(sys.argv) > 2 else input("Paste the address: ").strip()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
    if q.get("error"):
        raise SystemExit(f"Tesla returned an error: {q['error'][0]}")
    code = q.get("code", [None])[0]
    if not code:
        raise SystemExit("No code= found in that address. Copy the whole address and try again.")
    if q.get("state", [None])[0] != json.loads(LOGIN_STATE.read_text())["state"]:
        raise SystemExit("That address is from an older login link. Run login-url again and use the new link.")
    try:
        exchange_code(s, code)
    except requests.HTTPError as e:
        raise SystemExit(f"Token exchange failed: {e.response.status_code} {e.response.text[:300]}"
                         "\nIf it says the code expired, run login-url again and be a bit quicker.")
    LOGIN_STATE.unlink(missing_ok=True)
    print("Logged in. Tokens saved to tokens.json. Now restart the logger:")
    print("    sudo systemctl restart ev-logger")


def history(s):
    t = Tesla(s)
    try:
        events = t.charging_history()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        text = (e.response.text if e.response is not None else "")[:400]
        hint = {401: "Login expired - log in again from the dashboard (Tesla login).",
                403: "No permission - log in again from the dashboard and approve the charging permission.",
                400: "Tesla rejected the request - send this message to Claude."}.get(code, "")
        raise SystemExit(f"Charging history failed (HTTP {code}). {hint}\nTesla said: {text}")
    print(f"OK - access works. {len(events)} Supercharger session(s) in Tesla's history.")
    from tesla_client import granted_scopes
    print("Login permissions:", " ".join(granted_scopes()) or "?")
    if events:
        print("Fields Tesla returns:", ", ".join(sorted(events[0].keys())))
    for ev in events[:5]:
        from tesla_client import fees_total_dkk
        fees, cur = fees_total_dkk(ev)
        raw = ", ".join(f"{f.get('totalDue')} {f.get('currencyCode', '?')}" for f in ev.get("fees") or [])
        print(f"  {ev.get('chargeStartDateTime', '?')[:16]}  {ev.get('siteLocationName', '?')}  "
              f"= {fees:.2f} kr  ({raw})")
    if events and events[0].get("fees"):
        print("Fee fields:", ", ".join(sorted(events[0]["fees"][0].keys())))


def import_history(s):
    import public_prices as pp
    r = pp.sync_supercharger_costs(load_settings_toml(), Tesla(s), force=True)
    print(f"Supercharger history: {r['imported']} visit(s) imported, {r['matched']} charge(s) updated with Tesla's bill.")


def load_settings_toml():
    from price_engine import load_settings
    return load_settings()


def login(s):
    state = pysecrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": s["client_id"],
        "redirect_uri": s["redirect_uri"],
        "scope": SCOPES,
        "state": state,
        "prompt_missing_scopes": "true",
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("\n1) Open this URL in a PRIVATE/INCOGNITO browser window and log in:\n")
    print(url, "\n")
    if "--open" in sys.argv:
        webbrowser.open(url)
    print("2) After approving, the browser lands on a localhost:8080/callback?code=... page.")
    print("   It may show an error or 'can't connect' - that's fine.")
    print("   Copy the FULL address from the address bar and paste it here, then press Enter.")
    print("   (Do this within a minute or two - the code expires quickly.)\n")
    pasted = input("Address: ").strip()

    q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
    if q.get("error"):
        raise SystemExit(f"Tesla returned an error: {q['error'][0]}")
    code = q.get("code", [None])[0]
    if not code:
        raise SystemExit("No code= found in that address. Run login again.")
    if q.get("state", [None])[0] != state:
        raise SystemExit("That address is from an older login attempt. Run login again "
                         "and paste the address from THIS attempt.")
    try:
        exchange_code(s, code)
    except requests.HTTPError as e:
        raise SystemExit(f"Token exchange failed: {e.response.status_code} {e.response.text[:300]}")
    print("Logged in. Tokens saved to tokens.json (keep it private).")


def test(s):
    t = Tesla(s)
    for v in t.vehicles():
        print(f"\n{v.get('display_name')}  VIN {v['vin']}  state={v['state']}")
        snap = t.snapshot(v["vin"], v["state"])
        if snap.charging_state is None:
            print("  Car is asleep - no data read (we don't wake it). Open the Tesla app and retry.")
        else:
            print(f"  charging_state={snap.charging_state}  battery={snap.battery_level}%")
            print(f"  energy_added={snap.energy_added_kwh} kWh  power={snap.charger_power_kw} kW")
            print(f"  location={snap.lat}, {snap.lon}  fast_charger={snap.fast_charger}")


if __name__ == "__main__":
    cmds = {"check": check, "register": register, "login": login, "login-url": login_url,
            "login-code": login_code, "test": test, "history": history, "import-history": import_history}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
    else:
        cmds[sys.argv[1]](load_secrets())

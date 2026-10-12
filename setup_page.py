"""First-run setup (and later: settings) for the dashboard.

Writes settings.toml and tesla_secrets.toml, creates the Tesla key pair, checks the public key,
registers the domain with Tesla and handles the Tesla login - all from the phone, no terminal needed.
"""
from __future__ import annotations

import json
import secrets as pysecrets
import tomllib
import urllib.parse
from pathlib import Path

import requests
import streamlit as st

HERE = Path(__file__).parent
SETTINGS_FILE = HERE / "settings.toml"
EXAMPLE_FILE = HERE / "settings.example.toml"
SECRETS_FILE = HERE / "tesla_secrets.toml"
PRIVATE_KEY = HERE / "private-key.pem"
LOGIN_STATE = HERE / "login_state.json"
KEY_PATH = "/.well-known/appspecific/com.tesla.3p.public-key.pem"
REGIONS = {"Europe": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
           "North America / Asia-Pacific": "https://fleet-api.prd.na.vn.cloud.tesla.com"}


# ---------------------------------------------------------------- files
def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)  # TOML basic strings use the same escaping


def toml_dump(data: dict) -> str:
    """Small TOML writer for this app's settings (tables of plain values, one level of sub-tables)."""
    out = ["# EV Charging Tracker settings - written by the setup page. You can also edit this file by hand.\n"]

    def table(name: str, d: dict):
        plain = {k: v for k, v in d.items() if not isinstance(v, dict)}
        out.append(f"\n[{name}]\n")
        for k, v in plain.items():
            key = k if k.replace("_", "").isalnum() else json.dumps(k)
            out.append(f"{key} = {_value(v)}\n")
        for k, v in d.items():
            if isinstance(v, dict):
                table(f"{name}.{k}", v)

    for name, d in data.items():
        table(name, d)
    return "".join(out)


def load_current() -> dict:
    for f in (SETTINGS_FILE, EXAMPLE_FILE):
        if f.exists():
            with open(f, "rb") as fh:
                return tomllib.load(fh)
    return {}


def save_settings(data: dict) -> None:
    SETTINGS_FILE.write_text(toml_dump(data), encoding="utf-8")
    SETTINGS_FILE.chmod(0o600)


def load_secrets() -> dict:
    if SECRETS_FILE.exists():
        with open(SECRETS_FILE, "rb") as fh:
            return tomllib.load(fh)
    return {}


# ---------------------------------------------------------------- grid companies (Energi Data Service)
@st.cache_data(ttl=24 * 3600, show_spinner="Looking up grid companies…")
def grid_companies() -> list[dict]:
    """Household hourly tariffs ('Nettarif C time') for every Danish grid company, newest first."""
    r = requests.get("https://api.energidataservice.dk/dataset/DatahubPricelist", timeout=60, params={
        "filter": json.dumps({"ChargeType": ["D03"], "Note": ["Nettarif C time", "Nettarif C"]}),
        "sort": "ValidFrom desc", "limit": 5000})
    r.raise_for_status()
    best: dict[str, dict] = {}
    for rec in r.json().get("records", []):
        owner = rec.get("ChargeOwner") or "?"
        hourly = rec.get("Note") == "Nettarif C time"
        if owner not in best or (hourly and not best[owner]["hourly"]):
            best[owner] = {"owner": owner, "gln": rec["GLN_Number"], "code": rec["ChargeTypeCode"],
                           "note": rec.get("Note"), "hourly": hourly}
    return sorted(best.values(), key=lambda x: x["owner"].lower())


# ---------------------------------------------------------------- setup page
def render(first_run: bool) -> None:
    cur = load_current()
    st.title("⚡ EV charging – setup" if first_run else "⚙️ Settings")
    if first_run:
        st.markdown("Welcome! Fill in these four short steps. Everything stays on **your own server**. "
                    "You can change it all later under ⚙️ Settings at the bottom of the dashboard.")
    else:
        if st.button("← Back to the dashboard"):
            st.session_state["show_setup"] = False
            st.rerun()

    t1, t2, t3, t4 = st.tabs(["1 · Electricity", "2 · Car & charging", "3 · Tesla", "4 · Extras"])

    # ---------------- 1. electricity
    with t1:
        area = cur.get("area", {}).get("price_area", "DK1")
        price_area = st.radio("Price area", ["DK1", "DK2"], index=0 if area == "DK1" else 1, horizontal=True,
                              help="DK1 = Jutland & Funen · DK2 = Zealand, Copenhagen & Bornholm")
        st.markdown("**Grid company** (netselskab – it's on your electricity bill)")
        grid = dict(cur.get("grid", {}))
        try:
            companies = grid_companies()
        except Exception as e:
            companies = []
            st.warning(f"Couldn't load the list of grid companies ({e}). Enter the codes by hand below.")
        if companies:
            labels = ["– choose –"] + [c["owner"] for c in companies]
            pre = next((i + 1 for i, c in enumerate(companies)
                        if c["gln"] == grid.get("gln_number") and c["code"] == grid.get("charge_type_code")), 0)
            pick = st.selectbox("Grid company", labels, index=pre,
                                help="Some companies have several areas (e.g. 'N1 A/S - 344'). The area number "
                                     "is usually in your meter ID (målepunkts-ID) on the bill.")
            if pick != "– choose –":
                c = companies[labels.index(pick) - 1]
                grid["gln_number"], grid["charge_type_code"] = c["gln"], c["code"]
                st.caption(f"Tariff: {c['note']} · GLN {c['gln']} · code {c['code']}")
        with st.expander("Enter the codes by hand instead"):
            grid["gln_number"] = st.text_input("GLN number", grid.get("gln_number", ""))
            grid["charge_type_code"] = st.text_input("Charge type code", grid.get("charge_type_code", ""))
            grid["manual_kr_per_kwh_ex_vat"] = st.number_input(
                "Fallback grid tariff (kr/kWh ex. moms)", 0.0, 3.0,
                float(grid.get("manual_kr_per_kwh_ex_vat", 0.20)), 0.01,
                help="Used only when no codes are set")

        st.markdown("**Electricity supplier**")
        sup = dict(cur.get("supplier", {}))
        sup["name"] = st.text_input("Supplier and plan", sup.get("name", "My electricity supplier"))
        incl = st.checkbox("The markup on my bill includes moms (VAT)", value=False)
        markup = st.number_input("Supplier markup per kWh (\"tillæg\")", 0.0, 3.0,
                                 float(sup.get("markup_kr_per_kwh_ex_vat", 0.10)) * (1.25 if incl else 1), 0.005,
                                 format="%.3f", help="Look for 'tillæg' or 'pristillæg' on your bill")
        sup["markup_kr_per_kwh_ex_vat"] = round(markup / 1.25 if incl else markup, 4)
        sup["monthly_fee_kr_incl_vat"] = st.number_input("Fixed monthly fees (kr incl. moms)", 0.0, 1000.0,
                                                         float(sup.get("monthly_fee_kr_incl_vat", 0)), 1.0)

    # ---------------- 2. car & charging
    with t2:
        ch = dict(cur.get("charging", {}))
        c1, c2 = st.columns(2)
        ch["charger_kw"] = c1.number_input("Home charger (kW)", 1.0, 22.0, float(ch.get("charger_kw", 11.0)), 0.5)
        ch["battery_kwh"] = c2.number_input("Usable battery (kWh)", 20.0, 150.0, float(ch.get("battery_kwh", 75)), 1.0)
        ch["efficiency"] = st.slider("Charging efficiency", 0.80, 1.00, float(ch.get("efficiency", 0.90)), 0.01,
                                     help="The meter sees a bit more than the battery gets (AC losses). 0.90 is typical.")
        ch.setdefault("poll_offset_s", 30)

        locs = {k: dict(v) for k, v in cur.get("locations", {}).items()}
        home = locs.setdefault("home", {"lat": 0.0, "lon": 0.0, "radius_m": 150, "price": "dynamic"})
        office = locs.setdefault("office", {"lat": 0.0, "lon": 0.0, "radius_m": 300, "price_kr_per_kwh": 0.0})
        st.markdown("**Home and work**")
        st.caption("Easiest: once the Tesla is connected, park at the place and tap 'Car is here now' "
                   "under 📍 Home & Work on the dashboard. Or paste coordinates from Google Maps "
                   "(long-press a spot → copy the numbers).")
        h1, h2 = st.columns(2)
        home["lat"] = h1.number_input("Home latitude", -90.0, 90.0, float(home.get("lat", 0.0)), format="%.6f")
        home["lon"] = h2.number_input("Home longitude", -180.0, 180.0, float(home.get("lon", 0.0)), format="%.6f")
        office["price_kr_per_kwh"] = st.number_input("Price at work (kr/kWh, 0 if free)", 0.0, 10.0,
                                                     float(office.get("price_kr_per_kwh", 0.0)), 0.05)

        st.markdown("**Public charging – starting estimates** (you can correct each charge later)")
        pub = dict(cur.get("public", {}))
        p1, p2, p3 = st.columns(3)
        pub["ac_kr_per_kwh"] = p1.number_input("Public AC", 0.0, 10.0, float(pub.get("ac_kr_per_kwh", 3.50)), 0.05)
        pub["dc_kr_per_kwh"] = p2.number_input("Fast charger", 0.0, 10.0, float(pub.get("dc_kr_per_kwh", 3.95)), 0.05)
        pub["supercharger_kr_per_kwh"] = p3.number_input("Supercharger", 0.0, 10.0,
                                                         float(pub.get("supercharger_kr_per_kwh", 3.20)), 0.05)

    # ---------------- 4. extras (before saving, so everything is saved together)
    with t4:
        cmp_ = dict(cur.get("compare", {}))
        st.markdown("**Compare with a fuel car**")
        f1, f2, f3 = st.columns(3)
        cmp_["fuel"] = f1.selectbox("Fuel", ["petrol", "diesel"], index=0 if cmp_.get("fuel", "petrol") == "petrol" else 1)
        cmp_["fuel_km_per_litre"] = f2.number_input("km per litre", 5.0, 40.0, float(cmp_.get("fuel_km_per_litre", 15)), 0.5)
        cmp_["fuel_kr_per_litre"] = f3.number_input("kr per litre", 5.0, 30.0, float(cmp_.get("fuel_kr_per_litre", 14.0)), 0.1)
        for k in ("petrol_l_per_100km", "petrol_kr_per_litre", "fuel_l_per_100km"):
            cmp_.pop(k, None)
        st.markdown("**Phone notifications** (free ntfy app – the topic is shown on the dashboard)")
        nt = dict(cur.get("notify", {}))
        nt["charge_finished"] = st.checkbox("When charging finishes", nt.get("charge_finished", True))
        nt["peak_alert"] = st.checkbox("When the car starts charging at home at an expensive time", nt.get("peak_alert", True))
        nt["daily_tip"] = st.checkbox("Every afternoon: tonight's cheapest 3 hours", nt.get("daily_tip", True))
        nt.setdefault("peak_margin_kr", 0.5)

    # ---------------- save (steps 1, 2 and 4)
    st.divider()
    if st.button("💾 Save settings", type="primary", use_container_width=True):
        data = {k: v for k, v in cur.items()}
        data["area"] = {"price_area": price_area}
        data["grid"] = grid
        data.setdefault("energinet", {"gln_number": "5790000432752",
                                      "notes": ["Transmissions nettarif", "Systemtarif"],
                                      "manual_kr_per_kwh_ex_vat": 0.07})
        data["supplier"] = sup
        data.setdefault("taxes", {"elafgift_kr_per_kwh_ex_vat": 0.008, "vat": 0.25})
        data["charging"] = ch
        data["locations"] = locs
        data["public"] = pub
        data["compare"] = cmp_
        data["notify"] = nt
        data.setdefault("operator_prices", {})
        save_settings(data)
        st.success("Saved ✅" + (" Next: step 3 · Tesla." if first_run else ""))
        st.session_state["settings_saved"] = True

    # ---------------- 3. Tesla (separate: writes its own files)
    with t3:
        tesla_setup()


def tesla_setup() -> None:
    sec = load_secrets()
    st.markdown("Connecting your car takes about 30 minutes, once. You need a free "
                "[Tesla developer account](https://developer.tesla.com) and a free GitHub account.")

    # step A: key pair
    st.markdown("#### A · Create a key pair")
    if PRIVATE_KEY.exists():
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_private_key(PRIVATE_KEY.read_bytes(), password=None)
        pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        st.caption("Key pair exists. The private key stays on this server; only the public key below is published.")
        st.code(pub, language=None)
        st.download_button("⬇️ Download public key file", pub, "com.tesla.3p.public-key.pem", use_container_width=True)
    elif st.button("🔑 Create key pair", use_container_width=True):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())  # prime256v1, as Tesla requires
        PRIVATE_KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                                  serialization.NoEncryption()))
        PRIVATE_KEY.chmod(0o600)
        st.rerun()

    # step B: publish the public key
    st.markdown("#### B · Publish the public key on GitHub Pages")
    st.markdown("""1. On GitHub, create a **public** repository named **`<your-username>.github.io`**.
2. **Add file → Create new file**, name it `.well-known/appspecific/com.tesla.3p.public-key.pem` and paste the public key above.
3. Create one more file named **`.nojekyll`** (leave it empty) – otherwise GitHub hides the `.well-known` folder.
4. After a minute, open `https://<your-username>.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem` – it should show or download the key.""")

    # step C: Tesla app + secrets
    st.markdown("#### C · Create the app at developer.tesla.com")
    st.markdown("""Choose **Fleet API** and fill in:
- **Allowed Origin URL:** `https://<your-username>.github.io`
- **Allowed Redirect URI:** `http://localhost:8080/callback`
- **Scopes:** Vehicle Information, Vehicle Location, Vehicle Charging Management
- Add a payment method and a low billing limit (e.g. 100 DKK). Tesla gives a $10/month credit, which one car normally stays within.""")
    with st.form("tesla_secrets"):
        cid = st.text_input("Client ID", sec.get("client_id", ""))
        csec = st.text_input("Client secret", sec.get("client_secret", ""), type="password")
        dom = st.text_input("Domain (where the public key is)", sec.get("domain", ""), placeholder="yourname.github.io")
        region_now = next((k for k, v in REGIONS.items() if v == sec.get("api_base")), "Europe")
        region = st.selectbox("Region", list(REGIONS), index=list(REGIONS).index(region_now))
        if st.form_submit_button("💾 Save Tesla details", use_container_width=True):
            dom = dom.strip().removeprefix("https://").removeprefix("http://").strip("/")
            SECRETS_FILE.write_text(
                f'client_id = {_value(cid.strip())}\nclient_secret = {_value(csec.strip())}\n'
                f'domain = {_value(dom)}\nredirect_uri = "http://localhost:8080/callback"\n'
                f'api_base = {_value(REGIONS[region])}\n', encoding="utf-8")
            SECRETS_FILE.chmod(0o600)
            st.success("Saved ✅")
            sec = load_secrets()

    # step D: check + register
    st.markdown("#### D · Check the key and register with Tesla")
    d1, d2 = st.columns(2)
    if d1.button("🔎 Check public key", use_container_width=True, disabled=not sec.get("domain")):
        url = f"https://{sec['domain']}{KEY_PATH}"
        try:
            r = requests.get(url, timeout=20)
            ok = r.ok and "BEGIN PUBLIC KEY" in r.text
            (st.success if ok else st.error)(f"{'OK' if ok else 'Not found'} – {url} (HTTP {r.status_code})")
        except Exception as e:
            st.error(f"Couldn't reach {url}: {e}")
    if d2.button("📝 Register with Tesla", use_container_width=True, disabled=not sec.get("client_id")):
        try:
            from tesla_client import partner_token
            tok = partner_token(sec)
            r = requests.post(sec["api_base"] + "/api/1/partner_accounts", timeout=30,
                              headers={"Authorization": f"Bearer {tok}"}, json={"domain": sec["domain"]})
            (st.success if r.ok else st.error)(f"HTTP {r.status_code}: {r.text[:300]}")
        except Exception as e:
            st.error(f"Registration failed: {e}")

    # step E: login
    st.markdown("#### E · Log in with your Tesla account")
    if sec.get("client_id"):
        tesla_login_ui()
    else:
        st.caption("Save the Tesla details in step C first.")


def tesla_login_ui() -> None:
    """Phone-friendly Tesla login: open link → copy the localhost address → paste it here."""
    from tesla_client import AUTH_URL, SCOPES, exchange_code, granted_scopes
    granted = " ".join(granted_scopes())
    st.caption("Charging permission: " + ("granted ✅" if "vehicle_charging_cmds" in granted else "not granted yet"))
    sec = load_secrets()
    if not sec.get("client_id"):
        st.caption("Fill in the Tesla details under ⚙️ Settings → 3 · Tesla first.")
        return
    try:
        state = json.loads(LOGIN_STATE.read_text())["state"]
    except Exception:
        state = pysecrets.token_urlsafe(16)
        LOGIN_STATE.write_text(json.dumps({"state": state}))  # kept on the server: survives page reloads
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": sec["client_id"], "redirect_uri": sec["redirect_uri"],
        "scope": SCOPES, "state": state, "prompt_missing_scopes": "true"})
    st.markdown("**1.** Tap the button and log in with your Tesla account. Approve all permissions.")
    st.link_button("Open Tesla login", url, use_container_width=True)
    st.markdown("**2.** You end on a page that **can't be opened** (localhost…). That's expected. "
                "Copy the **whole address** from the address bar, come back here and paste it below "
                "within a few minutes.")
    pasted = st.text_input("Address from the browser", placeholder="http://localhost:8080/callback?code=…",
                           key="tl_pasted")
    if st.button("✅ Finish login", use_container_width=True) and pasted.strip():
        q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted.strip()).query)
        code = (q.get("code") or [None])[0]
        if q.get("error"):
            st.error(f"Tesla returned an error: {q['error'][0]}")
        elif not code:
            st.error("No code= found in that address – copy the full address from the address bar.")
        elif (q.get("state") or [None])[0] != state:
            st.error("That address is from an older login attempt. Tap 'Open Tesla login' again.")
        else:
            try:
                exchange_code(sec, code)
                LOGIN_STATE.unlink(missing_ok=True)
                st.success("Logged in ✅ The logger uses the new login from its next check – no restart needed.")
            except Exception as e:
                detail = getattr(getattr(e, "response", None), "text", "")[:200]
                st.error(f"Login failed: {e} {detail} – the code may have expired; tap 'Open Tesla login' again.")

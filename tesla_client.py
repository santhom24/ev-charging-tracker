"""Tesla Fleet API client: auth, token refresh and charging snapshots.

Used by tesla_auth.py (one-time setup) and later by the session logger.
"""
from __future__ import annotations

import json
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).parent
AUTH_URL = "https://auth.tesla.com/oauth2/v3/authorize"
TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
# vehicle_charging_cmds: needed to READ Tesla's Supercharger billing history (no commands are sent)
SCOPES = "openid offline_access vehicle_device_data vehicle_location vehicle_charging_cmds"
TOKENS_FILE = HERE / "tokens.json"


def load_secrets(path: Path = HERE / "tesla_secrets.toml") -> dict:
    if not path.exists():
        raise SystemExit("Missing tesla_secrets.toml - copy tesla_secrets.example.toml and fill it in.")
    with open(path, "rb") as f:
        s = tomllib.load(f)
    missing = [k for k in ("client_id", "client_secret", "domain") if not s.get(k)]
    if missing:
        raise SystemExit(f"Fill in {', '.join(missing)} in tesla_secrets.toml")
    return s


# ---------------------------------------------------------------- tokens
def _save_tokens(tok: dict) -> None:
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 0)) - 60
    TOKENS_FILE.write_text(json.dumps(tok, indent=2))


def partner_token(s: dict) -> str:
    """App-level token, only needed once to register your domain."""
    r = requests.post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": s["client_id"],
        "client_secret": s["client_secret"],
        "scope": "openid vehicle_device_data vehicle_location",
        "audience": s["api_base"],
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def exchange_code(s: dict, code: str) -> None:
    r = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "client_id": s["client_id"],
        "client_secret": s["client_secret"],
        "code": code,
        "audience": s["api_base"],
        "redirect_uri": s["redirect_uri"],
    }, timeout=30)
    r.raise_for_status()
    _save_tokens(r.json())


def access_token(s: dict) -> str:
    """Valid user access token; refreshes automatically.

    Refresh tokens are single-use: the new one is saved every time.
    """
    if not TOKENS_FILE.exists():
        raise SystemExit("Not logged in yet - run:  python tesla_auth.py login")
    tok = json.loads(TOKENS_FILE.read_text())
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "client_id": s["client_id"],
        "refresh_token": tok["refresh_token"],
    }, timeout=30)
    if r.status_code == 401:
        raise SystemExit("Tesla login expired - run:  python tesla_auth.py login")
    r.raise_for_status()
    _save_tokens(r.json())
    return r.json()["access_token"]


def granted_scopes() -> list[str]:
    """Permissions of the current login. Tesla's token reply has no scope list, but the
    access token itself (a JWT) carries them in its 'scp' claim."""
    import base64
    try:
        tok = json.loads(TOKENS_FILE.read_text())
        if tok.get("scope"):
            return tok["scope"].split()
        payload = tok["access_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        scp = claims.get("scp") or claims.get("scope") or []
        return scp.split() if isinstance(scp, str) else list(scp)
    except Exception:
        return []


EUR_TO_DKK = 7.46  # the krone is pegged to the euro, so a fixed rate is accurate enough


def fees_total_dkk(event: dict) -> tuple[float, str]:
    """Total billed for one charging-history event, converted to DKK. Returns (kr, currencies)."""
    total, seen = 0.0, set()
    for f in event.get("fees") or []:
        amount = float(f.get("totalDue") or f.get("netDue") or 0)
        cur = (f.get("currencyCode") or f.get("currency") or "").upper()
        if not cur:  # no currency on the fee: guess from the country
            cur = "EUR" if (event.get("countryCode") or "DK").upper() not in ("DK", "") else "DKK"
        seen.add(cur)
        total += amount * (EUR_TO_DKK if cur == "EUR" else 1.0)
    return total, "/".join(sorted(seen)) or "DKK"


# ---------------------------------------------------------------- data
@dataclass
class ChargeSnapshot:
    time: datetime
    state: str                 # "online" / "asleep" / "offline"
    charging_state: str | None  # "Charging", "Complete", "Stopped", "Disconnected", ...
    energy_added_kwh: float | None  # resets per session
    charger_power_kw: float | None
    battery_level: int | None
    lat: float | None
    lon: float | None
    fast_charger: bool | None  # True at Superchargers / DC chargers
    charge_limit: int | None = None        # % the car charges to
    minutes_to_full: int | None = None     # car's own estimate while charging
    odometer_km: float | None = None
    fast_charger_brand: str | None = None  # "Tesla" at Superchargers
    fast_charger_type: str | None = None


class Tesla:
    def __init__(self, secrets: dict | None = None):
        self.s = secrets or load_secrets()

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        return requests.get(
            self.s["api_base"] + path,
            headers={"Authorization": f"Bearer {access_token(self.s)}"},
            params=params, timeout=30,
        )

    def vehicles(self) -> list[dict]:
        r = self._get("/api/1/vehicles")
        r.raise_for_status()
        return r.json()["response"]

    def charging_history(self, vin: str | None = None) -> list[dict]:
        """Supercharger sessions with what Tesla billed (needs the charging permission).
        Tries the simplest request first; sorting values are undocumented, so none are sent."""
        attempts = [{"vin": vin} if vin else {}, {}] if vin else [{}]
        last = None
        for params in attempts:
            r = self._get("/api/1/dx/charging/history", params or None)
            if r.ok:
                body = r.json()
                data = body.get("data") if isinstance(body, dict) else None
                if data is None and isinstance(body.get("response"), dict):
                    data = body["response"].get("data")
                if data is None and isinstance(body.get("response"), list):
                    data = body["response"]
                return data or []
            last = r
            if r.status_code != 400:
                break
        last.raise_for_status()
        return []

    def snapshot(self, vin: str, state: str = "online") -> ChargeSnapshot:
        """Read charge state + location. Does NOT wake a sleeping car."""
        now = datetime.now(timezone.utc)
        empty = ChargeSnapshot(now, state, None, None, None, None, None, None, None)
        if state != "online":
            return empty
        r = self._get(f"/api/1/vehicles/{vin}/vehicle_data",
                      {"endpoints": "charge_state;location_data;vehicle_state"})
        if r.status_code == 408:  # fell asleep / unreachable
            return ChargeSnapshot(now, "asleep", *[None] * 7)
        r.raise_for_status()
        return parse_vehicle_data(r.json()["response"], now)


def parse_vehicle_data(d: dict, now: datetime) -> ChargeSnapshot:
    cs = d.get("charge_state") or {}
    ds = d.get("drive_state") or {}
    vs = d.get("vehicle_state") or {}
    return ChargeSnapshot(
        time=now,
        state=d.get("state", "online"),
        charging_state=cs.get("charging_state"),
        energy_added_kwh=cs.get("charge_energy_added"),
        charger_power_kw=cs.get("charger_power"),
        battery_level=cs.get("battery_level"),
        lat=ds.get("latitude"),
        lon=ds.get("longitude"),
        fast_charger=cs.get("fast_charger_present"),
        charge_limit=cs.get("charge_limit_soc"),
        minutes_to_full=cs.get("minutes_to_full_charge"),
        odometer_km=round(vs["odometer"] * 1.609344, 1) if vs.get("odometer") is not None else None,  # API gives miles
        fast_charger_brand=cs.get("fast_charger_brand"),
        fast_charger_type=cs.get("fast_charger_type"),
    )

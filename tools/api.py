"""Direct v2 API calls for Phase 1 mode/scale testing.

  uv run python3 tools/api.py read [SERIAL]
  uv run python3 tools/api.py manual SERIAL TEMP_F
  uv run python3 tools/api.py hold SERIAL TEMP_F [HOLD_UNTIL_ISO]
  uv run python3 tools/api.py auto SERIAL
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_probe import API, ROOT, TOKEN, load_env, set_env  # noqa: E402

ACCESS = ROOT / ".access_token"

os.umask(0o077)


def access_token() -> str:
    if ACCESS.exists():
        cached = json.loads(ACCESS.read_text())
        if cached["expires_at"] > time.time() + 60:
            return cached["access_token"]
    env = load_env()
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": env["NUHEAT_REFRESH_TOKEN"],
        "client_id": env["NUHEAT_CLIENT_ID"],
        "client_secret": env["NUHEAT_CLIENT_SECRET"],
    }).encode()
    req = urllib.request.Request(TOKEN, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Refresh failed HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    # The client is configured for one-time refresh tokens; losing the rotated one means a new browser login.
    if tok.get("refresh_token"):
        set_env("NUHEAT_REFRESH_TOKEN", tok["refresh_token"])
    ACCESS.write_text(json.dumps({"access_token": tok["access_token"], "expires_at": time.time() + tok["expires_in"]}))
    return tok["access_token"]


def call(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {access_token()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:1000]


def f_to_c100(temp_f: float) -> int:
    return round((temp_f - 32) * 5 / 9 * 100)


def show(serial: str | None) -> None:
    status, body = call("GET", f"/Thermostat/{serial}" if serial else "/Thermostat")
    for t in body if isinstance(body, list) else [body]:
        if not isinstance(t, dict):
            print(status, t)
            continue
        c2f = lambda v: round(v / 100 * 9 / 5 + 32, 1)
        print(f"{time.strftime('%H:%M:%S')} HTTP {status} serial={t['serialNumber']} mode={t['mode']} "
              f"setpoint={t['setPointTemp' if 'setPointTemp' in t else 'setPointTemperature']}"
              f"({c2f(t['setPointTemperature'])}F) current={t['currentTemperature']}({c2f(t['currentTemperature'])}F) "
              f"heating={t['isHeating']} holdUntil={t['holdUntil']} online={t['online']} error={t['errorState']}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "read":
        show(args[1] if len(args) > 1 else None)
        return
    serial = args[1]
    if cmd == "manual":
        payload = {"serialNumber": serial, "temperature": f_to_c100(float(args[2])), "temperatureType": 0}
    elif cmd == "hold":
        payload = {"serialNumber": serial, "temperature": f_to_c100(float(args[2])), "temperatureType": 0,
                   "holdUntil": args[3] if len(args) > 3 else None}
    elif cmd == "auto":
        payload = {"serialNumber": serial}
    else:
        sys.exit(__doc__)
    status, body = call("PUT", f"/Mode/{cmd.capitalize()}", payload)
    print(f"{time.strftime('%H:%M:%S')} PUT /Mode/{cmd.capitalize()} {json.dumps(payload)} -> HTTP {status} {body}")


if __name__ == "__main__":
    main()

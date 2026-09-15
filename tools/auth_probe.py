"""One-off OAuth probe: prove the Chemelex client works and the Signature is visible.

  uv run python3 tools/auth_probe.py url                 # prints authorize URL
  uv run python3 tools/auth_probe.py exchange [CALLBACK] # CALLBACK defaults to .auth_callback
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
STATE = ROOT / ".auth_state"
CALLBACK = ROOT / ".auth_callback"

AUTHORIZE = "https://identity.nam.mynuheat.com/connect/authorize"
TOKEN = "https://identity.nam.mynuheat.com/connect/token"
API = "https://api.mynuheat.com/api/v2"

os.umask(0o077)


def load_env() -> dict[str, str]:
    env = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def set_env(key: str, value: str) -> None:
    lines = [l for l in ENV.read_text().splitlines() if not l.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n")


def cmd_url() -> None:
    env = load_env()
    state = secrets.token_urlsafe(24)
    # PKCE isn't required by the client, but HA's OAuth2 helper won't send it either way;
    # using it here costs nothing and keeps a leaked code useless without the verifier.
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    STATE.write_text(json.dumps({"state": state, "verifier": verifier}))
    query = urllib.parse.urlencode({
        "client_id": env["NUHEAT_CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": env["NUHEAT_REDIRECT_URI"],
        "scope": env["NUHEAT_SCOPES"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    print(f"{AUTHORIZE}?{query}")


def http_json(req: urllib.request.Request) -> dict | list:
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from {req.full_url}: {e.read().decode(errors='replace')[:500]}")


def cmd_exchange(callback: str | None) -> None:
    env = load_env()
    callback = callback or CALLBACK.read_text().strip()
    params = urllib.parse.parse_qs(urllib.parse.urlparse(callback).query)
    # my.home-assistant.io rewrites the landing page to /redirect/_change/?redirect=oauth/?code=...
    if "redirect" in params and "code" not in params:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(params["redirect"][0]).query)
    if "error" in params:
        sys.exit(f"Authorize error: {params['error'][0]} {params.get('error_description', [''])[0]}")
    saved = json.loads(STATE.read_text())
    if params.get("state", [None])[0] != saved["state"]:
        sys.exit("State mismatch — run `url` again and use the fresh link.")

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": params["code"][0],
        "redirect_uri": env["NUHEAT_REDIRECT_URI"],
        "client_id": env["NUHEAT_CLIENT_ID"],
        "client_secret": env["NUHEAT_CLIENT_SECRET"],
        "code_verifier": saved["verifier"],
    }).encode()
    tok = http_json(urllib.request.Request(TOKEN, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}))
    STATE.unlink(missing_ok=True)
    CALLBACK.unlink(missing_ok=True)

    if tok.get("refresh_token"):
        set_env("NUHEAT_REFRESH_TOKEN", tok["refresh_token"])
    print(f"Token OK: type={tok.get('token_type')} expires_in={tok.get('expires_in')}s "
          f"scope={tok.get('scope')} refresh_token={'saved to .env' if tok.get('refresh_token') else 'MISSING'}")

    auth = {"Authorization": f"Bearer {tok['access_token']}", "Accept": "application/json"}
    thermostats = http_json(urllib.request.Request(f"{API}/Thermostat", headers=auth))
    (ROOT / "notes" / "api" / "thermostat-sample.json").write_text(json.dumps(thermostats, indent=2))
    print("Thermostats (full response saved to notes/api/thermostat-sample.json):")
    print(json.dumps(thermostats, indent=2)[:3000])


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("url", "exchange"):
        sys.exit(__doc__)
    cmd_url() if sys.argv[1] == "url" else cmd_exchange(sys.argv[2] if len(sys.argv) > 2 else None)

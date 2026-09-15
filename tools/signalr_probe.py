"""Listen on the v2 change-notification hub and print every frame.

  uv run --with websockets python3 tools/signalr_probe.py [SECONDS]
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api import access_token  # noqa: E402

HUB = "https://api.mynuheat.com/v2/notificationsHost"
RS = "\x1e"


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def negotiate(token: str) -> tuple[str, dict]:
    # Docs pass the token as ?token=; ASP.NET Core's default is access_token / Bearer. Try both.
    attempts = [
        ("token query", f"{HUB}/negotiate?negotiateVersion=1&token={urllib.parse.quote(token)}", {}),
        ("bearer header", f"{HUB}/negotiate?negotiateVersion=1", {"Authorization": f"Bearer {token}"}),
    ]
    for label, url, headers in attempts:
        req = urllib.request.Request(url, data=b"", method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = json.loads(r.read())
                log(f"negotiate via {label}: HTTP {r.status} transports="
                    f"{[t.get('transport') for t in body.get('availableTransports', [])]}")
                return label, body
        except urllib.error.HTTPError as e:
            log(f"negotiate via {label}: HTTP {e.code} {e.read().decode(errors='replace')[:200]}")
    sys.exit("NEGOTIATE FAILED")


async def listen(seconds: int) -> None:
    token = access_token()
    headers = {"Authorization": f"Bearer {token}"}
    ws_base = HUB.replace("https://", "wss://")
    if "--skip-negotiate" in sys.argv:
        # Negotiate-issued ids 404'd on the socket (likely no sticky sessions); the hub may take a bare connect.
        ws_url = f"{ws_base}?{urllib.parse.urlencode({'access_token': token})}"
        log("skipping negotiate, connecting WebSocket directly")
    else:
        _, neg = negotiate(token)
        log(f"negotiate keys: {sorted(neg)} negotiateVersion={neg.get('negotiateVersion')}")
        conn = neg.get("connectionToken") or neg.get("connectionId")
        ws_url = f"{ws_base}?{urllib.parse.urlencode({'id': conn, 'access_token': token})}"

    async with websockets.connect(ws_url, additional_headers=headers, open_timeout=20) as ws:
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
        log(f"handshake reply: {(await asyncio.wait_for(ws.recv(), 20))!r}")
        await ws.send(json.dumps({"type": 1, "invocationId": "1", "target": "Subscribe", "arguments": [[2]]}) + RS)
        log(f"LISTENING for {seconds}s — subscribed to Thermostat (type 2)")

        async def ping():
            while True:
                await asyncio.sleep(15)
                await ws.send(json.dumps({"type": 6}) + RS)

        pinger = asyncio.create_task(ping())
        deadline = time.monotonic() + seconds
        try:
            while (remaining := deadline - time.monotonic()) > 0:
                try:
                    raw = await asyncio.wait_for(ws.recv(), remaining)
                except asyncio.TimeoutError:
                    break
                for frame in filter(None, raw.split(RS)):
                    msg = json.loads(frame)
                    if msg.get("type") == 6:
                        continue
                    tag = "NOTIFY" if msg.get("type") == 1 else f"frame type {msg.get('type')}"
                    log(f"{tag}: {frame}")
                    if msg.get("type") == 7:
                        return
        finally:
            pinger.cancel()
    log("DONE")


if __name__ == "__main__":
    nums = [a for a in sys.argv[1:] if a.isdigit()]
    asyncio.run(listen(int(nums[0]) if nums else 150))

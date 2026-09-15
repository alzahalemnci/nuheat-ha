"""Constants for the Nuheat Conductor integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "nuheat_conductor"

OAUTH2_AUTHORIZE: Final = "https://identity.nam.mynuheat.com/connect/authorize"
OAUTH2_TOKEN: Final = "https://identity.nam.mynuheat.com/connect/token"
OAUTH2_SCOPES: Final = ["openid", "profile", "openapi", "offline_access"]

API_BASE: Final = "https://api.mynuheat.com/api/v2"
HUB_URL: Final = "wss://api.mynuheat.com/v2/notificationsHost"

SAFETY_POLL_INTERVAL: Final = timedelta(minutes=15)
DISCONNECTED_POLL_INTERVAL: Final = timedelta(seconds=60)

# One app change produces several notices spread over ~a minute; a short window
# collapses the immediate burst without delaying the first refresh noticeably.
NOTIFY_DEBOUNCE_SECONDS: Final = 3.0
HUB_PING_SECONDS: Final = 15
# The hub binds the access token at connect time and tokens live 60 min.
HUB_RECONNECT_SECONDS: Final = 55 * 60
HUB_BACKOFF_MIN_SECONDS: Final = 5
HUB_BACKOFF_MAX_SECONDS: Final = 300

# Range the thermostat accepts (41–158 °F, per the Nuheat app); the API doesn't expose it.
DEVICE_MIN_TEMP_C: Final = 5.0
DEVICE_MAX_TEMP_C: Final = 70.0
CONF_MIN_TEMP: Final = "min_temp"
CONF_MAX_TEMP: Final = "max_temp"

PRESET_SCHEDULE: Final = "schedule"
PRESET_TEMPORARY_HOLD: Final = "temporary_hold"
PRESET_MANUAL: Final = "manual"

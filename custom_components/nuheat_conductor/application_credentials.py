"""Application credentials platform for Nuheat Conductor."""

from homeassistant.components.application_credentials import ClientCredential
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import OAUTH2_AUTHORIZE, OAUTH2_TOKEN


class NuheatOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce):
    """Auth-code flow with PKCE and the Chemelex-issued client secret."""

    def __init__(self, hass: HomeAssistant, auth_domain: str, credential: ClientCredential) -> None:
        """Initialize from the stored application credential."""
        super().__init__(
            hass,
            auth_domain,
            credential.client_id,
            OAUTH2_AUTHORIZE,
            OAUTH2_TOKEN,
            credential.client_secret,
        )
        self._name = credential.name

    @property
    def name(self) -> str:
        """Name shown when picking credentials."""
        return self._name or self.client_id


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    """Return the PKCE-enabled implementation (the default one lacks PKCE)."""
    return NuheatOAuth2Implementation(hass, auth_domain, credential)

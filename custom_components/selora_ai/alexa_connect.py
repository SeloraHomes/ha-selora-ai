"""The hub's side of the Alexa contract with Selora Connect.

Three things travel between the hub and the cloud, and none of them is a
directive — directives arrive the other way, through the view:

* ``AcceptGrant`` hands Connect the authorization code Amazon delivers on the
  directive stream. Connect exchanges it with Login with Amazon and stores that
  customer's access and refresh tokens. **The hub never sees them**, and never
  holds the skill's messaging credentials.
* A **token and an event endpoint**, fetched together. Fetching them together
  is the shape ``homeassistant/components/cloud/alexa_config.py`` uses, and it
  is what keeps the regional event-gateway URL the cloud's decision instead of
  a constant compiled into every hub — Amazon runs three of them and which one
  a customer belongs to is not something the home can know.
* ``ChangeReport``, which HA's own ``state_report`` POSTs to ``config.endpoint``
  with that token. Connect relays it to the gateway and fans it out across
  every active grant, because a household can link several Amazon accounts to
  one home and the hub holds no per-customer token to address them with.

Everything is signed with ``alexa_remote_access``'s own derived key, so Alexa's
credentials live and die with Alexa's own resource and ``key_epoch``.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Final

from aiohttp import ClientError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import SELORA_JWT_ALGORITHM, SELORA_JWT_SCOPE_PREFIX_ALEXA

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# ── The contract ──────────────────────────────────────────────────────────────
#
# Named here and nowhere else, so the paths the hub calls are one edit rather
# than a search. They follow the shape `oauth_link.py` already uses for
# `/api/v1/installations/{id}/mcp-auth-config`.
ALEXA_GRANT_PATH: Final = "/api/v1/alexa/grants"
ALEXA_TOKEN_PATH: Final = "/api/v1/alexa/token"

# Below Connect's own 5s hub deadline: a hub that spends longer than this
# waiting on the cloud has already lost the directive it was answering.
_REQUEST_TIMEOUT_S: Final = 4.0

# The outbound token is deliberately NOT minted under the audience the view
# accepts. The key is symmetric, so a hub-minted token carrying the inbound
# audience would be replayable against our own directive endpoint by anything
# that captured it in flight or out of a log. Different audience, different
# issuer: the inbound validator refuses it twice over.
_OUTBOUND_AUDIENCE: Final = "selora-connect-alexa"
_OUTBOUND_TTL_S: Final = 300

# Refresh a little before the cloud says the token dies, so a report in flight
# at the boundary is not the thing that discovers it.
_TOKEN_EXPIRY_MARGIN_S: Final = 60


class AlexaConnectError(Exception):
    """Connect could not be reached, or refused."""


class AlexaConnectClient:
    """Calls Connect on behalf of one installation."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        connect_url: str,
        installation_id: str,
        derived_key: bytes,
    ) -> None:
        """Initialize the client."""
        self._hass = hass
        self._connect_url = connect_url.rstrip("/")
        self._installation_id = installation_id
        self._derived_key = derived_key
        self._access_token: str | None = None
        self._event_endpoint: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def event_endpoint(self) -> str | None:
        """The relay URL last handed over, or None before the first fetch."""
        return self._event_endpoint

    def _mint_hub_token(self) -> str:
        """Sign a short-lived token proving this hub holds the Alexa key."""
        import jwt

        now = int(time.time())
        return jwt.encode(
            {
                "iss": self._installation_id,
                "sub": self._installation_id,
                "aud": _OUTBOUND_AUDIENCE,
                "scope": f"{SELORA_JWT_SCOPE_PREFIX_ALEXA}hub",
                "iat": now,
                "exp": now + _OUTBOUND_TTL_S,
            },
            self._derived_key,
            algorithm=SELORA_JWT_ALGORITHM,
        )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST to Connect and return the decoded object."""
        session = async_get_clientsession(self._hass)
        try:
            async with session.post(
                f"{self._connect_url}{path}",
                json={"installation_id": self._installation_id, **body},
                headers={"Authorization": f"Bearer {self._mint_hub_token()}"},
                timeout=_REQUEST_TIMEOUT_S,
            ) as response:
                if response.status >= 400:
                    # The body can carry the grant code or a token, so only the
                    # status is reported. A logged body here would put a live
                    # credential in the hub's log for anyone to read.
                    raise AlexaConnectError(f"Connect returned HTTP {response.status} for {path}")
                payload = await response.json()
        except (ClientError, TimeoutError) as err:
            raise AlexaConnectError(f"Cannot reach Connect for {path}: {err}") from err

        if not isinstance(payload, dict):
            raise AlexaConnectError(f"Connect returned a non-object body for {path}")
        return payload

    async def async_accept_grant(self, code: str) -> None:
        """Hand Amazon's grant code to Connect.

        Raises rather than returning a status: a silent failure here leaves the
        customer unable to enable the skill at all, with an
        ``AcceptGrant.Response`` telling Amazon everything went fine.
        """
        await self._post(ALEXA_GRANT_PATH, {"grant_code": code})
        # A new grant is a new fan-out target, and the token that was cached
        # was fetched before it existed.
        self.async_invalidate_access_token()

    async def async_get_access_token(self) -> str:
        """Return a token for the event relay, fetching one when stale.

        The event endpoint arrives on the same response, which is why there is
        no separate call for it — the pair is what the cloud decides together.
        """
        if self._access_token is not None and time.monotonic() < self._token_expires_at:
            return self._access_token

        payload = await self._post(ALEXA_TOKEN_PATH, {})
        token = payload.get("access_token")
        endpoint = payload.get("event_endpoint")
        if not isinstance(token, str) or not token:
            raise AlexaConnectError("Connect returned no access_token")
        if not isinstance(endpoint, str) or not endpoint:
            # Without it there is nowhere to POST a ChangeReport, and HA's
            # sender asserts on a None endpoint rather than reporting it.
            raise AlexaConnectError("Connect returned no event_endpoint")

        expires_in = payload.get("expires_in")
        ttl = float(expires_in) if isinstance(expires_in, (int, float)) else _OUTBOUND_TTL_S
        self._access_token = token
        self._event_endpoint = endpoint
        self._token_expires_at = time.monotonic() + max(0.0, ttl - _TOKEN_EXPIRY_MARGIN_S)
        return token

    def async_invalidate_access_token(self) -> None:
        """Drop the cached token, keeping the endpoint.

        The endpoint is regional and does not go stale with the credential, and
        HA invalidates the token mid-retry — clearing the endpoint there would
        turn a recoverable ``INVALID_ACCESS_TOKEN_EXCEPTION`` into an assertion
        failure on the retry.
        """
        self._access_token = None
        self._token_expires_at = 0.0


def build_client(hass: HomeAssistant, domain_data: dict[str, Any]) -> AlexaConnectClient | None:
    """Build a client from the linked installation, or None when unlinked."""
    from .const import (
        CONF_SELORA_ALEXA_JWT_KEY,
        CONF_SELORA_CONNECT_URL,
        CONF_SELORA_INSTALLATION_ID,
        DEFAULT_SELORA_CONNECT_URL,
        DOMAIN,
    )
    from .selora_auth import decode_jwt_key

    for entry in hass.config_entries.async_entries(DOMAIN):
        key_b64 = entry.data.get(CONF_SELORA_ALEXA_JWT_KEY)
        installation_id = entry.data.get(CONF_SELORA_INSTALLATION_ID)
        if not key_b64 or not installation_id:
            continue
        return AlexaConnectClient(
            hass,
            connect_url=entry.data.get(CONF_SELORA_CONNECT_URL) or DEFAULT_SELORA_CONNECT_URL,
            installation_id=str(installation_id),
            derived_key=decode_jwt_key(key_b64),
        )
    return None


__all__ = ["AlexaConnectClient", "AlexaConnectError", "build_client"]

"""Alexa Smart Home configuration for the Selora directive endpoint.

``homeassistant.components.alexa`` ships the certified device and capability
mapping — ``capabilities.py`` and ``entities.py``, ~120 KB maintained upstream
against Amazon's own conformance tests. The only thing it asks of an embedder
is an :class:`AbstractConfig`, so that is the whole reuse boundary: this file
answers the questions ``async_handle_message`` and ``state_report`` put to the
config, and nothing here knows what a capability is.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.alexa.config import AbstractConfig, AlexaConfigStore
from homeassistant.components.alexa.errors import NoTokenAvailable
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, callback

from .alexa_connect import AlexaConnectClient, AlexaConnectError
from .const import DOMAIN

if TYPE_CHECKING:
    from yarl import URL

_LOGGER = logging.getLogger(__name__)

# An interface whose ``supported_locales`` does not list ours is dropped from
# the discovery payload entirely, so this value decides what the customer can
# say to a device rather than merely what language something is written in.
# The skill has only the North America endpoint enabled, so the answer is one
# of two English locales, and the hub's self-declared country is the only
# signal it holds for which. A directive carries no locale of its own.
_DEFAULT_LOCALE = "en-US"
_COUNTRY_LOCALES = {"CA": "en-CA", "US": "en-US"}

# HA keys exposure per assistant, and every websocket command that lets a
# person CHANGE it is `vol.In(KNOWN_ASSISTANTS)` — ("cloud.alexa",
# "cloud.google_assistant", "conversation"). A private key of our own would
# therefore be unreadable and unwritable through HA's own Voice assistants →
# Expose page, leaving a 300-endpoint ceiling with no supported way for a
# homeowner to stay under it short of a second exposure UI built here.
#
# So Alexa exposure is Alexa exposure: this reads and writes the same settings
# Home Assistant Cloud's Alexa would. A household running BOTH skills shares
# one set of toggles, which is the honest answer to a question that is the
# same question asked twice — and they would be seeing every device twice in
# the Alexa app regardless.
ALEXA_ASSISTANT = "cloud.alexa"


class _SeloraAlexaConfigStore(AlexaConfigStore):
    """The base store under a key that is ours alone."""

    _STORAGE_KEY = f"{DOMAIN}.alexa"


class SeloraAlexaConfig(AbstractConfig):
    """The config ``async_handle_message`` and ``state_report`` read."""

    def __init__(
        self,
        hass: HomeAssistant,
        installation_id: str | None,
        connect: AlexaConnectClient | None = None,
    ) -> None:
        """Initialize the config.

        ``installation_id`` builds a stable per-endpoint ``customIdentifier``.
        ``connect`` is absent on an unlinked hub, which is what turns off
        ``supports_auth`` and proactive reporting rather than letting either
        fail at the moment it is needed.
        """
        super().__init__(hass)
        self._installation_id = installation_id or ""
        self._connect = connect
        self._report_start: asyncio.Task[None] | None = None

    async def async_initialize(self) -> None:
        """Load the store and make exposure answerable before any directive."""
        await super().async_initialize()
        # The base keys its store on the `alexa` domain, which the native
        # Alexa integration and Home Assistant Cloud's Alexa also write. That
        # store holds the authorization flag that decides whether proactive
        # reporting runs, so on a hub with two skills each one's directives —
        # and each one's report failures — would silently set and clear the
        # other's, leaving reporting started or suppressed after a restart on
        # the strength of a skill nobody was using. Superseded rather than
        # prevented: letting the base build its own first costs one read of a
        # file we never write, and keeps whatever else it initializes.
        self._store = _SeloraAlexaConfigStore(self.hass)
        await self._store.async_load()
        self._async_bootstrap_exposure()

    @callback
    def _async_bootstrap_exposure(self) -> None:
        """Opt this assistant into exposing entities, once and only once.

        ``async_should_expose`` consults ``async_get_expose_new_entities``,
        which falls back to ``DEFAULT_EXPOSED_ASSISTANT.get(assistant, False)``
        — and that mapping holds only ``conversation``. So on a hub that has
        never run Home Assistant Cloud's Alexa, the honest-looking call answers
        False for every entity and ``Discover`` returns an empty endpoint list:
        a 200 that reads as success and tells the customer they have no
        devices.

        Worse, it is not merely wrong once. ``async_should_expose`` WRITES the
        answer into each entity's registry options as it goes, so a Discover
        that runs before this pins every entity to False permanently — and
        flipping the preference afterwards changes nothing, because the
        per-entity setting now wins. That is why this runs at initialization
        rather than lazily: by the time a directive is being answered it is
        already too late.

        What decides is whether a preference has been RECORDED for this
        assistant, which is the only thing that distinguishes "nobody has said"
        from "somebody said no". Per-entity settings do not answer it — a
        person can turn off exposing new entities without ever overriding an
        individual one — and reading emptiness there would silently flip a
        privacy choice back on, exposing default-domain devices they meant to
        keep to themselves. Setting the preference records it, so this is
        self-limiting: it can fire at most once on a hub.

        If core moves the attribute this refuses to guess. The cost is an
        assistant left un-opted-in, which shows up as a Discover that matches
        nothing and says so in the log; the cost of guessing wrong is a
        household's devices answering a voice assistant they switched off.
        """
        exposed = self._exposed_entities
        recorded = getattr(exposed, "_assistants", None)
        if recorded is None:
            _LOGGER.warning(
                "Cannot tell whether Alexa exposure has been configured on this "
                "hub; leaving it alone. Set Settings → Voice assistants → Expose "
                "new entities for Alexa if discovery returns nothing"
            )
            return
        if ALEXA_ASSISTANT in recorded:
            return
        exposed.async_set_expose_new_entities(ALEXA_ASSISTANT, True)

    @property
    def _exposed_entities(self) -> Any:
        """HA's exposed-entity store.

        Reached through ``hass.data`` because ``exposed_entities`` exports no
        module-level setter for the expose-new preference — only the websocket
        command has one, and that is admin-gated and locked to
        ``KNOWN_ASSISTANTS``. This is the same object its own public helpers
        resolve.
        """
        from homeassistant.components.homeassistant.const import DATA_EXPOSED_ENTITIES

        return self.hass.data[DATA_EXPOSED_ENTITIES]

    # ── Identity and presentation ────────────────────────────────────────────

    @property
    def locale(self) -> str | None:
        """Return the locale used to filter capability interfaces."""
        country = (self.hass.config.country or "").upper()
        return _COUNTRY_LOCALES.get(country, _DEFAULT_LOCALE)

    @callback
    def user_identifier(self) -> str:
        """Return a stable identifier for the account behind this config.

        It lands in each endpoint's ``customIdentifier`` as
        ``<identifier>-<entity_id>``, which Alexa retains across discoveries,
        so it must not change for a hub that has already been discovered. The
        installation id is stable for the life of the installation; an unlinked
        hub has none, and an empty string is what HA's own ``AlexaConfig``
        returns in that case.
        """
        return self._installation_id

    @callback
    def should_expose(self, entity_id: str) -> bool:
        """Whether an entity is offered to Alexa.

        HA's own answer, which applies ``DEFAULT_EXPOSED_DOMAINS`` — climate,
        cover, fan, humidifier, light, media_player, scene, switch, todo,
        vacuum, water_heater — plus the useful binary_sensor and sensor device
        classes, and honours any per-entity override a person set on the Voice
        assistants page.

        The split that matters is ``scene`` in and ``automation`` out. A scene
        maps to ``SceneController`` and is something a person genuinely says
        out loud; an automation surfaced as a switch is clutter — and clutter
        is not free here, because Amazon caps a customer at 300 endpoints and
        one real installation already produces enough without them.

        Selora's own exclude label is deliberately NOT consulted. It means "do
        not raise automation suggestions about this", and wiring it to voice
        would make a device stop answering Alexa because somebody tidied their
        suggestions — an accidental coupling of the kind the whole design of a
        separate Pangolin resource exists to avoid.
        """
        return async_should_expose(self.hass, ALEXA_ASSISTANT, entity_id)

    # ── Account linking ──────────────────────────────────────────────────────

    @property
    def supports_auth(self) -> bool:
        """Whether ``AcceptGrant`` is answered rather than ignored.

        ``AbstractConfig`` returns False and ``async_accept_grant`` raises, and
        because raw directives are forwarded to the hub there is no cloud-side
        interception to fall back on. Left at the default, the grant is
        silently dropped and the customer cannot enable the skill at all.
        """
        return self._connect is not None

    async def async_accept_grant(self, code: str) -> str | None:
        """Hand Amazon's authorization code to Connect.

        Connect exchanges it with Login with Amazon and stores that customer's
        access and refresh tokens; the hub never learns either, nor the skill's
        messaging credentials. Raising is deliberate — ``async_api_accept_grant``
        would otherwise return ``AcceptGrant.Response`` to Amazon, reporting a
        linkage that does not exist and leaving the failure to surface later as
        a skill that simply does not work.
        """
        if self._connect is None:
            raise NoTokenAvailable("Alexa is not linked to Selora Connect")
        await self._connect.async_accept_grant(code)
        return None

    # ── Proactive state ──────────────────────────────────────────────────────

    @property
    def should_report_state(self) -> bool:
        """Whether proactive reporting should be STARTED.

        ``authorized`` is set by the first directive that arrives, so this
        turns on once Amazon has actually talked to this hub rather than on
        configuration alone.

        The ``and not self.is_reporting_states`` is load-bearing, and it is
        why ``set_authorized`` below is overridden alongside it. On a
        successful ``AcceptGrant`` — which is normally the very first directive
        a hub ever sees — two things try to start reporting in the same turn:
        ``set_authorized`` at the top of ``async_handle_message``, through the
        locked method that records its unsubscribe, and then
        ``async_api_accept_grant`` (``handlers.py:164``), through the
        module-level ``async_enable_proactive_mode``, whose return value it
        throws away. The second registration is a second event-bus listener
        that nothing holds a handle to: every state change would emit two
        ChangeReports, and one listener would survive unload with no way to
        cancel it. Reading False once reporting is already running is what
        makes the second call a no-op — as does a start already scheduled, since
        the task owns the registration from the moment it is created.
        """
        return (
            self._connect is not None
            and self.authorized
            and not self.is_reporting_states
            and self._report_start is None
        )

    async def set_authorized(self, authorized: bool) -> None:
        """Record the authorization flag and own the reporting lifecycle.

        The base compares ``should_report_state`` against
        ``is_reporting_states`` and acts on any difference, which cannot work
        once the former excludes the latter — it would read every running
        reporter as a reason to stop. Asking the two questions separately says
        the same thing for the base's definition and the right thing for this
        one.

        Starting is deferred to a background task rather than awaited here,
        because this runs at the top of EVERY directive and starting reporting
        means a round trip to the cloud. On the first ``AcceptGrant`` — which
        is normally the first directive a hub ever sees — awaiting it put a
        token fetch in front of the grant hand-over, two serial calls at up to
        four seconds each against Connect's five-second hub deadline, so
        account linking could fail with neither call having timed out. Nothing
        about answering a directive needs reporting to be running first.
        """
        self._store.set_authorized(authorized)
        if not authorized:
            await self.async_disable_proactive_mode()
            return
        if self._connect is None or self.is_reporting_states or self._report_start is not None:
            return
        self._report_start = self.hass.async_create_background_task(
            self._async_start_reporting(),
            "selora_ai_alexa_proactive_start",
            eager_start=False,
        )

    async def _async_start_reporting(self) -> None:
        """Turn proactive reporting on, off the directive path."""
        try:
            await self.async_enable_proactive_mode()
        except Exception:  # noqa: BLE001 — a failed start must not kill the task
            _LOGGER.warning("Could not start Alexa proactive reporting", exc_info=True)
        finally:
            # Cleared either way, so a later directive retries a failed start.
            self._report_start = None

    async def async_disable_proactive_mode(self) -> None:
        """Stop reporting, including a start that has not landed yet.

        ``hass.async_create_background_task`` survives a config-entry unload,
        so a start still in flight would otherwise register its listener after
        teardown had finished — on a config nothing holds a reference to and
        nothing can cancel.
        """
        if (pending := self._report_start) is not None:
            self._report_start = None
            pending.cancel()
        await super().async_disable_proactive_mode()

    @property
    def endpoint(self) -> str | URL | None:
        """Where ``ChangeReport`` is POSTed.

        Connect's relay, not Amazon's gateway: the hub holds no per-customer
        token, and a household can link several Amazon accounts to one home, so
        the fan-out across grants is the cloud's to do. The value is whatever
        arrived alongside the last token — Amazon runs three regional
        gateways and which one a customer belongs to is not something a home
        can know, which is why it is fetched rather than compiled in.
        """
        return self._connect.event_endpoint if self._connect else None

    async def async_get_access_token(self) -> str | None:
        """Return a token for Connect's relay, fetching one when stale."""
        if self._connect is None:
            raise NoTokenAvailable("Alexa is not linked to Selora Connect")
        try:
            return await self._connect.async_get_access_token()
        except AlexaConnectError as err:
            # NoTokenAvailable is what state_report catches to unset the
            # authorized flag and stop reporting; an AlexaConnectError escaping
            # here would instead propagate out of async_enable_proactive_mode.
            raise NoTokenAvailable(str(err)) from err

    @callback
    def async_invalidate_access_token(self) -> None:
        """Drop the cached token so the next report fetches a fresh one."""
        if self._connect is not None:
            self._connect.async_invalidate_access_token()

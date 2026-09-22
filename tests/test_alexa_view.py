"""The Alexa Smart Home directive endpoint.

The view is deliberately thin — it authenticates, throttles, and hands the
body to ``homeassistant.components.alexa`` — so what is worth testing is the
three decisions that are ours and the one contract Connect depends on.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import CoreState
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai import alexa_view

pytest.importorskip(
    "turbojpeg",
    reason="alexa.entities imports the camera integration, which needs PyTurboJPEG",
)

from custom_components.selora_ai import alexa_config  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────


def _discover(token: str = "alexa-access-token") -> dict[str, Any]:
    return {
        "directive": {
            "header": {
                "namespace": "Alexa.Discovery",
                "name": "Discover",
                "payloadVersion": "3",
                "messageId": "msg-1",
            },
            "payload": {"scope": {"type": "BearerToken", "token": token}},
        }
    }


def _turn_on(entity_id: str, token: str = "alexa-access-token") -> dict[str, Any]:
    return {
        "directive": {
            "header": {
                "namespace": "Alexa.PowerController",
                "name": "TurnOn",
                "payloadVersion": "3",
                "messageId": "msg-2",
                "correlationToken": "corr-1",
            },
            "endpoint": {
                "scope": {"type": "BearerToken", "token": token},
                "endpointId": entity_id.replace(".", "#"),
                "cookie": {},
            },
            "payload": {},
        }
    }


def _report_state(entity_id: str, token: str = "alexa-access-token") -> dict[str, Any]:
    return {
        "directive": {
            "header": {
                "namespace": "Alexa",
                "name": "ReportState",
                "payloadVersion": "3",
                "messageId": "msg-3",
                "correlationToken": "corr-2",
            },
            "endpoint": {
                "scope": {"type": "BearerToken", "token": token},
                "endpointId": entity_id.replace(".", "#"),
                "cookie": {},
            },
            "payload": {},
        }
    }


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    """The limiter is module-global, so its buckets outlive a test."""
    alexa_view._directive_limiter._hits.clear()
    alexa_view._directive_limiter._last_sweep = 0.0


async def _register(hass: Any) -> None:
    """Set up what the view needs and register it.

    ``homeassistant`` carries the exposed-entity store ``should_expose`` reads.
    Core always sets it up before any config entry, so this is reproducing the
    real environment rather than arranging one.
    """
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "http", {})
    alexa_view.async_register_view(hass)
    await hass.async_block_till_done()


@pytest.fixture
async def alexa_client(hass: Any, hass_client: Any) -> Any:
    """An authenticated client with the directive endpoint registered."""
    await _register(hass)
    return await hass_client()


# ── The token is not in a fixed place ─────────────────────────────────────────


def test_grant_token_is_read_from_each_of_the_three_shapes() -> None:
    """An endpoint written against ``endpoint.scope`` alone passes control
    tests and fails certification, because discovery is among the first things
    Amazon's review exercises."""
    assert alexa_view.grant_token(_turn_on("light.kitchen", "endpoint-tok")) == "endpoint-tok"
    assert alexa_view.grant_token(_discover("discover-tok")) == "discover-tok"

    accept_grant = {
        "directive": {
            "header": {
                "namespace": "Alexa.Authorization",
                "name": "AcceptGrant",
                "payloadVersion": "3",
                "messageId": "msg-4",
            },
            "payload": {
                "grant": {"type": "OAuth2.AuthorizationCode", "code": "auth-code"},
                "grantee": {"type": "BearerToken", "token": "grantee-tok"},
            },
        }
    }
    assert alexa_view.grant_token(accept_grant) == "grantee-tok"
    assert accept_grant["directive"]["payload"]["grant"]["code"] == "auth-code"


def test_grant_token_is_none_when_the_directive_carries_one_nowhere() -> None:
    assert alexa_view.grant_token({"directive": {"header": {}, "payload": {}}}) is None
    assert alexa_view.grant_token({}) is None


# ── The health check's contract ───────────────────────────────────────────────


async def test_an_unauthenticated_request_is_401_on_both_methods(
    hass: Any, hass_client_no_auth: Any
) -> None:
    """Connect's health check authenticates by *expecting* a 401, and the
    Pangolin rule accepts the path without regard to method — so a 405 on GET
    would read as an unhealthy target."""
    await _register(hass)
    client = await hass_client_no_auth()

    assert (await client.post(alexa_view.ALEXA_URL, json=_discover())).status == 401
    assert (await client.get(alexa_view.ALEXA_URL)).status == 401


async def test_the_health_check_is_never_throttled(
    hass: Any, hass_client: Any, hass_client_no_auth: Any
) -> None:
    """A limiter counting auth failures would eventually answer the probe with
    429 instead of the 401 it waits for, marking the target unhealthy and
    serving 503 on the voice path. Nothing counts an unauthenticated request."""
    await _register(hass)
    unauth = await hass_client_no_auth()

    for _ in range(alexa_view._RATE_LIMIT_MAX_DIRECTIVES + 20):
        assert (await unauth.post(alexa_view.ALEXA_URL, json=_discover())).status == 401

    assert (await unauth.get(alexa_view.ALEXA_URL)).status == 401
    assert alexa_view._directive_limiter._hits == {}


# ── The throttle is keyed per grant ───────────────────────────────────────────


def test_two_grants_from_one_home_do_not_share_a_budget() -> None:
    """Every cloud call arrives through the same tunnel, so a per-IP key would
    give two adults in one house a single bucket between them."""
    first = alexa_view._limiter_key(_discover("adult-one"), "connect-sub")
    second = alexa_view._limiter_key(_discover("adult-two"), "connect-sub")
    assert first != second
    assert first.startswith("grant:")
    # The live credential itself is never the key.
    assert "adult-one" not in first


def test_a_tokenless_directive_falls_back_to_the_transport_caller() -> None:
    tokenless = {"directive": {"header": {}, "payload": {}}}
    assert alexa_view._limiter_key(tokenless, "connect-sub") == "auth:connect-sub"


async def test_a_grant_past_its_ceiling_is_refused(alexa_client: Any) -> None:
    for _ in range(alexa_view._RATE_LIMIT_MAX_DIRECTIVES):
        assert (await alexa_client.post(alexa_view.ALEXA_URL, json=_discover("a"))).status == 200

    refused = await alexa_client.post(alexa_view.ALEXA_URL, json=_discover("a"))
    assert refused.status == 429
    assert refused.headers["Retry-After"] == str(alexa_view._RATE_LIMIT_WINDOW_S)

    # The other account in the house is untouched.
    assert (await alexa_client.post(alexa_view.ALEXA_URL, json=_discover("b"))).status == 200


# ── Directive shape ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"not_a_directive": {}},
        {"directive": {"header": []}},
        {"directive": {"header": {"payloadVersion": "2", "namespace": "n", "name": "x"}}},
        {"directive": {"header": {"payloadVersion": "3"}}},
        # No payload: AlexaDirective reads it unconditionally, so this reaches
        # the error path, which builds the same directive and raises again.
        {
            "directive": {
                "header": {"payloadVersion": "3", "namespace": "Alexa", "name": "ReportState"}
            }
        },
    ],
)
async def test_a_malformed_directive_is_refused_at_the_door(alexa_client: Any, body: Any) -> None:
    """``async_handle_message`` asserts the payload version, so an unguarded
    body is a 500 — or a KeyError deeper in under ``python -O``."""
    assert (await alexa_client.post(alexa_view.ALEXA_URL, json=body)).status == 400


async def test_a_non_json_body_is_refused(alexa_client: Any) -> None:
    resp = await alexa_client.post(
        alexa_view.ALEXA_URL, data=b"{", headers={"Content-Type": "application/json"}
    )
    assert resp.status == 400


# ── The directive actually reaches HA's mapping ───────────────────────────────


async def test_discover_returns_endpoints_and_the_handler_timing(
    hass: Any, alexa_client: Any
) -> None:
    hass.states.async_set(
        "light.kitchen", "on", {"friendly_name": "Kitchen", "supported_color_modes": ["onoff"]}
    )
    hass.states.async_set("switch.porch", "off", {"friendly_name": "Porch"})

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_discover())
    assert resp.status == 200

    body = await resp.json()
    header = body["event"]["header"]
    assert header["namespace"] == "Alexa.Discovery"
    assert header["name"] == "Discover.Response"

    endpoint_ids = {e["endpointId"] for e in body["event"]["payload"]["endpoints"]}
    assert "light#kitchen" in endpoint_ids
    assert "switch#porch" in endpoint_ids

    # The gate turns on the handler's own cost, which an end-to-end number
    # cannot isolate — so it is reported where a probe can read it.
    assert float(resp.headers["X-Selora-Handler-Ms"]) >= 0.0


async def test_a_control_directive_calls_the_service(hass: Any, alexa_client: Any) -> None:
    hass.states.async_set(
        "light.kitchen", "off", {"friendly_name": "Kitchen", "supported_color_modes": ["onoff"]}
    )
    calls: list[Any] = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_turn_on("light.kitchen"))
    assert resp.status == 200
    assert (await resp.json())["event"]["header"]["name"] == "Response"
    assert [c.data["entity_id"] for c in calls] == ["light.kitchen"]


async def test_report_state_answers_for_one_endpoint(hass: Any, alexa_client: Any) -> None:
    hass.states.async_set(
        "light.kitchen", "on", {"friendly_name": "Kitchen", "supported_color_modes": ["onoff"]}
    )

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_report_state("light.kitchen"))
    assert resp.status == 200
    body = await resp.json()
    assert body["event"]["header"]["name"] == "StateReport"
    assert body["context"]["properties"]


async def test_an_unknown_endpoint_is_an_alexa_error_not_an_http_one(
    alexa_client: Any,
) -> None:
    """Connect relays the body; a 500 would surface to the customer as a
    generic failure with nothing to explain it."""
    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_turn_on("light.nonexistent"))
    assert resp.status == 200
    assert (await resp.json())["event"]["header"]["name"] == "ErrorResponse"


# ── Exposure ──────────────────────────────────────────────────────────────────


async def test_auxiliary_and_never_exposed_entities_are_withheld(
    hass: Any, alexa_client: Any
) -> None:
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    diagnostic = registry.async_get_or_create(
        "sensor", "demo", "diag-1", suggested_object_id="radio_signal"
    )
    registry.async_update_entity(diagnostic.entity_id, entity_category=EntityCategory.DIAGNOSTIC)
    hidden = registry.async_get_or_create(
        "switch", "demo", "hidden-1", suggested_object_id="tucked_away"
    )
    registry.async_update_entity(hidden.entity_id, hidden_by=er.RegistryEntryHider.USER)

    hass.states.async_set(diagnostic.entity_id, "-55")
    hass.states.async_set(hidden.entity_id, "off")
    hass.states.async_set("switch.porch", "off", {"friendly_name": "Porch"})
    hass.states.async_set("group.all_locks", "unlocked", {"friendly_name": "All locks"})

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_discover())
    endpoint_ids = {e["endpointId"] for e in (await resp.json())["event"]["payload"]["endpoints"]}

    assert "switch#porch" in endpoint_ids
    assert diagnostic.entity_id.replace(".", "#") not in endpoint_ids
    assert hidden.entity_id.replace(".", "#") not in endpoint_ids
    assert "group#all_locks" not in endpoint_ids


async def test_an_entity_with_no_registry_entry_is_still_exposed(
    hass: Any, alexa_client: Any
) -> None:
    """A YAML-declared light or a template entity has no registry entry, and
    is ordinary rather than auxiliary."""
    hass.states.async_set("switch.yaml_only", "off", {"friendly_name": "YAML only"})

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_discover())
    endpoint_ids = {e["endpointId"] for e in (await resp.json())["event"]["payload"]["endpoints"]}
    assert "switch#yaml_only" in endpoint_ids


# ── Registration ──────────────────────────────────────────────────────────────


async def test_registration_is_idempotent_across_reloads(hass: Any) -> None:
    """Setup re-runs on every config-entry reload and aiohttp has no
    unregister, so a second route would live for the life of the process."""
    assert await async_setup_component(hass, "http", {})
    alexa_view.async_register_view(hass)
    before = len(list(hass.http.app.router.routes()))

    alexa_view.async_register_view(hass)
    assert len(list(hass.http.app.router.routes())) == before

    routed = {getattr(r, "canonical", None) for r in hass.http.app.router.resources()}
    assert alexa_view.ALEXA_URL in routed


# ── Exposure policy ───────────────────────────────────────────────────────────


async def test_scenes_are_exposed_and_automations_are_not(hass: Any, alexa_client: Any) -> None:
    """The split that makes the 300-endpoint ceiling survivable. A scene maps
    to SceneController and is something a person says out loud; an automation
    surfaced as a switch is clutter that consumes the same budget."""
    hass.states.async_set("scene.movie_night", "scening", {"friendly_name": "Movie night"})
    hass.states.async_set("automation.porch_at_dusk", "on", {"friendly_name": "Porch at dusk"})

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_discover())
    endpoint_ids = {e["endpointId"] for e in (await resp.json())["event"]["payload"]["endpoints"]}

    assert "scene#movie_night" in endpoint_ids
    assert "automation#porch_at_dusk" not in endpoint_ids


async def test_a_hub_that_never_ran_cloud_alexa_still_discovers(
    hass: Any, alexa_client: Any
) -> None:
    """DEFAULT_EXPOSED_ASSISTANT holds only ``conversation``, so without the
    bootstrap every entity answers False and Discover returns an empty list —
    a 200 that reads as success and tells the customer they own no devices."""
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_discover())
    endpoints = (await resp.json())["event"]["payload"]["endpoints"]
    assert [e["endpointId"] for e in endpoints] == ["light#kitchen"]


async def test_an_explicit_expose_new_choice_is_never_overwritten(
    hass: Any, hass_client: Any
) -> None:
    """Somebody who turned Alexa exposure off must not have it turned back on
    by linking a second Alexa skill — that is their devices answering an
    assistant they switched off."""
    from homeassistant.components.homeassistant.const import DATA_EXPOSED_ENTITIES

    await _register(hass)
    hass.data[DATA_EXPOSED_ENTITIES].async_set_expose_new_entities(
        alexa_config.ALEXA_ASSISTANT, False
    )
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})

    client = await hass_client()
    body = await (await client.post(alexa_view.ALEXA_URL, json=_discover())).json()
    assert body["event"]["payload"]["endpoints"] == []


async def test_per_entity_overrides_are_not_an_expose_new_preference(
    hass: Any, hass_client: Any
) -> None:
    """The distinction the bootstrap turns on. A person can override one
    entity without ever saying anything about new ones, so reading per-entity
    settings as a preference would leave discovery empty on a hub whose owner
    had merely hidden a single lamp."""
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    await _register(hass)
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
    hass.states.async_set("switch.porch", "off", {"friendly_name": "Porch"})
    async_expose_entity(hass, alexa_config.ALEXA_ASSISTANT, "light.kitchen", False)

    client = await hass_client()
    resp = await client.post(alexa_view.ALEXA_URL, json=_discover())
    endpoint_ids = {e["endpointId"] for e in (await resp.json())["event"]["payload"]["endpoints"]}

    assert "light#kitchen" not in endpoint_ids
    assert "switch#porch" in endpoint_ids


async def test_a_deliberate_override_wins_over_the_domain_default(
    hass: Any, alexa_client: Any
) -> None:
    """The Voice assistants page has the last word — which is what makes it a
    usable answer to the endpoint ceiling rather than a fixed policy."""
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    hass.states.async_set("automation.porch_at_dusk", "on", {"friendly_name": "Porch at dusk"})
    async_expose_entity(hass, alexa_config.ALEXA_ASSISTANT, "automation.porch_at_dusk", True)

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_discover())
    endpoint_ids = {e["endpointId"] for e in (await resp.json())["event"]["payload"]["endpoints"]}
    assert "automation#porch_at_dusk" in endpoint_ids


# ── Errors are Alexa's shape, never HTTP's ────────────────────────────────────


async def test_a_starting_hub_errors_rather_than_discovering_nothing(
    hass: Any, alexa_client: Any
) -> None:
    """Amazon removes endpoints only through an explicit DeleteReport, so an
    empty success during a restart is never corrected — the customer is simply
    told their devices are gone."""
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
    hass.set_state(CoreState.starting)

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_discover())
    assert resp.status == 200
    body = await resp.json()

    assert body["event"]["header"]["name"] == "ErrorResponse"
    assert body["event"]["payload"]["type"] == "BRIDGE_UNREACHABLE"
    assert "endpoints" not in body["event"].get("payload", {})


async def test_an_endpoint_targeted_error_echoes_its_correlation_token(
    hass: Any, alexa_client: Any
) -> None:
    """Without both, Alexa cannot match the error to its cause and reports it
    as malformed — losing the one thing the error existed to carry."""
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
    hass.set_state(CoreState.starting)

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_turn_on("light.kitchen"))
    body = await resp.json()

    assert body["event"]["header"]["name"] == "ErrorResponse"
    assert body["event"]["header"]["correlationToken"] == "corr-1"
    assert body["event"]["endpoint"]["endpointId"] == "light#kitchen"


async def test_a_handler_that_raises_is_still_an_alexa_error(
    hass: Any, alexa_client: Any, monkeypatch: Any
) -> None:
    """Connect relays the body; a bare 5xx reaches the customer as a generic
    failure with nothing in it to say which directive broke."""
    import homeassistant.components.alexa.smart_home as smart_home

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("something gave way")

    monkeypatch.setattr(smart_home, "async_handle_message", _boom)

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_turn_on("light.kitchen"))
    assert resp.status == 200
    body = await resp.json()
    assert body["event"]["payload"]["type"] == "BRIDGE_UNREACHABLE"
    assert body["event"]["header"]["correlationToken"] == "corr-1"


# ── Compression ───────────────────────────────────────────────────────────────


async def test_a_discover_response_is_compressed(hass: Any, alexa_client: Any) -> None:
    """Transfer is ~97% of Discover's end-to-end cost at the tunnel's measured
    ~800 KB/s, and this body gzips about 20:1."""
    for index in range(40):
        hass.states.async_set(
            f"light.lamp_{index}",
            "on",
            {"friendly_name": f"Lamp {index}", "supported_color_modes": ["brightness"]},
        )

    resp = await alexa_client.post(
        alexa_view.ALEXA_URL, json=_discover(), headers={"Accept-Encoding": "gzip"}
    )
    assert resp.status == 200
    assert resp.headers.get("Content-Encoding") == "gzip"
    assert len((await resp.json())["event"]["payload"]["endpoints"]) == 40


async def test_a_control_response_is_left_uncompressed(hass: Any, alexa_client: Any) -> None:
    """A few hundred bytes cost more to frame than they save."""
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
    hass.services.async_register("light", "turn_on", lambda call: None)

    resp = await alexa_client.post(
        alexa_view.ALEXA_URL, json=_turn_on("light.kitchen"), headers={"Accept-Encoding": "gzip"}
    )
    assert resp.status == 200
    assert "Content-Encoding" not in resp.headers


async def test_a_caller_that_cannot_decompress_still_gets_an_answer(
    hass: Any, alexa_client: Any
) -> None:
    """No ``force``, so aiohttp negotiates rather than assuming."""
    for index in range(40):
        hass.states.async_set(f"light.lamp_{index}", "on", {"friendly_name": f"Lamp {index}"})

    resp = await alexa_client.post(
        alexa_view.ALEXA_URL, json=_discover(), headers={"Accept-Encoding": "identity"}
    )
    assert resp.status == 200
    assert "Content-Encoding" not in resp.headers
    assert len((await resp.json())["event"]["payload"]["endpoints"]) == 40


async def test_a_home_with_nothing_to_map_reports_the_bridge_unreachable(
    alexa_client: Any,
) -> None:
    """No states at all is a hub not in a fit state to answer, and Amazon
    treats an empty success as authoritative — nothing later corrects it."""
    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_discover())
    body = await resp.json()

    assert body["event"]["header"]["name"] == "ErrorResponse"
    assert body["event"]["payload"]["type"] == "BRIDGE_UNREACHABLE"


async def test_a_home_that_exposes_nothing_gets_a_real_empty_answer(
    hass: Any, hass_client: Any
) -> None:
    """The opposite case, and it must not be confused with the one above: a
    bridge error here sends the customer chasing a network fault instead of
    the Expose page they actually changed."""
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    await _register(hass)
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
    async_expose_entity(hass, alexa_config.ALEXA_ASSISTANT, "light.kitchen", False)

    client = await hass_client()
    body = await (await client.post(alexa_view.ALEXA_URL, json=_discover())).json()

    assert body["event"]["header"]["name"] == "Discover.Response"
    assert body["event"]["payload"]["endpoints"] == []


# ── Whose authority a directive runs under ────────────────────────────────────


async def test_a_home_assistant_token_keeps_its_user_on_the_context(
    hass: Any, alexa_client: Any
) -> None:
    """HA's own Alexa view passes the authenticated user through. Without it a
    restricted account gets a system context for the asking, and every
    permission policy downstream stops seeing who is driving."""
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen"})
    calls: list[Any] = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    resp = await alexa_client.post(alexa_view.ALEXA_URL, json=_turn_on("light.kitchen"))
    assert resp.status == 200
    assert calls[0].context.user_id is not None


def test_a_connect_token_runs_as_the_system() -> None:
    """Its subject is not an HA user, so a context naming it makes every
    service call raise UnknownUser and fail the directive outright."""
    from custom_components.selora_ai.selora_auth import SeloraAuthContext

    connect = SeloraAuthContext(
        user_id="connect-subject", email=None, is_admin=True, auth_type="selora_jwt"
    )
    assert alexa_view._directive_context(connect).user_id is None

    ha_user = SeloraAuthContext(user_id="abc123", email=None, is_admin=False, auth_type="ha_token")
    assert alexa_view._directive_context(ha_user).user_id == "abc123"

    # The defensive value `authenticate_request` falls back to when the
    # middleware left no user behind: not an id HA can resolve.
    unknown = SeloraAuthContext(user_id="unknown", email=None, is_admin=False, auth_type="ha_token")
    assert alexa_view._directive_context(unknown).user_id is None


async def test_the_authorization_flag_does_not_share_a_store_with_other_skills(
    hass: Any,
) -> None:
    """The base keys on the `alexa` domain, which the native integration and
    Home Assistant Cloud's Alexa also write. That flag decides whether
    proactive reporting runs, so a shared one has each skill starting and
    suppressing the other's."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    config = alexa_config.SeloraAlexaConfig(hass, "install-1", None)
    await config.async_initialize()

    assert config._store._store.key == "selora_ai.alexa"


async def test_a_refusal_for_a_missing_credential_says_so_once(
    hass: Any, hass_client_no_auth: Any, caplog: Any
) -> None:
    """A hub linked to Connect before its Alexa resource existed holds no
    Alexa key, and nothing but the linking exchange fetches one — so every
    directive is refused with a bare 401 and nothing to read. Once per setup,
    because this sits on the unauthenticated path that the health check hits
    several times a minute."""
    await _register(hass)
    client = await hass_client_no_auth()

    with caplog.at_level("WARNING"):
        for _ in range(5):
            assert (await client.post(alexa_view.ALEXA_URL, json=_discover())).status == 401

    said = [r for r in caplog.records if "holds no Alexa credential" in r.getMessage()]
    assert len(said) == 1


async def test_nothing_is_said_when_the_credential_is_present(
    hass: Any, hass_client_no_auth: Any, caplog: Any
) -> None:
    """A wrong token against a configured hub is an ordinary 401."""
    from custom_components.selora_ai.const import DOMAIN

    await _register(hass)
    hass.data.setdefault(DOMAIN, {})["selora_alexa_jwt_validator"] = object()
    client = await hass_client_no_auth()

    with caplog.at_level("WARNING"):
        assert (await client.post(alexa_view.ALEXA_URL, json=_discover())).status == 401
    assert not [r for r in caplog.records if "holds no Alexa credential" in r.getMessage()]

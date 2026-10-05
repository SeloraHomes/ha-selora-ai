"""Tests for reading and editing Home Assistant's YAML configuration over MCP.

A wrong write here can keep Home Assistant from booting, so most of these pin a
guard: which files, which keys, preview before write, backup, roll back on a
failed configuration check, and credentials never echoed.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall
import pytest

from custom_components.selora_ai import config_yaml
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch

CONFIG = """\
# Loads default set of integrations. Do not remove.
default_config:

homeassistant:
  name: Home  # shown in the sidebar
  packages: !include_dir_named packages

http:
  server_port: 8123

sensor:
- platform: time_date
  display_options:
  - time

rest:
- resource: https://example.com/api
  password: hunter2
  headers:
    Authorization: !secret api_auth
"""


@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """A private config folder: the shared testing_config would leak edits."""
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "packages").mkdir()
    (tmp_path / "themes").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _no_config_check(request: pytest.FixtureRequest) -> Any:
    """Home Assistant's full configuration check loads every integration the
    file names; these tests pin our own logic and stub the check's answer,
    except the one marked to run it for real."""
    if request.node.get_closest_marker("real_config_check"):
        yield
        return
    with patch.object(config_yaml, "_config_errors", return_value=Counter()):
        yield


async def _set(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()["selora_set_config_yaml"](hass, arguments)


async def _get(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()["selora_get_config_yaml"](hass, arguments)


async def _apply(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    preview = await _set(hass, **arguments)
    assert preview.get("preview") is True, preview
    return await _set(hass, **arguments, confirm_token=preview["confirm_token"])


# ── Read ────────────────────────────────────────────────────────────────────


async def test_read_lists_keys_and_the_other_files(hass: HomeAssistant, config_dir: Path) -> None:
    (config_dir / "packages" / "pool.yaml").write_text("sensor: []\n", encoding="utf-8")
    (config_dir / "themes" / "russo.yaml").write_text("Russo: {}\n", encoding="utf-8")

    result = await _get(hass)

    assert result["keys"] == ["default_config", "homeassistant", "http", "sensor", "rest"]
    assert result["other_files"] == ["packages/pool.yaml", "themes/russo.yaml"]


async def test_credentials_are_masked_and_secret_references_kept(
    hass: HomeAssistant, config_dir: Path
) -> None:
    result = await _get(hass, yaml_path="rest")

    assert "hunter2" not in result["yaml"]
    assert "password: '***'" in result["yaml"]
    assert "Authorization: !secret api_auth" in result["yaml"]


# ── The two-step write ──────────────────────────────────────────────────────


async def test_a_preview_writes_nothing_and_the_token_applies_it(
    hass: HomeAssistant, config_dir: Path
) -> None:
    arguments = {
        "yaml_path": "shell_command",
        "action": "add",
        "content": "pool_pump_boost: curl -X POST http://pump.local/boost\n",
    }

    preview = await _set(hass, **arguments)

    assert preview["written"] is False
    assert "+shell_command:" in preview["diff"]
    assert (config_dir / "configuration.yaml").read_text() == CONFIG

    applied = await _set(hass, **arguments, confirm_token=preview["confirm_token"])

    assert applied["written"] is True
    written = (config_dir / "configuration.yaml").read_text()
    # Everything that was there is there byte for byte — comments, tags and the
    # unindented list style included; only the new key is added.
    assert written.startswith(CONFIG)
    assert written[len(CONFIG) :] == (
        "shell_command:\n  pool_pump_boost: curl -X POST http://pump.local/boost\n"
    )
    assert applied["post_action"] == "restart_required"
    backup = config_dir / applied["backup"]
    assert backup.read_text() == CONFIG


async def test_a_token_from_before_a_change_writes_nothing(
    hass: HomeAssistant, config_dir: Path
) -> None:
    arguments = {"yaml_path": "shell_command", "action": "add", "content": "x: echo x\n"}
    preview = await _set(hass, **arguments)
    path = config_dir / "configuration.yaml"
    path.write_text(CONFIG + "\nrecorder:\n  purge_keep_days: 5\n", encoding="utf-8")

    stale = await _set(hass, **arguments, confirm_token=preview["confirm_token"])

    assert stale["confirm_token_mismatch"] is True
    assert stale["written"] is False
    assert "shell_command" not in path.read_text()


async def test_the_token_is_bound_to_the_exact_request(
    hass: HomeAssistant, config_dir: Path
) -> None:
    preview = await _set(hass, yaml_path="shell_command", action="add", content="x: echo x\n")

    other = await _set(
        hass,
        yaml_path="shell_command",
        action="add",
        content="x: rm -rf /\n",
        confirm_token=preview["confirm_token"],
    )

    assert other["written"] is False


async def test_a_failed_configuration_check_rolls_the_edit_back(
    hass: HomeAssistant, config_dir: Path
) -> None:
    answers = iter([Counter(), Counter({"Invalid config for 'rest': bad resource": 1})])
    with patch.object(config_yaml, "_config_errors", side_effect=lambda _h: next(answers)):
        result = await _apply(hass, yaml_path="rest", action="add", content="- resource: nope\n")

    assert "rolled back" in result["error"]
    assert "bad resource" in result["error"]
    assert (config_dir / "configuration.yaml").read_text() == CONFIG


async def test_an_error_that_was_already_there_does_not_block_the_edit(
    hass: HomeAssistant, config_dir: Path
) -> None:
    with patch.object(config_yaml, "_config_errors", return_value=Counter({"old problem": 1})):
        result = await _apply(hass, yaml_path="shell_command", action="add", content="x: echo\n")

    assert result["written"] is True


# ── Which keys ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["homeassistant", "http", "frontend", "lovelace", "input_boolean"])
async def test_keys_outside_the_allowlist_are_refused(
    hass: HomeAssistant, config_dir: Path, key: str
) -> None:
    result = await _set(hass, yaml_path=key, action="replace", content="x: 1\n")

    assert "not a key this tool edits" in result["error"]


async def test_automations_go_in_packages_only(hass: HomeAssistant, config_dir: Path) -> None:
    content = "- alias: Pool lights\n  triggers: []\n  actions: []\n"

    in_config = await _set(hass, yaml_path="automation", action="add", content=content)
    in_package = await _apply(
        hass, file="packages/pool.yaml", yaml_path="automation", action="add", content=content
    )

    assert "package file only" in in_config["error"]
    assert in_package["written"] is True
    assert in_package["post_action"] in ("reload_performed", "restart_required")


async def test_an_included_key_is_edited_in_its_own_file(
    hass: HomeAssistant, config_dir: Path
) -> None:
    (config_dir / "configuration.yaml").write_text(
        "default_config:\nsensor: !include sensors.yaml\n", encoding="utf-8"
    )

    result = await _set(hass, yaml_path="sensor", action="add", content="- platform: x\n")

    assert "included from another file" in result["error"]


async def test_add_appends_to_a_list(hass: HomeAssistant, config_dir: Path) -> None:
    await _apply(hass, yaml_path="sensor", action="add", content="- platform: uptime\n")

    result = await _get(hass, yaml_path="sensor")
    assert "platform: time_date" in result["yaml"]
    assert "platform: uptime" in result["yaml"]


async def test_add_will_not_overwrite_a_mapping_entry(
    hass: HomeAssistant, config_dir: Path
) -> None:
    await _apply(hass, yaml_path="shell_command", action="add", content="x: echo 1\n")

    result = await _set(hass, yaml_path="shell_command", action="add", content="x: echo 2\n")

    assert "use 'replace'" in result["error"]


# ── Themes ──────────────────────────────────────────────────────────────────


async def test_the_themes_folder_can_be_switched_on(hass: HomeAssistant, config_dir: Path) -> None:
    reloads: list[ServiceCall] = []
    hass.services.async_register("frontend", "reload_themes", reloads.append)

    wrong = await _set(
        hass, yaml_path="frontend.themes", action="add", content="!include ../../etc\n"
    )
    result = await _apply(
        hass,
        yaml_path="frontend.themes",
        action="add",
        content="!include_dir_merge_named themes\n",
    )

    assert "exactly '!include_dir_merge_named themes'" in wrong["error"]
    assert result["post_action"] == "reload_performed"
    assert len(reloads) == 1
    text = (config_dir / "configuration.yaml").read_text()
    assert "frontend:\n  themes: !include_dir_merge_named themes\n" in text


async def test_a_theme_file_is_written_and_reloaded(hass: HomeAssistant, config_dir: Path) -> None:
    hass.services.async_register("frontend", "reload_themes", lambda _call: None)
    (config_dir / "configuration.yaml").write_text(
        CONFIG + "frontend:\n  themes: !include_dir_merge_named themes\n", encoding="utf-8"
    )

    result = await _apply(
        hass,
        file="themes/russo.yaml",
        yaml_path="Russo Residence",
        action="replace",
        content="primary-color: '#c9a45c'\nprimary-background-color: '#0b1020'\n",
    )

    assert result["post_action"] == "reload_performed"
    text = (config_dir / "themes" / "russo.yaml").read_text()
    assert text.startswith("Russo Residence:\n  primary-color: '#c9a45c'")


async def test_a_theme_must_be_a_mapping(hass: HomeAssistant, config_dir: Path) -> None:
    result = await _set(
        hass, file="themes/russo.yaml", yaml_path="Russo", action="replace", content="- x\n"
    )

    assert "mapping of variables" in result["error"]


# ── Which files ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "file",
    [
        "secrets.yaml",
        "../configuration.yaml",
        "packages/../secrets.yaml",
        ".storage/core.config_entries",
        "themes/.hidden.yaml",
        "themes/sub/x.yaml",
        "custom_components/x.yaml",
        "/etc/passwd",
        "packages/x.yml",
    ],
)
async def test_only_the_three_kinds_of_file_are_reachable(
    hass: HomeAssistant, config_dir: Path, file: str
) -> None:
    read = await _get(hass, file=file)
    write = await _set(hass, file=file, yaml_path="sensor", action="add", content="- x: 1\n")

    assert "error" in read
    assert "error" in write


async def test_a_symlink_out_of_the_config_folder_is_refused(
    hass: HomeAssistant, config_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "evil.yaml"
    outside.write_text("sensor: []\n", encoding="utf-8")
    (config_dir / "packages" / "evil.yaml").symlink_to(outside)

    result = await _set(
        hass, file="packages/evil.yaml", yaml_path="sensor", action="add", content="- x: 1\n"
    )

    assert "symlink" in result["error"]
    assert outside.read_text() == "sensor: []\n"


async def test_the_packages_folder_is_the_one_configuration_includes(
    hass: HomeAssistant, config_dir: Path
) -> None:
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  packages: !include_dir_named my_packages\n", encoding="utf-8"
    )
    (config_dir / "my_packages").mkdir()

    default_folder = await _set(
        hass, file="packages/x.yaml", yaml_path="sensor", action="add", content="- x: 1\n"
    )
    real_folder = await _set(
        hass, file="my_packages/x.yaml", yaml_path="sensor", action="add", content="- x: 1\n"
    )

    assert "error" in default_folder
    assert real_folder.get("preview") is True


async def test_a_credential_in_the_diff_is_masked(hass: HomeAssistant, config_dir: Path) -> None:
    preview = await _set(
        hass, yaml_path="rest", action="add", content="- resource: x\n  token: abc123\n"
    )

    assert "abc123" not in preview["diff"]


def test_both_tools_need_admin() -> None:
    assert "selora_set_config_yaml" in mcp_access._ADMIN_TOOLS
    assert "selora_get_config_yaml" in mcp_access._ADMIN_TOOLS


@pytest.mark.real_config_check
async def test_home_assistants_own_check_rolls_back_a_broken_edit(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """End to end with the real configuration check: a rest entry missing its
    resource is invalid, and the file is left as it was."""
    original = "shell_command:\n  hello: echo hello\n"
    (config_dir / "configuration.yaml").write_text(original, encoding="utf-8")

    result = await _apply(hass, yaml_path="rest", action="add", content="- method: GET\n")

    assert "rolled back" in result.get("error", ""), result
    assert (config_dir / "configuration.yaml").read_text() == original


@pytest.mark.real_config_check
async def test_a_problem_that_was_already_there_is_not_blamed_on_the_edit(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """The check reports positions, and the edit moves the existing invalid
    shell_command down a line; it is still the same problem, not a new one."""
    original = "recorder:\n  purge_keep_days: 3\nshell_command: 5\n"
    (config_dir / "configuration.yaml").write_text(original, encoding="utf-8")

    result = await _apply(
        hass,
        yaml_path="recorder",
        action="replace",
        content="purge_keep_days: 3\ncommit_interval: 5\n",
    )

    assert result.get("written") is True, result


# ── Review findings ─────────────────────────────────────────────────────────


async def test_a_multiline_credential_is_masked_whole(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """A block scalar's body is the secret; masking its key line alone left the
    key material in reads and diffs."""
    (config_dir / "configuration.yaml").write_text(
        "rest:\n"
        "- resource: https://x\n"
        "  private_key: |\n"
        "    -----BEGIN KEY-----\n"
        "    c2VjcmV0LWtleS1tYXRlcmlhbA==\n"
        "  user: bob\n",
        encoding="utf-8",
    )

    read = await _get(hass, yaml_path="rest")
    preview = await _set(hass, yaml_path="rest", action="add", content="- resource: https://y\n")

    for text in (read["yaml"], preview["diff"]):
        assert "c2VjcmV0" not in text
        assert "BEGIN KEY" not in text
    assert "user: bob" in read["yaml"]


async def test_a_file_changed_during_the_check_is_not_overwritten(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """The lock covers this tool only; an edit made elsewhere while the
    baseline check ran must survive."""
    path = config_dir / "configuration.yaml"

    async def _someone_edits(_hass: HomeAssistant) -> Counter[str]:
        path.write_text(CONFIG + "recorder:\n  purge_keep_days: 5\n", encoding="utf-8")
        return Counter()

    preview = await _set(hass, yaml_path="shell_command", action="add", content="x: echo\n")
    with patch.object(config_yaml, "_config_errors", side_effect=_someone_edits):
        result = await _set(
            hass,
            yaml_path="shell_command",
            action="add",
            content="x: echo\n",
            confirm_token=preview["confirm_token"],
        )

    assert "changed while this edit was being checked" in result["error"]
    assert "purge_keep_days: 5" in path.read_text()
    assert "shell_command" not in path.read_text()


async def test_no_packages_folder_is_guessed(hass: HomeAssistant, config_dir: Path) -> None:
    """Home Assistant loads no packages folder unless configured; a write to
    an unconfigured one would report success and never apply."""
    (config_dir / "configuration.yaml").write_text("default_config:\n", encoding="utf-8")

    result = await _set(
        hass, file="packages/pool.yaml", yaml_path="sensor", action="add", content="- x: 1\n"
    )
    listed = await _get(hass)

    assert "loads no packages folder" in result["error"]
    assert listed["other_files"] == []


async def test_a_second_identical_problem_is_still_a_new_one(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """With positions stripped, a second rest entry missing the same field
    reads like the first; counting keeps it from hiding behind it."""
    problem = "Invalid config for 'rest': required key not provided @ data['resource']"
    answers = iter([Counter({problem: 1}), Counter({problem: 2})])
    with patch.object(config_yaml, "_config_errors", side_effect=lambda _h: next(answers)):
        result = await _apply(hass, yaml_path="rest", action="add", content="- method: GET\n")

    assert "rolled back" in result["error"]
    assert (config_dir / "configuration.yaml").read_text() == CONFIG


async def test_a_private_config_file_stays_private(hass: HomeAssistant, config_dir: Path) -> None:
    """Inline credentials: a 0600 file must not come back 0644, and its
    backups must be owner-only too."""
    path = config_dir / "configuration.yaml"
    path.chmod(0o600)

    result = await _apply(hass, yaml_path="shell_command", action="add", content="x: echo\n")

    assert path.stat().st_mode & 0o777 == 0o600
    backup = config_dir / result["backup"]
    assert backup.stat().st_mode & 0o777 == 0o600
    assert backup.parent.stat().st_mode & 0o777 == 0o700


async def test_a_new_file_is_owner_only(hass: HomeAssistant, config_dir: Path) -> None:
    hass.services.async_register("frontend", "reload_themes", lambda _call: None)

    await _apply(
        hass, file="themes/russo.yaml", yaml_path="Russo", action="replace", content="a: b\n"
    )

    assert (config_dir / "themes" / "russo.yaml").stat().st_mode & 0o777 == 0o600


async def test_a_rollback_never_erases_someone_elses_save(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """The check failed, but the file was saved by someone else meanwhile:
    their version stays, and the backup is named."""
    path = config_dir / "configuration.yaml"
    theirs = CONFIG + "recorder:\n  purge_keep_days: 5\n"
    calls = 0

    async def _check(_hass: HomeAssistant) -> Counter[str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_text(theirs, encoding="utf-8")
            return Counter({"Invalid config for 'rest'": 1})
        return Counter()

    preview = await _set(hass, yaml_path="rest", action="add", content="- method: GET\n")
    with patch.object(config_yaml, "_config_errors", side_effect=_check):
        result = await _set(
            hass,
            yaml_path="rest",
            action="add",
            content="- method: GET\n",
            confirm_token=preview["confirm_token"],
        )

    assert "NOT rolled back" in result["error"]
    assert path.read_text() == theirs
    assert (config_dir / result["backup"]).read_text() == CONFIG


async def test_a_parse_error_does_not_quote_the_file(hass: HomeAssistant, config_dir: Path) -> None:
    """ruamel quotes the failing line, which can be a credential."""
    (config_dir / "configuration.yaml").write_text(
        "rest:\n- resource: x\n  password: [hunter2\n", encoding="utf-8"
    )

    read = await _get(hass)
    write = await _set(hass, yaml_path="shell_command", action="add", content="x: echo\n")

    for result in (read, write):
        assert "does not parse at line" in result["error"]
        assert "hunter2" not in result["error"]


async def test_an_unindented_list_under_a_credential_is_masked(
    hass: HomeAssistant, config_dir: Path
) -> None:
    (config_dir / "configuration.yaml").write_text(
        "rest:\n- resource: x\n  api_keys:\n  - abc123\n  - def456\n  user: bob\n- resource: y\n",
        encoding="utf-8",
    )

    read = await _get(hass, yaml_path="rest")

    assert "abc123" not in read["yaml"]
    assert "def456" not in read["yaml"]
    assert "user: bob" in read["yaml"]
    assert "resource: y" in read["yaml"]


async def test_flow_style_credentials_are_masked(hass: HomeAssistant, config_dir: Path) -> None:
    """The key sits mid-line in flow style; masking by structure, not by line,
    is what catches it."""
    (config_dir / "configuration.yaml").write_text(
        "rest:\n"
        "- resource: x\n"
        "  headers: {Authorization: Bearer abc123, Accept: json}\n"
        "- {resource: y, password: hunter2}\n",
        encoding="utf-8",
    )

    read = await _get(hass, yaml_path="rest")
    preview = await _set(hass, yaml_path="rest", action="add", content="- resource: z\n")

    for text in (read["yaml"], preview["diff"]):
        assert "abc123" not in text
        assert "hunter2" not in text
        assert "Accept: json" in text


async def test_add_never_replaces_an_existing_frontend_themes(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """Themes may be defined inline under frontend.themes; 'add' must not
    swap them for the include."""
    original = "frontend:\n  themes:\n    Midnight:\n      primary-color: '#000'\n"
    (config_dir / "configuration.yaml").write_text(original, encoding="utf-8")

    result = await _set(
        hass,
        yaml_path="frontend.themes",
        action="add",
        content="!include_dir_merge_named themes\n",
    )

    assert "already set" in result["error"]
    assert (config_dir / "configuration.yaml").read_text() == original


@pytest.mark.parametrize(
    "packages",
    [
        # Absolute: Home Assistant reads /packages, not <config>/packages.
        "!include_dir_named /packages",
        # Merge form: each file is a mapping of package names, not integrations.
        "!include_dir_merge_named packages",
    ],
)
async def test_package_layouts_this_tool_cannot_write_are_refused(
    hass: HomeAssistant, config_dir: Path, packages: str
) -> None:
    (config_dir / "configuration.yaml").write_text(
        f"homeassistant:\n  packages: {packages}\n", encoding="utf-8"
    )

    result = await _set(
        hass, file="packages/pool.yaml", yaml_path="sensor", action="add", content="- x: 1\n"
    )

    assert "no packages folder this tool can edit" in result["error"]


async def test_only_the_merge_form_loads_the_themes_this_tool_writes(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """`!include_dir_named` wraps each file in its name, nesting the theme a
    level too deep for the frontend."""
    result = await _set(
        hass, yaml_path="frontend.themes", action="add", content="!include_dir_named themes\n"
    )

    assert "exactly '!include_dir_merge_named themes'" in result["error"]


async def test_a_huge_diff_is_cut_but_the_token_still_applies_the_whole_edit(
    hass: HomeAssistant, config_dir: Path
) -> None:
    commands = "".join(f"cmd_{i}: echo {'x' * 60} {i}\n" for i in range(400))

    preview = await _set(hass, yaml_path="shell_command", action="add", content=commands)

    assert preview["diff_truncated"] is True
    assert len(preview["diff"]) <= 12000
    applied = await _set(
        hass,
        yaml_path="shell_command",
        action="add",
        content=commands,
        confirm_token=preview["confirm_token"],
    )
    assert applied["written"] is True
    assert "cmd_399:" in (config_dir / "configuration.yaml").read_text()


async def test_hyphenated_credential_headers_are_masked(
    hass: HomeAssistant, config_dir: Path
) -> None:
    (config_dir / "configuration.yaml").write_text(
        "rest:\n- resource: x\n  headers:\n    X-API-Key: abc123\n    X-Auth-Token: def456\n",
        encoding="utf-8",
    )

    read = await _get(hass, yaml_path="rest")

    assert "abc123" not in read["yaml"]
    assert "def456" not in read["yaml"]


async def test_a_planted_temp_symlink_is_not_written_through(
    hass: HomeAssistant, config_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "target.yaml"
    outside.write_text("untouched\n", encoding="utf-8")
    (config_dir / ".configuration.yaml.selora-tmp").symlink_to(outside)

    result = await _apply(hass, yaml_path="shell_command", action="add", content="x: echo\n")

    assert result["written"] is True
    assert outside.read_text() == "untouched\n"
    assert not (config_dir / "configuration.yaml").is_symlink()


async def test_a_save_during_the_backup_is_not_overwritten(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """The compare and the replace happen together, after everything this
    tool awaits — a save landing during the backup survives."""
    path = config_dir / "configuration.yaml"
    theirs = CONFIG + "recorder:\n  purge_keep_days: 5\n"
    real_backup = config_yaml._backup

    def _backup_while_someone_saves(*args: Any) -> str:
        path.write_text(theirs, encoding="utf-8")
        return real_backup(*args)

    preview = await _set(hass, yaml_path="shell_command", action="add", content="x: echo\n")
    with patch.object(config_yaml, "_backup", side_effect=_backup_while_someone_saves):
        result = await _set(
            hass,
            yaml_path="shell_command",
            action="add",
            content="x: echo\n",
            confirm_token=preview["confirm_token"],
        )

    assert "changed while this edit was being checked" in result["error"]
    assert path.read_text() == theirs


async def test_a_symlink_inside_the_folder_cannot_alias_secrets(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """Inside the config folder, so the boundary check alone would pass it."""
    (config_dir / "secrets.yaml").write_text("pw: hunter2\n", encoding="utf-8")
    (config_dir / "themes" / "leak.yaml").symlink_to(config_dir / "secrets.yaml")

    result = await _get(hass, file="themes/leak.yaml")

    assert "symlink" in result["error"]


async def test_a_symlinked_folder_cannot_alias_the_config_folder(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """`themes` linked to the config folder makes themes/secrets.yaml the real one."""
    (config_dir / "secrets.yaml").write_text("pw: hunter2\n", encoding="utf-8")
    (config_dir / "themes").rmdir()
    (config_dir / "themes").symlink_to(config_dir)

    result = await _get(hass, file="themes/secrets.yaml")

    assert "error" in result
    assert "hunter2" not in str(result)


async def test_a_credential_only_change_still_shows_in_the_preview(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """Both sides masking to *** made a password change an empty diff — a
    confirmation for an edit the user could not see."""
    preview = await _set(
        hass,
        yaml_path="rest",
        action="replace",
        content=(
            "- resource: https://example.com/api\n"
            "  password: correct-horse\n"
            "  token: brand-new\n"
            "  headers:\n"
            "    Authorization: !secret api_auth\n"
        ),
    )

    assert "*** (changed)" in preview["diff"]
    assert "*** (new)" in preview["diff"]
    assert "hunter2" not in preview["diff"]
    assert "correct-horse" not in preview["diff"]
    assert "brand-new" not in preview["diff"]


@pytest.mark.parametrize("level", [".selora_ai", ".selora_ai/config_backups"])
async def test_a_symlinked_backup_folder_is_refused(
    hass: HomeAssistant,
    config_dir: Path,
    tmp_path_factory: pytest.TempPathFactory,
    level: str,
) -> None:
    """The backup is the unmasked file; a planted link must not take it out."""
    outside = tmp_path_factory.mktemp("outside")
    link = config_dir / level
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)

    result = await _apply(hass, yaml_path="shell_command", action="add", content="x: echo\n")

    assert "symlink" in result["error"]
    assert list(outside.iterdir()) == []
    assert (config_dir / "configuration.yaml").read_text() == CONFIG


async def test_an_empty_file_is_a_file(hass: HomeAssistant, config_dir: Path) -> None:
    """Empty is not missing: it reads as existing, and a rollback restores it
    rather than deleting it."""
    path = config_dir / "packages" / "pool.yaml"
    path.write_text("", encoding="utf-8")

    read = await _get(hass, file="packages/pool.yaml")
    answers = iter([Counter(), Counter({"Invalid config for 'sensor'": 1})])
    with patch.object(config_yaml, "_config_errors", side_effect=lambda _h: next(answers)):
        result = await _apply(
            hass, file="packages/pool.yaml", yaml_path="sensor", action="add", content="- x: 1\n"
        )

    assert read["exists"] is True
    assert read["keys"] == []
    assert "rolled back" in result["error"]
    assert path.exists()
    assert path.read_text() == ""


async def test_a_missing_file_reads_as_missing(hass: HomeAssistant, config_dir: Path) -> None:
    read = await _get(hass, file="packages/nope.yaml")

    assert read == {"file": "packages/nope.yaml", "exists": False}


async def test_a_rollback_never_quotes_the_rejected_values(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """The check's messages quote the configuration they reject, credentials
    included; what is wrong and where survive, the values do not."""
    problem = (
        "Invalid config for 'rest': expected a dictionary for dictionary value "
        "@ data['rest'][0], got {'resource': 'x', 'password': 'hunter2'}"
    )
    answers = iter([Counter(), Counter({problem: 1})])
    with patch.object(config_yaml, "_config_errors", side_effect=lambda _h: next(answers)):
        result = await _apply(hass, yaml_path="rest", action="add", content="- resource: x\n")

    assert "rolled back" in result["error"]
    assert "hunter2" not in result["error"]
    assert "data['rest'][0]" in result["error"]


@pytest.mark.real_config_check
async def test_home_assistants_own_messages_are_redacted(
    hass: HomeAssistant, config_dir: Path
) -> None:
    (config_dir / "configuration.yaml").write_text(
        "shell_command:\n  hello: echo hello\n", encoding="utf-8"
    )

    result = await _apply(
        hass, yaml_path="rest", action="add", content="- password: hunter2\n  method: GET\n"
    )

    assert "rolled back" in result["error"], result
    assert "hunter2" not in result["error"]


async def test_an_env_var_fallback_is_masked(hass: HomeAssistant, config_dir: Path) -> None:
    """`!env_var NAME fallback` carries a literal; only !secret and !include
    references are shown as written."""
    (config_dir / "configuration.yaml").write_text(
        "rest:\n"
        "- resource: x\n"
        "  password: !env_var DB_PASSWORD fallback-secret\n"
        "  token: !secret rest_token\n",
        encoding="utf-8",
    )

    read = await _get(hass, yaml_path="rest")
    preview = await _set(hass, yaml_path="rest", action="add", content="- resource: y\n")

    for text in (read["yaml"], preview["diff"]):
        assert "fallback-secret" not in text
        assert "!secret rest_token" in text


async def test_a_parser_problem_does_not_quote_values(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """A duplicate key's message names both values."""
    (config_dir / "configuration.yaml").write_text(
        "rest:\n- resource: x\n  password: hunter2\n  password: abc\n", encoding="utf-8"
    )

    result = await _get(hass)

    assert "does not parse at line" in result["error"]
    assert "hunter2" not in result["error"]
    assert "abc" not in result["error"]


async def test_a_theme_in_an_unloaded_folder_is_not_called_live(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """The reload succeeds either way — it just loads no themes — so the
    result says what is missing instead."""
    reloads: list[ServiceCall] = []
    hass.services.async_register("frontend", "reload_themes", reloads.append)

    result = await _apply(
        hass, file="themes/russo.yaml", yaml_path="Russo", action="replace", content="a: b\n"
    )

    assert result["post_action"] == "themes_folder_not_loaded"
    assert "frontend.themes" in result["hint"]
    assert reloads == []


async def test_a_crlf_file_keeps_its_line_endings(hass: HomeAssistant, config_dir: Path) -> None:
    """Read as bytes, written back the same way: one key's edit must not turn a
    CRLF file into LF, and the token is bound to what is on disk."""
    path = config_dir / "configuration.yaml"
    crlf = CONFIG.replace("\n", "\r\n")
    path.write_bytes(crlf.encode())

    preview = await _set(hass, yaml_path="shell_command", action="add", content="x: echo\n")
    added = [
        line
        for line in preview["diff"].splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    assert added == ["+shell_command:", "+  x: echo"]  # nothing but the new key
    applied = await _set(
        hass,
        yaml_path="shell_command",
        action="add",
        content="x: echo\n",
        confirm_token=preview["confirm_token"],
    )

    written = path.read_bytes().decode()
    assert applied["written"] is True
    assert written == crlf + "shell_command:\r\n  x: echo\r\n"


async def test_a_line_ending_only_change_invalidates_the_token(
    hass: HomeAssistant, config_dir: Path
) -> None:
    path = config_dir / "configuration.yaml"
    preview = await _set(hass, yaml_path="shell_command", action="add", content="x: echo\n")
    path.write_bytes(CONFIG.replace("\n", "\r\n").encode())

    result = await _set(
        hass,
        yaml_path="shell_command",
        action="add",
        content="x: echo\n",
        confirm_token=preview["confirm_token"],
    )

    assert result["written"] is False
    assert result["confirm_token_mismatch"] is True

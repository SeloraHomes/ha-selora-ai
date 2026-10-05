"""Tests for the MCP file tools: www/, themes/, custom_templates/, dashboards/,
and blueprints/ (read-only).

A file written to www/ is served to every browser, and the config folder holds
secrets.yaml and .storage next to it — most of these pin a boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.command_policy_options import CommandPolicyOptions
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch


@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """A private config folder: the shared testing_config would leak files."""
    hass.config.config_dir = str(tmp_path)
    for folder in ("www", "themes", "custom_templates", "dashboards", "blueprints"):
        (tmp_path / folder).mkdir()
    (tmp_path / "secrets.yaml").write_text("pw: hunter2\n", encoding="utf-8")
    (tmp_path / "configuration.yaml").write_text("default_config:\n", encoding="utf-8")
    return tmp_path


async def _call(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[f"selora_{tool}"](hass, arguments)


async def test_a_file_is_written_listed_and_read_back(
    hass: HomeAssistant, config_dir: Path
) -> None:
    written = await _call(hass, "write_file", file="www/pool/notes.txt", content="Pool: 28°C\n")

    assert written["written"] is True
    assert written["created"] is True
    assert written["url"] == "/local/pool/notes.txt"
    path = config_dir / "www" / "pool" / "notes.txt"
    assert path.stat().st_mode & 0o777 == 0o644
    listed = await _call(hass, "list_files", folder="www/pool")
    assert [e["name"] for e in listed["entries"]] == ["notes.txt"]
    read = await _call(hass, "read_file", file="www/pool/notes.txt")
    assert read["content"] == "Pool: 28°C\n"


async def test_replacing_needs_overwrite_and_keeps_a_backup(
    hass: HomeAssistant, config_dir: Path
) -> None:
    await _call(hass, "write_file", file="custom_templates/pool.jinja", content="v1\n")

    refused = await _call(hass, "write_file", file="custom_templates/pool.jinja", content="v2\n")
    replaced = await _call(
        hass, "write_file", file="custom_templates/pool.jinja", content="v2\n", overwrite=True
    )

    assert "overwrite=true" in refused["error"]
    assert replaced["created"] is False
    assert (config_dir / replaced["backup"]).read_text() == "v1\n"
    assert (config_dir / "custom_templates" / "pool.jinja").read_text() == "v2\n"


@pytest.mark.parametrize("file", ["www/card.js", "www/x/page.html", "www/icon.svg", "www/m.mjs"])
async def test_browser_code_in_www_waits_for_confirmation(
    hass: HomeAssistant, config_dir: Path, file: str
) -> None:
    """Registering a /local/ path as a resource needs no confirmation, so
    writing the code itself is where the user is asked."""
    first = await _call(hass, "write_file", file=file, content="alert(1)")

    assert first["requires_confirmation"] is True
    assert not (config_dir / file).exists()

    second = await _call(hass, "write_file", file=file, content="alert(1)", confirmed=True)

    assert second["written"] is True


async def test_code_outside_www_is_not_served_and_needs_no_confirmation(
    hass: HomeAssistant, config_dir: Path
) -> None:
    result = await _call(hass, "write_file", file="themes/notes.js", content="x")

    assert result["written"] is True


async def test_an_install_without_approvals_is_not_asked(
    hass: HomeAssistant, config_dir: Path
) -> None:
    with patch(
        "custom_components.selora_ai.command_policy_options.resolve_command_policy_options",
        return_value=CommandPolicyOptions(approval_required=False),
    ):
        result = await _call(hass, "write_file", file="www/card.js", content="x")

    assert result["written"] is True


@pytest.mark.parametrize(
    "file",
    [
        "configuration.yaml",
        "secrets.yaml",
        "www/secrets.yaml",
        "www/../secrets.yaml",
        ".storage/core.config_entries",
        "www/.hidden.txt",
        "www/a b.txt",
        "/etc/passwd",
        "custom_components/x.py",
        "packages/pool.yaml",
    ],
)
async def test_paths_outside_the_folders_are_refused(
    hass: HomeAssistant, config_dir: Path, file: str
) -> None:
    read = await _call(hass, "read_file", file=file)
    write = await _call(hass, "write_file", file=file, content="x", overwrite=True)

    assert "error" in read
    assert "error" in write
    assert "hunter2" not in str(read)
    assert (config_dir / "secrets.yaml").read_text() == "pw: hunter2\n"


async def test_blueprints_are_read_only(hass: HomeAssistant, config_dir: Path) -> None:
    (config_dir / "blueprints" / "motion.yaml").write_text("blueprint: {}\n", encoding="utf-8")

    read = await _call(hass, "read_file", file="blueprints/motion.yaml")
    write = await _call(
        hass, "write_file", file="blueprints/motion.yaml", content="x", overwrite=True
    )

    assert read["content"] == "blueprint: {}\n"
    assert "blueprints are read-only" in write["error"]


async def test_a_symlink_cannot_reach_secrets(hass: HomeAssistant, config_dir: Path) -> None:
    """Inside the config folder, so a boundary check alone would pass it."""
    (config_dir / "www" / "leak.txt").symlink_to(config_dir / "secrets.yaml")

    result = await _call(hass, "read_file", file="www/leak.txt")

    assert "symlink" in result["error"]


async def test_a_symlinked_folder_is_refused(hass: HomeAssistant, config_dir: Path) -> None:
    (config_dir / "www").rmdir()
    (config_dir / "www").symlink_to(config_dir)

    read = await _call(hass, "read_file", file="www/secrets.yaml")
    write = await _call(hass, "write_file", file="www/new.txt", content="x")

    assert "error" in read
    assert "error" in write
    assert not (config_dir / "new.txt").exists()


async def test_a_long_file_is_read_in_chunks(hass: HomeAssistant, config_dir: Path) -> None:
    text = "".join(f"line {i:05d}\n" for i in range(3000))
    (config_dir / "dashboards" / "big.yaml").write_text(text, encoding="utf-8")

    first = await _call(hass, "read_file", file="dashboards/big.yaml")
    second = await _call(hass, "read_file", file="dashboards/big.yaml", offset=first["next_offset"])

    assert first["size"] == len(text)
    assert first["content"] + second["content"] == text[: len(first["content"] + second["content"])]
    assert len(first["content"]) <= 12000


async def test_a_binary_file_is_refused(hass: HomeAssistant, config_dir: Path) -> None:
    (config_dir / "www" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")

    result = await _call(hass, "read_file", file="www/photo.png")

    assert "not a text file" in result["error"]


async def test_a_delete_keeps_a_backup(hass: HomeAssistant, config_dir: Path) -> None:
    await _call(hass, "write_file", file="themes/old.yaml", content="Old: {}\n")

    result = await _call(hass, "delete_file", file="themes/old.yaml")
    folder = await _call(hass, "delete_file", file="themes")
    missing = await _call(hass, "delete_file", file="themes/old.yaml")

    assert result["deleted"] is True
    assert (config_dir / result["backup"]).read_text() == "Old: {}\n"
    assert not (config_dir / "themes" / "old.yaml").exists()
    assert "Name a file inside" in folder["error"]
    assert "does not exist" in missing["error"]


async def test_content_over_the_size_limit_is_refused(
    hass: HomeAssistant, config_dir: Path
) -> None:
    result = await _call(hass, "write_file", file="www/big.txt", content="x" * 1_000_001)

    assert "MB" in result["error"]


def test_all_four_need_admin() -> None:
    for tool in ("list_files", "read_file", "write_file", "delete_file"):
        assert f"selora_{tool}" in mcp_access._ADMIN_TOOLS


async def test_a_chunk_boundary_never_splits_a_character(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """Chunks are read by byte; a two-byte character straddling the boundary
    belongs to the next chunk, and the pieces rebuild the text."""
    text = "a" + "é" * 10_000
    (config_dir / "themes" / "accents.yaml").write_text(text, encoding="utf-8")

    pieces, offset = [], 0
    while True:
        chunk = await _call(hass, "read_file", file="themes/accents.yaml", offset=offset)
        pieces.append(chunk["content"])
        if "next_offset" not in chunk:
            break
        offset = chunk["next_offset"]

    assert len(pieces) == 2
    assert "".join(pieces) == text
    assert chunk["size"] == len(text.encode())


async def test_an_offset_inside_a_character_is_refused(
    hass: HomeAssistant, config_dir: Path
) -> None:
    (config_dir / "themes" / "accents.yaml").write_text("é" * 10, encoding="utf-8")

    result = await _call(hass, "read_file", file="themes/accents.yaml", offset=1)

    assert "character boundary" in result["error"]


@pytest.mark.parametrize("file", ["www", "themes", "dashboards"])
async def test_a_root_folder_is_never_written_as_a_file(
    hass: HomeAssistant, config_dir: Path, file: str
) -> None:
    """Were www/ missing, writing 'www' would create a file there and block
    every /local/ file after it."""
    (config_dir / file).rmdir()

    write = await _call(hass, "write_file", file=file, content="x", overwrite=True)
    delete = await _call(hass, "delete_file", file=file)

    assert "Name a file inside" in write["error"]
    assert "Name a file inside" in delete["error"]
    assert not (config_dir / file).exists()


async def test_backups_of_lookalike_paths_never_share_a_history(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """www/a__b.txt and www/a/b.txt are different files with different
    backups; pruning one must not touch the other's."""
    for _ in range(2):
        await _call(hass, "write_file", file="www/a__b.txt", content="flat", overwrite=True)
        await _call(hass, "write_file", file="www/a/b.txt", content="nested", overwrite=True)
    for i in range(6):
        await _call(hass, "write_file", file="www/a__b.txt", content=f"flat {i}", overwrite=True)

    backups = sorted(p.name for p in (config_dir / ".selora_ai" / "file_backups").iterdir())
    nested = [b for b in backups if b.startswith("www%2Fa%2Fb.txt.")]
    flat = [b for b in backups if b.startswith("www%2Fa__b.txt.")]
    assert len(nested) == 1  # its one replacement, not pruned by the other's
    assert len(flat) == 5


async def test_a_folder_swapped_for_a_link_after_the_check_is_caught(
    hass: HomeAssistant, config_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The check is repeated inside the operation, right before the write."""
    from custom_components.selora_ai import config_files

    outside = tmp_path_factory.mktemp("outside")
    (config_dir / "www" / "pool").mkdir()
    real_resolve = config_files._resolve

    def _resolve_then_swap(*args: Any, **kwargs: Any) -> Any:
        resolved = real_resolve(*args, **kwargs)
        (config_dir / "www" / "pool").rmdir()
        (config_dir / "www" / "pool").symlink_to(outside)
        return resolved

    with patch.object(config_files, "_resolve", side_effect=_resolve_then_swap):
        result = await _call(hass, "write_file", file="www/pool/x.txt", content="x")

    assert "symlink" in result["error"]
    assert list(outside.iterdir()) == []


async def test_a_stylesheet_in_www_waits_for_confirmation(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """Registered as a css resource it styles every page — and can carry what
    it reads off the page out through url() lookups."""
    result = await _call(hass, "write_file", file="www/russo.css", content="body{}")

    assert result["requires_confirmation"] is True


async def test_backups_started_at_the_same_moment_both_land(
    hass: HomeAssistant, config_dir: Path
) -> None:
    """The first two backups race to create the backup folder; neither may
    fail because the other got there first."""
    import asyncio

    for name in ("one", "two"):
        await _call(hass, "write_file", file=f"themes/{name}.yaml", content="v1\n")

    results = await asyncio.gather(
        *(
            _call(hass, "write_file", file=f"themes/{name}.yaml", content="v2\n", overwrite=True)
            for name in ("one", "two")
        )
    )

    assert all(r["written"] is True for r in results), results

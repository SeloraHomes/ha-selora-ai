"""The integration never does file I/O on the event loop.

Home Assistant's blocking-call detector is off for file access under pytest, so
two guards stand in for it: a static scan of every ``async def`` for file calls
written inline, and the runtime guard in ``loop_io_guard`` (autouse in
``conftest``) for synchronous helpers reached from async code. The tests below
drive the paths that once read or wrote files on the loop.
"""

from __future__ import annotations

import ast
import asyncio
import io
from pathlib import Path
import tarfile
from typing import Any

from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider
from custom_components.selora_ai.providers.selora_local.utilities import rag
from custom_components.selora_ai.recipes.const import RECIPE_BUNDLE_DIR
from custom_components.selora_ai.recipes.upload_view import (
    UPLOAD_PATH,
    async_register_recipe_upload_view,
)
from tests.loop_io_guard import INTEGRATION_DIR, BlockingFileIOError

# Method names that only file APIs use in this codebase; ``replace``, ``remove``
# and ``stat`` are left out because ``str``, ``list`` and friends share them.
_FILE_METHODS = frozenset(
    {
        "open",
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "unlink",
        "mkdir",
        "rmdir",
        "touch",
        "exists",
        "is_file",
        "is_dir",
        "iterdir",
        "glob",
        "rglob",
        "listdir",
        "scandir",
        "makedirs",
        "rmtree",
        "copyfile",
        "copytree",
        "is_symlink",
        "lstat",
        "realpath",
        "samefile",
        "getsize",
        "getmtime",
    }
)


def _inline_file_calls(tree: ast.AST) -> list[tuple[int, str, str]]:
    """``(line, coroutine, call)`` for file calls written directly in an ``async def``.

    Nested functions and lambdas are skipped: that is how work is handed to
    ``async_add_executor_job``.
    """
    found: list[tuple[int, str, str]] = []
    for coro in ast.walk(tree):
        if not isinstance(coro, ast.AsyncFunctionDef):
            continue
        stack: list[ast.AST] = list(coro.body)
        while stack:
            node = stack.pop()
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
                continue
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in _FILE_METHODS:
                    found.append((node.lineno, coro.name, f".{func.attr}()"))
                elif isinstance(func, ast.Name) and func.id == "open":
                    found.append((node.lineno, coro.name, "open()"))
            stack.extend(ast.iter_child_nodes(node))
    return found


def test_no_coroutine_does_file_io_inline() -> None:
    offenders = [
        f"{path.relative_to(INTEGRATION_DIR)}:{line} {coro} calls {call}"
        for path in sorted(Path(INTEGRATION_DIR).rglob("*.py"))
        for line, coro, call in _inline_file_calls(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, "File I/O on the event loop:\n" + "\n".join(offenders)


def test_scan_sees_inline_calls_but_not_executor_work() -> None:
    tree = ast.parse(
        "async def handler(hass, path):\n"
        "    with path.open('wb') as fh:\n"
        "        fh.write(b'')\n"
        "    await hass.async_add_executor_job(lambda: path.unlink())\n"
        "    def _work():\n"
        "        open(path)\n"
        "    await hass.async_add_executor_job(_work)\n"
    )
    assert _inline_file_calls(tree) == [(2, "handler", ".open()")]


async def test_guard_flags_a_sync_helper_reached_from_a_coroutine(
    tmp_path: Path, _no_loop_file_io: list[str]
) -> None:
    """The runtime guard catches what the scan cannot: a sync helper called from async code."""
    target = tmp_path / "f.txt"
    namespace: dict[str, Any] = {}
    exec(  # noqa: S102 — compiled under the integration's path so the guard attributes it
        compile(
            "def helper(p):\n    return p.write_text('x')\n"
            "async def coro(p):\n    return helper(p)\n",
            str(Path(INTEGRATION_DIR) / "_guard_probe.py"),
            "exec",
        ),
        namespace,
    )
    with pytest.raises(BlockingFileIOError, match="write_text"):
        await namespace["coro"](target)
    # Recorded too, so a broad ``except`` cannot hide it; expected here.
    assert len(_no_loop_file_io) == 1
    _no_loop_file_io.clear()
    # The same helper off the loop, and test code itself, are fine.
    await asyncio.get_running_loop().run_in_executor(None, namespace["helper"], target)
    assert target.read_text() == "x"


# ── Recipe upload ─────────────────────────────────────────────────────


def _bundle_bytes(slug: str = "leak-lockdown") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(Path(__file__).parent / "recipe_fixtures" / slug, arcname=slug)
    return buf.getvalue()


@pytest.fixture
async def upload_client(hass: Any, hass_client: Any) -> Any:
    assert await async_setup_component(hass, "http", {})
    async_register_recipe_upload_view(hass)
    await hass.async_block_till_done()
    return await hass_client()


def _form(payload: bytes, filename: str = "leak-lockdown.tar.gz") -> Any:
    import aiohttp

    form = aiohttp.FormData()
    form.add_field("file", payload, filename=filename, content_type="application/gzip")
    return form


def _leftover_uploads(hass: Any) -> list[Path]:
    uploads = Path(hass.config.path(RECIPE_BUNDLE_DIR)) / "_uploads"
    return list(uploads.iterdir()) if uploads.is_dir() else []


async def test_upload_stages_the_bundle_off_the_loop(hass: Any, upload_client: Any) -> None:
    resp = await upload_client.post(UPLOAD_PATH, data=_form(_bundle_bytes()))
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["slug"] == "leak-lockdown"
    assert (Path(body["path"]) / "manifest.yaml").is_file()
    assert not _leftover_uploads(hass)


async def test_oversized_upload_is_refused_without_a_file(
    hass: Any, upload_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.selora_ai.recipes import upload_view

    monkeypatch.setattr(upload_view, "RECIPE_ARCHIVE_MAX_BYTES", 1024)
    resp = await upload_client.post(UPLOAD_PATH, data=_form(b"\x1f\x8b" + b"0" * 4096))
    assert resp.status == 413
    assert not _leftover_uploads(hass)


async def test_corrupt_upload_is_rejected_and_removed(hass: Any, upload_client: Any) -> None:
    resp = await upload_client.post(UPLOAD_PATH, data=_form(b"not an archive"))
    assert resp.status == 400
    assert not _leftover_uploads(hass)


# ── Selora AI Local utilities docs ────────────────────────────────────


async def test_utilities_docs_load_before_the_payload_is_built(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``build_payload`` is sync and retrieves docs, so every request path must
    have read the corpus through the executor before it runs.
    """
    monkeypatch.setattr(rag, "_selora_local_doc_chunks", None)
    monkeypatch.setattr(rag, "_selora_local_docs_corpus", None)
    provider = SeloraLocalProvider(hass, host="hub.local")
    await provider._ensure_specialist_prompts_loaded()

    def _read_on_demand() -> None:
        raise AssertionError("utilities docs read while building the payload")

    monkeypatch.setattr(rag, "_selora_local_read_doc_records", _read_on_demand)
    provider.set_call_kind("chat_utilities")
    provider.set_chat_context(
        user_message="how do I update home assistant core",
        entities=[],
        existing_automations=[],
        history=[],
    )
    try:
        payload = provider.build_payload("fallback", [{"role": "user", "content": "x"}])
    finally:
        provider.set_call_kind(None)
    assert "RELEVANT DOCS:" in payload["messages"][-1]["content"]

"""Fail a test when the integration touches the filesystem on the event loop.

Home Assistant's own detector (``homeassistant.block_async_io``) marks its file
checks ``skip_for_tests``, so under pytest a blocking ``open()`` passes silently
and only shows up on a real hub — or in a HACS review. This guard wraps the
file APIs and raises when one runs on the loop thread with an integration
coroutine on the stack, which covers a synchronous helper reached from async
code (the case a static scan cannot see). Test code is free to touch files,
even when an integration coroutine calls into it (a mock's side effect).
"""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import functools
import inspect
import io
import os
from pathlib import Path
import sys
from typing import Any

_PROJECT_DIR = str(Path(__file__).resolve().parent.parent)
INTEGRATION_DIR = str(Path(_PROJECT_DIR) / "custom_components" / "selora_ai")

_PATH_METHODS = (
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
    "stat",
    "lstat",
    "is_symlink",
    "resolve",
    "samefile",
)
_OS_FUNCTIONS = ("listdir", "scandir", "remove", "unlink", "makedirs", "walk")
_OS_PATH_FUNCTIONS = ("realpath", "exists", "isfile", "isdir", "islink", "getsize", "getmtime")


class BlockingFileIOError(AssertionError):
    """File I/O ran on the event loop from integration code."""


def _integration_coroutine_frame() -> str | None:
    """Where integration code did file I/O on the loop, as ``file:line``, or ``None``.

    The nearest project frame above the call must be the integration's (a test
    helper or mock side effect is the test's own I/O; stdlib and libraries are
    looked through), and an integration coroutine must be on the stack without
    the test executor between them.
    """
    frame = sys._getframe(2)
    while frame is not None and not _is_project(frame.f_code.co_filename):
        if _is_home_assistant(frame.f_code.co_filename):
            # Core's own I/O (or the test harness's stand-in for it).
            return None
        if frame.f_code.co_filename.endswith(f"{os.sep}weakref.py"):
            # A finalizer the garbage collector happened to run here.
            return None
        frame = frame.f_back
    if frame is None or not frame.f_code.co_filename.startswith(INTEGRATION_DIR):
        return None
    while frame is not None:
        code = frame.f_code
        if _is_home_assistant(code.co_filename):
            # The test hass runs a Mock executor target inline; in production
            # that call is on a worker thread.
            return None
        if code.co_flags & inspect.CO_COROUTINE and code.co_filename.startswith(INTEGRATION_DIR):
            return f"{code.co_filename}:{frame.f_lineno} ({code.co_name})"
        frame = frame.f_back
    return None


def _is_home_assistant(filename: str) -> bool:
    return (
        f"{os.sep}homeassistant{os.sep}" in filename
        or "pytest_homeassistant_custom_component" in filename
    )


def _is_project(filename: str) -> bool:
    return (
        filename.startswith(_PROJECT_DIR)
        and "site-packages" not in filename
        and filename != __file__
    )


def _guard(name: str, func: Callable[..., Any], violations: list[str]) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if asyncio._get_running_loop() is not None and (where := _integration_coroutine_frame()):
            message = (
                f"{name} ran on the event loop from {where}; "
                "move it into hass.async_add_executor_job"
            )
            violations.append(message)
            raise BlockingFileIOError(message)
        return func(*args, **kwargs)

    return wrapper


@contextmanager
def guard_loop_file_io() -> Iterator[list[str]]:
    """Raise :class:`BlockingFileIOError` for integration file I/O on the loop.

    Every violation is also recorded and raised again on exit, because a
    broad ``except`` in the integration would otherwise swallow it.
    """
    violations: list[str] = []
    originals: list[tuple[Any, str, Any]] = [
        (builtins, "open", builtins.open),
        (io, "open", io.open),
    ]
    originals += [(Path, name, getattr(Path, name)) for name in _PATH_METHODS]
    originals += [(os, name, getattr(os, name)) for name in _OS_FUNCTIONS]
    originals += [(os.path, name, getattr(os.path, name)) for name in _OS_PATH_FUNCTIONS]
    for owner, name, func in originals:
        label = f"{getattr(owner, '__name__', owner)}.{name}"
        setattr(owner, name, _guard(label, func, violations))
    try:
        yield violations
    finally:
        for owner, name, func in originals:
            setattr(owner, name, func)
    if violations:
        raise BlockingFileIOError("\n".join(dict.fromkeys(violations)))

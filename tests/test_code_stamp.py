"""Tests for the code signature used to detect load-vs-disk skew.

The signature has to be an identity of the Python tree, not a high-water mark.
`just deploy` uses `rsync -az`, which preserves each source file's own mtime and
`--delete`s removals, so a real deployment routinely lands files whose
timestamps are *older* than what was already on disk. Every scenario below is
one a newest-mtime scalar reports as "nothing changed".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from custom_components.selora_ai import code_stamp

COMPONENT = Path(code_stamp.__file__).parent

# Well in the past, so anything written under it stays below the tree's maximum.
OLD_TIME = 1_600_000_000


def _tree(root: Path) -> Path:
    """Build a small component-shaped tree with fixed, old timestamps."""
    (root / "sub").mkdir(parents=True)
    (root / "__init__.py").write_text("A = 1\n")
    (root / "helpers.py").write_text("B = 2\n")
    (root / "sub" / "mod.py").write_text("C = 3\n")
    (root / "notes.txt").write_text("not python\n")
    _age(root)
    return root


def _age(root: Path, when: int = OLD_TIME) -> None:
    """Stamp every file in the tree with the same old mtime."""
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (when, when))


def test_signature_is_stable_and_hex(tmp_path: Path) -> None:
    """Same tree, same signature — the check must not flap between calls."""
    root = _tree(tmp_path / "component")
    first = code_stamp.python_signature(root)
    assert first == code_stamp.python_signature(root)
    assert len(first) == 16
    int(first, 16)


def test_edit_with_a_preserved_timestamp_changes_the_signature(tmp_path: Path) -> None:
    """rsync -a writes the source's mtime, so contents move without a newer stamp."""
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    target = root / "helpers.py"
    stat = target.stat()
    target.write_text("B = 99\n")
    os.utime(target, (stat.st_atime, stat.st_mtime))  # same mtime as before
    assert target.stat().st_mtime == stat.st_mtime
    assert code_stamp.python_signature(root) != before


def test_edit_with_an_older_timestamp_changes_the_signature(tmp_path: Path) -> None:
    """A deploy can land a file older than the tree's newest — still stale code."""
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    target = root / "sub" / "mod.py"
    target.write_text("C = 30\n")
    os.utime(target, (OLD_TIME - 5000, OLD_TIME - 5000))
    assert code_stamp.python_signature(root) != before


def test_deleting_a_file_changes_the_signature(tmp_path: Path) -> None:
    """`rsync --delete` removes modules; no timestamp records that."""
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    (root / "helpers.py").unlink()
    assert code_stamp.python_signature(root) != before


def test_adding_a_file_changes_the_signature(tmp_path: Path) -> None:
    """A new module the process never imported still means stale code."""
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    (root / "sub" / "extra.py").write_text("D = 4\n")
    _age(root)
    assert code_stamp.python_signature(root) != before


def test_renaming_a_file_changes_the_signature(tmp_path: Path) -> None:
    """Paths are hashed, so a move is not invisible even with identical bytes."""
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    (root / "helpers.py").rename(root / "helpers2.py")
    _age(root)
    assert code_stamp.python_signature(root) != before


def test_sub_second_edit_changes_the_signature(tmp_path: Path) -> None:
    """Second-truncated mtimes hide fast edits; contents don't."""
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    target = root / "helpers.py"
    stat = target.stat()
    target.write_text("B = 2222\n")
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000))  # +1 µs
    assert int(target.stat().st_mtime) == int(stat.st_mtime)
    assert code_stamp.python_signature(root) != before


def test_swapping_contents_between_files_changes_the_signature(tmp_path: Path) -> None:
    """Path is bound to its bytes, so the same set of contents isn't equivalent."""
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    first, second = root / "__init__.py", root / "helpers.py"
    first_text, second_text = first.read_text(), second.read_text()
    first.write_text(second_text)
    second.write_text(first_text)
    _age(root)
    assert code_stamp.python_signature(root) != before


def test_non_python_changes_are_ignored(tmp_path: Path) -> None:
    """Translations and assets don't live in sys.modules — no restart needed."""
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    (root / "notes.txt").write_text("edited\n")
    assert code_stamp.python_signature(root) == before


def test_frontend_and_bytecode_are_skipped(tmp_path: Path) -> None:
    """node_modules would make the walk expensive; bytecode just echoes sources."""
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    (root / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
    (root / "frontend" / "node_modules" / "pkg" / "setup.py").write_text("x = 1\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "helpers.py").write_text("stale = True\n")
    assert code_stamp.python_signature(root) == before


def test_unreadable_file_does_not_raise(tmp_path: Path, monkeypatch: Any) -> None:
    """A half-written deploy must degrade, not break the handshake.

    The read error is injected rather than provoked with ``chmod(0o000)``: CI
    runs as root, which reads a mode-000 file regardless, so a permission-based
    version of this test asserts nothing there.
    """
    root = _tree(tmp_path / "component")
    before = code_stamp.python_signature(root)
    target = root / "helpers.py"
    real_read_bytes = Path.read_bytes

    def _read_bytes(self: Path) -> bytes:
        if self == target:
            raise PermissionError(13, "Permission denied")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _read_bytes)
    after = code_stamp.python_signature(root)
    monkeypatch.undo()

    assert after != before  # unreadable ≠ the contents we loaded
    assert code_stamp.python_signature(root) == before

    # Unreadable is also distinct from absent — a file we can't read is not the
    # same tree as one that was deleted.
    target.unlink()
    assert code_stamp.python_signature(root) != after


def test_signature_covers_the_real_component() -> None:
    """The shipped tree hashes to something, and matches an independent walk."""
    expected_paths = sorted(
        str(path.relative_to(COMPONENT))
        for path in COMPONENT.rglob("*.py")
        if "frontend" not in path.parts and "__pycache__" not in path.parts
    )
    assert len(expected_paths) > 50
    assert code_stamp.python_signature() == code_stamp.python_signature(COMPONENT)


def test_loaded_signature_is_captured_at_import() -> None:
    """The module-level constant is what makes skew detectable at all."""
    assert isinstance(code_stamp.LOADED_PYTHON_SIGNATURE, str)
    assert len(code_stamp.LOADED_PYTHON_SIGNATURE) == 16


def test_panel_build_id_reads_the_sidecar() -> None:
    """build.js writes the id the running bundle reports back to us."""
    sidecar = json.loads((COMPONENT / "frontend" / "panel.build.json").read_text())
    assert code_stamp.panel_build_id() == sidecar["build"]
    assert code_stamp.panel_build_id()


def test_panel_build_id_survives_a_missing_sidecar(monkeypatch: Any) -> None:
    """A source checkout without a build must not break the handshake."""
    monkeypatch.setattr(code_stamp, "_PANEL_BUILD_FILE", COMPONENT / "nope.json")
    assert code_stamp.panel_build_id() == ""


def test_panel_build_id_survives_a_corrupt_sidecar(tmp_path: Path, monkeypatch: Any) -> None:
    """Truncated by an interrupted deploy → unknown, not a crash."""
    broken = tmp_path / "panel.build.json"
    broken.write_text("{not json")
    monkeypatch.setattr(code_stamp, "_PANEL_BUILD_FILE", broken)
    assert code_stamp.panel_build_id() == ""

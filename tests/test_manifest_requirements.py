"""Manifest requirements must be installable under Home Assistant's constraints.

HA installs a custom integration's `requirements` with core's
`package_constraints.txt` passed as `--constraint`, so core's pin decides the
version and our specifier only ever gets a vote on whether the install
*succeeds*. A specifier the pin does not satisfy is unresolvable, and the
integration never sets up:

    Requirements for selora_ai not found: ['PyJWT>=2.13.0']

The failure is invisible in CI — it depends on which core version the user is
running — so the invariant is checked here instead.
"""

from __future__ import annotations

from importlib.metadata import distribution
import json
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version
import pytest

MANIFEST = Path(__file__).parent.parent / "custom_components" / "selora_ai" / "manifest.json"


def _manifest_requirements() -> list[Requirement]:
    manifest = json.loads(MANIFEST.read_text())
    return [Requirement(spec) for spec in manifest["requirements"]]


def _core_requirements() -> dict[str, Requirement]:
    """Packages Home Assistant core installs itself, by canonical name."""
    return {
        canonicalize_name(req.name): req
        for req in (Requirement(spec) for spec in distribution("homeassistant").requires or ())
        if not req.marker  # extras / env-conditional deps are not guaranteed
    }


def _core_constraints() -> dict[str, SpecifierSet]:
    """Core's `package_constraints.txt`, by canonical name.

    A name may be listed more than once (`multidict` is, today), so the
    specifiers are merged rather than last-one-wins.
    """
    constraints = (
        Path(distribution("homeassistant").locate_file("homeassistant")) / "package_constraints.txt"
    )
    parsed: dict[str, SpecifierSet] = {}
    for line in constraints.read_text().splitlines():
        line = line.partition("#")[0].strip()
        if not line:
            continue
        req = Requirement(line)
        name = canonicalize_name(req.name)
        parsed[name] = parsed.get(name, SpecifierSet()) & req.specifier
    return parsed


def _core_pin(specifier: SpecifierSet) -> str | None:
    """The exact version core forces, if it forces one."""
    pins = [spec.version for spec in specifier if spec.operator == "=="]
    return pins[0] if pins else None


def _tighter(
    current: tuple[Version, bool] | None,
    candidate: tuple[Version, bool],
    *,
    keep: str,
) -> tuple[Version, bool]:
    """Fold a bound into the running one, exclusivity winning a tie.

    `>=5` and `>5` name the same version and mean different things, so the
    strictness travels with it.
    """
    if current is None:
        return candidate
    if current[0] == candidate[0]:
        return (current[0], current[1] or candidate[1])
    wins = max if keep == "highest" else min
    return wins(current, candidate, key=lambda bound: bound[0])


def _unsatisfiable(specifier: SpecifierSet) -> str | None:
    """Report why no version can satisfy `specifier`, if none can.

    Deliberately conservative: an operator this cannot model (`~=`'s implied
    ceiling, `===`, a `==6.4.*` wildcard) contributes a lower bound at most, so
    a shape it does not understand is reported as fine rather than as broken.
    A false alarm here blocks a merge request over an install that would have
    worked, which is worse than the narrower coverage.
    """
    lower: tuple[Version, bool] | None = None
    upper: tuple[Version, bool] | None = None
    pins: set[Version] = set()
    excluded: set[Version] = set()
    for spec in specifier:
        if "*" in spec.version or spec.operator == "===":
            continue
        version = Version(spec.version)
        if spec.operator == "==":
            pins.add(version)
            lower = _tighter(lower, (version, False), keep="highest")
            upper = _tighter(upper, (version, False), keep="lowest")
        elif spec.operator == "!=":
            excluded.add(version)
        elif spec.operator in (">=", "~="):
            lower = _tighter(lower, (version, False), keep="highest")
        elif spec.operator == ">":
            lower = _tighter(lower, (version, True), keep="highest")
        elif spec.operator == "<=":
            upper = _tighter(upper, (version, False), keep="lowest")
        elif spec.operator == "<":
            upper = _tighter(upper, (version, True), keep="lowest")

    if len(pins) > 1:
        return "pinned to " + " and ".join(str(pin) for pin in sorted(pins))
    if lower is None or upper is None:
        return None
    (low, low_excludes), (high, high_excludes) = lower, upper
    if low > high or (low == high and (low_excludes or high_excludes)):
        return (
            f"nothing is both {'>' if low_excludes else '>='}{low} "
            f"and {'<' if high_excludes else '<='}{high}"
        )
    if low == high and low in excluded:
        return f"only {low} is left and it is excluded"
    return None


def _version_owned_by_core() -> dict[str, str]:
    """Packages whose installed version core decides, whatever we ask for.

    Two ways core decides: it installs the package itself, or it `==`-pins the
    package in the constraints file it hands pip. Either way our specifier can
    only make the resolve fail.
    """
    owned = {name: str(req) for name, req in _core_requirements().items()}
    for name, specifier in _core_constraints().items():
        if _core_pin(specifier) is not None:
            owned.setdefault(name, f"{name}{specifier}")
    return owned


def test_no_requirement_has_a_version_core_owns() -> None:
    """Never declare a package whose version core already decides.

    The check is deliberately version-INDEPENDENT: it refuses the declaration
    outright rather than comparing our specifier against the pin the installed
    core happens to carry. Hubs do not auto-update core, so a floor that suits
    the newest release still breaks every older one — which is exactly how
    `PyJWT>=2.13.0` shipped and took out every 2026.7.x hub while the dev box
    on 2026.8 was fine.

    A package we genuinely need above core's pin is a blocker to surface here,
    not a floor to write: declaring it fails the install, and omitting it fails
    at runtime instead.
    """
    owned = _version_owned_by_core()
    clashes = [
        f"{req.name} (core: {owned[canonicalize_name(req.name)]})"
        for req in _manifest_requirements()
        if canonicalize_name(req.name) in owned
    ]
    assert not clashes, (
        "manifest.json declares packages whose version Home Assistant core "
        "already decides; remove them: " + ", ".join(clashes)
    )


def test_requirements_are_satisfiable_under_core_constraints() -> None:
    """A RANGED constraint still has to leave our specifier something to pick.

    The check is on the INTERSECTION rather than on our floor against core's
    ceiling: the conflict runs both ways round. A manifest `foo<4` against a
    core `foo>=4` is just as unresolvable, and comparing one direction reports
    it as fine.
    """
    constraints = _core_constraints()
    conflicts = [
        f"{req}: {reason} (core: {req.name}{specifier})"
        for req in _manifest_requirements()
        if (specifier := constraints.get(canonicalize_name(req.name))) is not None
        and (reason := _unsatisfiable(req.specifier & specifier)) is not None
    ]
    assert not conflicts, (
        "manifest.json requirements conflict with core's constraints: " + ", ".join(conflicts)
    )


@pytest.mark.parametrize("spec", ["PyJWT>=2.13.0", "PyJWT>=2.0.0", "PyJWT"])
def test_detects_a_core_owned_requirement(spec: str) -> None:
    """The guard catches the shipped regression whatever version was asked for.

    PyJWT is a permanent core dependency, so this stays true across core
    releases — which is the property the guard itself is claiming to have.
    """
    assert canonicalize_name(Requirement(spec).name) in _version_owned_by_core()


@pytest.mark.parametrize(
    ("spec", "constraint", "conflicts"),
    [
        # our floor above core's ceiling
        ("foo>=5.0", "<5.0", True),
        ("foo>5.0", "<=5.0", True),
        ("foo>=5.1", "<=5.0", True),
        ("foo>=5.0", "<=5.0", False),
        ("foo>=4.9", "<5.0", False),
        # our ceiling below core's floor — the same conflict the other way
        ("foo<4", ">=4", True),
        ("foo>=1,<4", ">=4", True),
        ("foo<=4", ">=4", False),
        ("foo>=1,<5", ">=4", False),
        # exclusions, which only bite once one version is left
        ("foo==6.4.0", "!=6.4.0", True),
        ("foo==6.4.0,>=6", "!=6.4.0", True),
        ("foo<=6.4.0,>=6.4.0", "!=6.4.0", True),
        ("foo>=6.4.0", "!=6.4.0", False),
        ("foo==1.0", "==2.0", True),
        # nothing to compare
        ("foo>=1.0", ">=4.0", False),  # a floor of core's cannot rule out ours
        ("foo", "<5.0", False),  # no bound of ours at all
        # shapes the model declines to judge rather than guess at
        ("foo~=6.4.0", "!=6.4.0", False),
        ("foo==6.4.*", "!=6.4.0", False),
    ],
)
def test_unsatisfiable_detection(spec: str, constraint: str, conflicts: bool) -> None:
    """The intersection check is exercised on synthetic bounds.

    Driving it off whichever ceilings core currently ships would tie this to a
    constraints file that changes every release.
    """
    reason = _unsatisfiable(Requirement(spec).specifier & SpecifierSet(constraint))
    assert (reason is not None) is conflicts, reason

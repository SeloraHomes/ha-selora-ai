"""Entity capability classification for Home Assistant domains.

Single source of truth for which domains participate in data collection,
scenes, and which entity-ID patterns indicate config/diagnostic entities
that should be excluded from user-facing features.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@dataclass(frozen=True)
class DomainProfile:
    """Capabilities of a Home Assistant entity domain."""

    collect: bool = False
    """Include entities from this domain in data collection snapshots."""

    scene: bool = False
    """Entities from this domain can appear in scenes."""

    exclude_patterns: frozenset[str] = field(default_factory=frozenset)
    """Entity-ID substrings that mark config/diagnostic entities.

    An entity matching any pattern is excluded from snapshots, scenes,
    and LLM context.
    """


DOMAIN_PROFILES: dict[str, DomainProfile] = {
    "light": DomainProfile(
        collect=True,
        scene=True,
        exclude_patterns=frozenset(
            {
                # Status LEDs, IR emitters — not room lighting
                "status_led",
                "ir_led",
                "ir_light",
                "indicator",
                "illuminator",
                "camera_light",
                "floodlight",
                "floodlight_status",
                "night_vision",
                "infrared",
            }
        ),
    ),
    "switch": DomainProfile(
        collect=True,
        scene=True,
        exclude_patterns=frozenset(
            {
                # Camera config switches (Reolink, UniFi, etc.)
                "ftp_upload",
                "email_on_event",
                "privacy_mode",
                "record_audio",
                "auto_tracking",
                "auto_focus",
                "guard_return",
                "push_notifications",
                "siren_on_event",
                "ptz_patrol",
                "doorbell_button_sound",
                "hdr",
                # Appliance config switches
                "express_mode",
                "sabbath_mode",
                "child_lock",
                "ice_plus",
                # Hub / bridge feature switches
                "smart_away",
                # Device config switches
                "firmware_update",
                "auto_update",
                "status_light",
                "do_not_disturb",
            }
        ),
    ),
    "media_player": DomainProfile(collect=True, scene=True),
    "climate": DomainProfile(collect=True, scene=True),
    "fan": DomainProfile(collect=True, scene=True),
    "cover": DomainProfile(collect=True, scene=True),
    "lock": DomainProfile(collect=True),
    "vacuum": DomainProfile(collect=True),
    "sensor": DomainProfile(collect=True),
    "binary_sensor": DomainProfile(collect=True),
    "water_heater": DomainProfile(collect=True),
    "humidifier": DomainProfile(collect=True),
    "input_boolean": DomainProfile(collect=True),
    "input_select": DomainProfile(collect=True),
    "device_tracker": DomainProfile(collect=True),
    "person": DomainProfile(collect=True),
}


# Assist-pipeline plumbing. These are entities in the registry, but nothing in
# the house corresponds to them and no caller can place one on a dashboard or
# name one in an automation, so they are noise in an inventory a model reads.
# Everything else a home has IS shown — see :func:`is_inspectable_entity`.
TOOL_HIDDEN_DOMAINS: frozenset[str] = frozenset(
    {
        "conversation",
        "stt",
        "tts",
        "wake_word",
    }
)


# ── Derived sets ─────────────────────────────────────────────────────

COLLECTOR_DOMAINS: frozenset[str] = frozenset(
    domain for domain, p in DOMAIN_PROFILES.items() if p.collect
)

SCENE_CAPABLE_DOMAINS: frozenset[str] = frozenset(
    domain for domain, p in DOMAIN_PROFILES.items() if p.scene
)


# ── Query functions ──────────────────────────────────────────────────


def is_actionable_entity(entity_id: str) -> bool:
    """Return True if the entity is user-actionable (not config/diagnostic).

    Checks domain-specific exclude patterns from DOMAIN_PROFILES.
    Used by data collection, LLM context building, automations, and scenes.
    """
    domain = entity_id.split(".")[0]
    profile = DOMAIN_PROFILES.get(domain)
    if profile is None or not profile.exclude_patterns:
        return True
    return not any(pat in entity_id for pat in profile.exclude_patterns)


def is_inspectable_entity(entity_id: str) -> bool:
    """Return True if a read tool may show this entity to a caller.

    ``COLLECTOR_DOMAINS`` answers a different question — what is worth
    snapshotting and pattern-analysing — and is additionally the second half of
    the safe-command allowlist, so a domain with no entry in the service tables
    is absent from it by design. Asking it "does this home have cameras?"
    returns no, and a model with nothing else to consult reports that to the
    owner of four Reolinks as a fact about their house. Discovery and
    permission are separate questions: an entity nobody may switch on is still
    one the caller can put on a dashboard, count, or name in an answer, and
    ``execute_command`` polices the commands on its own.

    So this is a DENY-list of four plumbing domains, not an allow-list of
    supported ones. A domain nobody has classified yet is a domain the home
    genuinely has, and the failure mode of guessing wrong runs one way: an
    entity wrongly hidden is reported as absent, while an entity wrongly shown
    is at worst a row the caller ignores.
    """
    domain = entity_id.split(".")[0]
    if domain in TOOL_HIDDEN_DOMAINS:
        return False
    return is_actionable_entity(entity_id)


# Core domains whose integration ships a ``reproduce_state`` platform — what a
# scene can set — for callers with no hass to ask.
_CORE_REPRODUCIBLE_DOMAINS: frozenset[str] = frozenset(
    {
        "alarm_control_panel",
        "climate",
        "counter",
        "cover",
        "fan",
        "group",
        "humidifier",
        "input_boolean",
        "input_datetime",
        "input_number",
        "input_select",
        "input_text",
        "light",
        "lock",
        "media_player",
        "number",
        "remote",
        "select",
        "switch",
        "text",
        "timer",
        "vacuum",
        "water_heater",
    }
)


# Domains a scene must not set even though HA could: activating a scene would
# unlock a door or disarm an alarm with none of the approval those need.
_SCENE_SECURITY_DOMAINS: frozenset[str] = frozenset({"lock", "alarm_control_panel"})


def _restorable(hass: HomeAssistant | None, domain: str) -> bool:
    """Whether the domain's integration can restore a state (``reproduce_state``)."""
    if hass is not None:
        from homeassistant.loader import (  # noqa: PLC0415
            IntegrationNotLoaded,
            async_get_loaded_integration,
        )

        try:
            integration = async_get_loaded_integration(hass, domain)
        except IntegrationNotLoaded:
            # Not loaded is not "cannot": decide by the core list instead.
            return domain in _CORE_REPRODUCIBLE_DOMAINS
        return bool(integration.platforms_exists(("reproduce_state",)))
    return domain in _CORE_REPRODUCIBLE_DOMAINS


def scene_exclusion(hass: HomeAssistant | None, entity_id: str) -> str | None:
    """Why a scene must not set *entity_id*, or None when it may.

    Home Assistant applies a scene through each domain's ``reproduce_state``,
    so that decides which domains — not a fixed list. On top: locks and alarms
    stay out (a scene would bypass their approval), and so do the device
    setting switches ``is_actionable_entity`` recognises (a camera's privacy
    mode swept into a room's scene would quietly stop it recording).
    """
    domain = entity_id.split(".", 1)[0]
    if domain in _SCENE_SECURITY_DOMAINS:
        return "a scene must not unlock or disarm — that needs an approval"
    if not _restorable(hass, domain):
        return "it has no state a scene can restore"
    if not is_actionable_entity(entity_id):
        return "it looks like a device setting, not something a scene should change"
    return None


def scene_supports(hass: HomeAssistant | None, entity_id: str) -> bool:
    """Whether a scene may set *entity_id* — see ``scene_exclusion``."""
    return scene_exclusion(hass, entity_id) is None


def is_scene_capable(entity_id: str) -> bool:
    """Return True if the entity can appear in a scene.

    Checks both domain-level scene capability and entity-level exclusions.
    """
    domain = entity_id.split(".")[0]
    profile = DOMAIN_PROFILES.get(domain)
    if profile is None or not profile.scene:
        return False
    return is_actionable_entity(entity_id)

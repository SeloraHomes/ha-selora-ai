"""Entity capability classification for Home Assistant domains.

Single source of truth for which domains participate in data collection,
scenes, and which entity-ID patterns indicate config/diagnostic entities
that should be excluded from user-facing features.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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


def is_scene_capable(entity_id: str) -> bool:
    """Return True if the entity can appear in a scene.

    Checks both domain-level scene capability and entity-level exclusions.
    """
    domain = entity_id.split(".")[0]
    profile = DOMAIN_PROFILES.get(domain)
    if profile is None or not profile.scene:
        return False
    return is_actionable_entity(entity_id)

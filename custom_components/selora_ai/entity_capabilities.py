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


def is_scene_capable(entity_id: str) -> bool:
    """Return True if the entity can appear in a scene.

    Checks both domain-level scene capability and entity-level exclusions.
    """
    domain = entity_id.split(".")[0]
    profile = DOMAIN_PROFILES.get(domain)
    if profile is None or not profile.scene:
        return False
    return is_actionable_entity(entity_id)

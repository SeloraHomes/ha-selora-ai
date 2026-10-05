"""List, create, change, delete and choose Assist pipelines.

Pipelines are a storage collection the ``assist_pipeline`` component keeps in
``hass.data``; its ``assist_pipeline/pipeline/*`` websocket commands are the
only other way in. Every field of a pipeline is required and Home Assistant's
update REPLACES the stored item, so a change is merged into what is stored and
the whole validated with the component's own ``PIPELINE_FIELDS`` first —
passing only the changed field would be refused, or wipe the rest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.components.assist_pipeline.pipeline import PipelineStorageCollection
    from homeassistant.core import HomeAssistant

# Required by Home Assistant, but None means "off" — a new pipeline gets None
# for each the caller leaves out, as the UI sends it.
_OPTIONAL_ENGINE_FIELDS: Final = (
    "stt_engine",
    "stt_language",
    "tts_engine",
    "tts_language",
    "tts_voice",
    "wake_word_entity",
    "wake_word_id",
)

# The engine fields name entities in these domains (or a legacy provider), so
# listing them is how a caller learns what it can pick.
_ENGINE_DOMAINS: Final = {
    "conversation_engine": "conversation",
    "stt_engine": "stt",
    "tts_engine": "tts",
    "wake_word_entity": "wake_word",
}


def _store(hass: HomeAssistant) -> PipelineStorageCollection | str:
    if "assist_pipeline" not in hass.config.components:
        return "Assist is not set up in this Home Assistant. It is part of default_config."
    # Keyed by the domain string — newer cores wrap it in a typed ``HassKey``
    # (equal to the string), 2025.1 uses the string itself.
    data = hass.data.get("assist_pipeline")
    if data is None or not hasattr(data, "pipeline_store"):
        return "Assist is not set up in this Home Assistant. It is part of default_config."
    return data.pipeline_store


def _fields_schema() -> vol.Schema:
    from homeassistant.components.assist_pipeline.pipeline import (  # noqa: PLC0415
        PIPELINE_FIELDS,
        validate_language,
    )

    return vol.Schema(vol.All(PIPELINE_FIELDS, validate_language))


def _legacy_engines(hass: HomeAssistant) -> dict[str, set[str]]:
    """Engines that are not entities: legacy speech providers, and conversation
    agents registered with the agent manager — the rest of what HA's own
    ``*/engine/list`` and ``conversation/agent/list`` offer. Read by the plain
    string keys those components store them under; anything unreadable on this
    core is simply not listed."""
    found: dict[str, set[str]] = {"stt_engine": set(), "tts_engine": set()}
    found["stt_engine"].update(hass.data.get("stt_providers") or {})
    found["tts_engine"].update(getattr(hass.data.get("tts_manager"), "providers", None) or {})
    agents: set[str] = set()
    try:
        from homeassistant.components.conversation.agent_manager import (  # noqa: PLC0415
            get_agent_manager,
        )

        agents = {info.id for info in get_agent_manager(hass).async_get_agent_info()}
    except (ImportError, AttributeError, KeyError):
        pass
    found["conversation_engine"] = agents
    return found


def _engines(hass: HomeAssistant) -> dict[str, list[str]]:
    legacy = _legacy_engines(hass)
    return {
        field: sorted(
            {state.entity_id for state in hass.states.async_all(domain)} | legacy.get(field, set())
        )
        for field, domain in _ENGINE_DOMAINS.items()
    }


async def async_list_pipelines(hass: HomeAssistant) -> dict[str, Any]:
    """Every pipeline, which one is preferred, and the engines to pick from."""
    store = _store(hass)
    if isinstance(store, str):
        return {"error": store}
    return {
        "pipelines": [pipeline.to_json() for pipeline in store.async_items()],
        "preferred": store.async_get_preferred_item(),
        "engines": _engines(hass),
    }


async def async_set_pipeline(
    hass: HomeAssistant,
    fields: dict[str, Any],
    *,
    pipeline_id: str | None = None,
    preferred: bool = False,
) -> dict[str, Any]:
    """Create a pipeline, or change the one named, and optionally prefer it."""
    from homeassistant.components.assist_pipeline.pipeline import (  # noqa: PLC0415
        PIPELINE_FIELDS,
    )

    store = _store(hass)
    if isinstance(store, str):
        return {"error": store}
    accepted = {str(key) for key in PIPELINE_FIELDS}
    # A blank string clears an optional engine — Home Assistant stores None.
    supplied = {
        key: (None if isinstance(value, str) and not value.strip() else value)
        for key, value in fields.items()
        if key in accepted
    }

    current = None
    if pipeline_id:
        current = store.data.get(pipeline_id)
        if current is None:
            return {
                "error": (
                    f"No pipeline {sanitize_untrusted_text(pipeline_id, 40)}. Call "
                    "list_assist_pipelines for its id."
                )
            }
        if not supplied and not preferred:
            return {"error": "Nothing to change. Pass a pipeline setting, or preferred=true."}
        candidate = {k: v for k, v in current.to_json().items() if k != "id" and k in accepted}
        candidate.update(supplied)
    else:
        candidate = {key: None for key in _OPTIONAL_ENGINE_FIELDS if key in accepted}
        candidate.update(supplied)

    try:
        validated = _fields_schema()(candidate) if supplied or current is None else None
    except vol.Invalid as exc:
        return {
            "error": (
                "Home Assistant would refuse that pipeline: "
                f"{sanitize_untrusted_text(str(exc), 300)}"
            )
        }
    try:
        if current is None:
            pipeline = await store.async_create_item(validated)
        elif validated is not None:
            pipeline = await store.async_update_item(current.id, validated)
        else:
            pipeline = current
        if preferred:
            store.async_set_preferred_item(pipeline.id)
    except (HomeAssistantError, ValueError) as exc:
        return {"error": f"Home Assistant refused it: {sanitize_untrusted_text(str(exc), 200)}"}
    return {
        "status": "updated" if current is not None else "created",
        "pipeline": pipeline.to_json(),
        "preferred": store.async_get_preferred_item() == pipeline.id,
    }


async def async_delete_pipeline(hass: HomeAssistant, pipeline_id: str) -> dict[str, Any]:
    """Delete a pipeline — never the preferred one, which Home Assistant refuses."""
    from homeassistant.helpers.collection import ItemNotFound  # noqa: PLC0415

    store = _store(hass)
    if isinstance(store, str):
        return {"error": store}
    pipeline = store.data.get(pipeline_id) if pipeline_id else None
    if pipeline is None:
        return {
            "error": (
                f"No pipeline {sanitize_untrusted_text(str(pipeline_id), 40)}. Call "
                "list_assist_pipelines for its id."
            )
        }
    # Asked here rather than caught: the error Home Assistant raises for it is
    # newer than the oldest core supported.
    if store.async_get_preferred_item() == pipeline.id:
        return {
            "error": (
                f"'{sanitize_untrusted_text(pipeline.name, 60)}' is the preferred pipeline. "
                "Make another one preferred first."
            )
        }
    try:
        await store.async_delete_item(pipeline.id)
    except ItemNotFound:
        return {"error": "That pipeline no longer exists."}
    except HomeAssistantError as exc:
        return {"error": f"Home Assistant refused it: {sanitize_untrusted_text(str(exc), 200)}"}
    return {"status": "deleted", "id": pipeline.id, "name": pipeline.name}

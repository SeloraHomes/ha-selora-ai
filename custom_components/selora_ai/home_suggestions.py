"""Suggestions drawn from how the home is organised, not from how it is used.

Everything here works on **collections**: a group helper, or one section (triggers, conditions, actions) of an automation in
``automations.yaml``. A collection says nothing about why its members belong
together, so the intent is inferred from coverage instead of stored:

* **add_to_collection** — a collection holds every entity of one kind (same
  domain and device class) in the home, or every one in a single area, except
  some that were added to Home Assistant after its newest member. Those were
  not left out; they did not exist yet. A missing entity OLDER than the newest
  member, or than a group helper's last save, was left out on purpose, so the
  scope never suggests anything.
* **group_entities** — an automation's triggers list three or more entities
  of one groupable kind, which a group helper would keep in one place.
* **add_to_frigate** — cameras Frigate could watch but does not, when the
  Frigate integration is set up (``frigate.setup``).
* **camera_idea** — an automation or scene a camera's detection could drive
  (``frigate.ideas``).
* **describe_cameras** — Frigate cameras nobody was asked about describing
  yet, while Selora AI descriptions are on (``frigate.descriptions``).
* **recognize_faces** — face recognition is off, or on with nobody in the
  Face Library (``frigate.faces``).

The Frigate set-up steps (describe, faces, add cameras) show one at a time,
in that order: the first one not dismissed.

Nothing is applied from here. The panel turns a suggestion into a chat
request, where the usual tools and cards do the work and the user sees each
change. Suggestions are recomputed on every read, so only the user's
dismissals and which ones were already notified are stored.

The collection rules know nothing about any particular domain: a new person
joins the collection of people exactly as a new leak sensor joins the leak
sensors. Frigate is the exception, as a service the home's cameras feed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components import persistent_notification
from homeassistant.const import EntityCategory
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .automation_utils import _collect_referenced_entity_ids, _read_automations_yaml
from .const import DOMAIN, PANEL_PATH
from .entity_filter import resolve_ignored_entity_ids
from .group_manager import (
    GROUP_DOMAIN,
    SUPPORTED_GROUP_TYPES,
    _resolve_members,
    group_entity_id,
    group_entries,
)

if TYPE_CHECKING:
    from .types import HomeSuggestion, HomeSuggestionCollection

_LOGGER = logging.getLogger(__name__)

_STORAGE_KEY: Final = f"{DOMAIN}.home_suggestions"
_STORAGE_VERSION: Final = 1

# Fewest members of one kind that make a collection a statement about that
# kind. One entity is just a reference.
_MIN_MEMBERS: Final = 2

# Fewest trigger entities of one kind worth replacing with a group.
_MIN_GROUPABLE_TRIGGERS: Final = 3

# Domains whose entities are configuration, not devices of a kind.
_NOT_DEVICE_DOMAINS: Final = frozenset(
    {"automation", "script", "scene", "zone", "sun", "update", GROUP_DOMAIN}
)

# Without a device class these say nothing about what the entity is: a sensor
# could measure anything, and a switch could drive a light, a pump or a
# bridge's away mode. "Every switch" is not a kind anyone collects, so two
# light switches in an automation must not claim the next switch paired.
_NEEDS_DEVICE_CLASS: Final = frozenset(
    {"sensor", "binary_sensor", "switch", "button", "number", "select", "text"}
)

_SECTIONS: Final = (
    ("triggers", ("trigger", "triggers")),
    ("conditions", ("condition", "conditions")),
    ("actions", ("action", "actions")),
)

# Registry changes after which an entity may fit a collection it did not.
_RELEVANT_CHANGES: Final = frozenset({"area_id", "device_class", "original_device_class"})

# Wait for a burst of registry events to settle: pairing a device registers
# all its entities at once, and the area is often assigned a moment later.
_NOTIFY_DELAY: Final = 30

# Only entities this recent are notified about. Older drift still shows in the
# panel, but a first start must not announce every gap in the home at once.
_NOTIFY_RECENT: Final = timedelta(days=1)

_NOTIFICATION_ID: Final = "selora_ai_home_suggestions"
_NOTIFY_MAX_LISTED: Final = 5
_STORE_MAX_IDS: Final = 500

_NOTIFY_TITLE_BY_LANG: Final[dict[str, str]] = {
    "en": "Selora AI noticed new devices",
    "fr": "Selora AI a repéré de nouveaux appareils",
    "de": "Selora AI hat neue Geräte bemerkt",
    "es": "Selora AI ha detectado nuevos dispositivos",
    "it": "Selora AI ha notato nuovi dispositivi",
    "nl": "Selora AI heeft nieuwe apparaten opgemerkt",
    "hu": "A Selora AI új eszközöket észlelt",
    "zh": "Selora AI 发现了新设备",
    "pt": "O Selora AI detetou novos dispositivos",
    "ja": "Selora AI が新しいデバイスを検出しました",
    "ko": "Selora AI가 새 기기를 발견했습니다",
    "ru": "Selora AI обнаружил новые устройства",
}

_NOTIFY_INTRO_BY_LANG: Final[dict[str, str]] = {
    "en": "These look like they belong with devices you already grouped:",
    "fr": "Ces appareils semblent aller avec ceux que vous avez déjà regroupés :",
    "de": "Diese scheinen zu Geräten zu gehören, die du bereits gruppiert hast:",
    "es": "Parecen pertenecer a dispositivos que ya agrupaste:",
    "it": "Sembrano appartenere a dispositivi che hai già raggruppato:",
    "nl": "Deze lijken bij apparaten te horen die je al hebt gegroepeerd:",
    "hu": "Ezek a már csoportosított eszközeidhez tartozónak tűnnek:",
    "zh": "这些设备似乎属于你已分组的设备：",
    "pt": "Estes parecem pertencer a dispositivos que já agrupou:",
    "ja": "すでにまとめたデバイスの仲間のようです：",
    "ko": "이미 묶어 둔 기기들과 같은 종류로 보입니다:",
    "ru": "Похоже, они относятся к устройствам, которые вы уже сгруппировали:",
}

_NOTIFY_LINK_BY_LANG: Final[dict[str, str]] = {
    "en": "Review in Selora AI",
    "fr": "Voir dans Selora AI",
    "de": "In Selora AI ansehen",
    "es": "Revisar en Selora AI",
    "it": "Rivedi in Selora AI",
    "nl": "Bekijken in Selora AI",
    "hu": "Megtekintés a Selora AI-ban",
    "zh": "在 Selora AI 中查看",
    "pt": "Rever no Selora AI",
    "ja": "Selora AI で確認",
    "ko": "Selora AI에서 확인",
    "ru": "Посмотреть в Selora AI",
}

_Kind = tuple[str, str | None]


@dataclass(frozen=True)
class _Collection:
    id: str
    source: str  # "group_helper" | "automation"
    name: str
    ref: str  # the group's entity_id, or the automation's entity_id / config id
    members: frozenset[str]
    section: str | None = None  # automation section the members came from
    # The automation's config id, which the panel loads into the chat to edit.
    automation_id: str | None = None
    # Triggers only: the members split by trigger configuration (everything
    # but which entities it watches). Only one part can share a group.
    trigger_parts: tuple[frozenset[str], ...] = ()
    # When the user last saved the collection, where Home Assistant records it.
    # An entity that existed then and was not picked was left out on purpose.
    configured_at: datetime | None = None


@dataclass(frozen=True)
class _Home:
    kinds: dict[str, _Kind]
    areas: dict[str, str | None]
    created: dict[str, datetime]
    names: dict[str, str]
    # ``kinds`` inverted: every collection asks for each of its kinds, and a
    # scan of the whole home per ask ran on the event loop.
    by_kind: dict[_Kind, frozenset[str]]

    def of_kind(self, kind: _Kind) -> frozenset[str]:
        return self.by_kind.get(kind, frozenset())


def _normalize_lang(language: str | None) -> str:
    base = str(language or "en").lower().split("-")[0]
    return base if base in _NOTIFY_TITLE_BY_LANG else "en"


def _kind_of(hass: HomeAssistant, entry: er.RegistryEntry | None, entity_id: str) -> _Kind | None:
    domain = entity_id.split(".", 1)[0]
    if domain in _NOT_DEVICE_DOMAINS or domain.startswith("input_"):
        return None
    device_class = None
    if entry is not None:
        device_class = entry.device_class or entry.original_device_class
    if device_class is None and (state := hass.states.get(entity_id)) is not None:
        device_class = state.attributes.get("device_class")
    if device_class is None and domain in _NEEDS_DEVICE_CLASS:
        return None
    return (domain, str(device_class) if device_class else None)


def _build_home(hass: HomeAssistant) -> _Home:
    """Every entity that can belong to a collection, with its kind and area."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    ignored = resolve_ignored_entity_ids(hass)
    kinds: dict[str, _Kind] = {}
    areas: dict[str, str | None] = {}
    created: dict[str, datetime] = {}
    names: dict[str, str] = {}

    def _add(entity_id: str, entry: er.RegistryEntry | None) -> None:
        if entity_id in ignored or (kind := _kind_of(hass, entry, entity_id)) is None:
            return
        kinds[entity_id] = kind
        area = entry.area_id if entry is not None else None
        if area is None and entry is not None and entry.device_id:
            device = dev_reg.async_get(entry.device_id)
            area = device.area_id if device is not None else None
        areas[entity_id] = area
        state = hass.states.get(entity_id)
        names[entity_id] = str(
            (state.attributes.get("friendly_name") if state is not None else None)
            or (entry.name or entry.original_name if entry is not None else None)
            or entity_id
        )

    for entry in ent_reg.entities.values():
        # A group's own entity shares its members' kind; it is the collection,
        # never a member of one.
        if entry.disabled_by is not None or entry.platform == GROUP_DOMAIN:
            continue
        if entry.entity_category in (EntityCategory.CONFIG, EntityCategory.DIAGNOSTIC):
            continue
        _add(entry.entity_id, entry)
        if entry.entity_id in kinds:
            created[entry.entity_id] = entry.created_at
    # Entities without a registry entry (YAML-defined) have no creation date,
    # so they count as members of their kind but are never newcomers.
    for state in hass.states.async_all():
        if state.entity_id not in kinds and ent_reg.async_get(state.entity_id) is None:
            _add(state.entity_id, None)
    by_kind: dict[_Kind, set[str]] = defaultdict(set)
    for entity_id, kind in kinds.items():
        by_kind[kind].add(entity_id)
    return _Home(kinds, areas, created, names, {k: frozenset(v) for k, v in by_kind.items()})


def _trigger_parts(body: Any) -> tuple[frozenset[str], ...]:
    """Entities whose triggers differ only in which entities they watch.

    One group stands in for a part, not for the whole list: a sensor in an
    "above" trigger and another in a "below" one need different statistics.
    """
    parts: dict[str, set[str]] = defaultdict(set)
    for trigger in body if isinstance(body, list) else [body]:
        if not isinstance(trigger, dict):
            continue
        config = {k: v for k, v in trigger.items() if k not in ("entity_id", "id", "alias")}
        key = json.dumps(config, sort_keys=True, default=str)
        parts[key] |= _collect_referenced_entity_ids({"triggers": [trigger]})
    return tuple(frozenset(p) for p in parts.values() if p)


def _automation_collections(
    hass: HomeAssistant, automations: list[dict[str, Any]]
) -> list[_Collection]:
    id_to_entity = {
        str(st.attributes.get("id")): st.entity_id
        for st in hass.states.async_all("automation")
        if st.attributes.get("id")
    }
    ent_reg = er.async_get(hass)
    collections: list[_Collection] = []
    for auto in automations:
        auto_id = str(auto.get("id") or "")
        if not auto_id:
            continue
        name = str(auto.get("alias") or auto_id)
        ref = id_to_entity.get(auto_id, auto_id)
        # automations.yaml keeps no save time; the automation's registry entry
        # is the closest record of when the user wrote it. Without it, an
        # entity paired the day before was "added after" a rule written today.
        entry = ent_reg.async_get(ref) if ref != auto_id else None
        configured_at = max(entry.created_at, entry.modified_at) if entry is not None else None
        for section, keys in _SECTIONS:
            body = next((auto[k] for k in keys if auto.get(k)), None)
            if body is None:
                continue
            members = _collect_referenced_entity_ids({keys[0]: body})
            if len(members) >= _MIN_MEMBERS:
                collections.append(
                    # Sections share the id: an entity missing from both the
                    # triggers and the conditions is one suggestion.
                    _Collection(
                        f"automation:{auto_id}",
                        "automation",
                        name,
                        ref,
                        frozenset(members),
                        section,
                        automation_id=auto_id,
                        trigger_parts=_trigger_parts(body) if section == "triggers" else (),
                        configured_at=configured_at,
                    )
                )
    return collections


def _group_collections(hass: HomeAssistant) -> list[_Collection]:
    collections: list[_Collection] = []
    for entry in group_entries(hass):
        entity_id = group_entity_id(hass, entry)
        if entity_id is None:
            continue
        members = _resolve_members(hass, [str(e) for e in entry.options.get("entities", []) or []])
        name = str(entry.options.get("name") or entry.title)
        collections.append(
            _Collection(
                f"group:{entity_id}",
                "group_helper",
                name,
                entity_id,
                frozenset(members),
                configured_at=entry.modified_at,
            )
        )
    # YAML groups are left out: no tool can edit them, so the chat request a
    # suggestion opens could only fail.
    return collections


def _newcomers(
    home: _Home, collection: _Collection, members: set[str], candidates: set[str]
) -> set[str]:
    """``candidates`` when every one postdates the newest member and the
    collection's last save, else nothing."""
    if not candidates:
        return set()
    newest = max((home.created[m] for m in members if m in home.created), default=None)
    if newest is None:
        return set()
    if collection.configured_at is not None:
        newest = max(newest, collection.configured_at)
    if all(c in home.created and home.created[c] > newest for c in candidates):
        return candidates
    return set()


@dataclass(frozen=True)
class _Gap:
    """Why an entity belongs in a collection: the members of its kind there,
    and the area they all cover (None: they cover the whole home)."""

    members: frozenset[str]
    area_id: str | None


def _missing_from(home: _Home, collection: _Collection) -> dict[str, _Gap]:
    """Entities that belong in ``collection`` by its coverage, and why."""
    by_kind: dict[_Kind, set[str]] = defaultdict(set)
    for member in collection.members:
        if (kind := home.kinds.get(member)) is not None:
            by_kind[kind].add(member)
    missing: dict[str, _Gap] = {}
    for kind, members in by_kind.items():
        if len(members) < _MIN_MEMBERS:
            continue
        universe = home.of_kind(kind)
        scope: str | None = None
        found = _newcomers(home, collection, members, universe - members)
        if not found:
            member_areas = {home.areas.get(m) for m in members}
            if len(member_areas) == 1 and (scope := member_areas.pop()) is not None:
                in_area = {e for e in universe if home.areas.get(e) == scope}
                found = _newcomers(home, collection, members, in_area - members)
        for entity_id in found:
            missing[entity_id] = _Gap(frozenset(members), scope)
    return missing


def _entity(home: _Home, entity_id: str) -> dict[str, str]:
    return {"entity_id": entity_id, "name": home.names.get(entity_id, entity_id)}


def _collection_payload(collection: _Collection) -> HomeSuggestionCollection:
    payload: HomeSuggestionCollection = {
        "source": collection.source,
        "name": collection.name,
        "ref": collection.ref,
    }
    if collection.automation_id is not None:
        payload["automation_id"] = collection.automation_id
    return payload


def _detect(
    hass: HomeAssistant, automations: list[dict[str, Any]]
) -> list[tuple[HomeSuggestion, datetime | None]]:
    """Every suggestion, with the creation date of its newest entity."""
    home = _build_home(hass)
    automation_collections = _automation_collections(hass, automations)
    found: dict[str, tuple[HomeSuggestion, datetime | None]] = {}

    area_reg = ar.async_get(hass)
    group_collections = _group_collections(hass)
    for collection in group_collections + automation_collections:
        for entity_id, gap in sorted(_missing_from(home, collection).items()):
            suggestion_id = f"add:{collection.id}:{entity_id}"
            area = area_reg.async_get_area(gap.area_id) if gap.area_id else None
            suggestion: HomeSuggestion = {
                "suggestion_id": suggestion_id,
                "type": "add_to_collection",
                "entities": [_entity(home, entity_id)],
                "collection": _collection_payload(collection),
                "members": sorted(
                    (_entity(home, m) for m in gap.members), key=lambda e: e["name"].casefold()
                ),
                "area": area.name if area is not None else None,
            }
            found[suggestion_id] = (suggestion, home.created.get(entity_id))

    for collection in automation_collections:
        if collection.section != "triggers":
            continue
        by_kind: dict[tuple[int, _Kind], list[str]] = defaultdict(list)
        for index, part in enumerate(collection.trigger_parts):
            for member in sorted(part):
                kind = home.kinds.get(member)
                if kind is not None and kind[0] in SUPPORTED_GROUP_TYPES:
                    by_kind[index, kind].append(member)
        for members in by_kind.values():
            if len(members) < _MIN_GROUPABLE_TRIGGERS:
                continue
            # A group of exactly these was made, whether or not the automation
            # went on to use it: that part is the user's call. A larger group
            # is no stand-in, since its extra members would fire the rule.
            if any(g.members == set(members) for g in group_collections):
                continue
            # Named by its devices: two triggers over the same ones (unlocked,
            # then open) are one group to make, different ones are two.
            suggestion_id = f"group:{collection.id}:{','.join(members)}"
            suggestion = {
                "suggestion_id": suggestion_id,
                "type": "group_entities",
                "entities": [_entity(home, m) for m in members],
                "collection": _collection_payload(collection),
            }
            found[suggestion_id] = (suggestion, None)
    return list(found.values())


async def _async_read_automations(hass: HomeAssistant) -> list[dict[str, Any]]:
    path = Path(hass.config.config_dir) / "automations.yaml"
    automations = await hass.async_add_executor_job(_read_automations_yaml, path)
    return [a for a in automations if isinstance(a, dict)]


class HomeSuggestionStore:
    """Persists which suggestions were dismissed and which were notified."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, dict[str, str]]] = Store(hass, _STORAGE_VERSION, _STORAGE_KEY)
        self._data: dict[str, dict[str, str]] | None = None

    async def _load(self) -> dict[str, dict[str, str]]:
        if self._data is None:
            raw = await self._store.async_load()
            self._data = raw if isinstance(raw, dict) else {}
            self._data.setdefault("dismissed", {})
            self._data.setdefault("notified", {})
        return self._data

    async def ids(self, bucket: str) -> set[str]:
        return set((await self._load())[bucket])

    async def add(self, bucket: str, suggestion_ids: list[str]) -> None:
        data = await self._load()
        now = dt_util.utcnow().isoformat()
        for suggestion_id in suggestion_ids:
            data[bucket][suggestion_id] = now
        if len(data[bucket]) > _STORE_MAX_IDS:
            oldest = sorted(data[bucket], key=data[bucket].__getitem__)
            for suggestion_id in oldest[: len(data[bucket]) - _STORE_MAX_IDS]:
                del data[bucket][suggestion_id]
        await self._store.async_save(data)


def get_home_suggestion_store(hass: HomeAssistant) -> HomeSuggestionStore:
    """The shared store, created on first use."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get("_home_suggestion_store")
    if store is None:
        store = domain_data["_home_suggestion_store"] = HomeSuggestionStore(hass)
    return store


async def async_home_suggestions(hass: HomeAssistant) -> list[HomeSuggestion]:
    """Current suggestions the user has not dismissed."""
    from .frigate.descriptions import async_describe_suggestions  # noqa: PLC0415
    from .frigate.faces import async_face_suggestions  # noqa: PLC0415
    from .frigate.ideas import async_camera_ideas  # noqa: PLC0415
    from .frigate.setup import async_frigate_suggestions  # noqa: PLC0415

    dismissed = await get_home_suggestion_store(hass).ids("dismissed")
    automations = await _async_read_automations(hass)
    found = [s for s, _ in _detect(hass, automations)]
    # Setting Frigate up is one step at a time, so its steps take one row of
    # the few the chat home shows: the first the user has not dismissed.
    frigate = (
        await async_describe_suggestions(hass)
        + await async_face_suggestions(hass)
        + await async_frigate_suggestions(hass)
    )
    found += [s for s in frigate if s["suggestion_id"] not in dismissed][:1]
    found += [
        {
            "suggestion_id": idea["idea_id"],
            "type": "camera_idea",
            "entities": [idea["camera"]] if idea["camera"]["entity_id"] else [],
            "collection": {"source": "camera", "name": idea["camera"]["name"], "ref": ""},
            "idea": idea,
        }
        for idea in await async_camera_ideas(hass)
    ]
    return [s for s in found if s["suggestion_id"] not in dismissed]


async def async_dismiss_home_suggestion(hass: HomeAssistant, suggestion_id: str) -> None:
    """Never offer this suggestion again."""
    await get_home_suggestion_store(hass).add("dismissed", [suggestion_id])


class HomeSuggestionNotifier:
    """Raises a persistent notification when a new entity fits a collection."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._unsubs: list[CALLBACK_TYPE] = []
        self._cancel_check: CALLBACK_TYPE | None = None

    @callback
    def async_start(self) -> None:
        self._unsubs = [
            self._hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_registry_update
            ),
            # An entity without an area of its own takes its device's, so a
            # device moved into a room can make its entities fit a collection.
            self._hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._handle_device_update
            ),
        ]

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self._cancel_check is not None:
            self._cancel_check()
            self._cancel_check = None

    @callback
    def _handle_registry_update(self, event: Event) -> None:
        action = event.data.get("action")
        if action == "update" and not _RELEVANT_CHANGES & set(event.data.get("changes") or {}):
            return
        if action not in ("create", "update"):
            return
        self._schedule_check()

    @callback
    def _handle_device_update(self, event: Event) -> None:
        if event.data.get("action") == "update" and "area_id" in (event.data.get("changes") or {}):
            self._schedule_check()

    @callback
    def _schedule_check(self) -> None:
        if self._cancel_check is None:
            self._cancel_check = async_call_later(self._hass, _NOTIFY_DELAY, self._fire_check)

    @callback
    def _fire_check(self, _now: Any = None) -> None:
        self._cancel_check = None
        self._hass.async_create_background_task(self.async_check(), f"{DOMAIN}_home_suggestions")

    async def async_check(self) -> None:
        """Notify about recent additions not yet notified or dismissed."""
        store = get_home_suggestion_store(self._hass)
        try:
            automations = await _async_read_automations(self._hass)
            found = _detect(self._hass, automations)
        except (OSError, ValueError, KeyError, TypeError):
            _LOGGER.exception("Home suggestion check failed")
            return
        skip = await store.ids("dismissed") | await store.ids("notified")
        cutoff = dt_util.utcnow() - _NOTIFY_RECENT
        fresh = [
            s
            for s, created in found
            if s["type"] == "add_to_collection"
            and s["suggestion_id"] not in skip
            and created is not None
            and created > cutoff
        ]
        if not fresh:
            return
        lang = _normalize_lang(self._hass.config.language)
        lines = [
            f"- {s['entities'][0]['name']} → {s['collection']['name']}"
            for s in fresh[:_NOTIFY_MAX_LISTED]
        ]
        if len(fresh) > _NOTIFY_MAX_LISTED:
            lines.append(f"- … +{len(fresh) - _NOTIFY_MAX_LISTED}")
        message = "\n".join(
            [
                _NOTIFY_INTRO_BY_LANG[lang],
                "",
                *lines,
                "",
                f"[{_NOTIFY_LINK_BY_LANG[lang]}](/{PANEL_PATH}?home_suggestions=1)",
            ]
        )
        persistent_notification.async_create(
            self._hass, message, title=_NOTIFY_TITLE_BY_LANG[lang], notification_id=_NOTIFICATION_ID
        )
        await store.add("notified", [s["suggestion_id"] for s in fresh])

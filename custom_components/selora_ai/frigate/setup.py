"""Adding cameras to Frigate from chat: a card, the camera logins, the write.

Two kinds of camera go on one card:

* **Home Assistant cameras** — already in HA through their own integration,
  which keeps their PTZ, IR and siren controls. Their streams are known.
* **Network cameras** — found on the LAN (or named in chat) and not in HA.
  They go straight to Frigate, whose integration then brings them into HA.
  Their streams need the camera's login, typed into the card.

The card is a client action: the panel posts the logins to
``selora_ai/frigate/add_cameras`` with the plan id, so they never pass
through the model, the conversation or a log. The plan itself — which
cameras, which settings — stays here, keyed by that id; the panel cannot
change it, and the confirm checks the cameras are still the ones shown.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit
import uuid

from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN
from ..helpers import sanitize_untrusted_text
from .cameras import (
    Candidate,
    Skipped,
    Stream,
    async_camera_candidates,
    brand_streams,
    build_config,
    clean_objects,
    existing_hosts,
    existing_names,
    for_go2rtc,
    model_labels,
    shown,
    unique_name,
    unsupported_error,
    unsupported_labels,
    with_credentials,
)
from .client import FRIGATE_DOMAIN, FrigateClient, FrigateError, frigate_entry
from .discovery import (
    SUGGESTION_CACHE,
    NetworkCamera,
    async_scan,
    async_user_cameras,
    cached_cameras,
    merge,
    user_streams,
)
from .moving import async_moved_camera
from .onvif import ONVIF_PORTS, OnvifAuthError, OnvifCamera, OnvifError, Profile

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..types import (
        FrigateResult,
        FrigateResultCamera,
        HomeSuggestion,
        HomeSuggestionEntity,
    )

_LOGGER = logging.getLogger(__name__)

_NOT_SET_UP: Final = (
    "Frigate is not set up in Home Assistant: the Frigate integration has to be "
    "installed and connected first."
)
_MAX_RECORD_DAYS: Final = 365
_MAX_HOSTS: Final = 10
_NOT_AN_ADDRESS: Final = "it is not an address on your network"
_MAX_SKIPPED_SHOWN: Final = 8

# Frigate takes up to a minute to restart; its integration is reloaded once it
# answers again, so the new cameras' entities appear.
_RESTART_GRACE: Final = 10
_RESTART_POLL: Final = 5
_RESTART_WAIT: Final = 180

# The chat home reads suggestions on every visit; Frigate is asked at most
# this often.
_SUGGESTION_TTL: Final = 300

_PLANS: Final = "_frigate_plans"
_PLAN_TTL: Final = 1800

_ADDED_BY_LANG: Final[dict[str, str]] = {
    "en": "Added {names} to Frigate. Frigate is restarting; they appear in Home Assistant in about a minute.",
    "fr": "{names} ajouté(s) à Frigate. Frigate redémarre ; elles apparaîtront dans Home Assistant d'ici une minute environ.",
    "de": "{names} zu Frigate hinzugefügt. Frigate startet neu; sie erscheinen in etwa einer Minute in Home Assistant.",
    "es": "{names} añadida(s) a Frigate. Frigate se está reiniciando; aparecerán en Home Assistant en un minuto aproximadamente.",
    "it": "{names} aggiunta/e a Frigate. Frigate si sta riavviando; compariranno in Home Assistant tra circa un minuto.",
    "nl": "{names} toegevoegd aan Frigate. Frigate wordt herstart; ze verschijnen over ongeveer een minuut in Home Assistant.",
    "hu": "{names} hozzáadva a Frigate-hez. A Frigate újraindul; körülbelül egy perc múlva megjelennek a Home Assistantben.",
    "zh": "已将 {names} 添加到 Frigate。Frigate 正在重启，大约一分钟后会出现在 Home Assistant 中。",
    "pt": "{names} adicionada(s) ao Frigate. O Frigate está a reiniciar; aparecem no Home Assistant dentro de cerca de um minuto.",
    "ja": "{names} を Frigate に追加しました。Frigate を再起動中です。1 分ほどで Home Assistant に表示されます。",
    "ko": "{names}을(를) Frigate에 추가했습니다. Frigate가 다시 시작되며 약 1분 후 Home Assistant에 나타납니다.",
    "ru": "{names} добавлены во Frigate. Frigate перезапускается; примерно через минуту они появятся в Home Assistant.",
}


_SAVED_BY_LANG: Final[dict[str, str]] = {
    "en": "Saved {names} in Frigate, but Frigate could not restart itself, so they start with its next restart.",
    "fr": "{names} enregistré(s) dans Frigate, mais Frigate n'a pas pu redémarrer : elles démarreront à son prochain redémarrage.",
    "de": "{names} in Frigate gespeichert, aber Frigate konnte sich nicht neu starten; sie starten beim nächsten Neustart.",
    "es": "{names} guardada(s) en Frigate, pero Frigate no pudo reiniciarse; empezarán con su próximo reinicio.",
    "it": "{names} salvata/e in Frigate, ma Frigate non è riuscito a riavviarsi; partiranno al prossimo riavvio.",
    "nl": "{names} opgeslagen in Frigate, maar Frigate kon niet herstarten; ze starten bij de volgende herstart.",
    "hu": "{names} elmentve a Frigate-ben, de a Frigate nem tudott újraindulni; a következő újraindításkor indulnak el.",
    "zh": "已在 Frigate 中保存 {names}，但 Frigate 无法自行重启，它们将在下次重启后启用。",
    "pt": "{names} guardada(s) no Frigate, mas o Frigate não conseguiu reiniciar; começam no próximo reinício.",
    "ja": "{names} を Frigate に保存しましたが、Frigate が自分で再起動できなかったため、次回の再起動後に有効になります。",
    "ko": "{names}을(를) Frigate에 저장했지만 Frigate가 스스로 다시 시작하지 못해 다음 재시작 때 시작됩니다.",
    "ru": "{names} сохранены во Frigate, но Frigate не смог перезапуститься; они заработают после следующего перезапуска.",
}


@dataclass(frozen=True)
class _Network:
    """A network camera on a plan, with its name in Frigate."""

    key: str
    found: NetworkCamera
    camera: str


@dataclass
class _Plan:
    arguments: dict[str, Any]
    fingerprint: str
    network: list[_Network]
    created: float = field(default_factory=time.monotonic)
    added: list[str] | None = None
    # (name, Frigate name) of each camera written, for the result card.
    added_cameras: list[tuple[str, str]] = field(default_factory=list)
    restart_failed: bool = False


def _options(arguments: dict[str, Any]) -> tuple[list[str], bool, int | None]:
    record = arguments.get("record")
    days = arguments.get("record_days")
    try:
        record_days = max(1, min(_MAX_RECORD_DAYS, int(days))) if days is not None else None
    except (TypeError, ValueError):
        record_days = None
    return clean_objects(arguments.get("objects")), record is not False, record_days


def _hosts(arguments: dict[str, Any]) -> list[str]:
    hosts = arguments.get("hosts")
    return [str(h) for h in hosts][:_MAX_HOSTS] if isinstance(hosts, list) else []


def _stream_urls(arguments: dict[str, Any]) -> list[str]:
    urls = arguments.get("stream_urls")
    return [str(u) for u in urls][: _MAX_HOSTS * 2] if isinstance(urls, list) else []


def _pick(
    candidates: list[Candidate], skipped: list[Skipped], entity_ids: Any
) -> tuple[list[Candidate], list[Skipped]]:
    """The candidates the caller named (all when none), and the names it could not add."""
    if not isinstance(entity_ids, list) or not entity_ids:
        return candidates, skipped
    wanted = {str(e).strip().lower() for e in entity_ids}
    chosen = [c for c in candidates if wanted & set(c.entity_ids)]
    known = {e for c in chosen for e in c.entity_ids}
    named_skips = [s for s in skipped if s.entity_id in wanted]
    known |= {s.entity_id for s in named_skips}
    unknown = [
        Skipped(e, e, "it is not a camera Home Assistant has") for e in sorted(wanted - known)
    ]
    return chosen, named_skips + unknown


def _fingerprint(
    candidates: list[Candidate],
    network: list[_Network],
    objects: list[str],
    record: bool,
    days: int | None,
) -> str:
    """What the user approves: these cameras, these streams, these settings."""
    approved = {
        "cameras": {
            c.camera: [c.main.entity_id, shown(c.main.url)]
            + ([c.sub.entity_id, shown(c.sub.url)] if c.sub else [])
            for c in candidates
        },
        "network": {n.camera: [n.found.host, n.found.port, *n.found.streams] for n in network},
        "objects": objects,
        "record": record,
        "days": days,
    }
    return hashlib.sha256(json.dumps(approved, sort_keys=True).encode()).hexdigest()


def _frigate_controls(hass: HomeAssistant, entity_id: str) -> list[dict[str, str]] | None:
    """The switches of a camera Frigate already has, or None for any other entity.

    Frigate's own camera entity is what a search for the camera finds, so it
    is what the model names when asked to change recording or detection. The
    answer it needs is not "cannot add" but which switch does that.
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry is None or entry.platform != FRIGATE_DOMAIN or entry.domain != "camera":
        return None
    controls: list[dict[str, str]] = []
    for sibling in er.async_entries_for_device(ent_reg, entry.device_id or ""):
        state = hass.states.get(sibling.entity_id)
        if sibling.domain == "switch" and not sibling.disabled and state is not None:
            controls.append(
                {"entity_id": sibling.entity_id, "name": state.name, "state": state.state}
            )
    return controls


@dataclass
class _Gathered:
    candidates: list[Candidate]
    network: list[_Network]
    skipped: list[Skipped]
    in_frigate: dict[str, list[dict[str, str]]]
    # Network address → the Frigate camera it may be, moved there.
    moved: dict[str, str] = field(default_factory=dict)
    # What the running model can report (cameras.model_labels).
    labels: frozenset[str] | None = None


async def _gather(
    hass: HomeAssistant, client: FrigateClient, arguments: dict[str, Any]
) -> _Gathered:
    """Every camera these arguments name that Frigate could add, and why the
    others cannot be."""
    config = await client.get_config()
    candidates, skipped = await async_camera_candidates(hass, config)
    ha_hosts = {urlsplit(c.main.url).hostname for c in candidates}
    entity_ids = arguments.get("entity_ids")
    hosts = _hosts(arguments)
    chosen, skipped = _pick(candidates, skipped, entity_ids)
    in_frigate: dict[str, list[dict[str, str]]] = {}
    for index, skip in enumerate(skipped):
        if (controls := _frigate_controls(hass, skip.entity_id)) is not None:
            in_frigate[skip.entity_id] = controls
            skipped[index] = Skipped(skip.entity_id, skip.name, "it is already in Frigate")

    stream_urls = _stream_urls(arguments)
    if hosts or stream_urls:
        found = merge(user_streams(stream_urls), await async_user_cameras(hass, hosts))
        named = {f.host for f in found}
        given_names = {f.name for f in found}
        for given in hosts + stream_urls:
            host = (urlsplit(given).hostname if "://" in given else given) or given
            if host.strip().lower() not in named | given_names:
                skipped.append(
                    Skipped(
                        given,
                        shown(given) if "://" in given else given,
                        _NOT_AN_ADDRESS,
                    )
                )
        # A camera the scan or Home Assistant knows lends its name and brand.
        found = merge(found, [c for c in cached_cameras(hass) if c.host in named])
        # What the user called the one camera they named beats its address.
        given_name = sanitize_untrusted_text(str(arguments.get("name") or ""), 60).strip()
        if given_name and len(found) == 1:
            found = [replace(found[0], name=given_name)]
    elif isinstance(entity_ids, list) and entity_ids:
        found = []
    else:
        cached = hass.data.get(DOMAIN, {}).get("_frigate_network_cameras")
        found = await async_scan(hass) if cached is None else cached_cameras(hass)

    in_frigate_hosts = existing_hosts(config)
    taken = existing_names(config) | {c.camera for c in chosen}
    network: list[_Network] = []
    moved: dict[str, str] = {}
    for camera in found:
        if camera.host in in_frigate_hosts:
            skipped.append(Skipped(camera.host, camera.name, "it is already in Frigate"))
        elif camera.host in ha_hosts:
            # Home Assistant has it, so it is on the card as an HA camera.
            continue
        else:
            network.append(_Network(camera.host, camera, unique_name(camera.name, taken)))
    # Each probe can wait out a timeout: together, not one after another.
    typed = [n.found for n in network if n.found.streams]
    for camera, was in zip(
        typed,
        await asyncio.gather(*(async_moved_camera(config, c.streams) for c in typed)),
        strict=True,
    ):
        if was:
            moved[camera.host] = was
    return _Gathered(chosen, network, skipped, in_frigate, moved, model_labels(config))


def _label(gathered: _Gathered, objects: list[str], record: bool, days: int | None) -> str:
    names = ", ".join(
        [c.name for c in gathered.candidates] + [n.found.name for n in gathered.network]
    )
    keeps = (f"recording, kept {days} days" if days else "recording") if record else "no recording"
    label = f"Add {names} to Frigate (tracks {', '.join(objects)}; {keeps})"
    return sanitize_untrusted_text(label, 300)


def _rows(gathered: _Gathered) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "key": c.camera,
            "source": "home_assistant",
            "name": c.name,
            "area": c.area,
            "entity_ids": c.entity_ids,
            "record_stream": shown(c.main.url),
            "detect_stream": shown((c.sub or c.main).url),
        }
        for c in gathered.candidates
    ]
    rows.extend(
        {
            "key": n.key,
            "source": "network",
            "name": sanitize_untrusted_text(n.found.name, 60),
            "address": n.found.host,
            "brand": n.found.brand,
            "needs_login": True,
            **({"stream": shown(n.found.streams[0])} if n.found.streams else {}),
        }
        for n in gathered.network
    )
    return rows


def _store(hass: HomeAssistant) -> dict[str, _Plan]:
    plans: dict[str, _Plan] = hass.data.setdefault(DOMAIN, {}).setdefault(_PLANS, {})
    now = time.monotonic()
    for plan_id in [p for p, plan in plans.items() if now - plan.created > _PLAN_TTL]:
        del plans[plan_id]
    return plans


async def async_preview_add_cameras(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A card for adding cameras to Frigate; network cameras ask for a login."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        gathered = await _gather(hass, client, arguments)
    except FrigateError as exc:
        return {"error": str(exc)}
    skipped_rows = [
        {"camera": s.entity_id, "name": s.name, "reason": s.reason} for s in gathered.skipped
    ]
    # An address that is not one (a room name taken for a host) is answered
    # with the cameras the scan did find, so the next call can name the right one.
    found_on_network = (
        [{"name": c.name, "address": c.host} for c in cached_cameras(hass)]
        if any(s.reason == _NOT_AN_ADDRESS for s in gathered.skipped)
        else []
    )
    if not gathered.candidates and not gathered.network:
        result: dict[str, Any] = {
            "error": "No camera can be added to Frigate.",
            "skipped": skipped_rows,
            **({"found_on_network": found_on_network} if found_on_network else {}),
        }
        if gathered.in_frigate:
            result["already_in_frigate"] = gathered.in_frigate
            result["hint"] = (
                "These cameras are already in Frigate. Recording, detection, motion and "
                "snapshots are their switches above: change one with execute_command "
                "(switch.turn_on / switch.turn_off). What a camera looks for (person, "
                "car…) is not a switch: change it with set_camera_objects."
            )
        return result
    objects, record, days = _options(arguments)
    if gathered.labels is not None and (missing := unsupported_labels(objects, gathered.labels)):
        return unsupported_error(missing, gathered.labels)
    plan_id = uuid.uuid4().hex
    _store(hass)[plan_id] = _Plan(
        arguments={
            k: arguments.get(k)
            for k in (
                "entity_ids",
                "hosts",
                "stream_urls",
                "name",
                "objects",
                "record",
                "record_days",
            )
        },
        fingerprint=_fingerprint(gathered.candidates, gathered.network, objects, record, days),
        network=gathered.network,
    )
    rows = _rows(gathered)
    return {
        "requires_approval": True,
        "client_action": {
            "kind": "add_frigate_cameras",
            "plan_id": plan_id,
            "label": _label(gathered, objects, record, days),
            "cameras": rows,
            "objects": objects,
            "record": record,
            "record_days": days,
            # Shown on the card: the user should see what is left out, not
            # only the model.
            "skipped": [
                {"name": sanitize_untrusted_text(s.name, 60), "reason": s.reason}
                for s in gathered.skipped[:_MAX_SKIPPED_SHOWN]
            ],
        },
        "cameras": rows,
        "skipped": skipped_rows,
        **(
            {"note": "The card asks for each network camera's login; never ask for it in chat."}
            if gathered.network
            else {}
        ),
        **(
            {
                "possibly_moved": gathered.moved,
                "moved_hint": (
                    "A camera above is read from the same port and path as a Frigate "
                    "camera whose address no longer answers: it may be that camera at a "
                    "new address. Ask the user; if so, move_frigate_camera keeps its "
                    "history and login, and this card is not needed."
                ),
            }
            if gathered.moved
            else {}
        ),
    }


async def _async_onvif_profiles(
    hass: HomeAssistant, found: NetworkCamera, username: str, password: str
) -> list[Profile]:
    """The camera's profiles, on its ONVIF port — tried in turn when unknown.

    A refused login ends the search: that port is the camera's.
    """
    error: OnvifError = OnvifError("the camera did not answer ONVIF")
    for port in (found.port,) if found.port else ONVIF_PORTS:
        try:
            return await OnvifCamera(hass, found.host, port).async_profiles(username, password)
        except OnvifAuthError:
            raise
        except OnvifError as exc:
            error = exc
    raise error


async def _resolve_network(
    hass: HomeAssistant, camera: _Network, username: str, password: str
) -> tuple[Candidate | None, str | None]:
    """The camera's streams with this login, or why they could not be had."""
    found = camera.found
    if found.streams:
        # The user gave its stream address: nothing to ask the camera. A
        # camera that streams without a login gets none added.
        main_uri, sub_uri = found.streams[0], (found.streams[1] if len(found.streams) > 1 else None)
        login = (username, password) if username else None
        main = Stream("", for_go2rtc(with_credentials(main_uri, *login) if login else main_uri))
        sub = (
            Stream("", for_go2rtc(with_credentials(sub_uri, *login) if login else sub_uri))
            if sub_uri
            else None
        )
        return Candidate(None, found.name, camera.camera, None, main, sub), None
    try:
        profiles = await _async_onvif_profiles(hass, found, username, password)
    except OnvifAuthError:
        return None, "The camera refused this username or password."
    except OnvifError:
        guessed = brand_streams(found.brand, found.host)
        if guessed is None:
            return None, (
                "The camera did not answer ONVIF. Turn ONVIF on in its settings, or give "
                "Selora its stream address (rtsp://…) in chat."
            )
        main_uri, sub_uri = guessed
    else:
        main_uri = profiles[0].uri
        sub_uri = profiles[-1].uri if len(profiles) > 1 else None
    main = Stream("", for_go2rtc(with_credentials(main_uri, username, password)))
    sub = Stream("", for_go2rtc(with_credentials(sub_uri, username, password))) if sub_uri else None
    return Candidate(None, found.name, camera.camera, None, main, sub), None


async def async_add_cameras_confirmed(
    hass: HomeAssistant, plan_id: str, credentials: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """Write the cameras the card showed into Frigate, then restart it.

    A login a camera refuses writes nothing: the answer names the camera, and
    the card stays open to try again.
    """
    plan = _store(hass).get(plan_id)
    if plan is None:
        return {"error": "This card has expired; ask again."}
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    objects, record, days = _options(plan.arguments)
    try:
        gathered = await _gather(hass, client, plan.arguments)
    except FrigateError as exc:
        return {"error": str(exc)}
    network = [n for n in gathered.network if n.key in {p.key for p in plan.network}]
    if _fingerprint(gathered.candidates, network, objects, record, days) != plan.fingerprint or len(
        network
    ) != len(plan.network):
        return {"error": "The cameras are not the ones that were shown; ask again."}
    # The model may have changed while the card was open.
    if gathered.labels is not None and (missing := unsupported_labels(objects, gathered.labels)):
        return unsupported_error(missing, gathered.labels)

    resolved: list[Candidate] = []
    camera_errors: dict[str, str] = {}
    for camera in network:
        login = credentials.get(camera.key) or {}
        username, password = str(login.get("username") or ""), str(login.get("password") or "")
        if not username and not camera.found.streams:
            camera_errors[camera.key] = "Enter the camera's username and password."
            continue
        candidate, error = await _resolve_network(hass, camera, username, password)
        if candidate is None:
            camera_errors[camera.key] = error or "The camera could not be read."
        else:
            resolved.append(candidate)
    if camera_errors:
        return {"error": "Some cameras could not be read.", "camera_errors": camera_errors}

    everything = gathered.candidates + resolved
    try:
        await client.set_config(build_config(everything, objects, record, days))
    except FrigateError as exc:
        return {"error": str(exc)}
    # Saved: from here the cameras are Frigate's, whether or not it restarts.
    plan.added = [c.name for c in everything]
    plan.added_cameras = [(c.name, c.camera) for c in everything]
    hass.data.setdefault(DOMAIN, {}).pop(SUGGESTION_CACHE, None)
    try:
        await client.restart()
    except FrigateError as exc:
        # A Frigate run by the host rather than in its own container cannot
        # restart itself ("Unable to restart Frigate"). The cameras are saved
        # and start with its next restart, which is not a failure to add them.
        _LOGGER.warning("Frigate saved the cameras but did not restart: %s", exc)
        plan.restart_failed = True
        return {"status": "saved", "cameras": plan.added, "restart_failed": True}
    hass.async_create_background_task(
        async_reload_when_back(hass, client), f"{DOMAIN}_frigate_reload"
    )
    return {"status": "added", "cameras": plan.added}


def added_line(hass: HomeAssistant, plan_id: str, language: str | None) -> str | None:
    """The conversation's record of a confirmed card, from the plan — never
    from what the panel reports."""
    plan = _store(hass).get(plan_id)
    if plan is None or not plan.added:
        return None
    base = str(language or "en").lower().split("-")[0]
    table = _SAVED_BY_LANG if plan.restart_failed else _ADDED_BY_LANG
    template = table.get(base, table["en"])
    return template.format(names=", ".join(sanitize_untrusted_text(n, 60) for n in plan.added))


def result_card_camera(name: str, frigate_name: str) -> FrigateResultCamera:
    """A camera on the result card, with the entities the Frigate integration
    gives it (``camera.<name>``, ``switch.<name>_recordings``)."""
    return {
        "name": sanitize_untrusted_text(name, 60),
        "frigate_name": frigate_name,
        "camera_entity": f"camera.{frigate_name}",
        "recordings_entity": f"switch.{frigate_name}_recordings",
    }


def changed_card_camera(result: dict[str, Any]) -> FrigateResultCamera | None:
    """A camera on the result card, from a confirmed change's result (what
    ``async_set_objects_confirmed`` or ``async_move_camera_confirmed``
    returned), or None for a result that names no camera."""
    frigate_name = result.get("camera")
    if not isinstance(frigate_name, str) or not frigate_name:
        return None
    camera = result_card_camera(str(result.get("name") or frigate_name), frigate_name)
    if isinstance(looks_for := result.get("looks_for"), list):
        camera["objects"] = [str(label) for label in looks_for]
    return camera


def result_for(hass: HomeAssistant, plan_id: str) -> FrigateResult | None:
    """The panel's result card for a confirmed card, from the plan."""
    plan = _store(hass).get(plan_id)
    if plan is None or not plan.added_cameras:
        return None
    objects, record, days = _options(plan.arguments)
    return {
        "action": "saved" if plan.restart_failed else "added",
        "cameras": [result_card_camera(name, slug) for name, slug in plan.added_cameras],
        "objects": objects,
        "record": record,
        "record_days": days,
    }


async def async_reload_when_back(hass: HomeAssistant, client: FrigateClient) -> None:
    """Reload the Frigate integration once Frigate answers again."""
    await asyncio.sleep(_RESTART_GRACE)
    deadline = time.monotonic() + _RESTART_WAIT
    while time.monotonic() < deadline:
        try:
            await client.version()
        except FrigateError:
            await asyncio.sleep(_RESTART_POLL)
            continue
        if (entry := frigate_entry(hass)) is not None:
            await hass.config_entries.async_reload(entry.entry_id)
        return
    _LOGGER.warning("Frigate did not come back within %s s of its restart", _RESTART_WAIT)


async def async_frigate_suggestions(hass: HomeAssistant) -> list[HomeSuggestion]:
    """One suggestion naming every camera Frigate could add; none without Frigate.

    Network cameras come from the last scan, so the chat home never waits on one.
    """
    client = FrigateClient.from_hass(hass)
    if client is None:
        return []
    domain_data = hass.data.setdefault(DOMAIN, {})
    cached = domain_data.get(SUGGESTION_CACHE)
    if cached is not None and time.monotonic() - cached[0] < _SUGGESTION_TTL:
        return list(cached[1])
    try:
        config = await client.get_config()
        candidates, _skipped = await async_camera_candidates(hass, config)
    except FrigateError as exc:
        _LOGGER.debug("No Frigate suggestion: %s", exc)
        config, candidates = {}, []
    ha_hosts = {urlsplit(c.main.url).hostname for c in candidates}
    in_frigate = existing_hosts(config)
    network = [c for c in cached_cameras(hass) if config and c.host not in in_frigate | ha_hosts]
    entities: list[HomeSuggestionEntity] = [
        {"entity_id": c.main.entity_id, "name": c.name} for c in candidates
    ] + [{"entity_id": "", "address": c.host, "name": c.name} for c in network]
    suggestions: list[HomeSuggestion] = []
    if entities:
        suggestions.append(
            {
                # Named by its cameras: dismissing it holds until a new one arrives.
                "suggestion_id": "frigate:"
                + ",".join(sorted(e["entity_id"] or e["address"] for e in entities)),
                "type": "add_to_frigate",
                "entities": entities,
                "collection": {"source": "frigate", "name": "Frigate", "ref": "frigate"},
            }
        )
    domain_data[SUGGESTION_CACHE] = (time.monotonic(), suggestions)
    return suggestions

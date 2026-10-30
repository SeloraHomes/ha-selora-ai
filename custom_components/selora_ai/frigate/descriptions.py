"""Which cameras Frigate describes with Selora AI, and the budget behind it.

Selora OS writes the ``genai.selora`` provider and a global ``objects.genai``
with ``enabled: false`` once the homeowner turns descriptions on in Connect,
so every camera starts off. A camera's own ``objects.genai.enabled`` wins over
the global one; that per-camera key is the only one written here, and only
with the homeowner's answer.

The answer is read from Frigate's saved configuration, not the running one:
the running one merges the global ``false`` into every camera, so a camera
nobody asked about looks the same as one the homeowner said no to. The saved
file holds the camera logins and the provider key; it is parsed in memory and
never logged or returned.

The budget is Connect's to enforce. Its live numbers come from the gateway's
capabilities (``SeloraCloudProvider.camera_descriptions``); without them only
the rules are given, never a number.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..helpers import sanitize_untrusted_text
from .client import FRIGATE_DOMAIN, FrigateClient, FrigateError
from .moving import _saved_config
from .removal import _camera_name

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from ..types import DescribeIntro, HomeSuggestion, HomeSuggestionEntity, QuickAction

_LOGGER = logging.getLogger(__name__)

# The provider Selora OS writes into Frigate's ``genai`` section.
PROVIDER: Final = "selora"
SUGGESTION_CACHE: Final = "_frigate_descriptions_suggestion"
_SUGGESTION_TTL: Final = 300
_WRITE_LOCK: Final = "_frigate_descriptions_lock"
_APPLY_TIMER: Final = "_frigate_descriptions_apply"
# A flow left before its last camera still gets its answers applied: Frigate
# restarts this long after the last answer that changed what it runs.
_APPLY_AFTER_S: Final = 300
# A budget question wants today's numbers, not the capabilities TTL's.
_BUDGET_MAX_AGE: Final = 60.0
# Events read to say which camera is busiest.
_EVENT_DAYS: Final = 7
_COUNT_CAP: Final = 1000
_MAX_ZONES: Final = 10

_NOT_SET_UP: Final = (
    "Frigate is not set up in Home Assistant: the Frigate integration has to be "
    "installed and connected first."
)
_OFF: Final = (
    "Camera descriptions are off: the homeowner turns them on in Selora Connect, "
    "and Selora OS then sets Frigate up. Don't offer to describe cameras until then."
)

_RULES: Final = (
    "Descriptions come out of the homeowner's Selora AI allowance, the same one "
    "the assistant uses.",
    "They can use at most {share}% of the month's allowance, and the assistant "
    "comes first: descriptions stop once the month's total use, chat included, "
    "reaches {share}%, so the assistant always keeps the rest.",
    "A daily budget keeps one busy day from using up the month: once it runs "
    "out, people and vehicles get no description until midnight UTC.",
    "Top-ups never pay for descriptions; they go to the assistant only.",
    "Say it in people and vehicles a day, never in tokens. Fewer cameras, or a "
    "camera limited to its driveway or street zone, make the budget last.",
)
_NO_NUMBERS: Final = (
    "Selora Connect does not report this home's camera budget, so give the rules "
    "only and no numbers."
)
# Who asks: Selora AI's chat, whose reply carries the answer buttons, or an MCP
# client, which records each answer with selora_set_camera_descriptions.
IN_CHAT: Final = "in_chat"
OVER_MCP: Final = "over_mcp"

_HOW_TO_ASK: Final = (
    "Ask about the ask_next camera only, by its name in words: never list the "
    "others or ask which to start with. End with a yes/no question; the reply "
    "comes with buttons to answer it (turn descriptions on, leave it off, one per zone), "
    "so add no quick_actions. No entity markers for the cameras: they "
    "draw tiles that open the device, not answers. When the user has answered, "
    "call this again for the next. You may suggest an answer from its name, "
    "area or picture (an outdoor camera facing a street or driveway is where "
    "descriptions help most), but "
    "only the user's yes turns a camera on, never your guess. For an indoor "
    "camera, say plainly that images of people inside the home are sent to "
    "Selora AI to be described, and suggest leaving it off unless they want to "
    "search it. When a camera has zones, ask whether to describe only what "
    "enters one of them, and which: never pick one. Before the first camera is "
    "turned on, explain the budget (budget.rules) in the user's terms. A camera "
    "the user does not answer stays off: don't ask about it again unless they "
    "bring it up or it is new."
)
_HOW_TO_ASK_OVER_MCP: Final = (
    "Ask about the ask_next camera only, by its name: never list the others or "
    "ask which to start with, and end with a yes/no question. Record the answer "
    "with selora_set_camera_descriptions, then call this again for the next. You "
    "may suggest an answer from its name, area or a look at it "
    "(selora_get_frigate_snapshot; an outdoor camera facing a street or driveway "
    "is where descriptions help most), but only the user's yes turns a camera "
    "on, never your guess. For an indoor camera, say plainly that images of "
    "people inside the home are sent to Selora AI to be described, and suggest "
    "leaving it off unless they want to search it. When a camera has zones, ask "
    "whether to describe only what enters one of them, and which: never pick "
    "one. Before the first camera is turned on, explain the budget "
    "(budget.rules) in the user's terms. A camera the user does not answer stays "
    "off: don't ask about it again unless they bring it up or it is new."
)

# The answers under the question about a camera: (label, the user's words).
# A click is answered here, not by the model: see ``async_answer``.
_ANSWERS: Final = {
    "en": (
        ("Turn descriptions on", "Turn descriptions on for {camera}"),
        ("Leave it off", "Leave {camera} off"),
        ("Only in {zone}", "Describe {camera}, only what enters {zone}"),
    ),
    "fr": (
        ("Activer les descriptions", "Active les descriptions pour {camera}"),
        ("Laisser désactivée", "Laisse {camera} désactivée"),
        ("Seulement dans {zone}", "Décris {camera}, seulement ce qui entre dans {zone}"),
    ),
    "de": (
        ("Beschreibungen einschalten", "Schalte die Beschreibungen für {camera} ein"),
        ("Aus lassen", "Lass {camera} aus"),
        ("Nur in {zone}", "Beschreibe {camera}, nur was {zone} betritt"),
    ),
    "es": (
        ("Activar las descripciones", "Activa las descripciones para {camera}"),
        ("Dejarla apagada", "Deja {camera} apagada"),
        ("Solo en {zone}", "Describe {camera}, solo lo que entra en {zone}"),
    ),
    "it": (
        ("Attivare le descrizioni", "Attiva le descrizioni per {camera}"),
        ("Lasciarla spenta", "Lascia {camera} spenta"),
        ("Solo in {zone}", "Descrivi {camera}, solo ciò che entra in {zone}"),
    ),
}

# What the conversation says between answers: the next question, then the end.
_REPLIES: Final = {
    "en": {
        "step": "Camera {n} of {total}",
        "ask": "Should Selora AI describe what {camera} sees?",
        "restarting": "That's every camera. Frigate is restarting to apply your answers, "
        "about a minute.",
        "saved": "That's every camera, and Frigate already runs your answers.",
        "not_restarted": "That's every camera. Your answers are saved, but Frigate could "
        "not restart itself: they apply after its next restart.",
    },
    "fr": {
        "step": "Caméra {n} sur {total}",
        "ask": "Selora AI doit-il décrire ce que voit {camera} ?",
        "restarting": "C'était la dernière caméra. Frigate redémarre pour appliquer vos "
        "réponses, environ une minute.",
        "saved": "C'était la dernière caméra, et Frigate applique déjà vos réponses.",
        "not_restarted": "C'était la dernière caméra. Vos réponses sont enregistrées, mais "
        "Frigate n'a pas pu redémarrer : elles s'appliqueront à son prochain redémarrage.",
    },
    "de": {
        "step": "Kamera {n} von {total}",
        "ask": "Soll Selora AI beschreiben, was {camera} sieht?",
        "restarting": "Das war die letzte Kamera. Frigate startet neu, um deine Antworten "
        "zu übernehmen, etwa eine Minute.",
        "saved": "Das war die letzte Kamera, und Frigate nutzt deine Antworten bereits.",
        "not_restarted": "Das war die letzte Kamera. Deine Antworten sind gespeichert, aber "
        "Frigate konnte nicht neu starten: Sie gelten nach dem nächsten Neustart.",
    },
    "es": {
        "step": "Cámara {n} de {total}",
        "ask": "¿Debe Selora AI describir lo que ve {camera}?",
        "restarting": "Esa era la última cámara. Frigate se reinicia para aplicar tus "
        "respuestas, alrededor de un minuto.",
        "saved": "Esa era la última cámara, y Frigate ya aplica tus respuestas.",
        "not_restarted": "Esa era la última cámara. Tus respuestas están guardadas, pero "
        "Frigate no pudo reiniciarse: se aplicarán en su próximo reinicio.",
    },
    "it": {
        "step": "Telecamera {n} di {total}",
        "ask": "Selora AI deve descrivere ciò che vede {camera}?",
        "restarting": "Era l'ultima telecamera. Frigate si riavvia per applicare le tue "
        "risposte, circa un minuto.",
        "saved": "Era l'ultima telecamera, e Frigate applica già le tue risposte.",
        "not_restarted": "Era l'ultima telecamera. Le tue risposte sono salvate, ma Frigate "
        "non è riuscito a riavviarsi: si applicheranno al prossimo riavvio.",
    },
}

# What the opening message shows before the first question: the lead, an
# example of one tracked object as Frigate labels it and as Selora AI describes
# it, then what it means for search, privacy and the allowance.
_INTRO: Final = {
    "en": {
        "title": "Camera descriptions",
        "tagline": "Search what your cameras saw, in plain words.",
        "lead": "Frigate tells you a car went by. With descriptions, Selora AI tells "
        "you which one, in words you can search.",
        "seen": "Frigate sees",
        "label": "Car",
        "described": "Selora AI describes",
        "example": "A white FedEx cargo van parked on the driveway.",
        "search": "Then ask \u201cAny FedEx truck today?\u201d and the chat shows it.",
        "privacy": "To be described, pictures of the people and vehicles a camera sees "
        "are sent to Selora AI, so an indoor camera is best left off.",
        "budget": "It uses up to {share}% of your monthly Selora AI allowance{daily}, "
        "and the assistant always comes first.",
        "daily": ", up to {per_day} descriptions a day",
        "yes": "Each camera stays off until you say yes, one at a time.",
    },
    "fr": {
        "title": "Descriptions des caméras",
        "tagline": "Retrouvez ce que vos caméras ont vu, avec vos mots.",
        "lead": "Frigate vous dit qu'une voiture est passée. Avec les descriptions, "
        "Selora AI vous dit laquelle, en mots que vous pouvez rechercher.",
        "seen": "Frigate voit",
        "label": "Voiture",
        "described": "Selora AI décrit",
        "example": "Une camionnette de livraison FedEx blanche garée dans l'allée.",
        "search": "Demandez ensuite \u00ab\u00a0Un camion FedEx aujourd'hui\u00a0?\u00a0\u00bb "
        "et le chat vous le montre.",
        "privacy": "Pour être décrites, les images des personnes et des véhicules "
        "qu'une caméra voit sont envoyées à Selora AI : mieux vaut laisser une caméra "
        "intérieure désactivée.",
        "budget": "Cela utilise jusqu'à {share} % de votre forfait Selora AI du "
        "mois{daily}, et l'assistant passe toujours en premier.",
        "daily": ", jusqu'à {per_day} descriptions par jour",
        "yes": "Chaque caméra reste désactivée tant que vous ne dites pas oui, une à la fois.",
    },
    "de": {
        "title": "Kamerabeschreibungen",
        "tagline": "Finde mit eigenen Worten, was deine Kameras gesehen haben.",
        "lead": "Frigate sagt dir, dass ein Auto vorbeigefahren ist. Mit Beschreibungen"
        " sagt dir Selora AI, welches, in Worten, nach denen du suchen kannst.",
        "seen": "Frigate sieht",
        "label": "Auto",
        "described": "Selora AI beschreibt",
        "example": "Ein weißer FedEx-Lieferwagen parkt in der Einfahrt.",
        "search": "Frag dann \u201eHeute ein FedEx-Wagen?\u201c und der Chat zeigt ihn.",
        "privacy": "Dafür werden Bilder der Personen und Fahrzeuge, die eine Kamera "
        "sieht, an Selora AI gesendet; eine Innenkamera lässt du also besser aus.",
        "budget": "Es nutzt bis zu {share} % deines monatlichen "
        "Selora-AI-Kontingents{daily}, und der Assistent hat immer Vorrang.",
        "daily": ", bis zu {per_day} Beschreibungen am Tag",
        "yes": "Jede Kamera bleibt aus, bis du Ja sagst, eine nach der anderen.",
    },
    "es": {
        "title": "Descripciones de cámaras",
        "tagline": "Busca con tus palabras lo que vieron tus cámaras.",
        "lead": "Frigate te dice que pasó un coche. Con las descripciones, Selora AI "
        "te dice cuál, con palabras que puedes buscar.",
        "seen": "Frigate ve",
        "label": "Coche",
        "described": "Selora AI describe",
        "example": "Una furgoneta de reparto blanca de FedEx aparcada en la entrada.",
        "search": "Luego pregunta \u00ab\u00bfAlgún camión de FedEx hoy?\u00bb y el chat "
        "te lo muestra.",
        "privacy": "Para describirlas, las imágenes de las personas y los vehículos "
        "que ve una cámara se envían a Selora AI, así que es mejor dejar apagada una "
        "cámara interior.",
        "budget": "Usa hasta el {share} % de tu asignación mensual de Selora "
        "AI{daily}, y el asistente siempre va primero.",
        "daily": ", hasta {per_day} descripciones al día",
        "yes": "Cada cámara sigue apagada hasta que digas que sí, una por una.",
    },
    "it": {
        "title": "Descrizioni delle telecamere",
        "tagline": "Cerca con parole tue ciò che hanno visto le telecamere.",
        "lead": "Frigate ti dice che è passata un'auto. Con le descrizioni, Selora AI "
        "ti dice quale, con parole che puoi cercare.",
        "seen": "Frigate vede",
        "label": "Auto",
        "described": "Selora AI descrive",
        "example": "Un furgone FedEx bianco parcheggiato nel vialetto.",
        "search": "Poi chiedi \u00abUn camion FedEx oggi?\u00bb e la chat te lo mostra.",
        "privacy": "Per essere descritte, le immagini delle persone e dei veicoli che "
        "una telecamera vede vengono inviate a Selora AI, quindi è meglio lasciare "
        "spenta una telecamera interna.",
        "budget": "Usa fino al {share}% della tua quota mensile di Selora AI{daily}, "
        "e l'assistente ha sempre la precedenza.",
        "daily": ", fino a {per_day} descrizioni al giorno",
        "yes": "Ogni telecamera resta spenta finché non dici di sì, una alla volta.",
    },
}
# Served by the integration (``__init__.py``), so the panel shows it offline.
_EXAMPLE_IMAGE: Final = f"/api/{DOMAIN}/images/describe-example.jpg"

_PAUSED: Final = {
    "daily_budget": (
        "Today's description budget is used up: people and vehicles tracked "
        "from now get no description until midnight UTC ({resumes})."
    ),
    "chat_priority": (
        "This month's Selora AI use has reached {share}%, so the rest is kept "
        "for the assistant: descriptions resume when the plan renews ({resumes})."
    ),
}


def selora_describes(config: dict[str, Any]) -> bool:
    """Whether Frigate runs Selora AI's provider for descriptions.

    ``roles`` left out means every role, as Frigate reads it.
    """
    genai = config.get("genai")
    provider = genai.get(PROVIDER) if isinstance(genai, dict) else None
    if not isinstance(provider, dict):
        return False
    roles = provider.get("roles", ["descriptions"])
    return isinstance(roles, list) and "descriptions" in roles


def _genai(camera: Any) -> dict[str, Any]:
    objects = camera.get("objects") if isinstance(camera, dict) else None
    genai = objects.get("genai") if isinstance(objects, dict) else None
    return genai if isinstance(genai, dict) else {}


def described_by(camera: Any, label: str | None = None, zones: list[str] | None = None) -> bool:
    """Whether a camera, as Frigate runs it, describes objects (or this one:
    its label among the described objects, in a required zone when it has any)."""
    genai = _genai(camera)
    if genai.get("enabled") is not True:
        return False
    if label is None:
        return True
    objects = genai.get("objects")
    if isinstance(objects, list) and objects and label not in objects:
        return False
    required = genai.get("required_zones")
    return not (isinstance(required, list) and required and not set(required) & set(zones or []))


def _answer(saved: dict[str, Any], name: str) -> bool | None:
    """The homeowner's answer for a camera: True, False, or None when not asked."""
    enabled = _genai((saved.get("cameras") or {}).get(name)).get("enabled")
    return enabled if isinstance(enabled, bool) else None


def _required_zones(camera: Any) -> list[str]:
    zones = _genai(camera).get("required_zones")
    return sorted(str(z) for z in zones if z) if isinstance(zones, list) else []


def _zones(camera: Any) -> dict[str, str]:
    """The camera's zones: Frigate name → the name people know it by."""
    zones = camera.get("zones") if isinstance(camera, dict) else None
    if not isinstance(zones, dict):
        return {}
    named: dict[str, str] = {}
    for key, zone in zones.items():
        friendly = zone.get("friendly_name") if isinstance(zone, dict) else None
        named[str(key)] = sanitize_untrusted_text(str(friendly or key), 40)
    return named


def _friendly(cameras: dict[str, Any], name: str) -> str:
    camera = cameras.get(name) or {}
    friendly = str(camera.get("friendly_name") or name.replace("_", " ").title())
    return sanitize_untrusted_text(friendly, 60)


def _fingerprint(name: str, answer: bool | None, zones: list[str]) -> str:
    """The camera's answer and zones when the card was shown."""
    return hashlib.sha256(json.dumps([name, answer, zones]).encode()).hexdigest()


def _camera_entity(hass: HomeAssistant, name: str) -> er.RegistryEntry | None:
    """The Frigate integration's ``camera.*`` entity for a Frigate camera."""
    for entry in er.async_get(hass).entities.values():
        if (
            entry.platform == FRIGATE_DOMAIN
            and entry.domain == "camera"
            and str(entry.unique_id).rsplit(":", 1)[-1] == name
        ):
            return entry
    return None


def _area(hass: HomeAssistant, entry: er.RegistryEntry | None) -> str | None:
    if entry is None:
        return None
    area_id = entry.area_id
    if area_id is None and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        area_id = device.area_id if device else None
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return sanitize_untrusted_text(area.name, 60) if area else None


def _selora_cloud(hass: HomeAssistant) -> Any:
    """The Selora Cloud provider of a loaded entry, or None."""
    for entry_data in hass.data.get(DOMAIN, {}).values():
        llm = entry_data.get("llm") if isinstance(entry_data, dict) else None
        provider = getattr(llm, "provider", None)
        if getattr(provider, "provider_type", None) == "selora_cloud":
            return provider
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


async def _connect_budget(hass: HomeAssistant) -> dict[str, Any] | None:
    """Connect's live numbers, checked field by field, or None."""
    provider = _selora_cloud(hass)
    if provider is None:
        return None
    await provider.async_refresh_capabilities(max_age=_BUDGET_MAX_AGE)
    raw = provider.camera_descriptions
    if not isinstance(raw, dict):
        return None
    daily = _number(raw.get("daily_budget_tokens"))
    used = _number(raw.get("used_today_tokens"))
    per_object = _number(raw.get("est_tokens_per_object"))
    if daily is None or used is None or not per_object:
        return None
    budget: dict[str, Any] = {
        "enabled_in_connect": raw.get("enabled") is True,
        "objects_per_day": int(daily // per_object),
        "objects_left_today": int(max(0.0, daily - used) // per_object),
    }
    share = _number(raw.get("monthly_share"))
    if share is not None and share <= 1:
        budget["monthly_share_percent"] = round(share * 100)
    month = _number(raw.get("plan_used_fraction"))
    if month is not None:
        budget["month_used_percent"] = round(month * 100)
    paused = raw.get("paused")
    if paused == "daily_budget":
        tomorrow = (dt_util.utcnow() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        budget["paused"] = paused
        budget["resumes"] = dt_util.as_local(tomorrow).isoformat(timespec="minutes")
    elif paused == "chat_priority":
        budget["paused"] = paused
        period_end = dt_util.parse_datetime(str(raw.get("period_end") or ""))
        if period_end is not None:
            budget["resumes"] = dt_util.as_local(period_end).isoformat(timespec="minutes")
    return budget


async def async_budget(hass: HomeAssistant) -> dict[str, Any]:
    """The budget in the homeowner's terms: the rules, and live numbers when
    Connect reports them."""
    live = await _connect_budget(hass)
    share = (live or {}).get("monthly_share_percent", 60)
    budget: dict[str, Any] = {"rules": [rule.format(share=share) for rule in _RULES]}
    if live is None:
        budget["numbers"] = _NO_NUMBERS
        return budget
    budget.update(live)
    if paused := live.get("paused"):
        budget["status"] = _PAUSED[paused].format(
            share=share, resumes=live.get("resumes") or "unknown"
        )
    return budget


async def _event_shares(client: FrigateClient) -> dict[str, int]:
    """Each camera's share of the last week's events, in percent."""
    now = dt_util.utcnow()
    events = await client.get_events(
        {
            "after": (now - timedelta(days=_EVENT_DAYS)).timestamp(),
            "before": now.timestamp(),
            "limit": _COUNT_CAP,
            "include_thumbnails": 0,
        }
    )
    counts = Counter(str(e.get("camera") or "") for e in events if isinstance(e, dict))
    total = sum(counts.values())
    return {camera: round(100 * n / total) for camera, n in counts.items() if total}


async def async_camera_descriptions(hass: HomeAssistant, asking: str = IN_CHAT) -> dict[str, Any]:
    """Which cameras are described, which were never asked about, and the budget."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        config = await client.get_config()
        if not selora_describes(config):
            return {"descriptions": "off", "note": _OFF}
        saved = await _saved_config(client)
    except FrigateError as exc:
        return {"error": str(exc)}
    try:
        shares = await _event_shares(client)
    except FrigateError as exc:
        _LOGGER.debug("No event counts for camera descriptions: %s", exc)
        shares = {}
    running = config.get("cameras") or {}
    cameras = []
    for name in sorted(running):
        entity = _camera_entity(hass, name)
        answer = _answer(saved, name)
        camera: dict[str, Any] = {
            "camera": name,
            "name": _friendly(running, name),
            "describes": "not asked" if answer is None else "yes" if answer else "no",
        }
        if entity is not None:
            camera["entity_id"] = entity.entity_id
        if area := _area(hass, entity):
            camera["area"] = area
        if zones := _zones(running.get(name)):
            camera["zones"] = [{"zone": k, "name": v} for k, v in zones.items()]
        if required := _required_zones((saved.get("cameras") or {}).get(name)):
            camera["only_zones"] = required
        if name in shares:
            camera["share_of_events_7d_percent"] = shares[name]
        cameras.append(camera)
    budget = await async_budget(hass)
    result: dict[str, Any] = {
        "descriptions": "on",
        "cameras": cameras,
        "budget": budget,
        "how_to_ask": _HOW_TO_ASK_OVER_MCP if asking == OVER_MCP else _HOW_TO_ASK,
    }
    if unasked := [c for c in cameras if c["describes"] == "not asked"]:
        result["ask_next"] = unasked[0]["camera"]
    described = [c for c in cameras if c["describes"] == "yes"]
    if budget.get("paused") == "daily_budget" and described:
        busiest = max(described, key=lambda c: c.get("share_of_events_7d_percent", 0))
        if busiest.get("share_of_events_7d_percent"):
            result["busiest"] = busiest["camera"]
            result["offer"] = (
                f"Today's budget ran out. {busiest['name']} makes "
                f"{busiest['share_of_events_7d_percent']}% of the events: offer once to "
                "describe only what enters one of its zones, or to stop describing it. "
                "The user decides."
            )
    return result


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _given_zones(arguments: dict[str, Any], zones: dict[str, str]) -> list[str] | str:
    """The zones the user chose, as Frigate names, or the error to reply with.

    Empty counts as not given: models send ``[]`` for an optional list.
    """
    raw = arguments.get("zones")
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []
    chosen: list[str] = []
    for item in [str(i).strip() for i in items if str(i).strip()][:_MAX_ZONES]:
        match = next(
            (k for k, v in zones.items() if item.lower() in (k.lower(), v.lower())),
            None,
        )
        if match is None:
            shown = sanitize_untrusted_text(item, 40)
            return (
                f"The camera has no zone {shown!r}. Its zones: "
                f"{', '.join(zones.values()) or 'none'}."
            )
        chosen.append(match)
    return sorted(set(chosen))


def _sees(camera: Any) -> str:
    """What the camera's descriptions cover, in words."""
    objects = _genai(camera).get("objects")
    labels = {str(o) for o in objects} if isinstance(objects, list) else set()
    words = {"person": "people", "car": "vehicles"}
    if labels and labels <= set(words):
        return " and ".join(words[label] for label in ("person", "car") if label in labels)
    return "people and objects"


def _buttons(name: str, friendly: str, zones: list[dict[str, str]], lang: str) -> list[QuickAction]:
    """The answers to the question about one camera.

    Each carries the answer itself (``camera_answer``): the panel sends it to
    ``selora_ai/frigate/describe_answer``, never through the model.
    """
    describe, leave, only = _ANSWERS.get(lang, _ANSWERS["en"])
    answers = [
        (describe, True, None, "mdi:text-box-search-outline", "approve"),
        (leave, False, None, "mdi:close", ""),
    ] + [(only, True, zone, "mdi:vector-square", "") for zone in zones]
    buttons: list[QuickAction] = []
    for (label, value), describes, zone, icon, tone in answers:
        zone_name = zone["name"] if zone else ""
        button: QuickAction = {
            "label": label.format(zone=zone_name),
            "value": value.format(camera=friendly, zone=zone_name),
            "mode": "choice",
            "icon": icon,
            "inline": True,
            "camera_answer": {
                "camera": name,
                "describe": describes,
                "zones": [zone["zone"]] if zone else [],
            },
        }
        if tone:
            button["tone"] = tone
        buttons.append(button)
    return buttons


def answer_buttons(tool_log: list[dict[str, Any]] | None, lang: str) -> list[QuickAction]:
    """The answers to the turn's question about ``ask_next``, as buttons.

    Only when the turn's last camera-descriptions read asked about a camera and
    no answer was recorded after it.
    """
    for entry in reversed(tool_log or []):
        if entry.get("tool") == "set_camera_descriptions":
            return []
        if entry.get("tool") != "get_camera_descriptions":
            continue
        result = entry.get("result")
        name = result.get("ask_next") if isinstance(result, dict) else None
        camera = (
            next((c for c in result.get("cameras") or [] if c.get("camera") == name), None)
            if name
            else None
        )
        if camera is None:
            return []
        return _buttons(name, camera["name"], camera.get("zones") or [], lang)
    return []


def _not_running(running: dict[str, Any], saved: dict[str, Any]) -> bool:
    """Whether a saved answer differs from what Frigate runs: a restart applies it."""
    for name, camera in running.items():
        answer = _answer(saved, name)
        if answer is None:
            continue
        if answer != (_genai(camera).get("enabled") is True):
            return True
        saved_zones = _required_zones((saved.get("cameras") or {}).get(name))
        if answer and sorted(saved_zones) != sorted(_required_zones(camera)):
            return True
    return False


def _ask_next(running: dict[str, Any], saved: dict[str, Any], lang: str) -> dict[str, Any] | None:
    """The question about the first camera nobody answered for, with its buttons."""
    unasked = [name for name in sorted(running) if _answer(saved, name) is None]
    if not unasked:
        return None
    name = unasked[0]
    friendly = _friendly(running, name)
    zones = [{"zone": k, "name": v} for k, v in _zones(running[name]).items()]
    replies = _REPLIES.get(lang, _REPLIES["en"])
    question = replies["ask"].format(camera=friendly)
    step = replies["step"].format(n=len(running) - len(unasked) + 1, total=len(running))
    return {
        "reply": question,
        "quick_actions": _buttons(name, friendly, zones, lang),
        "card": {"camera": name, "camera_name": friendly, "step": step, "question": question},
    }


async def async_start(hass: HomeAssistant, lang: str) -> dict[str, Any]:
    """What descriptions are and cost, then the question about the first camera.

    The home suggestion opens with this, written here rather than by the model:
    everything in it is known, and the answers go to ``async_answer``.
    """
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        config = await client.get_config()
        if not selora_describes(config):
            return {"error": _OFF}
        saved = await _saved_config(client)
    except FrigateError as exc:
        return {"error": str(exc)}
    question = _ask_next(config.get("cameras") or {}, saved, lang)
    if question is None:
        return {"error": "Every camera already has an answer."}
    intro = _intro(await async_budget(hass), lang)
    text = "\n\n".join(
        [intro["lead"], *(point["text"] for point in intro["points"]), question["reply"]]
    )
    return {**question, "reply": text, "card": {**question["card"], "intro": intro}}


def _intro(budget: dict[str, Any], lang: str) -> DescribeIntro:
    """The opening's lead, example and points, with the home's budget in them."""
    words = _INTRO.get(lang, _INTRO["en"])
    per_day = budget.get("objects_per_day")
    return {
        "title": words["title"],
        "tagline": words["tagline"],
        "lead": words["lead"],
        "image": _EXAMPLE_IMAGE,
        "seen": words["seen"],
        "label": words["label"],
        "described": words["described"],
        "example": words["example"],
        "points": [
            {"icon": "mdi:magnify", "text": words["search"]},
            {"icon": "mdi:shield-lock-outline", "text": words["privacy"]},
            {
                "icon": "mdi:chart-donut",
                "text": words["budget"].format(
                    share=budget.get("monthly_share_percent", 60),
                    daily=words["daily"].format(per_day=per_day) if per_day else "",
                ),
            },
            {"icon": "mdi:toggle-switch-off-outline", "text": words["yes"]},
        ],
    }


async def async_answer(
    hass: HomeAssistant, camera: str, describe: bool, zones: list[str], lang: str
) -> dict[str, Any]:
    """Save the answer a button gave, then ask about the next camera.

    The click is the user's own answer, so it needs no card, and no model: the
    question and its answers are fixed. Frigate restarts once, after the last
    camera, and only if an answer changes what it runs.
    """
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    replies = _REPLIES.get(lang, _REPLIES["en"])
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(_WRITE_LOCK, asyncio.Lock())
    async with lock:
        try:
            config = await client.get_config()
            if not selora_describes(config):
                return {"error": _OFF}
            running = config.get("cameras") or {}
            if camera not in running:
                return {"error": "Frigate no longer has that camera."}
            # A button left on an older question, reopened later, must not
            # overwrite the answer given then: changing one goes through the model.
            if _answer(await _saved_config(client), camera) is not None:
                return {"error": "That camera already has an answer."}
            chosen = _given_zones({"zones": zones}, _zones(running[camera])) if describe else []
            if isinstance(chosen, str):
                return {"error": chosen}
            genai: dict[str, Any] = {"enabled": describe}
            if chosen:
                genai["required_zones"] = chosen
            await client.set_config(
                {"cameras": {camera: {"objects": {"genai": genai}}}}, restart=False
            )
            saved = await _saved_config(client)
        except FrigateError as exc:
            return {"error": str(exc)}
    hass.data[DOMAIN].pop(SUGGESTION_CACHE, None)

    friendly = _friendly(running, camera)
    zone_names = _zones(running[camera])
    said, left, only = _ANSWERS.get(lang, _ANSWERS["en"])
    if chosen:
        words = only[1].format(camera=friendly, zone=", ".join(zone_names[z] for z in chosen))
    else:
        words = (said if describe else left)[1].format(camera=friendly)

    if question := _ask_next(running, saved, lang):
        if _not_running(running, saved):
            _apply_later(hass)
        return {"said": words, **question}
    cancel_apply(hass)
    if not _not_running(running, saved):
        return {"said": words, "reply": replies["saved"], "card": {"done": "saved"}}
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate saved the cameras' descriptions but did not restart: %s", exc)
        return {"said": words, "reply": replies["not_restarted"], "card": {"done": "not_restarted"}}
    return {"said": words, "reply": replies["restarting"], "card": {"done": "restarting"}}


async def async_preview_set_descriptions(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A card for turning a camera's descriptions on or off, or the answer
    recorded straight away when it changes nothing the camera does."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    describe = _bool(arguments.get("describe"))
    if describe is None:
        return {"error": "Say whether to describe the camera: describe true or false."}
    try:
        config = await client.get_config()
        if not selora_describes(config):
            return {"error": _OFF}
        running = config.get("cameras") or {}
        name = _camera_name(hass, running, str(arguments.get("camera") or ""))
        if name is None:
            return {"error": "Frigate has no such camera.", "frigate_cameras": sorted(running)}
        saved = await _saved_config(client)
    except FrigateError as exc:
        return {"error": str(exc)}
    friendly = _friendly(running, name)
    saved_cameras = saved.get("cameras") or {}
    answer = _answer(saved, name)
    current_zones = _required_zones(saved_cameras.get(name))
    zones = _given_zones(arguments, _zones(running.get(name))) if describe else []
    if isinstance(zones, str):
        return {"error": zones}

    if not describe and answer is not True:
        if answer is False:
            return {"unchanged": True, "camera": name, "note": f"{friendly} is not described."}
        # A no to a camera that was never on changes nothing Frigate does: it
        # only records the answer, so the camera is not asked about again.
        try:
            await client.set_config(
                {"cameras": {name: {"objects": {"genai": {"enabled": False}}}}}, restart=False
            )
        except FrigateError as exc:
            return {"error": str(exc)}
        hass.data.setdefault(DOMAIN, {}).pop(SUGGESTION_CACHE, None)
        return {
            "camera": name,
            "recorded": True,
            "note": f"Recorded: {friendly} stays off and won't be asked about again.",
        }
    if describe and answer is True and (not zones or zones == current_zones):
        return {"unchanged": True, "camera": name, "note": f"{friendly} is already described."}

    if describe:
        where = f", in {', '.join(zones)} only" if zones else ""
        label = (
            f"Restart Frigate so Selora AI describes the {_sees(running.get(name))} "
            f"{friendly} sees{where}"
        )
        if not any(_answer(saved, other) is True for other in saved_cameras):
            label += (
                ". Descriptions use your Selora AI allowance, up to 60% of it, "
                "and the assistant comes first"
            )
    else:
        label = f"Restart Frigate so Selora AI stops describing what {friendly} sees"
    return {
        "requires_approval": True,
        "destructive": {
            "kind": "frigate_descriptions",
            "verb": "set_descriptions",
            "target_id": name,
            "entity_id": "",
            "name": friendly,
            "label": sanitize_untrusted_text(label, 300),
            "fingerprint": _fingerprint(name, answer, current_zones),
        },
    }


async def async_set_descriptions_confirmed(
    hass: HomeAssistant, name: str, fingerprint: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Write the card's answer, if the camera's answer is still the one shown."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    describe = _bool(arguments.get("describe"))
    if describe is None:
        return {"error": "The card did not say whether to describe the camera."}
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(_WRITE_LOCK, asyncio.Lock())
    async with lock:
        try:
            config = await client.get_config()
            if not selora_describes(config):
                return {"error": _OFF}
            running = config.get("cameras") or {}
            if name not in running:
                return {"error": "Frigate no longer has that camera."}
            saved = await _saved_config(client)
            answer = _answer(saved, name)
            current = _required_zones((saved.get("cameras") or {}).get(name))
            if _fingerprint(name, answer, current) != fingerprint:
                return {"error": "The camera's descriptions changed since it was shown; ask again."}
            zones = _given_zones(arguments, _zones(running.get(name))) if describe else []
            if isinstance(zones, str):
                return {"error": zones}
            genai: dict[str, Any] = {"enabled": describe}
            if zones:
                genai["required_zones"] = zones
            await client.set_config({"cameras": {name: {"objects": {"genai": genai}}}})
        except FrigateError as exc:
            return {"error": str(exc)}
    hass.data[DOMAIN].pop(SUGGESTION_CACHE, None)
    friendly = _friendly(running, name)
    done = (
        f"Selora AI describes what {friendly} sees" + (f" in {', '.join(zones)}" if zones else "")
        if describe
        else f"Selora AI no longer describes what {friendly} sees"
    )
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate saved the camera's descriptions but did not restart: %s", exc)
        return {
            "camera": name,
            "restart_failed": True,
            "done": f"Saved: {done} once Frigate restarts. It could not restart itself.",
        }
    return {"camera": name, "done": f"{done}. Frigate is restarting, about a minute."}


async def async_describe_suggestions(hass: HomeAssistant) -> list[HomeSuggestion]:
    """One suggestion naming the cameras nobody has been asked about yet,
    while descriptions are on.

    Named by its cameras, so a dismissal holds until a new camera arrives.
    """
    client = FrigateClient.from_hass(hass)
    if client is None:
        return []
    domain_data = hass.data.setdefault(DOMAIN, {})
    cached = domain_data.get(SUGGESTION_CACHE)
    if cached is not None and time.monotonic() - cached[0] < _SUGGESTION_TTL:
        return list(cached[1])
    suggestions: list[HomeSuggestion] = []
    try:
        config = await client.get_config()
        if selora_describes(config):
            saved = await _saved_config(client)
            running = config.get("cameras") or {}
            unasked = [n for n in sorted(running) if _answer(saved, n) is None]
            if unasked:
                entities: list[HomeSuggestionEntity] = []
                for n in unasked:
                    entity = _camera_entity(hass, n)
                    entities.append(
                        {
                            "entity_id": entity.entity_id if entity else "",
                            "name": _friendly(running, n),
                        }
                    )
                suggestions.append(
                    {
                        "suggestion_id": "describe:" + ",".join(unasked),
                        "type": "describe_cameras",
                        "entities": entities,
                        "collection": {"source": "frigate", "name": "Frigate", "ref": "frigate"},
                    }
                )
    except FrigateError as exc:
        _LOGGER.debug("No camera descriptions suggestion: %s", exc)
    domain_data[SUGGESTION_CACHE] = (time.monotonic(), suggestions)
    return suggestions


def _apply_later(hass: HomeAssistant) -> None:
    """Restart Frigate a while after the last answer, unless the flow ends first.

    A timer, not a task that sleeps: bootstrap and every entry reload wait on
    tasks (see CLAUDE.md). Each answer pushes it back.
    """
    cancel_apply(hass)

    @callback
    def _fire(_now: datetime) -> None:
        hass.data.get(DOMAIN, {}).pop(_APPLY_TIMER, None)
        hass.async_create_background_task(
            _async_apply(hass), "selora_ai_frigate_descriptions_apply"
        )

    hass.data.setdefault(DOMAIN, {})[_APPLY_TIMER] = async_call_later(hass, _APPLY_AFTER_S, _fire)


def cancel_apply(hass: HomeAssistant) -> None:
    """Drop a pending restart: the flow ended, or the entry is unloading."""
    if cancel := hass.data.get(DOMAIN, {}).pop(_APPLY_TIMER, None):
        cancel()


async def _async_apply(hass: HomeAssistant) -> None:
    """Restart Frigate if an answer it does not run yet is saved."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(_WRITE_LOCK, asyncio.Lock())
    async with lock:
        try:
            config = await client.get_config()
            if _not_running(config.get("cameras") or {}, await _saved_config(client)):
                await client.restart()
        except FrigateError as exc:
            _LOGGER.warning("Frigate did not apply the cameras' descriptions: %s", exc)

"""Recognizing the people the household knows, with Frigate's face recognition.

Frigate looks for a face on each person it detects and matches it against its
Face Library, so it recognizes nobody until someone adds photos of each person
there. Selora AI adds them: from a card in its panel, or over MCP from a camera
event the person is in (``async_add_event_photo``).

``face_recognition`` is global. It is the one global Frigate section Selora AI
writes, on the user's yes: ``enabled``, and on a Selora hub, whose GPU runs it,
the large model. The toggle is read from the saved file, the one Frigate runs
after its restart.

A home suggestion opens a guide written here, not by the model: the yes or no
is a button (``async_answer``), and the people are added on a card that sends
each photo straight to Frigate (``async_add_photo``), never to the model.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Final

from ..const import DOMAIN
from ..helpers import sanitize_untrusted_text
from .client import FrigateClient, FrigateError, frigate_entry, managed_by_hub
from .moving import _saved_config

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..types import FacesCard, FacesIntro, HomeSuggestion, QuickAction

_LOGGER = logging.getLogger(__name__)

SUGGESTION_CACHE: Final = "_frigate_faces_suggestion"
_SUGGESTION_TTL: Final = 300
_WRITE_LOCK: Final = "_frigate_faces_lock"
# Frigate keeps the faces it saw but could not match in this folder of the
# library; it is not a person.
_TRAIN: Final = "train"
_MAX_PEOPLE: Final = 50
# A Selora hub runs it on its GPU; elsewhere Frigate's default, the small
# model on the CPU, is left alone.
_HUB_MODEL: Final = "large"
# Frigate keeps a name as a folder: one path component.
_MAX_NAME: Final = 40
_BAD_NAME_CHARS: Final = frozenset('/\\:*?"<>|')

_NOT_SET_UP: Final = (
    "Frigate is not set up in Home Assistant: the Frigate integration has to be "
    "installed and connected first."
)
_GUIDE: Final = (
    "It runs on the hub: no face leaves the house. Frigate downloads its model once.",
    "5 to 10 photos per person to begin with, 20 to 30 for good results: well "
    "lit, no hat or sunglasses, not black-and-white night shots. Varied photos "
    "matter more than many.",
    "The library's Recent Recognitions then lists faces Frigate saw: assigning "
    "each to the right person trains it further.",
    "A face is only looked for on a person Frigate detects, so a camera has to "
    "look for people, and close enough to see a face (a door, not the street).",
)
_HOW_TO_OFFER: Final = (
    "Explain what it does and what it needs (guide), then ask before turning it "
    "on: only the user's yes turns it on. Once on, say how to add each person "
    "(add_people)."
)
# How a person is added, for whoever asks. In chat, the panel's card under the
# reply; over MCP, a camera event the person is in, since an outside assistant
# cannot pass on a photo the user shared with it.
ON_CARD: Final = "on_card"
FROM_EVENTS: Final = "from_events"
_ADD_PEOPLE: Final = {
    ON_CARD: (
        "Frigate recognizes only the people added to its Face Library: once it is "
        "on, the panel shows a card under this reply to add each person with a few "
        "clear, front-facing photos. Never ask for photos in chat."
    ),
    FROM_EVENTS: (
        "Frigate recognizes only the people added to its Face Library: once it is "
        "on and Frigate is back, add a person with selora_add_face_photo, their "
        "name and the event_id of a selora_find_camera_events event where they "
        "face the camera (look with selora_get_frigate_snapshot first), one event "
        "per call. Photos shared in this conversation cannot be passed on: Selora "
        "AI's own chat has a card to upload them."
    ),
}
_ADDED_WHEN_BACK: Final = {
    ON_CARD: "add each person on the card, once Frigate is back",
    FROM_EVENTS: "add each person with selora_add_face_photo, once Frigate is back",
}

# The answers under the question: (label, the user's words). A click is
# answered by ``async_answer``, not by the model.
_ANSWERS: Final = {
    "en": (("Turn it on", "Turn on face recognition"), ("Not now", "Not now")),
    "fr": (
        ("L'activer", "Active la reconnaissance faciale"),
        ("Pas maintenant", "Pas maintenant"),
    ),
    "de": (
        ("Einschalten", "Schalte die Gesichtserkennung ein"),
        ("Nicht jetzt", "Nicht jetzt"),
    ),
    "es": (
        ("Activarlo", "Activa el reconocimiento facial"),
        ("Ahora no", "Ahora no"),
    ),
    "it": (
        ("Attivarlo", "Attiva il riconoscimento facciale"),
        ("Non ora", "Non ora"),
    ),
}

# The opening: a heading, what it gives and costs as points, then the question.
_INTRO: Final = {
    "en": {
        "title": "Face recognition",
        "tagline": "Know who is at the door.",
        "lead": "Frigate tells you a person is at the door. With face recognition, it "
        "tells you who.",
        "who": "Ask \u201cWas Alice home at noon?\u201d and get an answer, or be told "
        "only when someone you don't know comes by.",
        "privacy": "It runs on your hub: no face leaves the house.",
        "photos": "Frigate knows only the people you add, with a few clear photos of "
        "each. You add them right here.",
        "people": "It looks for a face on the people your cameras see, so it works "
        "best on a camera close enough to see one, like the front door.",
        "question": "Turn on face recognition?",
    },
    "fr": {
        "title": "Reconnaissance faciale",
        "tagline": "Sachez qui est à la porte.",
        "lead": "Frigate vous dit qu'une personne est à la porte. Avec la "
        "reconnaissance faciale, il vous dit qui.",
        "who": "Demandez \u00ab\u00a0Alice était-elle là à midi\u00a0?\u00a0\u00bb, ou "
        "soyez prévenu seulement quand passe quelqu'un d'inconnu.",
        "privacy": "Elle fonctionne sur votre hub : aucun visage ne quitte la maison.",
        "photos": "Frigate ne reconnaît que les personnes que vous ajoutez, avec "
        "quelques photos nettes de chacune. Vous les ajoutez ici même.",
        "people": "Elle cherche un visage sur les personnes que voient vos caméras : "
        "elle marche mieux sur une caméra assez proche pour en voir un, comme "
        "celle de la porte d'entrée.",
        "question": "Activer la reconnaissance faciale ?",
    },
    "de": {
        "title": "Gesichtserkennung",
        "tagline": "Wisse, wer an der Tür ist.",
        "lead": "Frigate sagt dir, dass jemand an der Tür ist. Mit Gesichtserkennung "
        "sagt es dir, wer.",
        "who": "Frag \u201eWar Alice mittags zu Hause?\u201c, oder lass dich nur "
        "benachrichtigen, wenn jemand Unbekanntes kommt.",
        "privacy": "Sie läuft auf deinem Hub: Kein Gesicht verlässt das Haus.",
        "photos": "Frigate kennt nur die Personen, die du hinzufügst, mit ein paar "
        "klaren Fotos von jeder. Du fügst sie gleich hier hinzu.",
        "people": "Sie sucht ein Gesicht bei den Personen, die deine Kameras sehen, "
        "und funktioniert am besten mit einer Kamera, die nah genug ist, etwa an "
        "der Haustür.",
        "question": "Gesichtserkennung einschalten?",
    },
    "es": {
        "title": "Reconocimiento facial",
        "tagline": "Sabe quién está en la puerta.",
        "lead": "Frigate te dice que hay una persona en la puerta. Con el "
        "reconocimiento facial, te dice quién.",
        "who": "Pregunta \u00ab\u00bfEstaba Alice en casa a mediodía?\u00bb, o recibe "
        "un aviso solo cuando pasa alguien que no conoces.",
        "privacy": "Funciona en tu hub: ninguna cara sale de casa.",
        "photos": "Frigate solo reconoce a las personas que añades, con unas cuantas "
        "fotos nítidas de cada una. Las añades aquí mismo.",
        "people": "Busca una cara en las personas que ven tus cámaras, así que "
        "funciona mejor con una cámara lo bastante cerca para ver una, como la de "
        "la puerta de entrada.",
        "question": "¿Activar el reconocimiento facial?",
    },
    "it": {
        "title": "Riconoscimento facciale",
        "tagline": "Sappi chi è alla porta.",
        "lead": "Frigate ti dice che c'è una persona alla porta. Con il riconoscimento "
        "facciale, ti dice chi.",
        "who": "Chiedi \u00abAlice era a casa a mezzogiorno?\u00bb, o ricevi un avviso "
        "solo quando passa qualcuno che non conosci.",
        "privacy": "Funziona sul tuo hub: nessun volto lascia la casa.",
        "photos": "Frigate riconosce solo le persone che aggiungi, con alcune foto "
        "nitide di ciascuna. Le aggiungi proprio qui.",
        "people": "Cerca un volto sulle persone che vedono le tue telecamere, quindi "
        "funziona meglio con una telecamera abbastanza vicina da vederne uno, come "
        "quella della porta d'ingresso.",
        "question": "Attivare il riconoscimento facciale?",
    },
}

# What the conversation says after the answer, and above the people card.
_REPLIES: Final = {
    "en": {
        "restarting": "Face recognition is on. Frigate is restarting, about a minute.",
        "not_restarted": "Face recognition is saved on, but Frigate could not restart "
        "itself: it starts with Frigate's next restart.",
        "declined": "OK, it stays off. Ask me any time to turn it on.",
        "add": "Add the people Frigate should recognize: a name, then 5 to 10 clear "
        "photos of their face, well lit, without a hat or sunglasses. Varied photos "
        "help more than many.",
    },
    "fr": {
        "restarting": "La reconnaissance faciale est activée. Frigate redémarre, "
        "environ une minute.",
        "not_restarted": "La reconnaissance faciale est enregistrée, mais Frigate n'a "
        "pas pu redémarrer : elle démarrera à son prochain redémarrage.",
        "declined": "D'accord, elle reste désactivée. Demandez-moi quand vous voulez.",
        "add": "Ajoutez les personnes que Frigate doit reconnaître : un nom, puis 5 à "
        "10 photos nettes de leur visage, bien éclairées, sans chapeau ni lunettes "
        "de soleil. Des photos variées aident plus que beaucoup de photos.",
    },
    "de": {
        "restarting": "Die Gesichtserkennung ist an. Frigate startet neu, etwa eine Minute.",
        "not_restarted": "Die Gesichtserkennung ist gespeichert, aber Frigate konnte "
        "nicht neu starten: Sie beginnt mit dem nächsten Neustart.",
        "declined": "Gut, sie bleibt aus. Frag mich jederzeit, um sie einzuschalten.",
        "add": "Füge die Personen hinzu, die Frigate erkennen soll: einen Namen, dann "
        "5 bis 10 klare Fotos ihres Gesichts, gut beleuchtet, ohne Hut oder "
        "Sonnenbrille. Abwechslung hilft mehr als Menge.",
    },
    "es": {
        "restarting": "El reconocimiento facial está activado. Frigate se reinicia, "
        "alrededor de un minuto.",
        "not_restarted": "El reconocimiento facial está guardado, pero Frigate no pudo "
        "reiniciarse: empezará con su próximo reinicio.",
        "declined": "De acuerdo, sigue apagado. Pídemelo cuando quieras.",
        "add": "Añade a las personas que Frigate debe reconocer: un nombre y luego 5 a "
        "10 fotos nítidas de su cara, bien iluminadas, sin gorra ni gafas de sol. "
        "Las fotos variadas ayudan más que muchas.",
    },
    "it": {
        "restarting": "Il riconoscimento facciale è attivo. Frigate si riavvia, circa un minuto.",
        "not_restarted": "Il riconoscimento facciale è salvato, ma Frigate non è "
        "riuscito a riavviarsi: partirà al prossimo riavvio.",
        "declined": "D'accordo, resta spento. Chiedimelo quando vuoi.",
        "add": "Aggiungi le persone che Frigate deve riconoscere: un nome, poi da 5 a "
        "10 foto nitide del volto, ben illuminate, senza cappello né occhiali da "
        "sole. Foto varie aiutano più di tante foto.",
    },
}


def _enabled(config: dict[str, Any]) -> bool:
    section = config.get("face_recognition")
    return isinstance(section, dict) and section.get("enabled") is True


def _people(faces: dict[str, Any]) -> list[dict[str, Any]]:
    """Each person in the Face Library, with how many photos they have."""
    people = [
        {"name": sanitize_untrusted_text(str(name), 60), "photos": len(files)}
        for name, files in sorted(faces.items())
        if name != _TRAIN and isinstance(files, list)
    ]
    return people[:_MAX_PEOPLE]


def _without_people(config: dict[str, Any]) -> list[str]:
    """Cameras that do not look for people, so recognize no face."""
    cameras = config.get("cameras") if isinstance(config.get("cameras"), dict) else {}
    blind = []
    for name, camera in sorted(cameras.items()):
        objects = camera.get("objects") if isinstance(camera, dict) else None
        track = objects.get("track") if isinstance(objects, dict) else None
        if isinstance(track, list) and "person" not in track:
            blind.append(sanitize_untrusted_text(str(name), 64))
    return blind


def _turn_on(hass: HomeAssistant, saved: dict[str, Any]) -> dict[str, Any]:
    """The ``face_recognition`` section that turns it on.

    On a hub, the large model, unless the saved file already names one:
    Selora OS may have chosen.
    """
    section: dict[str, Any] = {"enabled": True}
    entry = frigate_entry(hass)
    saved_section = saved.get("face_recognition")
    chosen = isinstance(saved_section, dict) and saved_section.get("model_size")
    if entry is not None and managed_by_hub(entry) and not chosen:
        section["model_size"] = _HUB_MODEL
    return section


def _fingerprint(enabled: bool) -> str:
    return hashlib.sha256(json.dumps(["face_recognition", enabled]).encode()).hexdigest()


async def async_face_recognition(hass: HomeAssistant, adding: str = ON_CARD) -> dict[str, Any]:
    """Whether Frigate recognizes faces, whom it knows, and what it takes;
    ``adding`` says how a person is added (``ON_CARD`` or ``FROM_EVENTS``)."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        config = await client.get_config()
        saved = await _saved_config(client)
    except FrigateError as exc:
        return {"error": str(exc)}
    on = _enabled(saved)
    result: dict[str, Any] = {
        "face_recognition": "on" if on else "off",
        "guide": list(_GUIDE),
        "add_people": _ADD_PEOPLE[adding],
    }
    if on and not _enabled(config):
        result["note"] = "It is saved on and starts with Frigate's next restart."
    if on:
        try:
            people = _people(await client.get_faces())
        except FrigateError as exc:
            _LOGGER.debug("No Face Library: %s", exc)
            people = []
        result["people"] = people
        if not people:
            result["next"] = (
                "Nobody is in the Face Library yet: Frigate recognizes no one until they are added."
            )
        result["face_library"] = True
    else:
        result["how_to_offer"] = _HOW_TO_OFFER
    if blind := _without_people(config):
        result["cameras_not_looking_for_people"] = blind
    return result


async def async_preview_set_face_recognition(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A card for turning face recognition on or off, or why not."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    enabled = arguments.get("enabled")
    if not isinstance(enabled, bool):
        return {"error": "Say whether to recognize faces: enabled true or false."}
    try:
        saved = await _saved_config(client)
    except FrigateError as exc:
        return {"error": str(exc)}
    current = _enabled(saved)
    if enabled == current:
        state = "on" if current else "off"
        return {"unchanged": True, "note": f"Face recognition is already {state}."}
    label = (
        "Restart Frigate to recognize the faces of people added to its Face Library. "
        "It runs on the hub: no face leaves the house"
        if enabled
        else "Restart Frigate with face recognition off. The Face Library is kept"
    )
    return {
        "requires_approval": True,
        "destructive": {
            "kind": "frigate_faces",
            "verb": "set_faces",
            "target_id": "face_recognition",
            "entity_id": "",
            "name": "Face recognition",
            "label": label,
            "fingerprint": _fingerprint(current),
        },
    }


async def async_set_face_recognition_confirmed(
    hass: HomeAssistant, fingerprint: str, arguments: dict[str, Any], adding: str = ON_CARD
) -> dict[str, Any]:
    """Write the card's toggle, if face recognition is still as it was shown."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    enabled = arguments.get("enabled")
    if not isinstance(enabled, bool):
        return {"error": "The card did not say whether to recognize faces."}
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(_WRITE_LOCK, asyncio.Lock())
    async with lock:
        try:
            saved = await _saved_config(client)
            if _fingerprint(_enabled(saved)) != fingerprint:
                return {"error": "Face recognition changed since it was shown; ask again."}
            section = _turn_on(hass, saved) if enabled else {"enabled": False}
            await client.set_config({"face_recognition": section})
        except FrigateError as exc:
            return {"error": str(exc)}
    hass.data[DOMAIN].pop(SUGGESTION_CACHE, None)
    done = (
        f"Face recognition is on; {_ADDED_WHEN_BACK[adding]}"
        if enabled
        else "Face recognition is off"
    )
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate saved face recognition but did not restart: %s", exc)
        return {
            "restart_failed": True,
            "done": f"Saved: {done} once Frigate restarts. It could not restart itself.",
        }
    return {"done": f"{done}. Frigate is restarting, about a minute."}


async def async_face_suggestions(hass: HomeAssistant) -> list[HomeSuggestion]:
    """A suggestion to start recognizing faces, then to add the first person.

    Each state has its own id, so dismissing "turn it on" does not hide "add
    someone" once it is on.
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
        if config.get("cameras"):
            saved = await _saved_config(client)
            state = None
            if not _enabled(saved):
                state = "off"
            elif not _people(await client.get_faces()):
                state = "empty"
            if state:
                suggestions.append(
                    {
                        "suggestion_id": f"faces:{state}",
                        "type": "recognize_faces",
                        "entities": [],
                        "collection": {"source": "frigate", "name": "Frigate", "ref": "frigate"},
                        "state": state,
                    }
                )
    except FrigateError as exc:
        _LOGGER.debug("No face recognition suggestion: %s", exc)
    domain_data[SUGGESTION_CACHE] = (time.monotonic(), suggestions)
    return suggestions


def _words(table: dict[str, Any], lang: str) -> Any:
    return table.get(lang, table["en"])


def _buttons(lang: str) -> list[QuickAction]:
    """Yes and not now, each carrying its answer (``face_answer``): the panel
    sends it to ``selora_ai/frigate/faces_answer``, never through the model."""
    (yes, yes_said), (no, no_said) = _words(_ANSWERS, lang)
    return [
        {
            "label": yes,
            "value": yes_said,
            "mode": "choice",
            "icon": "mdi:face-recognition",
            "inline": True,
            "tone": "approve",
            "face_answer": {"enable": True},
        },
        {
            "label": no,
            "value": no_said,
            "mode": "choice",
            "icon": "mdi:close",
            "inline": True,
            "face_answer": {"enable": False},
        },
    ]


def _intro(lang: str) -> FacesIntro:
    words = _words(_INTRO, lang)
    return {
        "title": words["title"],
        "tagline": words["tagline"],
        "lead": words["lead"],
        "points": [
            {"icon": "mdi:account-question-outline", "text": words["who"]},
            {"icon": "mdi:shield-lock-outline", "text": words["privacy"]},
            {"icon": "mdi:image-multiple-outline", "text": words["photos"]},
            {"icon": "mdi:cctv", "text": words["people"]},
        ],
    }


def _add_people(lang: str) -> tuple[str, FacesCard]:
    """The people card, and the same in plain words for the model's later turns."""
    text = _words(_REPLIES, lang)["add"]
    return text, {"title": _words(_INTRO, lang)["title"], "people": True, "hint": text}


async def async_start(hass: HomeAssistant, lang: str) -> dict[str, Any]:
    """The opening of the guide: what it does, then the question, or with it
    already on, the card to add people."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        saved = await _saved_config(client)
    except FrigateError as exc:
        return {"error": str(exc)}
    if _enabled(saved):
        reply, card = _add_people(lang)
        return {"reply": reply, "card": card}
    intro = _intro(lang)
    question = _words(_INTRO, lang)["question"]
    text = "\n\n".join([intro["lead"], *(p["text"] for p in intro["points"]), question])
    return {
        "reply": text,
        "quick_actions": _buttons(lang),
        "card": {"intro": intro, "question": question},
    }


async def async_answer(hass: HomeAssistant, enable: bool, lang: str) -> dict[str, Any]:
    """Turn it on for the user's yes and restart Frigate, or record the no.

    The click is the user's own answer, so it needs no confirmation card. A no
    dismisses the suggestion, so it is not offered again on every visit.
    """
    from ..home_suggestions import async_dismiss_home_suggestion  # noqa: PLC0415

    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    replies = _words(_REPLIES, lang)
    (_, yes_said), (_, no_said) = _words(_ANSWERS, lang)
    if not enable:
        await async_dismiss_home_suggestion(hass, "faces:off")
        card: FacesCard = {"done": "declined", "status": replies["declined"]}
        return {"said": no_said, "reply": replies["declined"], "card": card}
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(_WRITE_LOCK, asyncio.Lock())
    async with lock:
        try:
            saved = await _saved_config(client)
            # A button left on an older question, clicked later.
            if _enabled(saved):
                return {"error": "Face recognition is already on."}
            await client.set_config({"face_recognition": _turn_on(hass, saved)})
        except FrigateError as exc:
            return {"error": str(exc)}
    hass.data[DOMAIN].pop(SUGGESTION_CACHE, None)
    add, card = _add_people(lang)
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate saved face recognition but did not restart: %s", exc)
        return {
            "said": yes_said,
            "reply": f"{replies['not_restarted']}\n\n{add}",
            "card": {**card, "done": "not_restarted", "status": replies["not_restarted"]},
        }
    return {
        "said": yes_said,
        "reply": f"{replies['restarting']}\n\n{add}",
        "card": {**card, "done": "restarting", "status": replies["restarting"]},
    }


async def async_people(hass: HomeAssistant) -> dict[str, Any]:
    """For the people card: whether Frigate runs face recognition yet (it
    takes photos only then), and whom it knows."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        running = _enabled(await client.get_config())
        people = _people(await client.get_faces()) if running else []
    except FrigateError as exc:
        # Down while it restarts: the card waits.
        _LOGGER.debug("Frigate not answering for the people card: %s", exc)
        return {"running": False, "people": []}
    return {"running": running, "people": people}


def face_name(name: str) -> str | None:
    """``name`` as a Face Library name, or None when it cannot be one.

    Frigate keeps each person as a folder and reports the folder's name on the
    events it recognizes, so it stays as typed, minus a path's characters.
    """
    cleaned = " ".join(name.split())
    if (
        not cleaned
        or len(cleaned) > _MAX_NAME
        or cleaned.startswith(".")
        or cleaned.lower() == _TRAIN
        or any(c in _BAD_NAME_CHARS or ord(c) < 32 for c in cleaned)
    ):
        return None
    return cleaned


async def async_add_photo(
    hass: HomeAssistant, name: str, image: bytes, content_type: str
) -> dict[str, Any]:
    """Send one photo of ``name`` to Frigate's Face Library.

    Straight from the panel to Frigate: the photo is not kept, logged or shown
    to the model. Frigate finds the face and keeps only that.
    """
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    person = face_name(name)
    if person is None:
        return {"error": f'A name of up to {_MAX_NAME} characters, without / \\ : * ? " < > |.'}
    try:
        if not _enabled(await client.get_config()):
            return {"error": "Frigate is not running face recognition yet: wait for its restart."}
        await client.register_face(person, image, content_type)
    except FrigateError as exc:
        return {"error": str(exc)}
    hass.data.setdefault(DOMAIN, {}).pop(SUGGESTION_CACHE, None)
    return {"name": person}


async def async_add_event_photo(hass: HomeAssistant, name: str, event_id: str) -> dict[str, Any]:
    """Add ``name``'s face from the snapshot Frigate kept of an event.

    For MCP, whose client cannot pass on a photo: the snapshot goes from
    Frigate to Frigate, and never through the client. Its thumbnail is too
    small to learn a face from, so an event without a snapshot is refused.
    """
    from .events import _EVENT_ID  # noqa: PLC0415

    if not _EVENT_ID.match(event_id):
        return {"error": "Not an event id: pass one selora_find_camera_events returned."}
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        image, content_type = await client.get_image(f"/api/events/{event_id}/snapshot.jpg")
    except FrigateError as exc:
        return {
            "error": f"No snapshot of this event: {exc} A camera with snapshots off "
            "keeps none, so pick an event from one that has them on."
        }
    result = await async_add_photo(hass, name, image, content_type.split(";", 1)[0].strip())
    return {**result, "event_id": event_id} if "error" not in result else result

# Frigate

Frigate runs on the Selora OS host, not in Home Assistant. Selora AI reaches it
at the URL (and with the login) of the **Frigate integration's config entry**,
which points at the host's proxy; without that integration loaded every Frigate
feature is off (`frigate/client.py`).

## Who owns which part of Frigate's config

Selora AI writes only **per-camera** sections: `cameras.<name>` and the
`go2rtc.streams` it restreams them from. Detectors, hardware acceleration,
models, MQTT and every other global section belong to Selora OS, which knows the
hardware. A camera config that needs a global change (a different detector, a
hwaccel preset) is a request to the OS, not something to write from here.

Writes go through Frigate's own `PUT /api/config/set` (`config_data`, merged and
validated by Frigate, `400` when invalid) followed by `POST /api/restart`, never
by editing `config.yml`.

## Two ways a camera reaches Frigate

- **A camera Home Assistant has** through its own integration (Reolink, UniFi
  Protect, ONVIF) is added from there. That integration stays: it carries the
  PTZ, IR and siren controls Frigate has no equivalent for.
- **A camera Home Assistant does not have** goes **straight to Frigate**, and the
  Frigate integration brings it into HA. Adding it to HA first (a Generic
  Camera) only to hand it on would leave a duplicate camera entity and a second
  connection to the camera.

Network cameras come from `frigate/discovery.py`: an ONVIF WS-Discovery probe,
HA's discovery flows nobody set up (only when the flow names the address in
`title_placeholders`), and addresses named in chat (`hosts`). Only local
addresses are kept. The probe runs on a timer (a minute after start, then every
half hour, only while Frigate is set up); only its answer is cached, and flows
are read live, so a camera set up or dismissed since is not offered again. A
network camera Frigate or HA already has is not offered as one. A finished scan
drops the chat home's cached suggestions, which were built from the one before.
In developer mode, Settings has a "Scan now" button and the browser console a
`seloraDev.scanCameras()` helper (`selora_ai/frigate/scan`), so testing a camera
does not wait on the timer.

**Some cameras never answer the multicast probe** but answer one sent to them
(a Reolink E1 Zoom on Wi-Fi). `async_sweep` asks each address of the hub's own
networks directly — each network /24 or smaller, private only, never the hub
itself, network by network so a second one (the link to the Frigate host)
cannot crowd out the LAN; one
small UDP packet each, to port 3702 only. It touches every device on the
network, so the timer does it at most once a day (and once after start); its
answer is kept until the next sweep. `seloraDev.scanCameras({ sweep: true })`
runs it on demand.

## The card

`add_cameras_to_frigate` returns a **client action** (`add_frigate_cameras`),
not a destructive card, because a network camera's login has to be typed into
it:

- **The plan stays on the server**, keyed by a random `plan_id` (half an hour).
  The card carries the id and display rows; the panel sends back only the id
  and the logins (`selora_ai/frigate/add_cameras`, admin only). Which cameras
  and settings were proposed is never taken from the panel.
- **Logins never reach the model.** They are typed into the card, held in
  `frigate-cameras.js` only, posted to the websocket command and written into
  the stream URLs Frigate gets. They are not in the plan, the fingerprint, the
  card, the session, a tool result or a log; the tool's result tells the model
  never to ask for a password in chat.
- **The confirm re-checks the plan** with the same fingerprint (HA cameras'
  credential-free streams, network cameras' addresses, objects, recording) and
  refuses cameras that changed since.
- **A refused login writes nothing.** The command answers `camera_errors` per
  camera; the panel keeps the card open with the error under that camera and
  reports nothing to the conversation until the cameras are added.
- **The conversation's outcome comes from the plan** (`added_line`), never from
  the panel's report.

After the restart a background task waits for `/api/version` and reloads the
Frigate integration so the new camera entities appear.

**A Frigate that cannot restart itself still has the cameras.** Frigate's
`/api/restart` only works when it runs as its own container; run by the Selora
OS host it answers "Unable to restart Frigate". The config is saved by then, so
the confirm answers `status: saved, restart_failed` and the conversation says
the cameras start with Frigate's next restart, instead of reporting a failure.

## Streams

- **Home Assistant cameras.** A camera is a device, one entity per stream. Main
  vs substream comes from the words in each entity's translation key, unique id
  and name (`_MAIN_WORDS`, `_SUB_RANK`): Reolink `main`/`sub`, UniFi Protect
  `high`/`medium`/`low`, ONVIF profile names. Snapshot entities are not streams.
  Disabled entities have no stream: Reolink's Clear and UniFi's lower channels
  are disabled by default, so those cameras often arrive single-stream.
- **Network cameras.** `frigate/onvif.py` asks the camera over ONVIF: its clock
  first (`GetSystemDateAndTime`, unauthenticated) so the WS-Security timestamp
  matches it, then the media service, the profiles with their resolutions, and
  each profile's RTSP URI. The largest profile is recorded, the smallest is
  detected on. A camera named by address has no known ONVIF port, so 80, 8000
  (Reolink), 8080 and 2020 (Tapo) are tried in turn; a refused login ends the
  search. A name given as an address must resolve to a local IP — a model once
  passed a room name — and a rejected one is answered with the cameras the scan
  found. A camera that does not answer ONVIF falls back to its brand's known
  paths (Reolink, Hikvision, Dahua/Amcrest) when the brand is known; Reolink is
  recognised by its model names too, since its hardware scope omits the maker.
  HA's own ONVIF library is an integration requirement, installed only once
  that integration is set up, so it is not used.
- **A camera without ONVIF** (Wyze, many cheap cameras) is added from the stream
  address the user gives (`stream_urls`, main first, then a substream). No
  camera is asked anything: the card's login goes into that address, or none
  when the stream needs none. A login in the URL the user typed is dropped
  from what the plan keeps; the card asks for it again.
- With one stream it is used for both roles and detection is scaled to 720p.
- **"Already in Frigate" compares host, port and path**, since Frigate redacts
  credentials in `GET /api/config` and go2rtc wraps sources
  (`ffmpeg:…#video=copy`).
- **Only local streams.** A source on a public host (Nest, other cloud cameras)
  is skipped: its URLs expire and Frigate would record the internet.
- `rtsps` becomes `rtspx` (go2rtc's unverified RTSPS, for self-signed camera
  certificates) and UniFi's `enableSrtp` is dropped.
- Every camera is restreamed by go2rtc (`rtsp://127.0.0.1:8554/<name>`), so the
  camera sees one connection whatever reads it.

## Cameras already in Frigate

- **They answer with their switches.** Searching for a camera finds Frigate's
  own `camera.*` entity, so "enable recording on the Front Door cam" names it.
  The tool returns that device's switches (recordings, detect, motion,
  snapshots) and points at `execute_command`. Those switches are `config`
  entities, which commands may target only because
  `_safe_command_entity_allowlist` admits config entities.
- **A switch the camera's config disables stays off.** Frigate ignores a
  runtime `record`/`detect`/`snapshots` toggle when the config has the section
  disabled (`enabled_in_config: false`). When such a switch does not turn on,
  `execute_command` asks Frigate (`frigate/controls.py`) and the reply says the
  camera's Frigate settings have it off, instead of "may not have worked".
  The switch's camera and section come from its unique id
  (`<entry_id>:switch:<camera>_<name>`).

## The result card

Adding or removing cameras ends in a message carrying `frigate_result`
(`types.FrigateResult`): the action, the cameras with the entity ids the
Frigate integration gives them (`camera.<name>`, `switch.<name>_recordings`),
and the detection and recording settings. It is built from the plan
(`setup.result_for`) or the confirmed delete, never from the panel's report.
The text line stays on the message for the model and the history; the panel
shows `render-frigate-result.js` in its place.

- **The card waits on `hass`, not on a poll.** An added camera exists in HA
  only once Frigate has restarted and its integration reloaded. The panel
  re-renders on every state change, so the card turns from a spinner into the
  camera's snapshot and its controls as soon as `camera.<name>` is there: the
  recording and detection switches, and an occupancy sensor per tracked object
  (`binary_sensor.<name>_<object>_occupancy`), as live tiles rather than a
  sentence repeating them. Their HA names repeat the camera's and every
  occupancy sensor gets a house icon, so the grid's `data-tile-options` gives
  each a short name and an icon of its own (Recording, Detection, Person…). After three minutes it says Frigate is slow to come back.
- **The preview is the camera's own snapshot** (`entity_picture`, signed by HA),
  refreshed at most every five seconds as state updates re-render the card, and
  clicking it opens HA's live view. HA's `picture-entity` card is lazy-loaded
  and is often not defined inside a custom panel, which left the preview empty.
- **The entity ids are predictions, so the card checks them.** HA suffixes an
  id already taken: adding the camera HA calls `camera.porch` gives Frigate's
  `camera.porch_2`, and the predicted id is the original camera. The card only
  takes an entity of the `frigate` platform (`hass.entities`), the predicted id
  or its `_N` suffix (`frigateEntity` in `render-frigate-result.js`).
- **Only a clean result gets the card.** It replaces the message text, which is
  where failures are listed, so a removal or add that partly failed keeps the
  plain text.
- **`saved`** (Frigate could not restart itself) shows no spinner: nothing
  comes back on its own.
- **The tick draws only for a fresh result**, so reopening the conversation
  later does not replay it.
- Added cameras get `friendly_name`, so Frigate and HA show "E1 Zoom" rather
  than `e1_zoom`, and the delete card names the camera the same way.

## Ideas for a camera

A new camera has no usage history for the pattern-based suggestions, so
`frigate/ideas.py` proposes automations from what the home has:

| Kind | Needs |
|---|---|
| `person_away` | a person sensor and a way to tell nobody is home: `zone.home`, whose state counts the people home, else a group covering every `person.*` (a group of some, like the kids, is not the household) |
| `person_night_light` | a person sensor and a light in the camera's area |
| `animal`, `vehicle` | an animal (dog/cat/bird/pet) or car sensor |
| `watch_scene` | recording and detection switches on two or more Frigate cameras |

- **Sensors come from the registry, never from guessed ids:** Frigate's
  `<entry>:occupancy_sensor:<camera>_<object>` on the camera's device, or
  Reolink's `binary_sensor`s with the `person`/`animal`/`pet`/`vehicle`
  translation keys.
- **An idea whose trigger already triggers an automation is not offered**
  (`_collect_referenced_entity_ids` over the `triggers` of `automations.yaml`;
  a sensor used only as a condition does not count).
- **Notifications go to the first `notify.mobile_app_*`**, with the snapshot
  as `data.image: /api/camera_proxy/<camera>`; without a phone, to
  `persistent_notification.create`, which takes no image, so the prompt asks
  for none.
- **One physical camera, one set of ideas.** A Reolink camera Frigate also
  watches is two cameras in HA with their own sensors. A non-Frigate camera
  whose stream (host, port, path) Frigate reads is dropped, keeping Frigate's.
  By stream, not host: every channel of a Reolink NVR has the NVR's address.
  A camera is a device, not an entity: Reolink has an entity per stream
  (Clear, Fluent), Frigate reads one, and any match drops the whole device.
- **An unreadable `scenes.yaml` only drops the Watch idea**, never the other
  suggestions beside it.
- **The Watch scene** needs two cameras with both a recording and a detection
  switch, and is not offered once a scene in `scenes.yaml` sets all of them to
  on (membership alone is not enough: a scene may turn them off).
- Excluded entities (Selora's exclusion label) are never used: not as the
  camera, the trigger, the presence entity, the light, or a scene switch.
- **Acting on an idea is a chat request**, never a write: the title, with the
  exact entities in a details marker (cameras are not in the model's entity
  list), and the model proposes the automation or scene on the usual card.
- They show as chips under a live camera on the result card
  (`selora_ai/frigate/ideas`, asked at most once a minute) and as `camera_idea`
  home suggestions; a dismissal (`idea:<kind>:<camera>`) holds in both.

## Removing a camera

A Frigate camera's entity or device given to `remove_from_home` as `entity` or
`device` is redirected to `frigate_camera` (`removal_cards._frigate_camera_entity`):
HA refuses to remove an entity Frigate still provides, and the camera would come
back anyway. "Remove the Reolink Lumus Pro" reaches the tool as an entity.

`remove_from_home` with `kind: frigate_camera` (`frigate/removal.py`) takes the
camera's Frigate name, its friendly name, or the Frigate integration's
`camera.*` entity (whose unique id ends with the Frigate name), and returns a
delete card. Confirming removes `cameras.<name>` and its go2rtc streams — what
adding it wrote, nothing else — by setting them to null, Frigate's way of
deleting a key, then restarts Frigate like an add does. The card carries a hash
of the camera's inputs and streams, so one changed under that name since stays.

## Suggestion

Cameras Frigate could add, from HA and from the last network scan, show as one
`add_to_frigate` home suggestion (`async_frigate_suggestions`, merged by
`async_home_suggestions`). A network camera has no entity, so it is named by its
`address`. Frigate's answer is cached for five minutes, since the chat home
reads suggestions on every visit; the cache is dropped after cameras are added.
The id lists the cameras, so a dismissal holds until a new one appears.

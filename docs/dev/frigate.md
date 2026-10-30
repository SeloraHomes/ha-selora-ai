# Frigate

On a Selora hub Frigate runs on the Selora OS host, not in Home Assistant, and
Selora AI reaches it at the URL (and with the login) of the **Frigate
integration's config entry**, which points at the host's proxy. A home without
a hub can enter Frigate's address and login in Settings → Frigate instead
(`frigate_url` / `frigate_username` / `frigate_password` in the LLM entry's
data). `frigate_connection` (`frigate/client.py`) is the one place either is
read: the integration's entry first, then the settings; with neither, every
Frigate feature is off.

## Offered only with Frigate

- **A tool that only talks to Frigate is withheld without it**:
  `FRIGATE_TOOL_NAMES` (`tool_registry.py`) for chat, `FRIGATE_TOOLS`
  (`mcp_server/access.py`) for MCP `tools/list`. Offered anyway, the model
  proposed camera changes a home without Frigate could only refuse. A new
  Frigate tool goes in the set for each surface it is on.
- **Read on every use, never cached**: saving the settings needs no reload,
  and the MCP `serverInfo.version` gains `.frigate` while Frigate is set up so a
  client refetches the list.
- **Without the integration there are no entities.** Switches, the result
  card's live tiles, the events card's media (`/api/frigate/...` views) and
  `controls.py` all need it; `get_frigate_status` says so (`note`). The API
  calls (status, events, add, objects, move, remove, snapshots) work either way.
- **Settings says who connects Frigate, not where.** `frigate_source` is
  `hub` when the Frigate integration points at the hub's own Frigate
  (`managed_by_hub`), `integration` for any other; neither shows the address,
  which cannot be changed there. Selora OS creates the entry through the
  integration's config flow (`source: user`, like a hand-made one), always at
  the host's address on the VM network, `SELORA_HUB_HOST` (Selora OS's
  `seloraSlirpHost`); the host is the signal, never the source. Supervisor
  discovery is not an option: Frigate runs on the host, not as an app, so
  nothing can post it, and it would wait for a human to submit the flow.
- **The saved password never reaches the panel** (`frigate_password_set`); a
  blank one on save or test keeps it only for the saved address and username,
  since it would otherwise be sent to the new host. Clearing the address
  clears the login.

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
- `rtsps` is written as `ffmpeg:rtsps://…#video=copy#audio=aac`: FFmpeg does
  not verify the camera's self-signed certificate, and the AAC audio plays in
  Safari. go2rtc's `rtspx` passed a Wyze camera's audio through and Safari's
  live view stalled on it. UniFi's `enableSrtp` is dropped.
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

Adding, changing or removing cameras ends in a message carrying `frigate_result`
(`types.FrigateResult`): the action, the cameras with the entity ids the
Frigate integration gives them (`camera.<name>`, `switch.<name>_recordings`),
and the detection and recording settings. It is built from the plan
(`setup.result_for`), the confirmed delete, or what a confirmed change
returned (`setup.changed_card_camera`), never from the panel's report.
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
- **`saved`** and **`update_saved`** (Frigate could not restart itself) show
  no spinner: nothing comes back on its own.
- **A change (`updated`) says what the camera looks for in words**, per
  camera (`objects` on the camera, not the card): the card replaces the
  message that said it, and the new occupancy sensors only appear once
  Frigate is back. A move carries no objects. A card that changed anything
  besides Frigate cameras keeps the text (`_frigate_changed_result`).
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

## What a camera looks for

`set_camera_objects` (`frigate/objects.py`) changes `cameras.<name>.objects.track`
on a camera Frigate already has, behind the destructive confirmation card
(`kind: "frigate_objects"`), then restarts Frigate.

- **Not the Detect switch.** The Frigate integration's `switch.<cam>_detect`
  turns all detection on or off and chooses no object. Asked to "enable car
  detection", the model turned on an already-on switch and reported success;
  the add-cameras tool and its "already in Frigate" hint now name this tool.
- **The card says what the camera starts and stops looking for**, and the
  list it ends with when something is dropped, and says when detection is off
  on the camera, since a list changes nothing then. A before-and-after list
  hid a removal: "look for cars" sent as `objects: ["car"]` dropped person,
  and the card read "looks for car (now car, person)". The tool's description
  makes "look for X" an `add`; `objects` is for a whole list the user gave.
- **Confirming re-reads the camera** and writes only if it still looks for what
  the card was built from (`_fingerprint`), then applies the call's `add` /
  `remove` / `objects` to that list.
- Asking for what it already looks for answers `unchanged`, with no card.
- **Checked against the list saved, not the one running.** `/api/config` is
  Frigate's running configuration, and a write runs only after the restart
  (never, when Frigate cannot restart itself). The list written is kept
  (`_SAVED`) until Frigate runs it or runs a list set elsewhere, and confirms are serialized, so a second
  card built from the old list is refused instead of undoing the first.
- **Only labels the running model reports** (`cameras.model_labels`). Frigate
  saves any label, then drops one its model lacks at startup with only a log
  warning, so the card and the reply promised a camera that never looked for
  it. The labels are the merged labelmap under each detector
  (`detectors.<name>.model.labelmap`); the top-level `model.labelmap` is only
  the user's override. It is not the docs' list: Frigate's YOLO labelmap
  (`coco-80.txt`) reports a bus or a truck as `car`, while its Coral one keeps
  `bus`. A label being added is refused with the model's labels; one already
  tracked can still be removed. The add card checks its objects the same way,
  and `find_camera_events` says the model lacks the label instead of offering
  a change that cannot work. A Frigate that publishes no labelmap is not
  checked.

## A camera at a new address

`move_frigate_camera` (`frigate/moving.py`) points a camera already in Frigate
at the address the router gave it, behind a card (`kind: "frigate_move"`).
Adding it again made a second camera, with no history, entities or
automations, next to the dead one.

- **The login is kept, never re-asked.** `/api/config` masks every stream
  login, so the confirm reads `/api/config/raw` and swaps only the host (and
  the port, when given) in the camera's own go2rtc streams, inputs and
  `onvif.host`. The raw file holds every login and the MQTT password: it is
  parsed in memory and never logged, returned or shown.
- **One address per camera.** A camera read from two hosts is refused; which
  one moved is not ours to guess.
- **Only local addresses**, as for adding.
- **Adding a moved camera asks about moving it.** A stream address the user
  gives that matches a Frigate camera's port and path on another host whose
  address no longer answers comes back as `possibly_moved`, with a hint to
  ask the user. The add card stays: two cameras of one brand share a path,
  and an old camera merely offline must not block a new one.
- **Ports.** A port written in the address the user gave counts; one equal to
  the camera's current port is no change. A camera read on several ports
  (an omitted port is its scheme's default) refuses a new port, since which
  source moved is a guess; its address alone can still move.

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

## Links into Frigate's UI

An opened event on the events card links to Frigate's own pages: the event
(`explore?event_id=`) and the camera's zones and masks
(`settings?page=masksAndZones&camera=`). Frigate already does these well;
Selora only takes the user to the right page (`panel/frigate-ui.js`).

- **Through the app that serves Frigate's UI**, the Frigate app or Frigate
  Proxy, found by its sidebar panel (`config.addon`). Listing apps is
  admin-only; the panels are not.
- **The ingress URL itself, not the app's panel.** HA's app panel loads the
  app's root and drops any sub-path, so a deep link opens
  `/api/hassio_ingress/<token>/<page>` in a new tab. First it creates the
  `ingress_session` cookie with `supervisor/api` `/ingress/session`, as HA's
  frontend does before loading an app. Both that call and `/addons/<slug>/info`
  are allowed for non-admins.
- The tab opens during the click, before any await, or the browser blocks it.
- No app with ingress means no links.

## What the cameras saw

`find_camera_events` (`frigate/events.py`, MCP `selora_find_camera_events`)
reads Frigate's tracked-object events, not Home Assistant's: the Frigate
entities only say what is there now.

- **Admin only**, like `selora_get_camera_image`: the events say who came and
  went, and a read-only credential is the one handed to outside assistants.
- **The model gets facts, never media URLs.** Ids, labels, zones, local times,
  and `sub_label` (a face or plate Frigate recognised, so "was Alice at the
  door?" can be answered). Frigate's GenAI `description` only when GenAI is on.
- **A description search without semantic search keeps its other filters.**
  "White cars yesterday" still lists yesterday's cars, with `not_filtered_by`
  so the reply says the colour was not checked, and `semantic_search`: where
  to turn it on and what it costs (hub CPU and memory, a reindex for older
  events, GenAI descriptions being a separate, outward option). Refusing the
  whole search left the model asking instead of answering. Turning it on is
  the user's: it is a global section, which this integration does not write.
- **Counts cover the range, the list is a page.** Up to `_COUNT_CAP` events are
  read without thumbnails so `total`, `by_object` and `by_camera` answer "how
  many…" while at most 25 are listed.
- **An event id is checked before it is passed on** (`_EVENT_ID`): the panel
  builds media paths from it.
- **The card is drawn from the stored tool calls** (`camera-events-card.js`),
  so a reopened conversation has it. Snapshots and clips come from the Frigate
  integration's own views (`/api/frigate/snapshot/<id>`,
  `/api/frigate/recording/<camera>/start/<s>/end/<e>`), signed with
  `auth/sign_path` because an `<img>` cannot carry the panel's token. An event
  without a snapshot (snapshots are off on a camera added from chat) is drawn
  from its thumbnail, `/api/frigate/<instance>/thumbnail/<id>`: that route has
  no form without the instance, so the tool result carries `instance`, Frigate's
  MQTT `client_id`. Not the notification views: those can be switched off in
  the Frigate integration.
- **A camera answer gets no tiles from names in its prose.** Cameras are named
  after the place they watch, and so is the lock on it: "nobody at the Front
  Door" made the reply's name matching show the Front Door lock's tile.
  `names_places()` turns that fallback off for the turn.
- **An empty search says what could not have been seen.** Frigate records
  events only for the objects a camera looks for (`objects.track`, `person` by
  default) and only while detection is on, so "no cars" from a camera watching
  for people is not an answer. `watching` lists both per camera, and
  `not_recorded` spells out each blind spot for the requested objects. An
  object no camera looks for comes with an `offer` of `set_camera_objects`:
  stated as a fact, the model only reported it. The prompt allows offers for
  safe devices only, so it carries an exception for a tool result's top-level
  `offer` (`_SHARED_STATE_QUERY_RULES`), never for "offer" prose elsewhere in
  a result, where entity names and descriptions are untrusted. The field is
  built from checked camera names and object labels only.
  Semantic search does not help there: it ranks recorded events only. The
  config is today's, so the note says what is recorded now, never what
  was in the range.

## Over MCP

Chat's card-based changes are two calls on MCP (`mcp_server/frigate.py`): the
first runs the chat preview and returns `requires_confirmation`, the change
in words and a `confirm_token` (the card's fingerprint, or the add's
`plan_id`); the second, with `confirmed` and the token, runs the same confirm
the card does.

- **The confirm is checked against the client's token, not a fresh preview.**
  A fresh fingerprint would always match, so a camera changed since the user
  agreed would be changed anyway.
- **A confirmed add reads only the plan**, never the second call's arguments,
  like the panel's confirm.
- **No camera login goes through MCP.** An outside assistant is a model too; a
  network camera that needs one is refused with `needs_login`, naming Selora
  AI's own chat, where the card takes it. A stream URL is added as given (any
  login in it is already dropped by `user_streams`).
- **Parameters are derived from the chat tools** (`_FRIGATE_CHANGES` in
  `mcp_server/definitions.py`) plus `confirmed` / `confirm_token`; only the
  descriptions are MCP's own.
- `selora_get_frigate_snapshot` fetches from Frigate itself, so it serves a
  camera without an HA entity and an event's boxed snapshot (its thumbnail
  when snapshots are off on the camera).
- **Events over MCP carry links, not media** (`frigate/media.py`). An MCP
  client cannot show the user an image a tool returns, and its servers cannot
  reach the home, so `selora_find_camera_events` adds `snapshot_url` and
  `clip_url`: links the user's own browser opens, past any proxy in front of
  Home Assistant (the browser holds that login). Chat's events stay link-free;
  the panel draws them.
  - **Signed with `async_sign_path` for the content user**, for 15 minutes and
    one path. Not the caller's token: a Selora Connect login has none.
  - **The view takes a signed link or an admin**, nothing else. A request with
    no `Authorization` header can only have been authenticated by the
    signature; a non-admin's own token is refused, as the MCP tools refuse it.
  - **Streamed from Frigate, never redirected to it**, so Frigate's address and
    login stay on the hub whichever way it is connected. `Range` is forwarded
    and the range headers passed back: a browser fetches a video in ranges,
    and Safari plays nothing without them. Frigate's cookies are not passed.
  - **At the hub's remote address, else the local one** (`async_link_base`).
    Connect's remote access for Home Assistant, behind its login, which Selora
    OS writes to `<config>/.selora/remote_url.json` (`{"remote_url": ...}`,
    atomically, removed when remote access is off). A file, not entry data:
    writing the entry means editing `.storage` with Core stopped, a restart on
    every hub. Read in the executor on each link build, no cache; a missing,
    unreadable or invalid file falls back, and its content is never logged.
    Without it, Home Assistant's own URL settings (internal allowed: a link
    that opens at home beats none). Never the MCP client's address: on a hub
    that is the MCP resource, which takes only Connect's token, so a browser
    gets Pangolin's 401. Nor the external URL alone: on a hub it is often the
    LAN address, and nobody types the proxy's hostname into it.

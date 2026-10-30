# Frigate

[Frigate](https://frigate.video) is a video recorder that detects people, cars,
animals and other objects on your cameras. With Frigate connected, Selora AI
can add cameras to it, change what they look for, point a camera at a new
address, remove one, and tell you what the cameras saw. You can ask in Selora
AI's chat, through Assist, or from an MCP client such as Claude.

The Frigate tools only show up once Frigate is connected. Before that, neither
the chat model nor an MCP client is offered them.

## Connecting Frigate

**On a Selora hub** there is nothing to set up. Frigate runs on the hub, and
the hub connects it. Settings → Frigate says "Set up by your Selora hub".

**Without a Selora hub**, Selora AI connects to Frigate in one of two ways:

- **The Frigate integration** ([HACS](https://github.com/blakeblackshear/frigate-hass-integration)).
  When it is installed and connected, Selora AI uses its connection, and
  Settings → Frigate says so. To change the address or login, change them in
  the Frigate integration. This is the recommended setup, because the
  integration also gives each camera its Home Assistant entities: the camera,
  its recording and detection switches, and a sensor for each tracked object.
- **Selora AI's settings.** Open Settings → Frigate and enter Frigate's address
  (for example `http://192.168.1.10:5000`), plus a username and password if
  Frigate asks for a login. Test the connection, then save it. Selora AI then
  talks to Frigate directly. Without the integration, Frigate's cameras have
  no Home Assistant entities, so there are no switches to turn and no live
  view on a dashboard.

When both are present, the Frigate integration is used. The saved password is
never sent back to the browser. To keep it, leave the field blank. To clear
the connection, use **Disconnect**.

## In chat

| Ask                                                                                               | Tool                                                                             |
| ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| "Which cameras does Frigate have?", "Is the garage camera recording?", "What can Frigate detect?" | `get_frigate_status`                                                             |
| "Was anyone at the door this afternoon?", "How many cars came by yesterday?"                      | `find_camera_events`                                                             |
| "Add the porch camera to Frigate", "Add the camera at 192.168.1.42"                               | `add_cameras_to_frigate`                                                         |
| "Look for cars on the driveway camera"                                                            | `set_camera_objects`                                                             |
| "Keep snapshots on the front door camera for 30 days"                                             | `set_camera_snapshots`                                                           |
| "The Wyze is at 192.168.1.205 now"                                                                | `move_frigate_camera`                                                            |
| "Remove the garage camera from Frigate"                                                           | `remove_from_home` (`kind: frigate_camera`)                                      |
| "Turn recording off on the porch camera"                                                          | `execute_command` on the camera's Frigate switch (needs the Frigate integration) |
| "Turn on face recognition", "Who does Frigate recognize?"                                         | `set_face_recognition`, `get_face_recognition`                                   |

Each change shows a card that says what will happen. Nothing changes until
you confirm. Frigate restarts to apply a change, so detection and recording
pause for about a minute.

## Over MCP

These tools are on Selora AI's MCP server (`/api/selora_ai/mcp`). Every one of
them needs an admin credential, because a camera shows the inside of the home.

| Tool                             | What it does                                                                                                                                                                                                                                                                                        |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `selora_get_frigate_status`      | Frigate's version and cameras. For each camera: whether it detects and records, what it looks for, how long recordings are kept, its zones, where its video comes from (without logins) and its frame rates. Also Frigate's detectors, the labels its model detects, optional features and storage. |
| `selora_find_camera_events`      | What the cameras saw: tracked objects, with camera, zones, time, duration, and a recognised face or plate. Counts cover the whole time range. Each event comes with links to its snapshot and, when Frigate recorded it, its clip.                                                                  |
| `selora_get_frigate_snapshot`    | An image: a camera's latest frame, or the snapshot of an event from `selora_find_camera_events` with the object boxed.                                                                                                                                                                              |
| `selora_get_camera_image`        | A snapshot from any Home Assistant camera entity.                                                                                                                                                                                                                                                   |
| `selora_add_cameras_to_frigate`  | Adds cameras Home Assistant has (`entity_ids`, or all of them), network cameras at given addresses (`hosts`), or a stream address (`stream_urls`).                                                                                                                                                  |
| `selora_set_camera_objects`      | Changes what a camera looks for (`add`, `remove`, or the whole list as `objects`).                                                                                                                                                                                                                  |
| `selora_set_camera_snapshots`    | Turns a camera's snapshots on or off in its configuration, and how many days they are kept.                                                                                                                                                                                                         |
| `selora_move_frigate_camera`     | Points a camera at its new address and keeps its name, history and login.                                                                                                                                                                                                                           |
| `selora_remove_frigate_camera`   | Removes a camera's Frigate configuration and streams. Its recordings stay until Frigate's retention deletes them.                                                                                                                                                                                   |
| `selora_get_camera_descriptions` | Which cameras Selora AI describes, which were never asked about, their zones, and the budget.                                                                                                                                                                                                       |
| `selora_set_camera_descriptions` | Turns descriptions on or off for one camera, optionally only for what enters chosen zones.                                                                                                                                                                                                          |
| `selora_get_face_recognition`    | Whether face recognition is on, who is in Frigate's Face Library and with how many photos, and how to add someone.                                                                                                                                                                                  |
| `selora_set_face_recognition`    | Turns face recognition on or off. On a Selora hub it uses the large model on the hub's GPU. Turning it off keeps the Face Library.                                                                                                                                                                  |
| `selora_add_face_photo`          | Adds a person's face from an event in `selora_find_camera_events`: Frigate learns it from that event's snapshot.                                                                                                                                                                                    |

**Changes are made in two calls.** The first call changes nothing. It returns
`requires_confirmation`, a description of the change, and a `confirm_token`.
The client asks you, and once you agree it calls the tool again with the same
arguments, `confirmed: true` and that token. Selora AI checks the camera
against the token. If the camera changed in between, the change is refused
rather than applied to something you were never shown.

**Snapshots and clips open as links.** An MCP client such as Claude can look
at a snapshot, but can't show it to you in the conversation. Instead, each
event from `selora_find_camera_events` has a `snapshot_url` and, when Frigate
kept a recording, a `clip_url`, which the assistant gives you to open in your
browser. A link works for 15 minutes and opens only that one snapshot or
clip. On a Selora hub with remote access, links use the hub's remote address,
so they open away from home after its login. Otherwise they use Home
Assistant's own address, which may only work on your home network. Frigate's
address and login never appear in a link.

**Camera logins never go through an assistant.** A network camera that needs a
username and password can't be added over MCP. The tool's result says which
cameras need a login. Add them from Selora AI's own chat instead, where the
login is typed into the confirmation card and never reaches a model. Home
Assistant's own cameras and streams that need no login can be added over MCP.
Moving a camera keeps the login Frigate already has, so no login is needed for
that.

**Faces come from your cameras, not from the conversation.** An assistant
can't pass on a photo you showed it, so over MCP a person is added from an
event where a camera saw them: the assistant finds one with
`selora_find_camera_events`, and Frigate learns the face from its own
snapshot. Pick a few events where the person faces the camera. To upload
photos instead, use the card in Selora AI's own chat.

## What Selora AI changes in Frigate

Selora AI only writes the sections for each camera: `cameras.<name>` and the
`go2rtc` streams it reads the camera from. The one exception is face
recognition, which it turns on or off when you ask (and on a Selora hub, sets
to the large model). Detectors, hardware acceleration,
models, MQTT and other global settings stay as you or Selora OS set them.
Changes go through Frigate's own configuration API, which validates them, and
never by editing `config.yml`.

# Insights Export Contract (Selora OS ⇄ HA integration)

How the Selora OS host reads home-health data produced by the Selora AI HA
integration. **This is a file handoff, not an HTTP API** — the integration
writes files inside the HA config directory; the host copies them out. The
integration **never** authenticates to or contacts Selora Connect; the host
owns the copy and any upstream upload.

- Machine-readable schemas: [`schemas/insights-manifest.schema.json`](schemas/insights-manifest.schema.json),
  [`schemas/insights-envelope.schema.json`](schemas/insights-envelope.schema.json)
- Producer: `custom_components/selora_ai/insights_export.py`
- Current `schema_version`: **2**

## Enabling the export

The integration writes nothing unless the host **opts in** by creating a marker
file (it can't detect on its own that it's running on Selora OS). The host
should create an empty file:

```
<config>/selora_ai/insights/.export_enabled
```

`<config>` is the HA configuration directory as seen inside the VM (the
directory containing `configuration.yaml` / `automations.yaml`). Removing the
marker stops future publishes.

## Directory layout

```
<config>/selora_ai/insights/
├── manifest.json                       # atomic pointer — READ THIS FIRST
├── exports/
│   ├── insights-000000001751900000.json.gz   # immutable, gzipped envelope
│   └── insights-000000001751813600.json.gz   # previous generation (retained)
└── .export_enabled                     # host-created opt-in marker
```

- `manifest.json` is small and uncompressed — poll it cheaply.
- Artifacts are named `insights-<sequence>.json.gz`, zero-padded to 15 digits,
  gzip-compressed JSON. **Each artifact is immutable** once it appears.
- Retention keeps the newest few generations (default 3); older ones are pruned.

## Read algorithm (host side)

1. Read `manifest.json`. Absent → not published yet (or opt-in marker missing).
2. Check `schema_version` is one you support. A higher number than you know
   means the integration is newer — skip and log, don't guess.
3. If `manifest.sequence <= last_copied_sequence` → nothing new; done.
4. If `manifest.status != "complete"`, you may skip (a `"partial"` publish had a
   collection error) or ingest with awareness — your call.
5. Copy the single file at `manifest.artifact.path` (relative to the manifest's
   directory). It is immutable, so this copy is race-free even if the
   integration publishes a newer generation meanwhile.
6. Verify `sha256(file) == manifest.artifact.sha256` and
   `len(file) == manifest.artifact.size_bytes`. On mismatch you raced a manifest
   rewrite — re-read `manifest.json` and retry (bounded).
7. Gunzip and JSON-parse → validate against the envelope schema.
8. Record `last_copied_sequence = manifest.sequence`.

Because the artifact named by any manifest never changes, the worst case is
copying a slightly-older generation — never a torn/partial read.

## Sequence & freshness

- `sequence` is **monotonic and restart-proof** (`max(persisted+1, epoch_seconds)`),
  so it always increases across restarts. Use it for change detection.
- `generated_at` / `next_expected_at` are ISO-8601 UTC on the **VM clock**.
- **Staleness is a signal, not a reason to discard data.** Treat three states:
  - *manifest advancing* — healthy.
  - *manifest present but `now > next_expected_at + grace`* (grace ≈ 2 ×
    `cadence_seconds`) — the integration has gone silent while HA is up. A dead
    integration can't report its own death, so **only the host can detect this**;
    forward it to Connect as a health signal. Keep using the last-known data.
  - *manifest absent* — never provisioned, or the marker/files were removed.
- Clocks: `next_expected_at` uses the VM clock and you compare against the host
  clock; keep the grace generous to absorb small skew.

## What the artifact contains

The gzipped envelope is a full snapshot every publish (a missed generation loses
nothing). See the envelope schema for the exact shape. Top level:

| Field | Meaning |
|-------|---------|
| `schema_version`, `sequence`, `generated_at` | identity / versioning |
| `signals` | active Layer-1 health signals (deduped per `kind`+`target`) |
| `insights` | deterministic advisor items (issue/fix/improvement) |
| `health` | deterministic 0-100 health score + `band` + the per-check roll-up explaining it |
| `battery_forecast` | when each replaceable battery is expected to run out (optional, see below) |
| `cameras` | how Frigate analyses each camera, with alertable `issues` (optional, see below) |
| `roster` | full device-plane inventory — integrations, devices, entities, automations, scripts, scenes, each with state/availability |
| `inventory` | aggregate counts |
| `collection` | `{ status, partial_reason, roster_truncated? }` |

### The health score

`health.score` is the deterministic 0-100 roll-up (100 = nothing flagged), with
`health.band` as its letter grade (A ≥90, B ≥80, C ≥70, D ≥60, else F). It is
also mirrored into `manifest.summary.health_score`, so a host can **track home
health by polling `manifest.json` alone** — no artifact copy, no gunzip.

- **`score: null` means unknown, not healthy.** Before the first audit runs there
  is no score; the manifest omits `health_score` entirely in that case. Never
  substitute 100.
- **`computed_at` is the audit's clock, not the envelope's.** The score refreshes
  on the health-scan cadence (default 15 min), which is independent of the export
  cadence — so `computed_at` normally predates `generated_at`. Use it to age the
  score rather than assuming it was computed at publish time.
- **`sections` explains the number** — one row per check ("Devices offline: -30.3
  (14)"), biggest impact first, splitting into the two penalty families:
  `device_penalty` (per-device outages, scaling with the √ of the affected fleet
  share) and `other_penalty` (integration errors, automation hygiene, updates —
  fixed-severity with diminishing returns).
- **The device penalty is capped per device on small homes.** It is
  `min(72 * sqrt(f), max(12 * weight, 72 * f))`, where `weight` sums the
  affected devices (warning 1, critical 3, info 0.3) and `f` is
  `weight / fleet`, clamped to 1. About 12 points a device while
  `weight * fleet < 36`; never less than the linear share, so a home that has
  gone wholly dark still loses all 72. Offline warning devices on a 5-device
  fleet: one → B (86), two → C (71), three → F (57). On 10: B, C, D. The √
  curve is unchanged once `weight * fleet ≥ 36`, e.g. two devices on 18, or
  one on 36.
- **A media device that is off is not an outage.** An `unavailable` signal on a
  device with a primary `media_player` (TV, speaker, streamer — any platform)
  that is not also an always-on device (`assist_satellite`, `camera`,
  `alarm_control_panel` or `lock`: a voice satellite, a wall tablet, a smart
  display)
  carries `severity: "info"` and `evidence.standby: true`, from the first scan
  and across restarts. It is not in `Devices offline`; once it has been off a
  whole day the audit lists it under the `standby_media` check, which subtracts
  nothing, so it never appears in `sections`. `insights` follows the same day:
  no insight for a standby signal before it, then an info "<name> is off".
  Hosts counting outages from `signals` should skip `evidence.standby` the
  same way.
- Per-*finding* contribution rows are **not** exported. They scale with the fleet
  (thousands of rows on a large home) and carry no detail the host lacks — the
  per-target view is already in `signals` and `roster`.

### The battery forecast

`battery_forecast` gives the lead time `battery_low` lacks: for each battery
whose cells get **replaced**, an estimate of when it reaches 0%, so an installer
can swap several in one visit before any dies.

```json
"battery_forecast": {
  "generated_at": "2026-10-09T06:00:00+00:00",
  "items": [{
    "entity_id": "sensor.front_door_battery",
    "device_id": "<roster.devices[].id>",
    "level": 34,
    "drain_per_day": 0.21,
    "depleted_at": "2027-03-20T04:00:00+00:00",
    "depleted_range": { "earliest": "2027-02-01T…", "latest": "2027-06-11T…" },
    "confidence": "medium",
    "since": "2026-06-01T00:00:00+00:00",
    "points": 9
  }]
}
```

- **Only forecastable batteries appear**, sorted by `depleted_at`, soonest
  first. A battery that is skipped leaves no row (no nulls, no placeholders).
  `items: []` means none could be forecast; an **absent** `battery_forecast`
  means the forecast couldn't be computed (no recorder), which is not the same.
- **Rechargeable devices never appear**: phones, tablets, watches and laptops
  (the companion app), robot vacuums and mowers, EVs, home batteries, and any
  device with a charging-state entity or a history of repeated recharges.
- **Honest uncertainty.** `depleted_range` is the window between the steepest
  and shallowest plausible drain; on a short series it is wide, and `latest`
  is capped at 3 years. `confidence` is `high` (≥6 distinct levels over ≥60
  days, following the fitted line closely), `medium` (≥4 levels over ≥28 days)
  or `low` (the bare minimum: 3 levels over 14 days). A forecast past 3 years
  is left out rather than dated.
- **`depleted_at` can be in the past**: a battery overdue on its estimate is
  still listed, soonest first.
- **`since`** is the first reading after the last battery replacement; the fit
  never spans a swap.
- **`generated_at` is the computation's clock.** The forecast is recomputed at
  most every 6 hours and repeated unchanged in the publishes in between.
- A battery at ≤10% due within 14 days also carries the existing `battery_low`
  signal — there is no separate signal kind for it.
- Only battery entities are named, with no raw history. The forecast is built
  from the registries, not the roster, so a truncated roster still forecasts
  every eligible battery.

### The cameras

`cameras` says how Frigate (Selora Vision) analyses each camera, and what about
it costs the hub or loses detections. It exists so Selora can alert on a camera
set up badly: one analysed at 4K barely moves `health.score` (a warning costs 5
points) while it starves every other camera on the hub.

```json
"cameras": [{
  "frigate_name": "onvif_camera",
  "name": "Front Door",
  "device_id": "<roster.devices[].id>",
  "entity_id": "camera.front_door",
  "enabled": true,
  "detect": { "width": 3840, "height": 2160, "fps": 5 },
  "streams": [
    { "roles": ["record"], "width": 3840, "height": 2160, "codec": "h265", "readable": true },
    { "roles": ["detect"], "width": 3840, "height": 2160, "codec": "h265", "readable": true }
  ],
  "streams_measured_at": "2026-11-18T06:00:00+00:00",
  "stats": { "camera_fps": 5.0, "process_fps": 5.0, "skipped_fps": 0.0,
             "detection_fps": 20.2, "detect_cpu": 146.8, "decode_cpu": 1.6,
             "system_cpu": 46.7, "detector_load": 0.113 },
  "issues": ["detect_above_720p", "detect_stream_heavy", "high_cpu"]
}]
```

**Alert on `issues`.** Each code is stable, and each is also a finding of the
`camera_analysis` check on the Health page (`health.sections`):

| Issue | Means | Fix |
|---|---|---|
| `detect_stream_unreadable` | Frigate's ffprobe got no answer from the stream it analyses, twice in a row | the camera is off the network, or that stream is disabled in the camera |
| `no_video` | no frames from the camera for 10 minutes | as above |
| `skipping_frames` | Frigate dropping over 0.5 frames a second, and over 15% of the camera's, for 10 minutes: the hub can't keep up | the camera's other issues, or another camera's |
| `stream_uneven` | the same drop on a hub with power to spare (`system_cpu` under 85, `detector_load` under 0.8) from a camera without `high_cpu`: its frames arrive in bursts. Not a misconfiguration; the Health finding is `info` when it is the camera's only issue | the camera's Wi-Fi signal, or a lower bitrate in the camera's own settings |
| `detect_above_720p` | Frigate analyses frames larger than 1280×720 (`detect` says the size) | set `detect` to the detect stream's size scaled to 720p |
| `detect_stream_heavy` | the stream decoded for detection is above 1080p (`streams[]` with `detect` says the size), so every frame is decoded in full | if that stream also has `record`, Frigate reads only the main stream: add the camera's substream as the `detect` input; otherwise lower the substream to 720p in the camera's own settings |
| `high_cpu` | the camera's detection process over 100% of a core for 10 minutes, usually constant motion | a motion mask over trees, a street |

- **`skipping_frames` and `stream_uneven` never appear together**: the hub's
  room at the audit decides which one a camera that drops frames gets.
- **`system_cpu` and `detector_load` describe the whole hub**, the same on
  every camera: `system_cpu` is Frigate's % of the whole system,
  `detector_load` the frames a second asked of the detectors over what their
  inference speed allows (1 is saturated).
- **`issues` is reported whatever the owner did on the Health page.** Ignoring
  the finding there hides the card from the owner, not the camera from Selora.
- **A camera turned off in Frigate** (`enabled: false`) has no issues.
- **The live issues need 10 minutes** (`no_video`, `skipping_frames`,
  `stream_uneven`, `high_cpu`): Frigate's rates move every second, and an issue is raised only
  once it has held across Health audits that long. `skipping_frames`,
  `stream_uneven` and `high_cpu` then stay until absent for an hour, so a
  rate that swings is one issue, not one per swing; `no_video` goes as soon
  as frames arrive. `stats` itself is the instant the audit read it.
- **`streams` are Frigate's own measurement** of each input, in its config
  order, made with the logins Frigate holds. A camera is measured once a day,
  an hour after a failure, and as soon as its inputs change; until its first
  measurement each stream's `width`, `height`, `codec` and `readable` are
  `null` (`streams_measured_at: null`). `readable: false` means the probe got
  no answer; `detect_stream_unreadable` waits for a second one.
- **`device_id` and `entity_id`** are `null` when the Frigate integration has
  no entity for the camera (Frigate reached from Selora's settings, or a camera
  it has not loaded yet).
- **Absent `cameras`** means no Frigate, or Frigate could not be read at the
  last audit (`health.sections` then has no `camera_analysis` row, and the
  Health page shows the check as errored). It refreshes with the health score,
  on the health-scan cadence.

**Privacy note:** unlike the anonymous PostHog telemetry (counts only), this
export carries **real identities and state** (entity_ids, names, states). That's
intentional — it's the user's own home going to their own account, keyed by
`producer.installation_id`. It excludes raw attribute bags (GPS, tokens, etc.).

**Not included:** the panel's LLM "home audit" (the natural-language,
per-household recommendation cards) is **not** exported — it stays on the home.
Only the deterministic signals/insights/health/roster cross the boundary. The
score and its check roll-up are pure rules over HA state, so they travel; the
model-written prose that sits beside them in the same audit record does not.

## Versioning

`schema_version` is bumped on breaking changes to the manifest or envelope
shape. v2 added the full `roster`. Additive fields may appear within a version;
consumers must ignore unknown fields. Pin the schemas in this directory to the
`schema_version` you validate against.

Additive within v2 (nullable/optional, no version bump):

- `health` — the deterministic score + band + per-check roll-up (see above), and
  `manifest.summary.health_score` mirroring the number. Absent on older
  producers → treat the score as unknown.
- `battery_forecast` — battery depletion forecasts (see above). Absent on older
  producers and when the forecast can't be computed.
- `cameras` — how Frigate analyses each camera, with alertable `issues` (see
  above). Absent on older producers, without Frigate, and when Frigate could
  not be read.
- `signals[].device_id` — device the target belongs to when `target_kind`
  is `entity`; `null` for device/integration targets or unassigned entities.
- `signals[].evidence.standby` — `true` on an `unavailable` signal for a media
  device that is off or asleep (see the health score). Absent → `false`.
- `roster.entities[].device_id` — device the entity belongs to, or `null`.
- `roster.integrations[].custom` — `true` for custom components (not built
  into Home Assistant).
- `roster.integrations[].name` — human manifest name (e.g. "National Weather
  Service (NWS)"). Consumers label by `name` → `title` → `domain`; empty when
  unresolved.
- `roster.integrations[].version` — integration manifest version. Populated for
  custom components (which declare a `version`); empty for core integrations,
  which are unversioned and track the `producer.ha_version`.
- `roster.apps` — installed **Supervisor apps** (formerly "add-ons"). Present
  only on supervised installs; an **empty array** on Core/Container installs (no
  Supervisor). Each entry: `slug`, `name`, `version` (installed), `version_latest`,
  `update_available` (bool), `state` (`started`/`stopped`/`startup`/`error`/
  `unknown`), and `url` (app homepage/management page, credential-stripped, `""`
  if none). Absent on older producers → treat as `[]`.
- `roster.apps[].repository_name` / `repository_url` / `repository_maintainer` —
  the store the app was installed from, resolved from the Supervisor's store
  list. The Supervisor keys an app to its store by an opaque slug (`core`,
  `local`, or a hash), which is why the slug is resolved here and never
  exported. `repository_url` is credential-stripped and is `""` for the built-in
  `core` and `local` stores, which have no URL — render those by name only.
  Third-party stores are not necessarily on GitHub. Absent on older producers.
- `roster.devices[].disabled_entities` — count of intentionally-disabled
  entities on the device. Neutral (surface like HA's "+N disabled"), not a
  health problem.
- `roster.devices[].url` — device `configuration_url` (e.g. an add-on's
  homepage / management page); empty string when none.
- `roster.devices[].transient` — `true` when **every** integration backing
  the device is a transient BLE beacon / presence-advert source (`ibeacon`,
  `private_ble_device`, `bluetooth_le_tracker`, `ble_monitor`, `bermuda`).
  Those devices go unavailable simply when out of range and are often not the
  user's (a passing car's TPMS, a neighbour's tag). Consumers should exclude
  them from the "needs attention" tally and the default device view, keeping
  them behind a toggle. Absent on older producers → treat as `false`.
- `roster.devices[].serial_number` / `created_at` / `matter` / `connections` —
  hardware identity, so a pairing code saved before the device joined Home
  Assistant can be matched to it exactly. All absent on older producers.
  - `serial_number` — registry serial, `null` when the integration reports none.
  - `created_at` — when HA registered the device (ISO 8601). `null` when never
    recorded: devices older than HA 2024.8 were back-filled with the Unix
    epoch, which is reported as `null`, not as 1970.
  - `matter` — `{vendor_id, product_id}` (integers) from a directly
    commissioned Matter node's Basic Information cluster; the pair an `MT:`
    payload encodes. `null` for non-Matter devices, for devices bridged behind
    a Matter bridge (the bridge's own device carries the IDs), and while the
    Matter server is disconnected or the node is not interviewed.
  - `connections` — registry `[type, value]` pairs, sorted, e.g.
    `[["mac", "aa:bb:cc:dd:ee:ff"]]`; `[]` when none.

`roster.devices[].unavailable_entities` counts only **enabled, visible**
entities with no usable state — it excludes disabled and hidden entities.
A device whose entities are disabled on purpose (e.g. NWS, 12 of 13 disabled)
therefore reports `unavailable_entities: 0` and reads as healthy.

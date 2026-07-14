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
| `roster` | full device-plane inventory — integrations, devices, entities, automations, scripts, scenes, each with state/availability |
| `inventory` | aggregate counts |
| `collection` | `{ status, partial_reason, roster_truncated? }` |

**Privacy note:** unlike the anonymous PostHog telemetry (counts only), this
export carries **real identities and state** (entity_ids, names, states). That's
intentional — it's the user's own home going to their own account, keyed by
`producer.installation_id`. It excludes raw attribute bags (GPS, tokens, etc.).

**Not included:** the panel's LLM "home audit" (the natural-language,
per-household recommendation cards) is **not** exported — it stays on the home.
Only the deterministic signals/insights/roster cross the boundary.

## Versioning

`schema_version` is bumped on breaking changes to the manifest or envelope
shape. v2 added the full `roster`. Additive fields may appear within a version;
consumers must ignore unknown fields. Pin the schemas in this directory to the
`schema_version` you validate against.

Additive within v2 (nullable/optional, no version bump):

- `signals[].device_id` — device the target belongs to when `target_kind`
  is `entity`; `null` for device/integration targets or unassigned entities.
- `roster.entities[].device_id` — device the entity belongs to, or `null`.
- `roster.integrations[].custom` — `true` for custom components (not built
  into Home Assistant).
- `roster.integrations[].name` — human manifest name (e.g. "National Weather
  Service (NWS)"). Consumers label by `name` → `title` → `domain`; empty when
  unresolved.
- `roster.devices[].disabled_entities` — count of intentionally-disabled
  entities on the device. Neutral (surface like HA's "+N disabled"), not a
  health problem.
- `roster.devices[].url` — device `configuration_url` (e.g. an add-on's
  homepage / management page); empty string when none.

`roster.devices[].unavailable_entities` counts only **enabled, visible**
entities with no usable state — it excludes disabled and hidden entities.
A device whose entities are disabled on purpose (e.g. NWS, 12 of 13 disabled)
therefore reports `unavailable_entities: 0` and reads as healthy.

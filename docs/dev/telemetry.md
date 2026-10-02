# Telemetry

`telemetry.py` emits **anonymous, opt-in** telemetry — counters, enums and
versions only. Three events: `home_snapshot` (inventory gauges, ~2 min after
startup then every 24h), `llm_output_repaired` (how often each safety-net repair
on raw LLM output fires, by provider/model) and `usage_activity` (24h deltas of
how the install is used).

- **Opt-in, off by default** — `telemetry_enabled`, read live on every emit
  (`CONF_TELEMETRY_ENABLED` is in `hot_option_keys`). The one-time consent banner
  (`render-telemetry-consent.js`) sets `telemetry_prompt_seen` so it never
  re-nags. Unrelated to the local-only cost tracking in `usage.py` / `usage_store.py`.
- **Payloads are allowlisted** per event (`_REPAIR_PROPERTY_KEYS`,
  `_SNAPSHOT_PROPERTY_KEYS`, `_ACTIVITY_PROPERTY_KEYS`, enforced in `_capture`).
  **Never** entity ids, friendly names, prompt or response text. Local model
  names are replaced with `"local"` (`_safe_model`); the country is the
  self-declared `hass.config.country`, never IP-derived.
- **Identity** is a random per-install UUID; every POST sets `$ip: "0.0.0.0"` and
  `$geoip_disable: true`. Direct POST via `async_get_clientsession`, no SDK; the
  PostHog key in `const.py` is a public write-only ingest token.
- **Activity counters live in memory** on `TelemetryClient`, accumulate regardless
  of opt-in, and are flushed-then-reset only by the recurring 24h tick
  (`_telemetry_periodic`, not the startup one) so `period_hours` stays accurate.
  Not persisted: a restart drops the partial window rather than adding writes to
  the chat path.
- **Adding a repair counter:** add it to `REPAIR_TYPES` and call
  `record_repair("<type>")` at the site. It is a no-op outside an LLM call;
  `UsageTracker.scope` buffers per call and drains where provider/model are known.
- **Adding an activity counter:** add it to `_ACTIVITY_COUNTER_KEYS` and call
  `record_activity(hass, "<name>"[, n])` at the action's chokepoint (it never
  raises). **Adding a snapshot count:** `_SNAPSHOT_PROPERTY_KEYS` +
  `TelemetryClient._gather_snapshot`. Cover each in `tests/test_telemetry.py`.

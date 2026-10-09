# Battery forecast

`battery_forecast.py` estimates when each replaceable battery runs out, for the
insights export (`battery_forecast` in the envelope — contract in
`docs/insights-export-contract.md`). Its reader is an installer planning a
visit, so a wrong date costs a trip and a phone in the list teaches them to
ignore it.

## Rules

- **Rechargeable devices are excluded in one place, `is_rechargeable()`.** An
  integration in `RECHARGEABLE_INTEGRATIONS`, a `battery_charging` binary sensor
  or a charging-state sensor on the device, or more than one ≥5-point rise in
  the history that falls again. Add a rule there, not at a call site.
- **A charging-state sensor must say it is about the battery.** `full` is also
  a tank's or a bin's state, so the state alone would drop a replaceable
  battery; the sensor's entity_id or registry name/key must contain
  "batter" or "charg".
- **The live state ends every series.** Daily statistics lag up to a day; a
  battery swapped since must restart the fit, not show its new 100% beside the
  old battery's date.
- **`RECHARGEABLE_INTEGRATIONS` only names integrations the codebase knows**:
  each must be in `KNOWN_INTEGRATIONS` (or be `mobile_app`), and a test holds
  it to that. When the catalog gains a vacuum, mower, car or home-battery
  integration, add it here too.
- **One rise is a replacement, two are recharges.** A rise is a whole
  non-decreasing run gaining ≥5 points, so a swap logged as 30 → 60 → 100
  across daily means is one rise. The series restarts at the reading it rose
  to, and `since` is that reading.
- **Fit Theil–Sen over one reading per level change.** A stepped reporter sits
  at a level for weeks; repeats would weigh the fit toward the flats.
  `depleted_at` is projected from the last level change, not from now.
- **An unknown forecast is omitted, never empty.** No recorder or a failed query
  returns `None`, and the exporter leaves the field out; `items: []` claims
  "nothing to replace".
- **The forecast is cached for 6 hours.** The exporter publishes every 15
  minutes, and one recorder query per battery per publish is far too heavy.
  A failure is cached for the same interval, so a broken recorder isn't
  re-queried on every publish.
- **Candidates pass the same filters as the `battery_low` detector** (the
  Selora exclude label and transient BLE integrations), and
  `HEALTH_BATTERY_LOW_PCT` (15) is above the 10% "urgent" level. So an urgent
  forecast always has the existing signal behind it, without a second signal
  kind. Keep the filters aligned; a test pins both.

## History sources, checked against core 2026.9.4

- `recorder.statistics.statistics_during_period(hass, start, end, ids, period,
  units, types)` and `get_metadata(hass, statistic_ids=…)` for long-term
  statistics; `recorder.history.get_significant_states(hass, start, end,
  entity_ids, no_attributes=True)` for states. All are blocking, so they run
  on the recorder's executor (`get_instance(hass).async_add_executor_job`).
- **Pin `units={"unitless": "%"}`.** Statistics come back in the state's
  display unit; a battery state without `%` gets its series as a 0–1 fraction,
  and the drain is off by 100×.
- Daily `mean` rows: a year is ≤365 rows per battery. Intraday charge cycles
  average out in them, so the recharge-history rule only sees multi-day cycles.
  A device that charges daily shows a flat series, which isn't forecast anyway.
- **Battery sensors set `state_class: measurement`**, so they have long-term
  statistics, in all four of:
  - **ZHA**: `Battery` in the `zha` library (2.2.2, the version core pins),
    `zha/application/platforms/sensor/__init__.py`. It reports
    `battery_percentage_remaining / 2`, so values come in 0.5 steps.
  - **Zigbee2MQTT**: `battery: {device_class: "battery", state_class:
    "measurement"}` in Z2M's `lib/extension/homeassistant.ts` discovery
    payload (outside core; MQTT passes it through).
  - **Z-Wave JS**: the `battery_level` description (Battery command class,
    `level`) in `homeassistant/components/zwave_js/sensor.py`.
  - **Matter**: the `PowerSource` (`BatPercentRemaining`) description in
    `homeassistant/components/matter/sensor.py`, halved like ZHA's.
- The state-history fallback covers custom integrations that omit
  `state_class`. It only reaches back `purge_keep_days` (10 by default), short
  of the 14-day minimum, so those batteries are rarely forecast unless the user
  keeps longer history.

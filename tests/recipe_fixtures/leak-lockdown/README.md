# Leak Lockdown

A v2 recipe. Renders to a Home Assistant package that ships two
automations:

- **Engage** — any paired moisture sensor going from off → on closes
  every cover in the lockdown set, flashes the configured alarm lights
  red, and pushes a notification with the triggering sensor's name.
- **All clear** — when every leak sensor has been dry for N minutes
  (configurable, default 5), the alarm lights turn off and a "leak
  cleared" notification is sent. Covers stay closed deliberately so
  the homeowner can inspect before re-opening.

## What this recipe needs

| Role             | Required | What it expects                                              |
| ---------------- | -------- | ------------------------------------------------------------ |
| `leak_sensors`   | 1+       | `binary_sensor` entities with `device_class: moisture`       |
| `lockdown_covers`| 0+       | Any `cover.*` entity (windows, awnings, shutoff valves)      |
| `alarm_lights`   | 1–3      | `light.*` entities that support a colour mode (hs/rgb/…)     |

Inputs:

- `alarm_brightness` (1–100, default 100) — flash brightness percentage.
- `all_clear_delay_minutes` (1–60, default 5) — sensor-dry debounce.

## Install / uninstall

Install from the Recipes tab. The pipeline writes the rendered
package to `<config>/packages/selora_ai/leak-lockdown.yaml`. Uninstall
removes the package and reloads — no other state to clean up.

# Bedtime Routine

Time-triggered "wind down + secure" recipe. Demonstrates the v2 manifest
shape for a multi-domain recipe with all four input types.

## What this recipe does

At the configured bedtime, in order:

1. Fades the configured bedroom lights to off over N seconds.
2. Locks every door in the lock set (optional).
3. Sets the thermostat (optional, max one) to the night temperature.
4. Sends a goodnight notification — warm, brief, or playful (toggleable).

## What this recipe needs

| Role             | Required | What it expects                          |
| ---------------- | -------- | ---------------------------------------- |
| `bedroom_lights` | 1+       | Any `light.*` entity                     |
| `door_locks`     | 0+       | Any `lock.*` entity                      |
| `thermostat`     | 0–1      | One `climate.*` entity                   |

Inputs cover every form-field type the wizard renders:

- `bedtime` (string, default `"22:30"`) — time trigger.
- `dim_seconds` (number, 0–600, default 30) — fade duration.
- `night_temperature` (number, 10–26, default 18) — thermostat target.
- `announce_on_finish` (boolean, default true) — send notification.
- `greeting_style` (select: warm | brief | playful) — tone of the notification.

# Tornado Alert

Severe-weather recipe driven by the US National Weather Service feed.
Subscribes to a homeowner-chosen METAR station, fires the indoor siren
in occupied rooms when a tornado warning is issued, calls out any open
exterior doors, and announces the shelter zone on every configured
speaker.

## What's special about this recipe

This is the canonical example of a recipe that **installs and configures
an integration**. The manifest's `integrations:` list includes
`nws`, which makes Match-step row 1 render an inline "Set up National
Weather Service" button. Clicking it opens HA's actual `nws` config flow
inside the wizard — the homeowner enters their API key + station code
without leaving the page. When the flow lands, the row flips to
"Configured" and the wizard auto-advances.

## Inputs

| Input            | Type   | Notes                                                   |
| ---------------- | ------ | ------------------------------------------------------- |
| `station_code`   | string | **Auto-resolved** via `nws_station_from_location` —     |
|                  |        | computed from HA's lat/lon by calling api.weather.gov.  |
|                  |        | Hidden from the Settings step; homeowner never sees it. |
| `shelter_zone`   | string | Spoken in announcements. Defaults to "the basement".    |
| `warning_message`| select | Tone of the warning announcement: calm / urgent / terse |

## Roles

| Role                    | Kind          | Filter                  | Count |
| ----------------------- | ------------- | ----------------------- | ----- |
| `indoor_siren`          | switch        | —                       | 1     |
| `presence_sensors`      | binary_sensor | `device_class=occupancy`| 1+    |
| `door_sensors`          | binary_sensor | `device_class=door`     | 0+    |
| `announce_media_players`| media_player  | —                       | 1+    |

## US-only

NWS is the United States National Weather Service. If you're outside US
coverage the integration's config flow will fail — the recipe will halt
at the Match step with a "Set up failed" message.

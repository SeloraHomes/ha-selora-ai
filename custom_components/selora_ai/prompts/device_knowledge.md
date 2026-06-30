SMART DEVICE DOMAIN KNOWLEDGE:
Use this knowledge when building automations and answering questions about devices — to reason about device types, states, attributes, and the right entity to act on, regardless of manufacturer.

─── LIGHTING ───

Light (light.*)
- States: on, off. Key attributes: brightness (0-255), color_temp (mireds), color_mode, rgb_color, effect
- Brightness 255 = 100%, 128 ≈ 50%. Color temp: lower mireds = cooler/bluer, higher = warmer/yellower
- Color modes: color_temp, hs, rgb, rgbw, rgbww, xy, onoff, brightness
- Smart bulbs on Zigbee/Z-Wave act as mesh repeaters when powered — turning off at the wall breaks the mesh
- Configuration: group lights by room for unified control; set default brightness and color temp per scene; use transition times for smooth fading

─── CLIMATE & COMFORT ───

Climate (climate.*)
- States: off, heat, cool, heat_cool, auto, dry, fan_only
- HVAC action: heating, cooling, idle, off, drying, fan
- Key attributes: temperature (target), current_temperature, hvac_action, preset_mode (home, away, sleep, eco), fan_mode (auto, low, medium, high), humidity, target_humidity
- "idle" means the target temperature is reached and HVAC is not actively running — this is normal
- "heat_cool" means the system will heat or cool as needed to stay within a temperature range
- Configuration: set comfort range (typically 68-72°F / 20-22°C); pair with occupancy sensors for away mode; set schedule for sleep/wake; check temperature range limits per device

Water Heater (water_heater.*)
- States: off, eco, electric, performance, high_demand, heat_pump, gas
- Key attributes: temperature (target), current_temperature, operation_mode, away_mode
- Configuration: set target temperature 120°F / 49°C (safety and efficiency balance); enable eco/heat pump mode for energy savings; set away mode during vacations

Humidifier (humidifier.*)
- States: on, off. Key attributes: humidity (target), current_humidity, mode (normal, eco, sleep, auto)
- Ideal indoor humidity: 40-60% for comfort and health; below 30% causes dry skin and static; above 60% promotes mold
- Configuration: set target humidity per season (lower in winter to prevent condensation on windows); pair with humidity sensor for accurate readings

Fan (fan.*)
- States: on, off. Key attributes: percentage (speed 0-100), preset_mode (normal, sleep, nature, turbo), oscillating, direction (forward, reverse)
- Ceiling fan direction: forward (counterclockwise) for summer cooling downdraft, reverse (clockwise) for winter to push warm air down from ceiling
- Configuration: verify speed step count matches physical fan; map percentage to named speeds; set default preset per time of day

─── SECURITY & ACCESS ───

Lock (lock.*)
- States: locked, unlocked, locking, unlocking, jammed
- Key attributes: is_locked, changed_by (who/what triggered the lock action)
- "jammed" means the motor could not complete the lock/unlock cycle — check for physical obstruction at the bolt
- Configuration: enable auto-lock timer (typically 5 minutes); pair with door contact sensor for "left unlocked" alerts; set up user codes for different family members; enable lock logging for security audit

Alarm Control Panel (alarm_control_panel.*)
- States: armed_home, armed_away, armed_night, armed_vacation, armed_custom_bypass, disarmed, arming, pending, triggered
- Key attributes: code_arm_required, code_format, changed_by
- "arming" is the exit delay countdown; "pending" is the entry delay before triggering
- Configuration: set appropriate arm/disarm codes; configure entry/exit delays per zone; set up panic/duress codes; pair with notification for state changes

Camera (camera.*)
- States: idle, recording, streaming
- Key attributes: is_recording, motion_detected, model, brand, frontend_stream_type
- Configuration: set motion detection zones to reduce false alerts; adjust sensitivity per camera; set recording schedule (continuous vs motion-only); configure retention period for storage

Doorbell (event.* / binary_sensor.* / camera.*)
- A doorbell device combines several entities: a camera, motion/person detection, and — critically — the physical BUTTON PRESS. The button press is the entity you almost always want as the trigger for "someone is at the door" / "visitor" automations. It is NOT the motion or person sensor (those fire for anyone walking past).
- The button-press entity name is brand-specific and rarely says "button". Use the entity's `platform` (integration) to identify it:
  - `reolink` → the button press is `binary_sensor.<name>_visitor` (state off→on when pressed). This is the doorbell button, despite the "visitor" name. The separate person/motion sensors are NOT the button.
  - `amcrest` / `dahua` → button press is also a `binary_sensor` (often `*_doorbell` or `*_visitor`).
  - `unifiprotect` → button press is `binary_sensor.<name>_doorbell`.
  - `ring` → button press is `event.<name>_ding` (event domain, or `binary_sensor.<name>_ding` on older setups). Aqara, DoorBird, and Hikvision similarly expose the press as an `event.*` entity.
- IMPORTANT: `event.*` entities are NOT in the entity state list you are given. If the doorbell's button press is an event entity (ring/aqara/doorbird/etc.) and you cannot find a suitable `binary_sensor` press entity, call `get_device_triggers` with the doorbell's device_id to get the correct `platform: device` trigger block — do not guess an entity_id.
- For a `binary_sensor` press entity, trigger on the off→on transition (`to: "on"`).
- Configuration: set motion zones to exclude street/sidewalk; adjust ring notification settings; pair with lock for remote unlock after visual confirmation

─── COVERS & OPENINGS ───

Cover (cover.*)
- States: open, closed, opening, closing, stopped. Key attributes: current_position (0=closed, 100=open), current_tilt_position
- Device classes: awning, blind, curtain, damper, door, garage, gate, shade, shutter, window
- Configuration: calibrate open/close travel time on first setup; set tilt position if supported; create sun-tracking automations for energy savings

Garage Door (cover.* with device_class: garage)
- Operates as a cover entity with open/closed states
- Safety: always verify obstruction detection is working; never automate to close without safety confirmation or delay
- Configuration: pair with tilt sensor or contact sensor for accurate open/closed state; set up "left open" notifications with a time delay; integrate with vehicle presence for auto-open

─── MEDIA & ENTERTAINMENT ───

Media Player (media_player.*)
- States: on, off, playing, paused, idle, standby, buffering, unavailable
- Key attributes: media_title, media_artist, media_album_name, volume_level (0.0-1.0), is_volume_muted, source, source_list, media_content_type, media_duration, media_position
- "idle" means powered on but not playing — normal standby state
- Configuration: verify source list matches available inputs; set default volume per room; configure TTS (text-to-speech) engine; group speakers for multi-room audio

TV (media_player.* with device_class characteristics)
- Samsung, LG webOS, Sony Bravia, Roku, Apple TV, Android TV all expose as media_player entities
- Configuration: enable CEC for HDMI control; configure wake-on-LAN for network power on; map input sources to friendly names

Speaker (media_player.* — Sonos, Google Cast, etc.)
- Multi-room audio: group speakers for synchronized playback
- Configuration: set relative volume per speaker in a group; configure as announcement speaker for TTS; set do-not-disturb schedule

─── SENSORS & MONITORING ───

Sensor (sensor.*)
- States: numeric value or string. Key attributes: device_class, unit_of_measurement, state_class
- Device classes: temperature, humidity, battery, power (W), energy (kWh/Wh), voltage, current, illuminance (lx), pressure, co2, pm25, pm10, volatile_organic_compounds, gas, moisture, weight, distance, speed, wind_speed, precipitation, signal_strength
- state_class determines long-term statistics: measurement (instantaneous), total (cumulative), total_increasing (monotonically increasing like energy meters)
- Configuration: set appropriate state_class for statistics tracking; set unit of measurement if not auto-detected; group related sensors by device

Binary Sensor (binary_sensor.*)
- States: on, off. Meaning depends entirely on device_class:
  - motion: on = motion detected, off = clear
  - door: on = open, off = closed
  - window: on = open, off = closed
  - moisture/water leak: on = wet/leak detected, off = dry
  - smoke: on = smoke detected, off = clear
  - carbon_monoxide: on = CO detected, off = clear
  - occupancy: on = occupied, off = not occupied
  - vibration: on = vibration detected, off = still
  - tamper: on = tampered, off = OK
  - battery: on = battery low, off = battery OK
  - problem: on = problem detected, off = OK
  - connectivity: on = connected, off = disconnected
- Always check device_class before interpreting on/off — "on" for a door sensor means open, not "working"
- Configuration: adjust sensitivity and cooldown/off-delay where supported; set up alert automations for safety-critical sensors (smoke, CO, moisture)

─── CLEANING & OUTDOOR ───

Vacuum (vacuum.*)
- States: cleaning, docked, idle, paused, returning, error
- Key attributes: battery_level, fan_speed (off, silent, standard, medium, turbo, max), status, cleaned_area, cleaning_time
- "docked" means charging at base station — normal idle state
- "returning" means heading back to dock, either from low battery or completed cleaning
- Configuration: set no-go zones and virtual walls; schedule cleaning times per room; adjust suction level per floor type (low for hardwood, high for carpet); set up room-specific cleaning

Lawn Mower (lawn_mower.*)
- States: mowing, docked, paused, error, returning
- Key attributes: battery_level, activity
- Configuration: set mowing schedule avoiding rain; define boundary/guide wires or GPS zones; adjust cutting height per season

─── IRRIGATION & WATER ───

Valve (valve.*)
- States: open, closed, opening, closing
- Used for irrigation zones, water shutoff valves, gas valves
- Configuration: set zone run times; create watering schedules adjusted for weather; pair with rain sensor or weather integration to skip watering

─── ENERGY & POWER ───

Energy Monitor (sensor.* with device_class: energy/power)
- Power sensors (W) show instantaneous consumption; energy sensors (kWh) show cumulative usage
- Configuration: set up HA Energy Dashboard with grid consumption, solar production, and battery storage; calibrate CT clamps for accurate readings; set utility meter helpers for daily/monthly tracking
- Key integrations: Sense, Emporia Vue, IoTaWatt, Shelly EM, SolarEdge, Enphase

Solar / Battery (sensor.* — SolarEdge, Enphase, Tesla Powerwall)
- Solar production peaks midday; battery cycles between charging (solar excess) and discharging (evening/night)
- Configuration: set up grid import/export tracking; create automations to shift heavy loads to solar production hours; monitor battery state of charge

EV Charger (sensor.* / switch.* — Wallbox, Easee, Tesla Wall Connector)
- Typically exposes power sensor, energy sensor, and switch for start/stop charging
- Configuration: set charging schedule for off-peak rates; limit charge current based on home load; set target charge level; integrate with car entity for departure planning

─── VEHICLES ───

Car (device_tracker.* / sensor.* / lock.* / climate.*)
- Connected vehicles expose multiple entity types: location (device_tracker), battery/fuel level (sensor), lock state (lock), climate preconditioning (climate)
- Key integrations: Tesla, BMW, Volvo, Kia/Hyundai, Ford, Mercedes, Subaru, Polestar
- Configuration: set home zone for arrival/departure automations; create charging schedule; set up pre-conditioning based on departure time; track fuel/battery level for reminders

─── NETWORK & IOT ───

Switch (switch.*)
- States: on, off
- Covers smart plugs, smart outlets, relay switches, power strips
- Some report energy monitoring (power, voltage, current, energy attributes)
- Configuration: verify load rating matches connected device (don't exceed wattage); use energy monitoring switches for high-draw devices; label what's connected

Button (button.*)
- Triggers a momentary action — no persistent state
- Used for: restart device, identify (flash LED), refresh data, trigger scene
- Not stateful — pressing the button fires an event but doesn't change a state

Remote (remote.*)
- States: on, off. Used for IR/RF remote control devices (Broadlink, Harmony, SwitchBot Hub)
- Configuration: learn IR/RF codes from existing remotes; create activity-based sequences; map to HA scripts

─── PRESENCE & PEOPLE ───

Device Tracker (device_tracker.*)
- States: home, not_home, or zone name (e.g. "work", "school")
- Sources: GPS (phone app), router/network (ping, UniFi, SNMP), Bluetooth (ESPresense, iBeacon), Wi-Fi (DHCP)
- Configuration: pair with person entity for multi-source presence; set zone radius appropriately (GPS accuracy is 10-50m); use multiple sources for reliability

Person (person.*)
- States: home, not_home, or zone name
- Aggregates multiple device_tracker entities for one person — if any tracker says "home", person is "home"
- Configuration: add all tracking sources per person (phone GPS + router); set up zones for frequently visited places; use for presence-based automations (arrive home → lights on, leave → lock doors)

─── SMART HOME PROTOCOLS ───

Zigbee (via ZHA or Zigbee2MQTT)
- Mesh network: mains-powered devices (bulbs, plugs, repeaters) extend range; battery devices are end nodes
- Configuration: place coordinator centrally; ensure enough routers (1 per 6-8 end devices); use channel 15, 20, or 25 to avoid Wi-Fi 2.4GHz interference

Z-Wave (via Z-Wave JS)
- Mesh network: 232 device limit per network; mains-powered devices route, battery devices don't
- S2 security required for locks and garage doors
- Configuration: include devices close to controller then move to final location; run network heal after adding devices

Matter / Thread
- Local-first protocol: no cloud required; works across ecosystems (Apple Home, Google Home, Alexa, HA)
- Thread is the network layer (mesh, low-power, IPv6); Matter is the application layer
- Configuration: HA can act as Matter controller; Thread border routers (HomePod Mini, Nest Hub, etc.) extend the Thread mesh

Wi-Fi Devices (Shelly, Kasa, Tuya, ESPHome, Meross, WeMo)
- Direct IP connection — no hub needed but every device uses a Wi-Fi slot
- Configuration: set static IP or DHCP reservation for every device; use a dedicated IoT VLAN/SSID; limit to 2.4GHz network (most IoT devices don't support 5GHz)

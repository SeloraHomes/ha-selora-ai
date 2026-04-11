SMART DEVICE DOMAIN KNOWLEDGE:
When answering questions about devices, use this knowledge to reason about device types, states, configuration, and maintenance — regardless of manufacturer.

─── LIGHTING ───

Light (light.*)
- States: on, off. Key attributes: brightness (0-255), color_temp (mireds), color_mode, rgb_color, effect
- Brightness 255 = 100%, 128 ≈ 50%. Color temp: lower mireds = cooler/bluer, higher = warmer/yellower
- Color modes: color_temp, hs, rgb, rgbw, rgbww, xy, onoff, brightness
- Smart bulbs on Zigbee/Z-Wave act as mesh repeaters when powered — turning off at the wall breaks the mesh
- Configuration: group lights by room for unified control; set default brightness and color temp per scene; use transition times for smooth fading
- Maintenance: LED lifespan is 25,000-50,000 hours; firmware updates via manufacturer app or OTA; Zigbee bulbs may need re-pairing after extended power loss
- Common issues: unresponsive after power outage (re-pair or power cycle), color temp drift between brands, group sync delays with large groups, flickering at low brightness (check dimmer compatibility)

─── CLIMATE & COMFORT ───

Climate (climate.*)
- States: off, heat, cool, heat_cool, auto, dry, fan_only
- HVAC action: heating, cooling, idle, off, drying, fan
- Key attributes: temperature (target), current_temperature, hvac_action, preset_mode (home, away, sleep, eco), fan_mode (auto, low, medium, high), humidity, target_humidity
- "idle" means the target temperature is reached and HVAC is not actively running — this is normal
- "heat_cool" means the system will heat or cool as needed to stay within a temperature range
- Configuration: set comfort range (typically 68-72°F / 20-22°C); pair with occupancy sensors for away mode; set schedule for sleep/wake; check temperature range limits per device
- Maintenance: replace HVAC filters every 1-3 months; recalibrate temperature sensors annually; check battery on wireless thermostats (6-12 month battery life); clean vents and ducts yearly
- Common issues: thermostat shows wrong temperature (poor sensor placement, near heat sources), "unavailable" after Wi-Fi change (update network settings), short cycling (oversized HVAC unit), hysteresis too narrow (increase dead band)

Water Heater (water_heater.*)
- States: off, eco, electric, performance, high_demand, heat_pump, gas
- Key attributes: temperature (target), current_temperature, operation_mode, away_mode
- Configuration: set target temperature 120°F / 49°C (safety and efficiency balance); enable eco/heat pump mode for energy savings; set away mode during vacations
- Maintenance: flush tank annually to remove sediment; check anode rod every 2-3 years (prevents tank corrosion); inspect pressure relief valve annually
- Common issues: slow heating (check heating element), temperature fluctuation (thermostat calibration needed), high energy bills (check insulation and standby losses)

Humidifier (humidifier.*)
- States: on, off. Key attributes: humidity (target), current_humidity, mode (normal, eco, sleep, auto)
- Ideal indoor humidity: 40-60% for comfort and health; below 30% causes dry skin and static; above 60% promotes mold
- Configuration: set target humidity per season (lower in winter to prevent condensation on windows); pair with humidity sensor for accurate readings
- Maintenance: clean tank weekly to prevent mold and bacteria; replace wicking filter per manufacturer schedule (every 1-3 months); descale with vinegar monthly in hard water areas
- Common issues: humidity reading inaccurate (sensor too close to unit), mode not updating (check API polling interval), white dust (use distilled water with ultrasonic units)

Fan (fan.*)
- States: on, off. Key attributes: percentage (speed 0-100), preset_mode (normal, sleep, nature, turbo), oscillating, direction (forward, reverse)
- Ceiling fan direction: forward (counterclockwise) for summer cooling downdraft, reverse (clockwise) for winter to push warm air down from ceiling
- Configuration: verify speed step count matches physical fan; map percentage to named speeds; set default preset per time of day
- Maintenance: clean blades every 3-6 months (dust reduces efficiency); check motor bearings if noisy; tighten blade screws annually for ceiling fans
- Common issues: speed percentage doesn't match physical steps (firmware limitation), oscillation state not reported by some models, direction control not supported on all fans

─── SECURITY & ACCESS ───

Lock (lock.*)
- States: locked, unlocked, locking, unlocking, jammed
- Key attributes: is_locked, changed_by (who/what triggered the lock action)
- "jammed" means the motor could not complete the lock/unlock cycle — check for physical obstruction at the bolt
- Configuration: enable auto-lock timer (typically 5 minutes); pair with door contact sensor for "left unlocked" alerts; set up user codes for different family members; enable lock logging for security audit
- Maintenance: replace batteries every 6-12 months (most locks warn at 20%); lubricate the bolt mechanism annually with graphite; check door alignment seasonally (frame shifting causes jams)
- Common issues: jammed state (door alignment or deadbolt obstruction), Z-Wave locks require S2 security pairing, battery drain from frequent lock/unlock cycles, Bluetooth range issues

Alarm Control Panel (alarm_control_panel.*)
- States: armed_home, armed_away, armed_night, armed_vacation, armed_custom_bypass, disarmed, arming, pending, triggered
- Key attributes: code_arm_required, code_format, changed_by
- "arming" is the exit delay countdown; "pending" is the entry delay before triggering
- Configuration: set appropriate arm/disarm codes; configure entry/exit delays per zone; set up panic/duress codes; pair with notification for state changes
- Maintenance: test system monthly; replace sensor batteries annually; verify cellular/internet backup connection; update monitoring service contacts
- Common issues: false alarms (adjust sensor sensitivity, check pet immunity), entry delay too short, zone bypass not clearing after disarm

Camera (camera.*)
- States: idle, recording, streaming
- Key attributes: is_recording, motion_detected, model, brand, frontend_stream_type
- Configuration: set motion detection zones to reduce false alerts; adjust sensitivity per camera; set recording schedule (continuous vs motion-only); configure retention period for storage
- Maintenance: clean lens quarterly; check storage capacity; update firmware for security patches; verify night vision IR LEDs working
- Common issues: stream disconnects (check bandwidth — each HD camera needs 4-8 Mbps), motion detection false positives (trees, shadows, headlights), storage full (set retention policy), PoE cameras need adequate switch power budget

Doorbell (event.* / binary_sensor.* / camera.*)
- Often combines camera, motion sensor, and button press event
- Configuration: set motion zones to exclude street/sidewalk; adjust ring notification settings; pair with lock for remote unlock after visual confirmation
- Maintenance: charge battery (if wireless) every 2-6 months depending on traffic; clean camera lens; check Wi-Fi signal strength at door location
- Common issues: delayed notifications (network latency), missed ring events (Wi-Fi dead zone at door), false motion from passing cars

─── COVERS & OPENINGS ───

Cover (cover.*)
- States: open, closed, opening, closing, stopped. Key attributes: current_position (0=closed, 100=open), current_tilt_position
- Device classes: awning, blind, curtain, damper, door, garage, gate, shade, shutter, window
- Configuration: calibrate open/close travel time on first setup; set tilt position if supported; create sun-tracking automations for energy savings
- Maintenance: clean tracks for blinds/shades quarterly; lubricate garage door chain/belt annually; check motor and limit switches for garage doors; inspect weatherstripping
- Common issues: position drift over time (recalibrate endpoints), obstruction detection false triggers on garage doors, slow response on battery-powered blinds, RF interference with garage door remotes

Garage Door (cover.* with device_class: garage)
- Operates as a cover entity with open/closed states
- Safety: always verify obstruction detection is working; never automate to close without safety confirmation or delay
- Configuration: pair with tilt sensor or contact sensor for accurate open/closed state; set up "left open" notifications with a time delay; integrate with vehicle presence for auto-open
- Maintenance: lubricate rollers and hinges biannually; check spring tension annually (professional service); test auto-reverse safety monthly; replace remote batteries
- Common issues: state stuck as "opening" (check tilt sensor alignment), MyQ integration requires cloud (consider local alternatives like Ratgdo), opener doesn't respond (check RF frequency interference)

─── MEDIA & ENTERTAINMENT ───

Media Player (media_player.*)
- States: on, off, playing, paused, idle, standby, buffering, unavailable
- Key attributes: media_title, media_artist, media_album_name, volume_level (0.0-1.0), is_volume_muted, source, source_list, media_content_type, media_duration, media_position
- "idle" means powered on but not playing — normal standby state
- Configuration: verify source list matches available inputs; set default volume per room; configure TTS (text-to-speech) engine; group speakers for multi-room audio
- Maintenance: firmware updates via manufacturer app; check network bandwidth for 4K streaming (25+ Mbps); set static IP / DHCP reservation to prevent "unavailable"
- Common issues: "unavailable" after IP change, CEC conflicts between TV and receiver, volume sync issues between grouped speakers, TTS not working (check supported TTS platforms)

TV (media_player.* with device_class characteristics)
- Samsung, LG webOS, Sony Bravia, Roku, Apple TV, Android TV all expose as media_player entities
- Configuration: enable CEC for HDMI control; configure wake-on-LAN for network power on; map input sources to friendly names
- Common issues: TV shows "off" but is in standby (CEC or network standby must be enabled), slow to respond after wake, input switching delays

Speaker (media_player.* — Sonos, Google Cast, etc.)
- Multi-room audio: group speakers for synchronized playback
- Configuration: set relative volume per speaker in a group; configure as announcement speaker for TTS; set do-not-disturb schedule
- Common issues: speakers drop from group (Wi-Fi congestion), slight audio delay between rooms (adjust buffer), Bluetooth speakers disconnect when out of range

─── SENSORS & MONITORING ───

Sensor (sensor.*)
- States: numeric value or string. Key attributes: device_class, unit_of_measurement, state_class
- Device classes: temperature, humidity, battery, power (W), energy (kWh/Wh), voltage, current, illuminance (lx), pressure, co2, pm25, pm10, volatile_organic_compounds, gas, moisture, weight, distance, speed, wind_speed, precipitation, signal_strength
- state_class determines long-term statistics: measurement (instantaneous), total (cumulative), total_increasing (monotonically increasing like energy meters)
- Configuration: set appropriate state_class for statistics tracking; set unit of measurement if not auto-detected; group related sensors by device
- Maintenance: replace batteries on wireless sensors (typically 1-2 year life); recalibrate if readings drift; check reporting interval matches use case
- Common issues: stale values (check reporting interval and device health), unit mismatch between sensor and display, statistics not recording (state_class not set), battery percentage jumping (non-linear discharge curves)

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
- Maintenance: test smoke/CO detectors monthly; replace batteries annually; clean motion sensor lenses (dust reduces sensitivity); test water leak sensors with a damp cloth
- Common issues: false positives on motion sensors (pets, HVAC drafts, sunlight), delayed off state (cooldown timer), door sensor misaligned (gap too large between magnet halves)

─── CLEANING & OUTDOOR ───

Vacuum (vacuum.*)
- States: cleaning, docked, idle, paused, returning, error
- Key attributes: battery_level, fan_speed (off, silent, standard, medium, turbo, max), status, cleaned_area, cleaning_time
- "docked" means charging at base station — normal idle state
- "returning" means heading back to dock, either from low battery or completed cleaning
- Configuration: set no-go zones and virtual walls; schedule cleaning times per room; adjust suction level per floor type (low for hardwood, high for carpet); set up room-specific cleaning
- Maintenance: clean main brush and side brushes weekly (remove hair tangles); replace HEPA filter every 1-2 months; empty dustbin after each run; clean cliff sensors monthly; replace brushes every 6-12 months
- Common issues: "error" state (stuck on obstacles, cliff sensor blocked, full dustbin), map reset after firmware update, poor navigation in dark rooms (LiDAR works, camera-based may struggle), wheel hair tangles

Lawn Mower (lawn_mower.*)
- States: mowing, docked, paused, error, returning
- Key attributes: battery_level, activity
- Configuration: set mowing schedule avoiding rain; define boundary/guide wires or GPS zones; adjust cutting height per season
- Maintenance: clean blades monthly; replace blades every season; check wheel traction; clean charging contacts
- Common issues: stuck on slopes or obstacles, boundary wire breaks (locate with AM radio), rain sensor false triggers, GPS drift near buildings

─── IRRIGATION & WATER ───

Valve (valve.*)
- States: open, closed, opening, closing
- Used for irrigation zones, water shutoff valves, gas valves
- Configuration: set zone run times; create watering schedules adjusted for weather; pair with rain sensor or weather integration to skip watering
- Maintenance: test shutoff valves quarterly (they can seize if unused); check for leaks at connections; winterize irrigation systems before freeze
- Common issues: valve stuck (mineral buildup), slow response on battery-operated valves, schedule conflicts between zones (check max concurrent zones)

─── ENERGY & POWER ───

Energy Monitor (sensor.* with device_class: energy/power)
- Power sensors (W) show instantaneous consumption; energy sensors (kWh) show cumulative usage
- Configuration: set up HA Energy Dashboard with grid consumption, solar production, and battery storage; calibrate CT clamps for accurate readings; set utility meter helpers for daily/monthly tracking
- Key integrations: Sense, Emporia Vue, IoTaWatt, Shelly EM, SolarEdge, Enphase
- Common issues: energy values reset (use total_increasing state_class), CT clamp direction matters (negative values = reversed), solar production not showing (check inverter integration)

Solar / Battery (sensor.* — SolarEdge, Enphase, Tesla Powerwall)
- Solar production peaks midday; battery cycles between charging (solar excess) and discharging (evening/night)
- Configuration: set up grid import/export tracking; create automations to shift heavy loads to solar production hours; monitor battery state of charge
- Maintenance: panels need cleaning 1-2x per year (more in dusty areas); monitor inverter efficiency over time; check battery health metrics annually
- Common issues: production drop (panel shading, dirt, inverter clipping), battery not charging fully (check charge limits), API rate limits on cloud integrations

EV Charger (sensor.* / switch.* — Wallbox, Easee, Tesla Wall Connector)
- Typically exposes power sensor, energy sensor, and switch for start/stop charging
- Configuration: set charging schedule for off-peak rates; limit charge current based on home load; set target charge level; integrate with car entity for departure planning
- Maintenance: inspect cable and connector for wear; keep connector clean and dry; check breaker sizing annually
- Common issues: charging interrupted (home load management kicking in), slow charge (check circuit amperage), connectivity drops (Wi-Fi distance to garage)

─── VEHICLES ───

Car (device_tracker.* / sensor.* / lock.* / climate.*)
- Connected vehicles expose multiple entity types: location (device_tracker), battery/fuel level (sensor), lock state (lock), climate preconditioning (climate)
- Key integrations: Tesla, BMW, Volvo, Kia/Hyundai, Ford, Mercedes, Subaru, Polestar
- Configuration: set home zone for arrival/departure automations; create charging schedule; set up pre-conditioning based on departure time; track fuel/battery level for reminders
- Maintenance: firmware updates via manufacturer app; check API token refresh (some expire quarterly); monitor API rate limits
- Common issues: delayed location updates (30s-5min depending on integration), API auth expiry, climate preconditioning not starting (check if car is plugged in for EVs), battery level sensor stale when car is asleep

─── NETWORK & IOT ───

Switch (switch.*)
- States: on, off
- Covers smart plugs, smart outlets, relay switches, power strips
- Some report energy monitoring (power, voltage, current, energy attributes)
- Configuration: verify load rating matches connected device (don't exceed wattage); use energy monitoring switches for high-draw devices; label what's connected
- Maintenance: firmware updates; check for overheating on high-load devices; physical relay wear (~100k cycles on mechanical relays)
- Common issues: ghost switching (Zigbee/Z-Wave interference), state not updating (polling interval too long), switch resets after power outage (check power-on behavior setting)

Button (button.*)
- Triggers a momentary action — no persistent state
- Used for: restart device, identify (flash LED), refresh data, trigger scene
- Not stateful — pressing the button fires an event but doesn't change a state

Remote (remote.*)
- States: on, off. Used for IR/RF remote control devices (Broadlink, Harmony, SwitchBot Hub)
- Configuration: learn IR/RF codes from existing remotes; create activity-based sequences; map to HA scripts
- Common issues: IR codes not learning (distance/angle), RF codes frequency mismatch, learned codes don't work on different device model

─── PRESENCE & PEOPLE ───

Device Tracker (device_tracker.*)
- States: home, not_home, or zone name (e.g. "work", "school")
- Sources: GPS (phone app), router/network (ping, UniFi, SNMP), Bluetooth (ESPresense, iBeacon), Wi-Fi (DHCP)
- Configuration: pair with person entity for multi-source presence; set zone radius appropriately (GPS accuracy is 10-50m); use multiple sources for reliability
- Maintenance: check phone app battery optimization settings (may kill background location); update router integration if firmware changes; calibrate BLE beacon positions
- Common issues: GPS accuracy indoors (use Wi-Fi/BLE as supplement), delayed state changes (adjust update interval), phone enters deep sleep and stops reporting

Person (person.*)
- States: home, not_home, or zone name
- Aggregates multiple device_tracker entities for one person — if any tracker says "home", person is "home"
- Configuration: add all tracking sources per person (phone GPS + router); set up zones for frequently visited places; use for presence-based automations (arrive home → lights on, leave → lock doors)

─── SMART HOME PROTOCOLS ───

Zigbee (via ZHA or Zigbee2MQTT)
- Mesh network: mains-powered devices (bulbs, plugs, repeaters) extend range; battery devices are end nodes
- Configuration: place coordinator centrally; ensure enough routers (1 per 6-8 end devices); use channel 15, 20, or 25 to avoid Wi-Fi 2.4GHz interference
- Common issues: devices dropping off (weak mesh, too few routers), pairing failures (bring device close to coordinator), interference from USB 3.0 ports (use extension cable for USB coordinators)

Z-Wave (via Z-Wave JS)
- Mesh network: 232 device limit per network; mains-powered devices route, battery devices don't
- S2 security required for locks and garage doors
- Configuration: include devices close to controller then move to final location; run network heal after adding devices
- Common issues: ghost nodes (failed inclusion — remove via Z-Wave JS UI), slow response (too many hops — add repeaters), lock pairing requires S2 security (don't skip)

Matter / Thread
- Local-first protocol: no cloud required; works across ecosystems (Apple Home, Google Home, Alexa, HA)
- Thread is the network layer (mesh, low-power, IPv6); Matter is the application layer
- Configuration: HA can act as Matter controller; Thread border routers (HomePod Mini, Nest Hub, etc.) extend the Thread mesh
- Common issues: multi-admin pairing limits, Thread network instability with too few border routers, Matter device firmware updates required for compatibility

Wi-Fi Devices (Shelly, Kasa, Tuya, ESPHome, Meross, WeMo)
- Direct IP connection — no hub needed but every device uses a Wi-Fi slot
- Configuration: set static IP or DHCP reservation for every device; use a dedicated IoT VLAN/SSID; limit to 2.4GHz network (most IoT devices don't support 5GHz)
- Common issues: devices go unavailable (DHCP lease expired, router rebooted), too many devices saturate Wi-Fi (>30 devices per AP), cloud dependency (flash with ESPHome/Tasmota for local control where possible)

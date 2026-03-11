"""Constants for the Selora AI integration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

DOMAIN = "selora_ai"

# ── Dispatcher Signals ───────────────────────────────────────────────
SIGNAL_DEVICES_UPDATED = f"{DOMAIN}_devices_updated"
SIGNAL_ACTIVITY_LOG = f"{DOMAIN}_activity_log"


# ── Integration Discovery Database ──────────────────────────────────


class DiscoveryMethod(Enum):
    """How HA discovers this integration."""
    AUTO = "auto"          # SSDP / mDNS / USB — zero config
    CLOUD = "cloud"        # Requires account credentials
    MANUAL = "manual"      # User must provide host/IP
    PROTOCOL = "protocol"  # Protocol bridge (Zigbee, Z-Wave, Matter)


class IntegrationSource(Enum):
    """Where the integration comes from."""
    CORE = "core"  # Built into HA
    HACS = "hacs"  # Community store


class DeviceCategory(Enum):
    """Broad device categories for grouping."""
    LIGHTING = "lighting"
    TV = "tv"
    SPEAKER = "speaker"
    MEDIA = "media"
    APPLIANCE = "appliance"
    THERMOSTAT = "thermostat"
    CAMERA = "camera"
    SECURITY = "security"
    LOCK = "lock"
    VACUUM = "vacuum"
    CAR = "car"
    ENERGY = "energy"
    IOT = "iot"
    PROTOCOL = "protocol"
    IRRIGATION = "irrigation"
    MOWER = "mower"
    GAMING = "gaming"


@dataclass(frozen=True)
class IntegrationInfo:
    """Metadata about a known smart-home integration."""
    domain: str
    name: str
    category: DeviceCategory
    discovery: DiscoveryMethod
    source: IntegrationSource = IntegrationSource.CORE
    brands: tuple[str, ...] = ()
    notes: str = ""


# ~85 known integrations Selora AI can discover / recommend
KNOWN_INTEGRATIONS: dict[str, IntegrationInfo] = {i.domain: i for i in [
    # ── Lighting ──────────────────────────────────────────────────
    IntegrationInfo("hue", "Philips Hue", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("Philips",)),
    IntegrationInfo("lifx", "LIFX", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("LIFX",)),
    IntegrationInfo("nanoleaf", "Nanoleaf", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("Nanoleaf",)),
    IntegrationInfo("wiz", "WiZ", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("WiZ", "Philips")),
    IntegrationInfo("tplink", "TP-Link Kasa/Tapo", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("TP-Link",)),
    IntegrationInfo("lutron_caseta", "Lutron Caseta", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("Lutron",)),
    IntegrationInfo("yeelight", "Yeelight", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("Yeelight", "Xiaomi")),
    IntegrationInfo("elgato", "Elgato Key Light", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("Elgato",)),
    IntegrationInfo("twinkly", "Twinkly", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("Twinkly",)),
    IntegrationInfo("tradfri", "IKEA TRADFRI", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("IKEA",)),
    IntegrationInfo("cync", "GE Cync", DeviceCategory.LIGHTING, DiscoveryMethod.CLOUD, brands=("GE", "Cync")),

    # ── TVs ───────────────────────────────────────────────────────
    IntegrationInfo("samsungtv", "Samsung TV", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Samsung",)),
    IntegrationInfo("webostv", "LG webOS TV", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("LG",)),
    IntegrationInfo("braviatv", "Sony Bravia TV", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Sony",)),
    IntegrationInfo("roku", "Roku", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Roku",)),
    IntegrationInfo("apple_tv", "Apple TV", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Apple",)),
    IntegrationInfo("vizio", "Vizio SmartCast", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Vizio",)),
    IntegrationInfo("androidtv_remote", "Android TV Remote", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Google", "Sony", "Nvidia", "Xiaomi")),

    # ── Speakers / Media ──────────────────────────────────────────
    IntegrationInfo("cast", "Google Cast / Chromecast", DeviceCategory.SPEAKER, DiscoveryMethod.AUTO, brands=("Google",)),
    IntegrationInfo("sonos", "Sonos", DeviceCategory.SPEAKER, DiscoveryMethod.AUTO, brands=("Sonos",)),
    IntegrationInfo("bang_olufsen", "Bang & Olufsen", DeviceCategory.SPEAKER, DiscoveryMethod.AUTO, brands=("Bang & Olufsen",)),
    IntegrationInfo("denonavr", "Denon AVR", DeviceCategory.SPEAKER, DiscoveryMethod.AUTO, brands=("Denon", "Marantz")),
    IntegrationInfo("heos", "Denon HEOS", DeviceCategory.SPEAKER, DiscoveryMethod.AUTO, brands=("Denon",)),
    IntegrationInfo("yamaha_musiccast", "Yamaha MusicCast", DeviceCategory.SPEAKER, DiscoveryMethod.AUTO, brands=("Yamaha",)),
    IntegrationInfo("plex", "Plex Media Server", DeviceCategory.MEDIA, DiscoveryMethod.AUTO, brands=("Plex",)),
    IntegrationInfo("kodi", "Kodi", DeviceCategory.MEDIA, DiscoveryMethod.AUTO, brands=("Kodi",)),
    IntegrationInfo("harmony", "Logitech Harmony", DeviceCategory.MEDIA, DiscoveryMethod.AUTO, brands=("Logitech",)),
    IntegrationInfo("alexa_devices", "Alexa Media Player", DeviceCategory.SPEAKER, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("Amazon",)),
    IntegrationInfo("dlna_dmr", "DLNA Media Renderer", DeviceCategory.MEDIA, DiscoveryMethod.AUTO),
    IntegrationInfo("spotify", "Spotify", DeviceCategory.MEDIA, DiscoveryMethod.CLOUD, brands=("Spotify",)),
    IntegrationInfo("music_assistant", "Music Assistant", DeviceCategory.MEDIA, DiscoveryMethod.AUTO, source=IntegrationSource.HACS),

    # ── Appliances ────────────────────────────────────────────────
    IntegrationInfo("smartthings", "Samsung SmartThings", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, brands=("Samsung",)),
    IntegrationInfo("home_connect", "Home Connect (Bosch/Siemens)", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, brands=("Bosch", "Siemens")),
    IntegrationInfo("miele", "Miele", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, brands=("Miele",)),
    IntegrationInfo("whirlpool", "Whirlpool", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, brands=("Whirlpool", "Maytag", "KitchenAid")),
    IntegrationInfo("lg_thinq", "LG ThinQ", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("LG",)),
    IntegrationInfo("ge_home", "GE Home (SmartHQ)", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("GE",)),
    IntegrationInfo("dyson_local", "Dyson", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("Dyson",)),
    IntegrationInfo("anova", "Anova Sous Vide", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, brands=("Anova",)),
    IntegrationInfo("meater", "MEATER Thermometer", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, brands=("MEATER",)),
    IntegrationInfo("switchbot", "SwitchBot", DeviceCategory.APPLIANCE, DiscoveryMethod.AUTO, brands=("SwitchBot",)),

    # ── Thermostats / Climate ─────────────────────────────────────
    IntegrationInfo("ecobee", "ecobee", DeviceCategory.THERMOSTAT, DiscoveryMethod.CLOUD, brands=("ecobee",)),
    IntegrationInfo("nest", "Google Nest", DeviceCategory.THERMOSTAT, DiscoveryMethod.CLOUD, brands=("Google", "Nest")),

    # ── Cameras / Security ────────────────────────────────────────
    IntegrationInfo("ring", "Ring", DeviceCategory.SECURITY, DiscoveryMethod.CLOUD, brands=("Ring", "Amazon")),
    IntegrationInfo("unifiprotect", "UniFi Protect", DeviceCategory.CAMERA, DiscoveryMethod.AUTO, brands=("Ubiquiti",)),
    IntegrationInfo("reolink", "Reolink", DeviceCategory.CAMERA, DiscoveryMethod.AUTO, brands=("Reolink",)),
    IntegrationInfo("blink", "Blink", DeviceCategory.SECURITY, DiscoveryMethod.CLOUD, brands=("Blink", "Amazon")),
    IntegrationInfo("simplisafe", "SimpliSafe", DeviceCategory.SECURITY, DiscoveryMethod.CLOUD, brands=("SimpliSafe",)),
    IntegrationInfo("frigate", "Frigate NVR", DeviceCategory.CAMERA, DiscoveryMethod.MANUAL, source=IntegrationSource.HACS, brands=("Frigate",)),
    IntegrationInfo("eufy_security", "Eufy Security", DeviceCategory.SECURITY, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("Eufy", "Anker")),

    # ── Locks ─────────────────────────────────────────────────────
    IntegrationInfo("august", "August / Yale", DeviceCategory.LOCK, DiscoveryMethod.CLOUD, brands=("August", "Yale")),

    # ── Vacuums ───────────────────────────────────────────────────
    IntegrationInfo("roomba", "iRobot Roomba", DeviceCategory.VACUUM, DiscoveryMethod.AUTO, brands=("iRobot",)),
    IntegrationInfo("roborock", "Roborock", DeviceCategory.VACUUM, DiscoveryMethod.CLOUD, brands=("Roborock",)),
    IntegrationInfo("sharkiq", "Shark IQ", DeviceCategory.VACUUM, DiscoveryMethod.CLOUD, brands=("Shark",)),
    IntegrationInfo("ecovacs", "Ecovacs Deebot", DeviceCategory.VACUUM, DiscoveryMethod.CLOUD, brands=("Ecovacs",)),

    # ── Cars ──────────────────────────────────────────────────────
    IntegrationInfo("tesla_fleet", "Tesla", DeviceCategory.CAR, DiscoveryMethod.CLOUD, brands=("Tesla",)),
    IntegrationInfo("bmw_connected_drive", "BMW Connected Drive", DeviceCategory.CAR, DiscoveryMethod.CLOUD, brands=("BMW",)),
    IntegrationInfo("volvo", "Volvo On Call", DeviceCategory.CAR, DiscoveryMethod.CLOUD, brands=("Volvo",)),
    IntegrationInfo("subaru", "Subaru STARLINK", DeviceCategory.CAR, DiscoveryMethod.CLOUD, brands=("Subaru",)),
    IntegrationInfo("mbapi2020", "Mercedes-Benz", DeviceCategory.CAR, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("Mercedes-Benz",)),
    IntegrationInfo("fordpass", "Ford", DeviceCategory.CAR, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("Ford",)),
    IntegrationInfo("kia_uvo", "Kia / Hyundai", DeviceCategory.CAR, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("Kia", "Hyundai")),
    IntegrationInfo("polestar_api", "Polestar", DeviceCategory.CAR, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("Polestar",)),

    # ── Energy ────────────────────────────────────────────────────
    IntegrationInfo("powerwall", "Tesla Powerwall", DeviceCategory.ENERGY, DiscoveryMethod.AUTO, brands=("Tesla",)),
    IntegrationInfo("enphase_envoy", "Enphase Envoy", DeviceCategory.ENERGY, DiscoveryMethod.AUTO, brands=("Enphase",)),
    IntegrationInfo("tesla_wall_connector", "Tesla Wall Connector", DeviceCategory.ENERGY, DiscoveryMethod.AUTO, brands=("Tesla",)),
    IntegrationInfo("sense", "Sense Energy Monitor", DeviceCategory.ENERGY, DiscoveryMethod.CLOUD, brands=("Sense",)),
    IntegrationInfo("solaredge", "SolarEdge", DeviceCategory.ENERGY, DiscoveryMethod.CLOUD, brands=("SolarEdge",)),
    IntegrationInfo("wallbox", "Wallbox EV Charger", DeviceCategory.ENERGY, DiscoveryMethod.CLOUD, brands=("Wallbox",)),
    IntegrationInfo("easee", "Easee EV Charger", DeviceCategory.ENERGY, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("Easee",)),
    IntegrationInfo("emporia_vue", "Emporia Vue", DeviceCategory.ENERGY, DiscoveryMethod.CLOUD, brands=("Emporia",)),
    IntegrationInfo("iotawatt", "IoTaWatt", DeviceCategory.ENERGY, DiscoveryMethod.AUTO, brands=("IoTaWatt",)),
    IntegrationInfo("solar_forecast", "Forecast.Solar", DeviceCategory.ENERGY, DiscoveryMethod.CLOUD),
    IntegrationInfo("opower", "Opower (Utility)", DeviceCategory.ENERGY, DiscoveryMethod.CLOUD),

    # ── IoT Platforms ─────────────────────────────────────────────
    IntegrationInfo("shelly", "Shelly", DeviceCategory.IOT, DiscoveryMethod.AUTO, brands=("Shelly",)),
    IntegrationInfo("esphome", "ESPHome", DeviceCategory.IOT, DiscoveryMethod.AUTO, brands=("ESPHome",)),

    # ── Protocols ─────────────────────────────────────────────────
    IntegrationInfo("zha", "Zigbee Home Automation", DeviceCategory.PROTOCOL, DiscoveryMethod.PROTOCOL, notes="Requires Zigbee coordinator USB stick"),
    IntegrationInfo("zwave_js", "Z-Wave JS", DeviceCategory.PROTOCOL, DiscoveryMethod.PROTOCOL, notes="Requires Z-Wave USB stick + Z-Wave JS server"),
    IntegrationInfo("matter", "Matter", DeviceCategory.PROTOCOL, DiscoveryMethod.PROTOCOL, notes="Requires Matter server (built into HAOS)"),
    IntegrationInfo("homekit_controller", "HomeKit Controller", DeviceCategory.PROTOCOL, DiscoveryMethod.AUTO, notes="Imports HomeKit accessories into HA"),

    # ── Irrigation ────────────────────────────────────────────────
    IntegrationInfo("rainmachine", "RainMachine", DeviceCategory.IRRIGATION, DiscoveryMethod.AUTO, brands=("RainMachine",)),
    IntegrationInfo("rachio", "Rachio", DeviceCategory.IRRIGATION, DiscoveryMethod.CLOUD, brands=("Rachio",)),

    # ── Mowers ────────────────────────────────────────────────────
    IntegrationInfo("husqvarna_automower", "Husqvarna Automower", DeviceCategory.MOWER, DiscoveryMethod.CLOUD, brands=("Husqvarna",)),

    # ── Gaming ────────────────────────────────────────────────────
    IntegrationInfo("xbox", "Xbox", DeviceCategory.GAMING, DiscoveryMethod.CLOUD, brands=("Microsoft",)),
    IntegrationInfo("playstation_network", "PlayStation Network", DeviceCategory.GAMING, DiscoveryMethod.CLOUD, brands=("Sony",)),

    # ── Printers / Scanners ────────────────────────────────────
    IntegrationInfo("ipp", "IPP Printer", DeviceCategory.IOT, DiscoveryMethod.AUTO, notes="Internet Printing Protocol — auto-discovered via mDNS"),
    IntegrationInfo("brother", "Brother Printer", DeviceCategory.IOT, DiscoveryMethod.AUTO, brands=("Brother",)),
    IntegrationInfo("hp_ips", "HP Instant Ink", DeviceCategory.IOT, DiscoveryMethod.AUTO, brands=("HP",)),

    # ── Network / System ───────────────────────────────────────
    IntegrationInfo("bluetooth", "Bluetooth", DeviceCategory.PROTOCOL, DiscoveryMethod.AUTO, notes="Core Bluetooth adapter"),
    IntegrationInfo("upnp", "UPnP/IGD", DeviceCategory.IOT, DiscoveryMethod.AUTO, notes="Router port mapping"),
    IntegrationInfo("dhcp", "DHCP Discovery", DeviceCategory.PROTOCOL, DiscoveryMethod.AUTO, notes="Passive network device discovery"),
    IntegrationInfo("usb", "USB Discovery", DeviceCategory.PROTOCOL, DiscoveryMethod.AUTO, notes="USB device auto-detection"),
    IntegrationInfo("ssdp", "SSDP Discovery", DeviceCategory.PROTOCOL, DiscoveryMethod.AUTO, notes="Simple Service Discovery Protocol"),
    IntegrationInfo("zeroconf", "Zeroconf/mDNS", DeviceCategory.PROTOCOL, DiscoveryMethod.AUTO, notes="mDNS service discovery"),
    IntegrationInfo("network", "Network", DeviceCategory.IOT, DiscoveryMethod.AUTO, notes="Network adapter configuration"),

    # ── Plugs / Outlets ────────────────────────────────────────
    IntegrationInfo("tuya", "Tuya / Smart Life", DeviceCategory.IOT, DiscoveryMethod.CLOUD, brands=("Tuya",), notes="Covers many white-label smart plugs/switches"),
    IntegrationInfo("meross", "Meross", DeviceCategory.IOT, DiscoveryMethod.CLOUD, source=IntegrationSource.HACS, brands=("Meross",)),
    IntegrationInfo("wemo", "Belkin WeMo", DeviceCategory.IOT, DiscoveryMethod.AUTO, brands=("Belkin",)),
    IntegrationInfo("myq", "MyQ Garage", DeviceCategory.IOT, DiscoveryMethod.CLOUD, brands=("Chamberlain", "LiftMaster")),
]}

# ── Config Entry Types ──────────────────────────────────────────────
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_LLM = "llm_config"
ENTRY_TYPE_DEVICE = "device_onboarding"

# ── Background Services ─────────────────────────────────────────────
CONF_COLLECTOR_ENABLED = "collector_enabled"
CONF_COLLECTOR_MODE = "collector_mode"
CONF_COLLECTOR_START_TIME = "collector_start_time"
CONF_COLLECTOR_END_TIME = "collector_end_time"
CONF_COLLECTOR_INTERVAL = "collector_interval"

CONF_DISCOVERY_ENABLED = "discovery_enabled"
CONF_DISCOVERY_MODE = "discovery_mode"
CONF_DISCOVERY_START_TIME = "discovery_start_time"
CONF_DISCOVERY_END_TIME = "discovery_end_time"
CONF_DISCOVERY_INTERVAL = "discovery_interval"

MODE_CONTINUOUS = "continuous"
MODE_SCHEDULED = "scheduled"

DEFAULT_COLLECTOR_ENABLED = True
DEFAULT_COLLECTOR_MODE = MODE_CONTINUOUS
DEFAULT_COLLECTOR_INTERVAL = 3600  # 1 hour in seconds
DEFAULT_COLLECTOR_START_TIME = "09:00"
DEFAULT_COLLECTOR_END_TIME = "17:00"

DEFAULT_DISCOVERY_ENABLED = True
DEFAULT_DISCOVERY_MODE = MODE_CONTINUOUS
DEFAULT_DISCOVERY_INTERVAL = 14400  # 4 hours in seconds
DEFAULT_DISCOVERY_START_TIME = "00:00"
DEFAULT_DISCOVERY_END_TIME = "23:59"

# ── Device Selection ───────────────────────────────────────────────
CONF_SELECTED_DEVICES = "selected_devices"

# ── LLM Provider ────────────────────────────────────────────────────
CONF_LLM_PROVIDER = "llm_provider"
LLM_PROVIDER_ANTHROPIC = "anthropic"
LLM_PROVIDER_OLLAMA = "ollama"
LLM_PROVIDER_NONE = "none"
DEFAULT_LLM_PROVIDER = LLM_PROVIDER_ANTHROPIC

# ── Anthropic (Claude API) ──────────────────────────────────────────
CONF_ANTHROPIC_API_KEY = "anthropic_api_key"
CONF_ANTHROPIC_MODEL = "anthropic_model"

DEFAULT_ANTHROPIC_HOST = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_API_KEY = ""  # User must provide their own key during setup
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-6"
ANTHROPIC_API_VERSION = "2023-06-01"

# ── Ollama (Local LLM) ──────────────────────────────────────────────
# Uses Anthropic-compatible API: same /v1/messages endpoint format
#   https://docs.ollama.com/api/anthropic-compatibility
CONF_OLLAMA_HOST = "ollama_host"
CONF_OLLAMA_MODEL = "ollama_model"

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"

# Endpoint paths
ANTHROPIC_MESSAGES_ENDPOINT = "/v1/messages"
OLLAMA_CHAT_ENDPOINT = "/v1/chat/completions"
MESSAGES_ENDPOINT = ANTHROPIC_MESSAGES_ENDPOINT  # Legacy compatibility

# ── MQTT (future) ────────────────────────────────────────────────────
# Reaction-based behavior capture (Matthew, Mar 4).
# MQTT listening for point-in-time event pre-classification.
# TODO: implement mqtt_listener.py
CONF_MQTT_ENABLED = "mqtt_enabled"
DEFAULT_MQTT_ENABLED = False  # Off until implemented

# ── LLM Timeout ─────────────────────────────────────────────────────
DEFAULT_LLM_TIMEOUT = 120  # seconds — per-request timeout for LLM calls

# ── Data Collection ──────────────────────────────────────────────────
DEFAULT_PUSH_INTERVAL = 3600  # 1 hour — how often we collect + analyze
CONF_PUSH_INTERVAL = "push_interval"

# ── LLM Suggestion Limits ───────────────────────────────────────────
DEFAULT_MAX_SUGGESTIONS = 3  # Max automation suggestions per analysis cycle

# ── Automation Creation ──────────────────────────────────────────────
# Prefix for auto-created automation IDs (easy to find/filter).
AUTOMATION_ID_PREFIX = "selora_ai_"

# ── Device Discovery Webhook ─────────────────────────────────────────
WEBHOOK_DEVICES_ID = "selora_ai_devices"

# ── Protected Domains (never removed by reset) ──────────────────────
PROTECTED_DOMAINS = {
    "homeassistant", "automation", "frontend", "backup", "sun",
    "shopping_list", "google_translate", "radio_browser",
    "persistent_notification", "recorder", "logger", "system_log",
    "default_config", "config", "person", "zone", "script", "scene",
    "group", "template", "webhook", "conversation", "assist_pipeline",
    "cloud", "mobile_app", "tag", "blueprint", "ffmpeg", "met",
    "bluetooth", "dhcp", "ssdp", "zeroconf", "usb", "network",  # core system discovery
    DOMAIN,  # never remove ourselves
}

# ── Side Panel ──────────────────────────────────────────────────────
PANEL_NAME = "selora-ai-architect"
PANEL_TITLE = "Selora AI"
PANEL_ICON = "mdi:robot-confetti"
PANEL_PATH = "selora-ai-architect"

# ── HA Recorder ──────────────────────────────────────────────────────
# Historical data source (SQLite) for pattern detection.
# https://www.home-assistant.io/integrations/recorder/
DEFAULT_RECORDER_LOOKBACK_DAYS = 7
CONF_RECORDER_LOOKBACK_DAYS = "recorder_lookback_days"

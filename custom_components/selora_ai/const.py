"""Constants for the Selora AI integration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

DOMAIN = "selora_ai"

# ── Dispatcher Signals ───────────────────────────────────────────────
SIGNAL_DEVICES_UPDATED = f"{DOMAIN}_devices_updated"
SIGNAL_ACTIVITY_LOG = f"{DOMAIN}_activity_log"
SIGNAL_LLM_USAGE = f"{DOMAIN}_llm_usage"

# HA event fired for every LLM call, for audit / Logbook visibility.
EVENT_LLM_USAGE = f"{DOMAIN}_llm_usage"

# HA event fired when an LLM backend returns 429 (rate limit / quota).
# Payload: {"provider": str, "retry_after": int|None, "message": str}.
# The panel listens for this and surfaces a red-particle alert.
EVENT_LLM_QUOTA_EXCEEDED = f"{DOMAIN}_quota_exceeded"
# Default cool-down (seconds) used when the upstream response carries no
# Retry-After header. Long enough that users notice the alert; short
# enough that the next legitimate request clears it.
DEFAULT_QUOTA_BACKOFF_SECONDS = 60

# ── LLM Usage Sensors ────────────────────────────────────────────────
# Persistent counters survive restarts via RestoreSensor; the store key
# below is for an explicit fallback save (in case restore returns None
# during an unclean shutdown).
LLM_USAGE_STORE_KEY = f"{DOMAIN}.llm_usage_counters"
LLM_USAGE_STORE_VERSION = 1


# ── Integration Discovery Database ──────────────────────────────────


class DiscoveryMethod(Enum):
    """How HA discovers this integration."""

    AUTO = "auto"  # SSDP / mDNS / USB — zero config
    CLOUD = "cloud"  # Requires account credentials
    MANUAL = "manual"  # User must provide host/IP
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
KNOWN_INTEGRATIONS: dict[str, IntegrationInfo] = {
    i.domain: i
    for i in [
        # ── Lighting ──────────────────────────────────────────────────
        IntegrationInfo(
            "hue", "Philips Hue", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("Philips",)
        ),
        IntegrationInfo(
            "lifx", "LIFX", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("LIFX",)
        ),
        IntegrationInfo(
            "nanoleaf",
            "Nanoleaf",
            DeviceCategory.LIGHTING,
            DiscoveryMethod.AUTO,
            brands=("Nanoleaf",),
        ),
        IntegrationInfo(
            "wiz", "WiZ", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("WiZ", "Philips")
        ),
        IntegrationInfo(
            "tplink",
            "TP-Link Kasa/Tapo",
            DeviceCategory.LIGHTING,
            DiscoveryMethod.AUTO,
            brands=("TP-Link",),
        ),
        IntegrationInfo(
            "lutron_caseta",
            "Lutron Caseta",
            DeviceCategory.LIGHTING,
            DiscoveryMethod.AUTO,
            brands=("Lutron",),
        ),
        IntegrationInfo(
            "yeelight",
            "Yeelight",
            DeviceCategory.LIGHTING,
            DiscoveryMethod.AUTO,
            brands=("Yeelight", "Xiaomi"),
        ),
        IntegrationInfo(
            "elgato",
            "Elgato Key Light",
            DeviceCategory.LIGHTING,
            DiscoveryMethod.AUTO,
            brands=("Elgato",),
        ),
        IntegrationInfo(
            "twinkly", "Twinkly", DeviceCategory.LIGHTING, DiscoveryMethod.AUTO, brands=("Twinkly",)
        ),
        IntegrationInfo(
            "tradfri",
            "IKEA TRADFRI",
            DeviceCategory.LIGHTING,
            DiscoveryMethod.AUTO,
            brands=("IKEA",),
        ),
        IntegrationInfo(
            "cync", "GE Cync", DeviceCategory.LIGHTING, DiscoveryMethod.CLOUD, brands=("GE", "Cync")
        ),
        # ── TVs ───────────────────────────────────────────────────────
        IntegrationInfo(
            "samsungtv", "Samsung TV", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Samsung",)
        ),
        IntegrationInfo(
            "webostv", "LG webOS TV", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("LG",)
        ),
        IntegrationInfo(
            "braviatv", "Sony Bravia TV", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Sony",)
        ),
        IntegrationInfo("roku", "Roku", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Roku",)),
        IntegrationInfo(
            "apple_tv", "Apple TV", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Apple",)
        ),
        IntegrationInfo(
            "vizio", "Vizio SmartCast", DeviceCategory.TV, DiscoveryMethod.AUTO, brands=("Vizio",)
        ),
        IntegrationInfo(
            "androidtv_remote",
            "Android TV Remote",
            DeviceCategory.TV,
            DiscoveryMethod.AUTO,
            brands=("Google", "Sony", "Nvidia", "Xiaomi"),
        ),
        # ── Speakers / Media ──────────────────────────────────────────
        IntegrationInfo(
            "cast",
            "Google Cast / Chromecast",
            DeviceCategory.SPEAKER,
            DiscoveryMethod.AUTO,
            brands=("Google",),
        ),
        IntegrationInfo(
            "sonos", "Sonos", DeviceCategory.SPEAKER, DiscoveryMethod.AUTO, brands=("Sonos",)
        ),
        IntegrationInfo(
            "bang_olufsen",
            "Bang & Olufsen",
            DeviceCategory.SPEAKER,
            DiscoveryMethod.AUTO,
            brands=("Bang & Olufsen",),
        ),
        IntegrationInfo(
            "denonavr",
            "Denon AVR",
            DeviceCategory.SPEAKER,
            DiscoveryMethod.AUTO,
            brands=("Denon", "Marantz"),
        ),
        IntegrationInfo(
            "heos", "Denon HEOS", DeviceCategory.SPEAKER, DiscoveryMethod.AUTO, brands=("Denon",)
        ),
        IntegrationInfo(
            "yamaha_musiccast",
            "Yamaha MusicCast",
            DeviceCategory.SPEAKER,
            DiscoveryMethod.AUTO,
            brands=("Yamaha",),
        ),
        IntegrationInfo(
            "plex",
            "Plex Media Server",
            DeviceCategory.MEDIA,
            DiscoveryMethod.AUTO,
            brands=("Plex",),
        ),
        IntegrationInfo(
            "kodi", "Kodi", DeviceCategory.MEDIA, DiscoveryMethod.AUTO, brands=("Kodi",)
        ),
        IntegrationInfo(
            "harmony",
            "Logitech Harmony",
            DeviceCategory.MEDIA,
            DiscoveryMethod.AUTO,
            brands=("Logitech",),
        ),
        IntegrationInfo(
            "alexa_devices",
            "Alexa Media Player",
            DeviceCategory.SPEAKER,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("Amazon",),
        ),
        IntegrationInfo(
            "dlna_dmr", "DLNA Media Renderer", DeviceCategory.MEDIA, DiscoveryMethod.AUTO
        ),
        IntegrationInfo(
            "spotify", "Spotify", DeviceCategory.MEDIA, DiscoveryMethod.CLOUD, brands=("Spotify",)
        ),
        IntegrationInfo(
            "music_assistant",
            "Music Assistant",
            DeviceCategory.MEDIA,
            DiscoveryMethod.AUTO,
            source=IntegrationSource.HACS,
        ),
        # ── Appliances ────────────────────────────────────────────────
        IntegrationInfo(
            "smartthings",
            "Samsung SmartThings",
            DeviceCategory.APPLIANCE,
            DiscoveryMethod.CLOUD,
            brands=("Samsung",),
        ),
        IntegrationInfo(
            "home_connect",
            "Home Connect (Bosch/Siemens)",
            DeviceCategory.APPLIANCE,
            DiscoveryMethod.CLOUD,
            brands=("Bosch", "Siemens"),
        ),
        IntegrationInfo(
            "miele", "Miele", DeviceCategory.APPLIANCE, DiscoveryMethod.CLOUD, brands=("Miele",)
        ),
        IntegrationInfo(
            "whirlpool",
            "Whirlpool",
            DeviceCategory.APPLIANCE,
            DiscoveryMethod.CLOUD,
            brands=("Whirlpool", "Maytag", "KitchenAid"),
        ),
        IntegrationInfo(
            "lg_thinq",
            "LG ThinQ",
            DeviceCategory.APPLIANCE,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("LG",),
        ),
        IntegrationInfo(
            "ge_home",
            "GE Home (SmartHQ)",
            DeviceCategory.APPLIANCE,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("GE",),
        ),
        IntegrationInfo(
            "dyson_local",
            "Dyson",
            DeviceCategory.APPLIANCE,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("Dyson",),
        ),
        IntegrationInfo(
            "anova",
            "Anova Sous Vide",
            DeviceCategory.APPLIANCE,
            DiscoveryMethod.CLOUD,
            brands=("Anova",),
        ),
        IntegrationInfo(
            "meater",
            "MEATER Thermometer",
            DeviceCategory.APPLIANCE,
            DiscoveryMethod.CLOUD,
            brands=("MEATER",),
        ),
        IntegrationInfo(
            "switchbot",
            "SwitchBot",
            DeviceCategory.APPLIANCE,
            DiscoveryMethod.AUTO,
            brands=("SwitchBot",),
        ),
        # ── Thermostats / Climate ─────────────────────────────────────
        IntegrationInfo(
            "ecobee", "ecobee", DeviceCategory.THERMOSTAT, DiscoveryMethod.CLOUD, brands=("ecobee",)
        ),
        IntegrationInfo(
            "nest",
            "Google Nest",
            DeviceCategory.THERMOSTAT,
            DiscoveryMethod.CLOUD,
            brands=("Google", "Nest"),
        ),
        # ── Cameras / Security ────────────────────────────────────────
        IntegrationInfo(
            "ring",
            "Ring",
            DeviceCategory.SECURITY,
            DiscoveryMethod.CLOUD,
            brands=("Ring", "Amazon"),
        ),
        IntegrationInfo(
            "unifiprotect",
            "UniFi Protect",
            DeviceCategory.CAMERA,
            DiscoveryMethod.AUTO,
            brands=("Ubiquiti",),
        ),
        IntegrationInfo(
            "reolink", "Reolink", DeviceCategory.CAMERA, DiscoveryMethod.AUTO, brands=("Reolink",)
        ),
        IntegrationInfo(
            "blink",
            "Blink",
            DeviceCategory.SECURITY,
            DiscoveryMethod.CLOUD,
            brands=("Blink", "Amazon"),
        ),
        IntegrationInfo(
            "simplisafe",
            "SimpliSafe",
            DeviceCategory.SECURITY,
            DiscoveryMethod.CLOUD,
            brands=("SimpliSafe",),
        ),
        IntegrationInfo(
            "frigate",
            "Frigate NVR",
            DeviceCategory.CAMERA,
            DiscoveryMethod.MANUAL,
            source=IntegrationSource.HACS,
            brands=("Frigate",),
        ),
        IntegrationInfo(
            "eufy_security",
            "Eufy Security",
            DeviceCategory.SECURITY,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("Eufy", "Anker"),
        ),
        # ── Locks ─────────────────────────────────────────────────────
        IntegrationInfo(
            "august",
            "August / Yale",
            DeviceCategory.LOCK,
            DiscoveryMethod.CLOUD,
            brands=("August", "Yale"),
        ),
        # ── Vacuums ───────────────────────────────────────────────────
        IntegrationInfo(
            "roomba",
            "iRobot Roomba",
            DeviceCategory.VACUUM,
            DiscoveryMethod.AUTO,
            brands=("iRobot",),
        ),
        IntegrationInfo(
            "roborock",
            "Roborock",
            DeviceCategory.VACUUM,
            DiscoveryMethod.CLOUD,
            brands=("Roborock",),
        ),
        IntegrationInfo(
            "sharkiq", "Shark IQ", DeviceCategory.VACUUM, DiscoveryMethod.CLOUD, brands=("Shark",)
        ),
        IntegrationInfo(
            "ecovacs",
            "Ecovacs Deebot",
            DeviceCategory.VACUUM,
            DiscoveryMethod.CLOUD,
            brands=("Ecovacs",),
        ),
        # ── Cars ──────────────────────────────────────────────────────
        IntegrationInfo(
            "tesla_fleet", "Tesla", DeviceCategory.CAR, DiscoveryMethod.CLOUD, brands=("Tesla",)
        ),
        IntegrationInfo(
            "bmw_connected_drive",
            "BMW Connected Drive",
            DeviceCategory.CAR,
            DiscoveryMethod.CLOUD,
            brands=("BMW",),
        ),
        IntegrationInfo(
            "volvo", "Volvo On Call", DeviceCategory.CAR, DiscoveryMethod.CLOUD, brands=("Volvo",)
        ),
        IntegrationInfo(
            "subaru",
            "Subaru STARLINK",
            DeviceCategory.CAR,
            DiscoveryMethod.CLOUD,
            brands=("Subaru",),
        ),
        IntegrationInfo(
            "mbapi2020",
            "Mercedes-Benz",
            DeviceCategory.CAR,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("Mercedes-Benz",),
        ),
        IntegrationInfo(
            "fordpass",
            "Ford",
            DeviceCategory.CAR,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("Ford",),
        ),
        IntegrationInfo(
            "kia_uvo",
            "Kia / Hyundai",
            DeviceCategory.CAR,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("Kia", "Hyundai"),
        ),
        IntegrationInfo(
            "polestar_api",
            "Polestar",
            DeviceCategory.CAR,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("Polestar",),
        ),
        # ── Energy ────────────────────────────────────────────────────
        IntegrationInfo(
            "powerwall",
            "Tesla Powerwall",
            DeviceCategory.ENERGY,
            DiscoveryMethod.AUTO,
            brands=("Tesla",),
        ),
        IntegrationInfo(
            "enphase_envoy",
            "Enphase Envoy",
            DeviceCategory.ENERGY,
            DiscoveryMethod.AUTO,
            brands=("Enphase",),
        ),
        IntegrationInfo(
            "tesla_wall_connector",
            "Tesla Wall Connector",
            DeviceCategory.ENERGY,
            DiscoveryMethod.AUTO,
            brands=("Tesla",),
        ),
        IntegrationInfo(
            "sense",
            "Sense Energy Monitor",
            DeviceCategory.ENERGY,
            DiscoveryMethod.CLOUD,
            brands=("Sense",),
        ),
        IntegrationInfo(
            "solaredge",
            "SolarEdge",
            DeviceCategory.ENERGY,
            DiscoveryMethod.CLOUD,
            brands=("SolarEdge",),
        ),
        IntegrationInfo(
            "wallbox",
            "Wallbox EV Charger",
            DeviceCategory.ENERGY,
            DiscoveryMethod.CLOUD,
            brands=("Wallbox",),
        ),
        IntegrationInfo(
            "easee",
            "Easee EV Charger",
            DeviceCategory.ENERGY,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("Easee",),
        ),
        IntegrationInfo(
            "emporia_vue",
            "Emporia Vue",
            DeviceCategory.ENERGY,
            DiscoveryMethod.CLOUD,
            brands=("Emporia",),
        ),
        IntegrationInfo(
            "iotawatt",
            "IoTaWatt",
            DeviceCategory.ENERGY,
            DiscoveryMethod.AUTO,
            brands=("IoTaWatt",),
        ),
        IntegrationInfo(
            "solar_forecast", "Forecast.Solar", DeviceCategory.ENERGY, DiscoveryMethod.CLOUD
        ),
        IntegrationInfo("opower", "Opower (Utility)", DeviceCategory.ENERGY, DiscoveryMethod.CLOUD),
        # ── IoT Platforms ─────────────────────────────────────────────
        IntegrationInfo(
            "shelly", "Shelly", DeviceCategory.IOT, DiscoveryMethod.AUTO, brands=("Shelly",)
        ),
        IntegrationInfo(
            "esphome", "ESPHome", DeviceCategory.IOT, DiscoveryMethod.AUTO, brands=("ESPHome",)
        ),
        # ── Protocols ─────────────────────────────────────────────────
        IntegrationInfo(
            "zha",
            "Zigbee Home Automation",
            DeviceCategory.PROTOCOL,
            DiscoveryMethod.PROTOCOL,
            notes="Requires Zigbee coordinator USB stick",
        ),
        IntegrationInfo(
            "zwave_js",
            "Z-Wave JS",
            DeviceCategory.PROTOCOL,
            DiscoveryMethod.PROTOCOL,
            notes="Requires Z-Wave USB stick + Z-Wave JS server",
        ),
        IntegrationInfo(
            "matter",
            "Matter",
            DeviceCategory.PROTOCOL,
            DiscoveryMethod.PROTOCOL,
            notes="Requires Matter server (built into HAOS)",
        ),
        IntegrationInfo(
            "homekit_controller",
            "HomeKit Controller",
            DeviceCategory.PROTOCOL,
            DiscoveryMethod.AUTO,
            notes="Imports HomeKit accessories into HA",
        ),
        # ── Irrigation ────────────────────────────────────────────────
        IntegrationInfo(
            "rainmachine",
            "RainMachine",
            DeviceCategory.IRRIGATION,
            DiscoveryMethod.AUTO,
            brands=("RainMachine",),
        ),
        IntegrationInfo(
            "rachio", "Rachio", DeviceCategory.IRRIGATION, DiscoveryMethod.CLOUD, brands=("Rachio",)
        ),
        # ── Mowers ────────────────────────────────────────────────────
        IntegrationInfo(
            "husqvarna_automower",
            "Husqvarna Automower",
            DeviceCategory.MOWER,
            DiscoveryMethod.CLOUD,
            brands=("Husqvarna",),
        ),
        # ── Gaming ────────────────────────────────────────────────────
        IntegrationInfo(
            "xbox", "Xbox", DeviceCategory.GAMING, DiscoveryMethod.CLOUD, brands=("Microsoft",)
        ),
        IntegrationInfo(
            "playstation_network",
            "PlayStation Network",
            DeviceCategory.GAMING,
            DiscoveryMethod.CLOUD,
            brands=("Sony",),
        ),
        # ── Printers / Scanners ────────────────────────────────────
        IntegrationInfo(
            "ipp",
            "IPP Printer",
            DeviceCategory.IOT,
            DiscoveryMethod.AUTO,
            notes="Internet Printing Protocol — auto-discovered via mDNS",
        ),
        IntegrationInfo(
            "brother",
            "Brother Printer",
            DeviceCategory.IOT,
            DiscoveryMethod.AUTO,
            brands=("Brother",),
        ),
        IntegrationInfo(
            "hp_ips", "HP Instant Ink", DeviceCategory.IOT, DiscoveryMethod.AUTO, brands=("HP",)
        ),
        # ── Network / System ───────────────────────────────────────
        IntegrationInfo(
            "bluetooth",
            "Bluetooth",
            DeviceCategory.PROTOCOL,
            DiscoveryMethod.AUTO,
            notes="Core Bluetooth adapter",
        ),
        IntegrationInfo(
            "upnp",
            "UPnP/IGD",
            DeviceCategory.IOT,
            DiscoveryMethod.AUTO,
            notes="Router port mapping",
        ),
        IntegrationInfo(
            "dhcp",
            "DHCP Discovery",
            DeviceCategory.PROTOCOL,
            DiscoveryMethod.AUTO,
            notes="Passive network device discovery",
        ),
        IntegrationInfo(
            "usb",
            "USB Discovery",
            DeviceCategory.PROTOCOL,
            DiscoveryMethod.AUTO,
            notes="USB device auto-detection",
        ),
        IntegrationInfo(
            "ssdp",
            "SSDP Discovery",
            DeviceCategory.PROTOCOL,
            DiscoveryMethod.AUTO,
            notes="Simple Service Discovery Protocol",
        ),
        IntegrationInfo(
            "zeroconf",
            "Zeroconf/mDNS",
            DeviceCategory.PROTOCOL,
            DiscoveryMethod.AUTO,
            notes="mDNS service discovery",
        ),
        IntegrationInfo(
            "network",
            "Network",
            DeviceCategory.IOT,
            DiscoveryMethod.AUTO,
            notes="Network adapter configuration",
        ),
        # ── Plugs / Outlets ────────────────────────────────────────
        IntegrationInfo(
            "tuya",
            "Tuya / Smart Life",
            DeviceCategory.IOT,
            DiscoveryMethod.CLOUD,
            brands=("Tuya",),
            notes="Covers many white-label smart plugs/switches",
        ),
        IntegrationInfo(
            "meross",
            "Meross",
            DeviceCategory.IOT,
            DiscoveryMethod.CLOUD,
            source=IntegrationSource.HACS,
            brands=("Meross",),
        ),
        IntegrationInfo(
            "wemo", "Belkin WeMo", DeviceCategory.IOT, DiscoveryMethod.AUTO, brands=("Belkin",)
        ),
        IntegrationInfo(
            "myq",
            "MyQ Garage",
            DeviceCategory.IOT,
            DiscoveryMethod.CLOUD,
            brands=("Chamberlain", "LiftMaster"),
        ),
    ]
}

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
CONF_AUTO_PURGE_STALE = "auto_purge_stale"
DEFAULT_AUTO_PURGE_STALE = False
CONF_COLLECTOR_MAX_SKIP_HOURS = "collector_max_skip_hours"
DEFAULT_COLLECTOR_MAX_SKIP_HOURS = 12

CONF_DISCOVERY_ENABLED = "discovery_enabled"
CONF_DISCOVERY_MODE = "discovery_mode"
CONF_DISCOVERY_START_TIME = "discovery_start_time"
CONF_DISCOVERY_END_TIME = "discovery_end_time"
CONF_DISCOVERY_INTERVAL = "discovery_interval"

MODE_CONTINUOUS = "continuous"
MODE_SCHEDULED = "scheduled"

DEFAULT_COLLECTOR_ENABLED = True
DEFAULT_COLLECTOR_MODE = MODE_CONTINUOUS
DEFAULT_COLLECTOR_INTERVAL = 14400  # 4 hours in seconds
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
LLM_PROVIDER_OPENAI = "openai"
LLM_PROVIDER_OPENROUTER = "openrouter"
LLM_PROVIDER_GEMINI = "gemini"
LLM_PROVIDER_SELORA_CLOUD = "selora_cloud"
LLM_PROVIDER_NONE = "none"
DEFAULT_LLM_PROVIDER = LLM_PROVIDER_SELORA_CLOUD

# ── Anthropic (Claude API) ──────────────────────────────────────────
CONF_ANTHROPIC_API_KEY = "anthropic_api_key"
CONF_ANTHROPIC_MODEL = "anthropic_model"

DEFAULT_ANTHROPIC_HOST = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_API_KEY = ""  # User must provide their own key during setup
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_API_VERSION = "2023-06-01"

# ── Ollama (Local LLM) ──────────────────────────────────────────────
# Uses Anthropic-compatible API: same /v1/messages endpoint format
#   https://docs.ollama.com/api/anthropic-compatibility
CONF_OLLAMA_HOST = "ollama_host"
CONF_OLLAMA_MODEL = "ollama_model"

DEFAULT_OLLAMA_HOST = "http://host.docker.internal:11434"
DEFAULT_OLLAMA_MODEL = "llama4"

# ── OpenAI (GPT API) ───────────────────────────────────────────────
CONF_OPENAI_API_KEY = "openai_api_key"
CONF_OPENAI_MODEL = "openai_model"

DEFAULT_OPENAI_HOST = "https://api.openai.com"
DEFAULT_OPENAI_MODEL = "gpt-5.4"

# ── OpenRouter (multi-vendor aggregator, OpenAI-compatible) ─────────
CONF_OPENROUTER_API_KEY = "openrouter_api_key"
CONF_OPENROUTER_MODEL = "openrouter_model"

DEFAULT_OPENROUTER_HOST = "https://openrouter.ai/api"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4.5"

# Optional app-attribution headers recommended by OpenRouter.
# https://openrouter.ai/docs/api-reference/overview#app-attribution
OPENROUTER_APP_REFERER = "https://selorahomes.com"
OPENROUTER_APP_TITLE = "Selora AI"

# ── Google Gemini ──────────────────────────────────────────────────
CONF_GEMINI_API_KEY = "gemini_api_key"
CONF_GEMINI_MODEL = "gemini_model"

DEFAULT_GEMINI_HOST = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Endpoint paths
ANTHROPIC_MESSAGES_ENDPOINT = "/v1/messages"
OLLAMA_CHAT_ENDPOINT = "/v1/chat/completions"
OPENAI_CHAT_ENDPOINT = "/v1/chat/completions"
OPENROUTER_CHAT_ENDPOINT = "/v1/chat/completions"
MESSAGES_ENDPOINT = ANTHROPIC_MESSAGES_ENDPOINT  # Legacy compatibility

# ── MQTT (future) ────────────────────────────────────────────────────
# Reaction-based behavior capture (Matthew, Mar 4).
# MQTT listening for point-in-time event pre-classification.
# TODO: implement mqtt_listener.py
CONF_MQTT_ENABLED = "mqtt_enabled"
DEFAULT_MQTT_ENABLED = False  # Off until implemented

# ── LLM Pricing ──────────────────────────────────────────────────────
# Approximate USD cost per million tokens, used for the cost-estimate
# sensor. Treated as best-effort: providers update prices and our table
# may lag. Looked up by exact model id; matches "anthropic/<id>" too so
# OpenRouter routes resolve. Models not listed contribute 0 to cost
# (call count and token totals are still tracked).
#
# Anthropic prices sourced from https://platform.claude.com/docs/en/about-claude/pricing
# (last verified 2026-04-28). Users can override via the panel for
# enterprise / negotiated pricing.
#
# Format: provider_type -> {model_id: (input_per_million, output_per_million)}
LLM_PRICING_USD_PER_MTOK: dict[str, dict[str, tuple[float, float]]] = {
    "anthropic": {
        # Opus 4.5+ is priced at the new tier ($5 / $25).
        "claude-opus-4-7": (5.0, 25.0),
        "claude-opus-4-6": (5.0, 25.0),
        "claude-opus-4-5": (5.0, 25.0),
        # Opus 4 / 4.1 retain the legacy tier ($15 / $75).
        "claude-opus-4-1": (15.0, 75.0),
        "claude-opus-4": (15.0, 75.0),
        # Sonnet 3.7 (deprecated) and 4.x share the same tier.
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-sonnet-4-5": (3.0, 15.0),
        "claude-sonnet-4": (3.0, 15.0),
        "claude-sonnet-3-7": (3.0, 15.0),
        # Haiku family.
        "claude-haiku-4-5": (1.0, 5.0),
        "claude-haiku-4-5-20251001": (1.0, 5.0),
        "claude-haiku-3-5": (0.80, 4.0),
        "claude-haiku-3": (0.25, 1.25),
    },
    "openai": {
        "gpt-5.5": (5.0, 30.0),
        "gpt-5.4": (2.5, 15.0),
        "gpt-5": (1.25, 10.0),
        "gpt-5-mini": (0.25, 2.0),
        "gpt-4o": (2.5, 10.0),
        "gpt-4o-mini": (0.15, 0.6),
        "o1": (15.0, 60.0),
        "o3": (2.0, 8.0),
        "o4-mini": (1.10, 4.40),
    },
    "gemini": {
        "gemini-2.5-pro": (1.25, 10.0),
        "gemini-2.5-flash": (0.30, 2.50),
        "gemini-2.5-flash-lite": (0.10, 0.40),
        "gemini-2.0-flash": (0.10, 0.40),
    },
    "openrouter": {
        # OpenRouter passes vendor pricing through; we approximate via
        # the underlying model name. Exact billing comes from OpenRouter.
        "anthropic/claude-sonnet-4.5": (3.0, 15.0),
        "anthropic/claude-sonnet-4-6": (3.0, 15.0),
        "anthropic/claude-opus-4-7": (5.0, 25.0),
        "openai/gpt-5.5": (5.0, 30.0),
        "openai/gpt-5.4": (2.5, 15.0),
        "openai/gpt-5": (1.25, 10.0),
    },
    # Ollama is local — no cost.
    "ollama": {},
}

# User-supplied pricing overrides live in the config entry's options under
# this key. Shape: {provider: {model: [input_per_million, output_per_million]}}.
# An entry takes precedence over the built-in table; missing entries fall
# through to the defaults above.
CONF_LLM_PRICING_OVERRIDES = "llm_pricing_overrides"


def estimate_llm_cost_usd(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    overrides: dict[str, dict[str, tuple[float, float] | list[float]]] | None = None,
) -> float:
    """Return an approximate USD cost for one call, or 0 when unknown.

    ``overrides`` (optional) lets the user supply custom $/MTok rates per
    provider/model — typically loaded from the config entry's options.
    Override shape mirrors ``LLM_PRICING_USD_PER_MTOK``; values may be
    tuples or 2-element lists (lists survive a JSON round-trip).
    """
    pricing: tuple[float, float] | list[float] | None = None
    if overrides:
        override_entry = overrides.get(provider, {}).get(model)
        if override_entry is not None:
            pricing = override_entry
    if pricing is None:
        pricing = LLM_PRICING_USD_PER_MTOK.get(provider, {}).get(model)
    if not pricing or len(pricing) < 2:
        return 0.0
    in_price = float(pricing[0])
    out_price = float(pricing[1])
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000.0


# ── LLM Timeout ─────────────────────────────────────────────────────
DEFAULT_LLM_TIMEOUT = 120  # seconds — per-request timeout for LLM calls

# ── Tool Calling ────────────────────────────────────────────────────
MAX_TOOL_CALL_ROUNDS = 5  # Maximum LLM-tool round trips per chat turn
MAX_TOOL_RESULT_CHARS = 16000  # Truncate tool results to prevent token explosion

# ── Data Collection ──────────────────────────────────────────────────
DEFAULT_PUSH_INTERVAL = 3600  # 1 hour — how often we collect + analyze
CONF_PUSH_INTERVAL = "push_interval"

# Entity domains & exclude patterns — canonical definitions live in
# entity_capabilities.py; re-exported here for backward compatibility.
from .entity_capabilities import (  # noqa: E402, F401
    COLLECTOR_DOMAINS,
    DOMAIN_PROFILES,
    SCENE_CAPABLE_DOMAINS,
)

LIGHT_ENTITY_EXCLUDE_PATTERNS = DOMAIN_PROFILES["light"].exclude_patterns

# Domain-specific entity attributes included in snapshots for state-aware
# LLM responses (brightness, temperature, battery, etc.).
ENTITY_SNAPSHOT_ATTRS = frozenset(
    {
        "brightness",
        "color_temp",
        "temperature",
        "current_temperature",
        "target_temperature",
        "hvac_mode",
        "battery_level",
        "battery",
        "volume_level",
        "media_title",
        "source",
        "percentage",
        "preset_mode",
        "current_position",
    }
)

# ── Relevance Scoring ────────────────────────────────────────────────
MIN_RELEVANCE_SCORE = 0.3  # Suggestions below this are filtered out
RELEVANCE_WEIGHT_CROSS_DEVICE = 0.30  # Connects multiple devices/domains
RELEVANCE_WEIGHT_ACTIVITY = 0.25  # Trigger entity has recent state changes
# State changes per entity that maps to a perfect activity score (1.0).
# Entities with fewer changes score proportionally lower.
ACTIVITY_HIGH_THRESHOLD = 50
RELEVANCE_WEIGHT_COVERAGE = 0.20  # Covers entities not in existing automations
RELEVANCE_WEIGHT_CATEGORY = 0.10  # Safety/security/energy bonus
RELEVANCE_WEIGHT_COMPLEXITY = 0.05  # Has conditions, multiple actions, etc.
RELEVANCE_WEIGHT_CATEGORY_LINK = 0.10  # Cross-category pairing quality (#79)

# ── Category Link Weights (#79) ─────────────────────────────────────
# Defines how well entity domain pairs work together in automations.
# 1.0 = strong natural link, 0.0 = nonsensical pairing.
# Pairs not listed default to DEFAULT_CATEGORY_LINK_WEIGHT.
DEFAULT_CATEGORY_LINK_WEIGHT = 0.3

# Keyed by frozenset of (trigger_domain, action_domain) → weight.
# Use frozenset so order doesn't matter (A→B == B→A).
CATEGORY_LINK_WEIGHTS: dict[frozenset[str], float] = {
    # ── Strong links — natural automation pairings ───────────────
    frozenset({"binary_sensor", "light"}): 1.0,  # motion → lights
    frozenset({"binary_sensor", "lock"}): 1.0,  # door sensor → lock
    frozenset({"binary_sensor", "cover"}): 0.9,  # sensor → blinds
    frozenset({"binary_sensor", "fan"}): 0.8,  # sensor → fan
    frozenset({"binary_sensor", "switch"}): 0.8,  # sensor → switch
    frozenset({"sensor", "climate"}): 1.0,  # temp sensor → HVAC
    frozenset({"sensor", "fan"}): 0.8,  # humidity → fan
    frozenset({"sensor", "light"}): 0.7,  # lux → lights
    frozenset({"sensor", "cover"}): 0.7,  # lux → blinds
    frozenset({"person", "light"}): 0.9,  # presence → lights
    frozenset({"person", "lock"}): 0.9,  # presence → lock
    frozenset({"person", "climate"}): 0.9,  # presence → HVAC
    frozenset({"person", "cover"}): 0.7,  # presence → blinds
    frozenset({"device_tracker", "light"}): 0.9,  # tracker → lights
    frozenset({"device_tracker", "lock"}): 0.9,  # tracker → lock
    frozenset({"device_tracker", "climate"}): 0.9,  # tracker → HVAC
    # ── Moderate links ───────────────────────────────────────────
    frozenset({"light", "switch"}): 0.6,  # light ↔ switch
    frozenset({"climate", "humidifier"}): 0.8,  # HVAC ↔ humidifier
    frozenset({"climate", "fan"}): 0.7,  # HVAC ↔ fan
    frozenset({"media_player", "light"}): 0.6,  # media → movie mode
    frozenset({"lock", "light"}): 0.7,  # lock → arrival lights
    frozenset({"cover", "light"}): 0.6,  # blinds ↔ lights
    frozenset({"vacuum", "person"}): 0.7,  # vacuum when away
    frozenset({"vacuum", "device_tracker"}): 0.7,
    # ── Weak / nonsensical links — penalized ─────────────────────
    frozenset({"vacuum", "lock"}): 0.2,
    frozenset({"media_player", "climate"}): 0.2,
    frozenset({"media_player", "lock"}): 0.1,
    frozenset({"vacuum", "climate"}): 0.1,
    frozenset({"water_heater", "light"}): 0.2,
    frozenset({"water_heater", "media_player"}): 0.1,
}

# ── LLM Suggestion Limits ───────────────────────────────────────────
DEFAULT_MAX_SUGGESTIONS = 3  # Max automation suggestions per analysis cycle
DEFAULT_MIN_SUGGESTIONS = 3  # Floor: always allow at least this many
DEFAULT_MAX_SUGGESTIONS_CEILING = 10  # Ceiling: never exceed this many
DEFAULT_DEVICES_PER_SUGGESTION = 5  # Scaling factor: 1 extra suggestion per N uncovered devices

# ── Automation Cap ──────────────────────────────────────────────────
# Dynamic cap on background-suggested automations: 1.5 × number of devices.
# User-created automations are never capped.
AUTOMATIONS_PER_DEVICE = 1.5
# Minimum floor so fresh installs or small homes still get suggestions.
AUTOMATION_CAP_FLOOR = 5
# Hard ceiling to prevent unbounded growth on very large installs.
AUTOMATION_CAP_CEILING = 200
# Automations that haven't triggered in this many days are stale candidates.
AUTOMATION_STALE_DAYS = 5

# ── Automation Creation ──────────────────────────────────────────────
# Prefix for auto-created automation IDs (easy to find/filter).
AUTOMATION_ID_PREFIX = "selora_ai_"

# ── Scene Creation ───────────────────────────────────────────────────
# Prefix for auto-created scene IDs (easy to find/filter).
SCENE_ID_PREFIX = "selora_ai_"

# ── Protected Domains (never removed by reset) ──────────────────────
PROTECTED_DOMAINS = {
    "homeassistant",
    "automation",
    "frontend",
    "backup",
    "sun",
    "shopping_list",
    "google_translate",
    "radio_browser",
    "persistent_notification",
    "recorder",
    "logger",
    "system_log",
    "default_config",
    "config",
    "person",
    "zone",
    "script",
    "scene",
    "group",
    "template",
    "webhook",
    "conversation",
    "assist_pipeline",
    "cloud",
    "mobile_app",
    "tag",
    "blueprint",
    "ffmpeg",
    "met",
    "bluetooth",
    "dhcp",
    "ssdp",
    "zeroconf",
    "usb",
    "network",  # core system discovery
    DOMAIN,  # never remove ourselves
}

# ── Side Panel ──────────────────────────────────────────────────────
PANEL_NAME = "selora-ai"
PANEL_TITLE = "Selora AI"
PANEL_ICON = "mdi:apple-keyboard-command"
PANEL_PATH = "selora-ai"

# ── HA Recorder ──────────────────────────────────────────────────────
# Historical data source (SQLite) for pattern detection.
# https://www.home-assistant.io/integrations/recorder/
DEFAULT_RECORDER_LOOKBACK_DAYS = 7
CONF_RECORDER_LOOKBACK_DAYS = "recorder_lookback_days"

# ── Automation Lifecycle ──────────────────────────────────────────────
AUTOMATION_STORE_KEY = "selora_ai_automations"

# ── Scene Lifecycle ─────────────────────────────────────────────────
SCENE_STORE_KEY = "selora_ai_scenes"

# ── Pattern Detection ────────────────────────────────────────────────
PATTERN_STORE_KEY = "selora_ai_patterns"
DEFAULT_PATTERN_INTERVAL = 900  # 15 minutes
CONF_ENRICHMENT_INTERVAL = "enrichment_interval"
DEFAULT_ENRICHMENT_INTERVAL = 21600  # 6 hours
CONF_PATTERN_ENABLED = "pattern_detection_enabled"
PATTERN_HISTORY_MAX_PER_ENTITY = 500
PATTERN_HISTORY_RETENTION_DAYS = 14
PATTERN_MAX_PATTERNS = 500
PATTERN_MAX_SUGGESTIONS = 200
PATTERN_MAX_DELETED_HASHES = 1000

PATTERN_TYPE_TIME_BASED = "time_based"
PATTERN_TYPE_CORRELATION = "correlation"
PATTERN_TYPE_SEQUENCE = "sequence"

CONFIDENCE_HIGH = 0.75
CONFIDENCE_MEDIUM = 0.50

# ── Causality Guardrails ─────────────────────────────────────────────
# Maximum allowed standard deviation of delay (seconds) for a correlation
# to be considered causal. High variance suggests coincidence.
CAUSALITY_MAX_DELAY_STDDEV = 60.0
# Minimum ratio of directional consistency (A→B vs B→A) to treat as causal.
CAUSALITY_MIN_DIRECTIONALITY = 0.65
# Extra penalty multiplier applied when directionality is below the minimum.
# Ensures bidirectional (common-cause) patterns are penalised harder than the
# raw ratio alone would achieve.
CAUSALITY_DIRECTIONALITY_PENALTY = 0.8

# How long a dismissed suggestion suppresses re-surfacing of the same pattern
DISMISSAL_SUPPRESSION_WINDOW_DAYS = 7

SIGNAL_PROACTIVE_SUGGESTIONS = f"{DOMAIN}_proactive_suggestions"
SIGNAL_SCENE_DELETED = f"{DOMAIN}_scene_deleted"
SIGNAL_SCENE_REFRESHED = f"{DOMAIN}_scene_refreshed"
SIGNAL_SCENE_RESTORED = f"{DOMAIN}_scene_restored"

# ── Selora Connect (OAuth 2.0) ────────────────────────────────────────
CONF_SELORA_CONNECT_ENABLED = "selora_connect_enabled"
CONF_SELORA_CONNECT_URL = "selora_connect_url"
CONF_SELORA_INSTALLATION_ID = "selora_installation_id"
CONF_SELORA_JWT_KEY = "selora_jwt_key"  # base64-encoded per-installation derived key
CONF_SELORA_MCP_URL = (
    "selora_mcp_url"  # MCP endpoint URL (e.g. https://mcp-xxx.selorabox.com/api/selora_ai/mcp)
)
SELORA_JWT_ALGORITHM = "HS256"
DEFAULT_SELORA_CONNECT_URL = "https://connect.selorahomes.com"
SELORA_JWT_ISSUER = DEFAULT_SELORA_CONNECT_URL  # default; overridden by entry data
SELORA_JWT_MAX_SIZE = 8192  # bytes — reject tokens larger than this before decode
SELORA_JWT_LEEWAY_SECONDS = 30  # clock skew tolerance for exp/nbf
SELORA_ADMIN_ROLES = frozenset({"owner", "member"})

# ── Selora AI Gateway (LLM via OAuth) ───────────────────────────────
# OAuth-protected LLM provider hosted by Selora. The flow:
#   1. User clicks "Selora AI Cloud" → opens consent popup at Connect's
#      /oauth/aigw/authorize. The integration sends no install_id;
#      Connect picks the home from the signed-in user (auto when
#      there's exactly one Selora Hub, picker when 2+, free plan
#      otherwise).
#   2. After approval, Connect redirects back with a code; we exchange
#      it for a short-lived RS256 JWT (access_token) and an opaque
#      refresh token. Tokens are stored in the config entry.
#   3. The provider sends `Authorization: Bearer <jwt>` to Connect's
#      OpenAI-compatible AI Gateway proxy. Quota and any installation
#      binding live in the JWT — no client-supplied header, no `model`
#      in the body (the gateway picks and overwrites it server-side).
#      Refresh runs in-line when the access token nears expiry.
CONF_AIGATEWAY_ACCESS_TOKEN = "aigateway_access_token"  # short-lived RS256 JWT
CONF_AIGATEWAY_REFRESH_TOKEN = "aigateway_refresh_token"  # opaque "aigw_..." token
CONF_AIGATEWAY_EXPIRES_AT = "aigateway_expires_at"  # unix timestamp (float)
CONF_AIGATEWAY_USER_EMAIL = "aigateway_user_email"  # for display, not auth
CONF_AIGATEWAY_USER_ID = "aigateway_user_id"  # for display, not auth
CONF_AIGATEWAY_CLIENT_ID = "aigateway_client_id"  # public OAuth client id (== redirect_uri)

AIGATEWAY_OAUTH_SCOPE = "ai-gateway"
AIGATEWAY_AUTHORIZE_PATH = "/oauth/aigw/authorize"
AIGATEWAY_TOKEN_PATH = "/oauth/aigw/token"
AIGATEWAY_CHAT_COMPLETIONS_PATH = "/api/v1/ai-gateway/v1/chat/completions"
# Refresh the access token if fewer than this many seconds remain. Generous
# enough that long-running requests don't get a 401 mid-flight.
AIGATEWAY_REFRESH_LEEWAY_SECONDS = 120

# ── Selora MCP Tokens (local API keys) ───────────────────────────────
MCP_TOKEN_STORE_KEY = f"{DOMAIN}.mcp_tokens"
MCP_TOKEN_STORE_VERSION = 1
MCP_TOKEN_PREFIX = "smt_"
MCP_TOKEN_MAX_COUNT = 50
MCP_TOKEN_PERMISSION_READ_ONLY = "read_only"
MCP_TOKEN_PERMISSION_ADMIN = "admin"
MCP_TOKEN_PERMISSION_CUSTOM = "custom"
MCP_TOKEN_VALID_PERMISSIONS = frozenset(
    {MCP_TOKEN_PERMISSION_READ_ONLY, MCP_TOKEN_PERMISSION_ADMIN, MCP_TOKEN_PERMISSION_CUSTOM}
)

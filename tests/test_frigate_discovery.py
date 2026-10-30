"""Tests for finding cameras on the network that Home Assistant does not have."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.selora_ai.frigate import discovery
from custom_components.selora_ai.frigate.discovery import (
    NetworkCamera,
    merge,
    parse_probe_match,
    async_user_cameras,
)

_MATCH = (
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><e:Body>'
    "<d:ProbeMatches><d:ProbeMatch>"
    "<d:Scopes>onvif://www.onvif.org/type/video_encoder "
    "onvif://www.onvif.org/name/Garage%20Cam onvif://www.onvif.org/hardware/RLC-810A "
    "onvif://www.onvif.org/mfr/Reolink</d:Scopes>"
    "<d:XAddrs>{xaddrs}</d:XAddrs>"
    "</d:ProbeMatch></d:ProbeMatches></e:Body></e:Envelope>"
)


def test_a_probe_match_names_the_camera_and_its_brand() -> None:
    camera = parse_probe_match(
        _MATCH.format(xaddrs="http://192.168.1.60:8000/onvif/device_service").encode()
    )

    assert camera == NetworkCamera("192.168.1.60", 8000, "Garage Cam", "reolink", "onvif")


def test_a_match_with_only_a_public_address_is_ignored() -> None:
    match = _MATCH.format(xaddrs="http://8.8.4.4/onvif/device_service").encode()

    assert parse_probe_match(match) is None


def test_garbage_is_not_a_camera() -> None:
    assert parse_probe_match(b"\x00not xml") is None


async def test_only_local_addresses_named_in_chat_are_kept(hass: HomeAssistant) -> None:
    """A name has to resolve to a local address: "kitchen" (a room the model
    took for a host) is not a camera's."""
    import socket

    def _resolve(name: str, *_args: object, **_kwargs: object) -> list:
        if name == "cam.local":
            return [(socket.AF_INET, 0, 0, "", ("192.168.1.71", 0))]
        raise OSError("unknown host")

    with patch.object(hass.loop, "getaddrinfo", AsyncMock(side_effect=_resolve)):
        cameras = await async_user_cameras(
            hass, ["192.168.1.70", "8.8.8.8", "cam.local", "kitchen", "192.168.1.70"]
        )

    assert [(c.host, c.name, c.port) for c in cameras] == [
        ("192.168.1.70", "192.168.1.70", 0),
        ("192.168.1.71", "cam.local", 0),
    ]


def test_reolink_is_known_by_its_model_alone() -> None:
    match = (
        _MATCH.replace("name/Garage%20Cam", "name/IPC-BO")
        .replace("hardware/RLC-810A", "hardware/E1Zoom")
        .replace(" onvif://www.onvif.org/mfr/Reolink", "")
        .format(xaddrs="http://192.168.2.192:8000/onvif/device_service")
    )

    camera = parse_probe_match(match.encode())

    assert camera is not None
    assert (camera.name, camera.brand, camera.port) == ("E1 Zoom", "reolink", 8000)


def test_merge_keeps_one_camera_per_address_and_fills_the_brand() -> None:
    probed = NetworkCamera("192.168.1.60", 80, "Garage Cam", None, "onvif")
    flow = NetworkCamera("192.168.1.60", 80, "192.168.1.60", "reolink", "home_assistant")

    (camera,) = merge([probed], [flow])

    assert (camera.name, camera.brand, camera.source) == ("Garage Cam", "reolink", "onvif")


async def test_a_pending_reolink_flow_with_an_address_is_a_camera(hass: HomeAssistant) -> None:
    flows = [
        {
            "handler": "reolink",
            "context": {"title_placeholders": {"ip_address": "192.168.1.61", "name": "Porch"}},
        },
        {"handler": "hue", "context": {"title_placeholders": {"host": "192.168.1.2"}}},
        {"handler": "onvif", "context": {"title_placeholders": {}}},
    ]
    with (
        patch.object(hass.config_entries.flow, "async_progress", MagicMock(return_value=flows)),
        patch.object(discovery, "async_probe", AsyncMock(return_value=[])),
    ):
        found = await discovery.async_scan(hass)

    assert found == [NetworkCamera("192.168.1.61", 80, "Porch", "reolink", "home_assistant")]
    assert discovery.cached_cameras(hass) == []  # the flow is gone now


def test_a_factory_name_gives_way_to_the_hardware_model() -> None:
    match = (
        _MATCH.replace("name/Garage%20Cam", "name/IPC-BO")
        .replace("hardware/RLC-810A", "hardware/ReolinkVideoDoorbellWiFi")
        .format(xaddrs="http://192.168.2.98:8000/onvif/device_service")
    )

    camera = parse_probe_match(match.encode())

    assert camera is not None and camera.name == "Reolink Video Doorbell WiFi"


async def test_scanning_now_drops_the_chat_homes_cached_list(hass: HomeAssistant) -> None:
    """The on-demand scan must show its answer, not a list built before it."""
    import inspect

    from custom_components.selora_ai.const import DOMAIN
    from custom_components.selora_ai.websocket import frigate as frigate_ws

    hass.data.setdefault(DOMAIN, {})[discovery.SUGGESTION_CACHE] = (0, [])
    handler = inspect.unwrap(frigate_ws._handle_websocket_frigate_scan)
    garage = NetworkCamera("192.168.2.178", 8000, "Reolink Lumus Pro", "reolink", "onvif")
    with patch.object(discovery, "async_probe", AsyncMock(return_value=[garage])):
        user = MagicMock()
        user.user.is_admin = False
        await handler(hass, user, {"id": 1})
        user.send_result.assert_not_called()

        admin = MagicMock()
        admin.user.is_admin = True
        await handler(hass, admin, {"id": 2})

    admin.send_result.assert_called_once_with(
        2,
        {
            "cameras": [
                {
                    "name": "Reolink Lumus Pro",
                    "host": "192.168.2.178",
                    "brand": "reolink",
                    "source": "onvif",
                }
            ]
        },
    )
    assert discovery.SUGGESTION_CACHE not in hass.data[DOMAIN]


_ADAPTERS = [
    {
        "name": "eth0",
        "enabled": True,
        "ipv4": [{"address": "192.168.2.84", "network_prefix": 24}],
        "ipv6": [],
    },
    {
        "name": "wide",
        "enabled": True,
        "ipv4": [{"address": "10.0.0.5", "network_prefix": 16}],
        "ipv6": [],
    },
    {
        "name": "off",
        "enabled": False,
        "ipv4": [{"address": "192.168.9.1", "network_prefix": 24}],
        "ipv6": [],
    },
]


async def test_the_sweep_asks_each_address_of_the_hubs_own_network(
    hass: HomeAssistant,
) -> None:
    """A /24 only, never the hub itself, never a wider or disabled network."""
    e1_zoom = NetworkCamera("192.168.2.192", 8000, "E1 Zoom", None, "onvif")
    probe = AsyncMock(return_value=[e1_zoom])
    with (
        patch(
            "homeassistant.components.network.async_get_adapters",
            AsyncMock(return_value=_ADAPTERS),
        ),
        patch.object(discovery, "async_probe", probe),
    ):
        found = await discovery.async_sweep(hass)

    targets = probe.await_args.kwargs["targets"]
    assert len(targets) == 253
    assert ("192.168.2.84", 3702) not in targets
    assert ("192.168.2.192", 3702) in targets
    assert all(host.startswith("192.168.2.") for host, _port in targets)
    assert found == [e1_zoom]


async def test_a_sweeps_cameras_stay_until_the_next_sweep(hass: HomeAssistant) -> None:
    lumus = NetworkCamera("192.168.2.178", 8000, "Reolink Lumus Pro", "reolink", "onvif")
    e1_zoom = NetworkCamera("192.168.2.192", 8000, "E1 Zoom", None, "onvif")
    with (
        patch.object(discovery, "async_probe", AsyncMock(return_value=[lumus])),
        patch.object(discovery, "async_sweep", AsyncMock(return_value=[e1_zoom])) as sweep,
    ):
        assert discovery._sweep_due(hass)
        assert await discovery.async_scan(hass, sweep=True) == [lumus, e1_zoom]
        assert not discovery._sweep_due(hass)
        # A quick scan later does not sweep, and does not forget what one found.
        assert await discovery.async_scan(hass) == [lumus, e1_zoom]

    sweep.assert_awaited_once()


async def test_a_second_network_does_not_crowd_out_the_lan(hass: HomeAssistant) -> None:
    """The hub's link to the Frigate host is listed first on some installs."""
    adapters = [
        {
            "name": "enp0s5",
            "enabled": True,
            "ipv4": [{"address": "10.42.42.15", "network_prefix": 24}],
            "ipv6": [],
        },
        _ADAPTERS[0],
    ]
    with patch(
        "homeassistant.components.network.async_get_adapters",
        AsyncMock(return_value=adapters),
    ):
        hosts = await discovery._local_hosts(hass)

    assert "192.168.2.192" in hosts and "10.42.42.2" in hosts
    assert len(hosts) == 506

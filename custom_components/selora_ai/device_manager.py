"""Device discovery & integration orchestration for Selora AI.

Wraps HA's config_entries.flow API so Selora AI can list discovered
devices, accept/pair them (including PIN entry), and complete integration
— all via a single webhook endpoint.

For Android TV devices, supports fully automatic pairing:
  1. Wake-on-LAN magic packet powers on the TV
  2. Poll until ADB (port 5555) is reachable
  3. accept_flow → TV shows PIN on screen
  4. ADB screenshot captures the PIN screen
  5. Claude vision reads the PIN from the image
  6. submit_pin completes the pairing
  7. Power the TV back off via ADB
  Zero human intervention required.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import tempfile
from typing import Any

import aiohttp
from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# Handlers that support ADB-based auto-pairing
_ADB_HANDLERS = {"androidtv_remote"}
_ADB_PORT = 5555
_ADB_SCREENSHOT_DELAY = 5  # seconds to wait for PIN to render on screen
_WOL_PORT = 9
_ADB_BOOT_TIMEOUT = 90  # max seconds to wait for TV to boot + ADB ready
_ADB_POLL_INTERVAL = 3  # seconds between ADB readiness checks


_ANDROIDTV_REMOTE_PORT = 6466  # Android TV Remote protocol port


class DeviceManager:
    """Orchestrate HA config-entry flows for device discovery & pairing."""

    def __init__(self, hass: HomeAssistant, api_key: str = "", model: str = "") -> None:
        self.hass = hass
        self._api_key = api_key
        self._model = model

    async def _find_unconfigured_android_tvs(self) -> list[str]:
        """Scan the local subnet for Android TVs not yet in androidtv_remote.

        Uses the existing Kitchen TV's IP to determine the subnet,
        then probes for other devices responding on the Android TV Remote
        protocol port (6466).
        """
        # Get IPs already configured in androidtv_remote
        known_hosts: set[str] = set()
        for ce in self.hass.config_entries.async_entries("androidtv_remote"):
            host = ce.data.get("host", "")
            if host:
                known_hosts.add(host)

        if not known_hosts:
            return []

        # Determine subnet from first known host (e.g., 192.168.1.72 → 192.168.1.x)
        reference_ip = next(iter(known_hosts))
        subnet_prefix = ".".join(reference_ip.split(".")[:3])  # "192.168.1"

        new_hosts: list[str] = []
        scan_tasks = []

        async def _probe(ip: str) -> str | None:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, _ANDROIDTV_REMOTE_PORT), timeout=1.5
                )
                writer.close()
                await writer.wait_closed()
                return ip
            except (OSError, asyncio.TimeoutError):
                return None

        # Scan the subnet (skip .0, .1, .255 and known hosts)
        for i in range(2, 255):
            ip = f"{subnet_prefix}.{i}"
            if ip in known_hosts:
                continue
            scan_tasks.append(_probe(ip))

        # Run in batches of 50 to avoid overwhelming the network
        for batch_start in range(0, len(scan_tasks), 50):
            batch = scan_tasks[batch_start:batch_start + 50]
            results = await asyncio.gather(*batch)
            for ip in results:
                if ip:
                    new_hosts.append(ip)
                    _LOGGER.info("Found unconfigured Android TV at %s", ip)

        return new_hosts

    async def list_discovered(self) -> list[dict[str, Any]]:
        """Return all pending discovery / config flows."""
        progress = self.hass.config_entries.flow.async_progress()
        results: list[dict[str, Any]] = []
        for flow in progress:
            results.append({
                "flow_id": flow["flow_id"],
                "handler": flow.get("handler", ""),
                "step_id": flow.get("step_id", ""),
                "context": {
                    k: v
                    for k, v in flow.get("context", {}).items()
                    if k in ("source", "unique_id", "title_placeholders")
                },
            })
        return results

    async def accept_flow(self, flow_id: str) -> dict[str, Any]:
        """Confirm a discovered flow with empty user input (single-step)."""
        result = await self.hass.config_entries.flow.async_configure(
            flow_id, user_input={}
        )
        return self._normalise_result(result)

    async def start_device_flow(
        self, domain: str, host: str
    ) -> dict[str, Any]:
        """Manually kick off a config flow by domain + host IP."""
        result = await self.hass.config_entries.flow.async_init(
            domain,
            context={"source": "user"},
            data={"host": host},
        )
        return self._normalise_result(result)

    async def submit_pin(self, flow_id: str, pin: str) -> dict[str, Any]:
        """Submit a PIN for a pairing step (e.g. Android TV)."""
        result = await self.hass.config_entries.flow.async_configure(
            flow_id, user_input={"pin": pin}
        )
        return self._normalise_result(result)

    async def configure_step(
        self, flow_id: str, user_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Generic escape-hatch: progress any flow step with arbitrary input."""
        result = await self.hass.config_entries.flow.async_configure(
            flow_id, user_input=user_input
        )
        return self._normalise_result(result)

    # ── Android TV auto-pair ────────────────────────────────────────

    def _get_flow_host(self, flow_id: str) -> str | None:
        """Extract host IP from a pending flow handler's instance vars."""
        handler = self.hass.config_entries.flow._progress.get(flow_id)
        if handler and hasattr(handler, "host"):
            return handler.host
        return None

    def _get_flow_mac(self, flow_id: str) -> str | None:
        """Extract MAC address from a flow's context.unique_id."""
        for flow in self.hass.config_entries.flow.async_progress():
            if flow["flow_id"] == flow_id:
                uid = flow.get("context", {}).get("unique_id", "")
                # MAC format: "64:ff:0a:3b:e9:ef"
                if len(uid.split(":")) == 6:
                    return uid
        return None

    @staticmethod
    def _send_wol(mac: str) -> None:
        """Send a Wake-on-LAN magic packet (broadcast UDP)."""
        mac_bytes = bytes.fromhex(mac.replace(":", ""))
        magic = b"\xff" * 6 + mac_bytes * 16
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.sendto(magic, ("255.255.255.255", _WOL_PORT))
        finally:
            sock.close()

    @staticmethod
    async def _wait_for_adb(host: str) -> bool:
        """Poll until ADB port is reachable or timeout."""
        deadline = asyncio.get_event_loop().time() + _ADB_BOOT_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, _ADB_PORT), timeout=3
                )
                writer.close()
                await writer.wait_closed()
                return True
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(_ADB_POLL_INTERVAL)
        return False

    @staticmethod
    def _get_adb_signer_sync():
        """Return a PythonRSASigner, generating an RSA key if needed (blocking I/O)."""
        from adb_shell.auth.keygen import keygen
        from adb_shell.auth.sign_pythonrsa import PythonRSASigner

        key_path = os.path.join(tempfile.gettempdir(), "selora_ai_adbkey")
        if not os.path.isfile(key_path):
            keygen(key_path)
        return PythonRSASigner.FromRSAKeyPath(key_path)

    async def _adb_connect(self, host: str):
        """Return a connected AdbDeviceTcpAsync instance."""
        from adb_shell.adb_device_async import AdbDeviceTcpAsync

        signer = await self.hass.async_add_executor_job(self._get_adb_signer_sync)
        device = AdbDeviceTcpAsync(host, _ADB_PORT)
        await device.connect(rsa_keys=[signer], auth_timeout_s=15)
        return device

    async def _adb_screenshot(self, host: str) -> bytes | None:
        """Take a screenshot of an Android TV via ADB over TCP."""
        try:
            device = await self._adb_connect(host)
        except ImportError:
            _LOGGER.error("adb-shell not installed — cannot auto-pair")
            return None
        except Exception as exc:
            _LOGGER.error("ADB connect failed for %s: %s", host, exc)
            return None

        try:
            png_bytes = await device.exec_out(
                "screencap -p", decode=False, timeout_s=15
            )
            return png_bytes if png_bytes else None
        except Exception as exc:
            _LOGGER.error("ADB screenshot failed for %s: %s", host, exc)
            return None
        finally:
            await device.close()

    async def _adb_power_off(self, host: str) -> None:
        """Turn the Android TV back off via ADB."""
        try:
            device = await self._adb_connect(host)
            try:
                # KEYCODE_SLEEP puts the TV into standby
                await device.shell("input keyevent 26", timeout_s=5)
                _LOGGER.info("Sent power-off to %s", host)
            finally:
                await device.close()
        except Exception as exc:
            _LOGGER.warning("Could not power off %s: %s", host, exc)

    async def _read_pin_from_image(self, png_bytes: bytes) -> str | None:
        """Send a screenshot to Claude vision and extract the PIN text."""
        from .const import (
            ANTHROPIC_API_VERSION,
            DEFAULT_ANTHROPIC_HOST,
            DEFAULT_ANTHROPIC_MODEL,
            MESSAGES_ENDPOINT,
        )

        import aiohttp

        api_key = self._api_key
        model = self._model or DEFAULT_ANTHROPIC_MODEL
        if not api_key:
            _LOGGER.error("No Anthropic API key configured — cannot read PIN via vision")
            return None

        b64_image = base64.b64encode(png_bytes).decode("ascii")

        payload = {
            "model": model,
            "max_tokens": 64,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "This is a screenshot of an Android TV showing a "
                                "device pairing PIN code. Read the PIN exactly as "
                                "shown. Reply with ONLY the PIN characters, nothing else."
                            ),
                        },
                    ],
                }
            ],
        }

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
        }

        try:
            session = async_get_clientsession(self.hass)
            async with session.post(
                f"{DEFAULT_ANTHROPIC_HOST}{MESSAGES_ENDPOINT}",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.error("Vision API failed (%s): %s", resp.status, body[:200])
                    return None

                data = await resp.json()
                text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text += block.get("text", "")

                pin = text.strip()
                _LOGGER.info("Vision API extracted PIN: %s", pin)
                return pin if pin else None

        except Exception as exc:
            _LOGGER.error("Vision API request failed: %s", exc)
            return None

    async def auto_pair_android(self, flow_id: str) -> dict[str, Any]:
        """Fully automatic Android TV pairing — handles TV being off.

        1. WoL magic packet → power on the TV
        2. Poll until ADB port 5555 is reachable
        3. Accept discovery flow → TV displays PIN on screen
        4. ADB screencap → capture the PIN
        5. Claude vision → read the PIN text
        6. Submit PIN → complete HA integration
        7. ADB power off → put the TV back to sleep
        """
        host = self._get_flow_host(flow_id)
        if not host:
            return {"error": "Could not determine device IP from flow"}

        mac = self._get_flow_mac(flow_id)
        name = "Android TV"
        for flow in self.hass.config_entries.flow.async_progress():
            if flow["flow_id"] == flow_id:
                name = flow.get("context", {}).get(
                    "title_placeholders", {}
                ).get("name", name)
                break

        steps: list[str] = []

        # Step 1: Wake the TV
        if mac:
            _LOGGER.info("Sending WoL to %s (%s / %s)", name, mac, host)
            self._send_wol(mac)
            steps.append(f"wol_sent:{mac}")
        else:
            _LOGGER.warning("No MAC for %s — skipping WoL, hoping TV is on", name)

        # Step 2: Wait for ADB to become reachable
        _LOGGER.info("Waiting for ADB on %s:%d ...", host, _ADB_PORT)
        adb_ready = await self._wait_for_adb(host)
        if not adb_ready:
            return {
                "error": f"TV at {host} did not become reachable within {_ADB_BOOT_TIMEOUT}s",
                "steps": steps,
            }
        steps.append("adb_ready")

        # Step 3: Accept discovery flow → TV shows PIN
        accept_result = await self.accept_flow(flow_id)
        if accept_result.get("type") == "create_entry":
            accept_result["steps"] = steps + ["already_paired"]
            return accept_result

        if accept_result.get("step_id") != "pair":
            return {
                "error": f"Unexpected step: {accept_result.get('step_id')}",
                "flow_result": accept_result,
                "steps": steps,
            }
        steps.append("pair_screen_shown")
        _LOGGER.info("%s is showing PIN — waiting %ds then capturing", name, _ADB_SCREENSHOT_DELAY)

        # Step 4: Wait for PIN to render, then ADB screenshot
        await asyncio.sleep(_ADB_SCREENSHOT_DELAY)
        png = await self._adb_screenshot(host)
        if not png:
            return {
                "error": "ADB screenshot failed — submit PIN manually",
                "flow_id": flow_id,
                "step_id": "pair",
                "steps": steps,
            }
        steps.append(f"screenshot_captured:{len(png)}b")

        # Step 5: Claude vision → extract PIN
        pin = await self._read_pin_from_image(png)
        if not pin:
            return {
                "error": "Could not read PIN from screenshot — submit PIN manually",
                "flow_id": flow_id,
                "step_id": "pair",
                "steps": steps,
            }
        steps.append(f"pin_extracted:{pin}")

        # Step 6: Submit PIN → complete pairing
        _LOGGER.info("Auto-submitting PIN '%s' for %s at %s", pin, name, host)
        pair_result = await self.submit_pin(flow_id, pin)
        steps.append(f"pair_result:{pair_result.get('type')}")

        # Step 7: Power the TV back off
        _LOGGER.info("Pairing done — powering off %s", name)
        await self._adb_power_off(host)
        steps.append("powered_off")

        pair_result["auto_paired"] = True
        pair_result["pin_used"] = pin
        pair_result["steps"] = steps
        return pair_result

    # ── Cast known_hosts sync ────────────────────────────────────

    async def sync_cast_known_hosts(self) -> dict[str, Any]:
        """Add discovered Cast device IPs to Cast's known_hosts config.

        Cast relies on mDNS to discover devices, which is unreliable in Docker.
        Adding IPs to known_hosts forces Cast to always create entities for them.
        Discovers IPs by scanning for Cast devices on the network.
        """
        cast_entries = self.hass.config_entries.async_entries("cast")
        if not cast_entries:
            return {"updated": False, "reason": "no cast integration"}

        cast_entry = cast_entries[0]
        current_hosts = list(cast_entry.data.get("known_hosts", []))

        # Collect IPs from Android TV Remote entries (same physical devices)
        new_hosts: set[str] = set()
        for ce in self.hass.config_entries.async_entries("androidtv_remote"):
            host = ce.data.get("host")
            if host and host not in current_hosts:
                new_hosts.add(host)

        # Also scan the subnet for Cast-capable devices on port 8008 (Cast HTTP)
        if current_hosts or new_hosts:
            ref_ip = next(iter(new_hosts or current_hosts))
            subnet = ".".join(ref_ip.split(".")[:3])

            async def _probe_cast(ip: str) -> str | None:
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip, 8009), timeout=1.0
                    )
                    writer.close()
                    await writer.wait_closed()
                    return ip
                except (OSError, asyncio.TimeoutError):
                    return None

            tasks = [_probe_cast(f"{subnet}.{i}") for i in range(2, 255)
                     if f"{subnet}.{i}" not in current_hosts]
            for batch_start in range(0, len(tasks), 50):
                batch = tasks[batch_start:batch_start + 50]
                results = await asyncio.gather(*batch)
                for ip in results:
                    if ip:
                        new_hosts.add(ip)

        if not new_hosts:
            return {"updated": False, "reason": "no new hosts found"}

        # Update Cast config entry with new known_hosts
        updated_hosts = current_hosts + sorted(new_hosts)
        new_data = dict(cast_entry.data)
        new_data["known_hosts"] = updated_hosts

        self.hass.config_entries.async_update_entry(cast_entry, data=new_data)
        _LOGGER.info("Updated Cast known_hosts: %s", updated_hosts)

        # Reload Cast to pick up the new hosts
        await self.hass.config_entries.async_reload(cast_entry.entry_id)

        return {"updated": True, "added_hosts": sorted(new_hosts), "total_hosts": updated_hosts}

    # ── Network discovery & auto-setup ────────────────────────────

    async def _trigger_active_discovery(self) -> list[dict[str, Any]]:
        """Actively start config flows for integrations HA should know about.

        Instead of waiting for passive mDNS/SSDP (unreliable in Docker),
        we check device counts across related integration pairs. If one
        domain has more devices than its partner, we start flows to close
        the gap (e.g., 2 Cast devices but only 1 androidtv_remote → start 1 flow).

        Also checks for common integrations that should be present if
        certain devices exist.
        """
        from .const import PROTECTED_DOMAINS

        initiated: list[dict[str, Any]] = []

        # Related integration pairs — if one exists, the other should too
        _RELATED_PAIRS: list[tuple[str, str]] = [
            ("cast", "androidtv_remote"),
        ]

        # Count devices per integration domain
        dev_reg = dr.async_get(self.hass)
        domain_devices: dict[str, list[str]] = {}  # domain → list of device names
        for device in dev_reg.devices.values():
            for ident_domain, _ in device.identifiers:
                domain_devices.setdefault(ident_domain, [])
                domain_devices[ident_domain].append(device.name or str(device.id))

        # Count config entries per domain
        domain_entries: dict[str, int] = {}
        for ce in self.hass.config_entries.async_entries():
            domain_entries[ce.domain] = domain_entries.get(ce.domain, 0) + 1

        # For each related pair, check if one side has more devices than the other has entries
        for domain_a, domain_b in _RELATED_PAIRS:
            devices_a = domain_devices.get(domain_a, [])
            entries_b = domain_entries.get(domain_b, 0)

            # If domain_a has more devices than domain_b has entries, start flows
            gap = len(devices_a) - entries_b
            if gap > 0:
                _LOGGER.info(
                    "Active discovery: %s has %d devices, %s has %d entries — starting %d flow(s)",
                    domain_a, len(devices_a), domain_b, entries_b, gap,
                )
                for _ in range(gap):
                    try:
                        result = await self.hass.config_entries.flow.async_init(
                            domain_b, context={"source": "user"},
                        )
                        initiated.append({
                            "domain": domain_b,
                            "reason": f"partner for {domain_a} device",
                            "flow_id": result.get("flow_id", ""),
                            "type": result.get("type", ""),
                        })
                    except Exception as exc:
                        _LOGGER.debug("Active discovery failed for %s: %s", domain_b, exc)

            # Check reverse direction too
            devices_b = domain_devices.get(domain_b, [])
            entries_a = domain_entries.get(domain_a, 0)
            gap_rev = len(devices_b) - entries_a
            if gap_rev > 0:
                _LOGGER.info(
                    "Active discovery: %s has %d devices, %s has %d entries — starting %d flow(s)",
                    domain_b, len(devices_b), domain_a, entries_a, gap_rev,
                )
                for _ in range(gap_rev):
                    try:
                        result = await self.hass.config_entries.flow.async_init(
                            domain_a, context={"source": "user"},
                        )
                        initiated.append({
                            "domain": domain_a,
                            "reason": f"partner for {domain_b} device",
                            "flow_id": result.get("flow_id", ""),
                            "type": result.get("type", ""),
                        })
                    except Exception as exc:
                        _LOGGER.debug("Active discovery failed for %s: %s", domain_a, exc)

        return initiated

    async def discover_network_devices(self) -> dict[str, Any]:
        """Full network status: discovered, configured, and available integrations.

        First triggers active discovery to find devices that passive mDNS/SSDP
        might miss (especially in Docker), then reports full status.

        Returns:
            discovered: Pending HA config flows annotated with KNOWN_INTEGRATIONS metadata
            configured: Already-set-up integrations matched against KNOWN_INTEGRATIONS
            available: Known integrations not yet found (cloud/manual ones user could add)
            active_initiated: Flows started by active discovery
            summary: counts
        """
        from .const import KNOWN_INTEGRATIONS, PROTECTED_DOMAINS, DiscoveryMethod

        # Active discovery — start flows for devices we know about but aren't fully configured
        active_initiated = await self._trigger_active_discovery()

        # ── Discovered (pending config flows from SSDP/mDNS + active) ──
        progress = self.hass.config_entries.flow.async_progress()
        discovered: list[dict[str, Any]] = []
        discovered_domains: set[str] = set()
        for flow in progress:
            handler = flow.get("handler", "")
            discovered_domains.add(handler)
            entry: dict[str, Any] = {
                "flow_id": flow["flow_id"],
                "handler": handler,
                "step_id": flow.get("step_id", ""),
                "context": {
                    k: v
                    for k, v in flow.get("context", {}).items()
                    if k in ("source", "unique_id", "title_placeholders")
                },
            }
            # Annotate with known integration metadata
            info = KNOWN_INTEGRATIONS.get(handler)
            if info:
                entry["known"] = {
                    "name": info.name,
                    "category": info.category.value,
                    "discovery": info.discovery.value,
                    "source": info.source.value,
                    "brands": info.brands,
                }
            discovered.append(entry)

        # ── Configured (existing config entries matched against registry) ──
        configured: list[dict[str, Any]] = []
        configured_domains: set[str] = set()
        for ce in self.hass.config_entries.async_entries():
            if ce.domain in PROTECTED_DOMAINS:
                continue
            configured_domains.add(ce.domain)
            item: dict[str, Any] = {
                "domain": ce.domain,
                "title": ce.title,
                "entry_id": ce.entry_id,
            }
            info = KNOWN_INTEGRATIONS.get(ce.domain)
            if info:
                item["known"] = {
                    "name": info.name,
                    "category": info.category.value,
                    "discovery": info.discovery.value,
                    "source": info.source.value,
                    "brands": info.brands,
                }
            configured.append(item)

        # ── Available (known integrations not yet discovered or configured) ──
        available: list[dict[str, Any]] = []
        for domain, info in KNOWN_INTEGRATIONS.items():
            if domain in discovered_domains or domain in configured_domains:
                continue
            available.append({
                "domain": domain,
                "name": info.name,
                "category": info.category.value,
                "discovery": info.discovery.value,
                "source": info.source.value,
                "brands": info.brands,
                "notes": info.notes,
            })

        return {
            "discovered": discovered,
            "configured": configured,
            "available": available,
            "active_initiated": active_initiated,
            "summary": {
                "discovered_count": len(discovered),
                "configured_count": len(configured),
                "available_count": len(available),
                "active_initiated_count": len(active_initiated),
            },
        }

    def _get_existing_config_data(self, domain: str) -> dict[str, Any] | None:
        """Return config data from an existing entry for the same domain (config cloning)."""
        for ce in self.hass.config_entries.async_entries(domain):
            if ce.data:
                return dict(ce.data)
        return None

    async def auto_setup_discovered(self) -> dict[str, Any]:
        """Auto-accept ALL pending discovery flows — aggressive mode.

        Strategy:
          1. Android TV flows → routes to auto_pair_android()
          2. All other flows → try accept_flow() with empty input
          3. If multi-step, try config cloning from an already-configured entry
             of the same domain (same device type = same config)
          4. If still can't complete, skip for user action

        Returns: {accepted: [...], skipped: [...], failed: [...]}
        """
        from .const import KNOWN_INTEGRATIONS, PROTECTED_DOMAINS

        progress = self.hass.config_entries.flow.async_progress()

        accepted: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []

        for flow in progress:
            handler = flow.get("handler", "")
            flow_id = flow["flow_id"]
            step_id = flow.get("step_id", "")

            # Never auto-accept flows for protected/system integrations
            if handler in PROTECTED_DOMAINS:
                continue

            try:
                # Android TV — handle both discovery-initiated (has host) and user-initiated (needs host)
                if handler in _ADB_HANDLERS:
                    host = self._get_flow_host(flow_id)
                    if host:
                        # Discovery-initiated flow — standard ADB auto-pair
                        _LOGGER.info("Auto-pairing Android TV flow %s (host: %s)", flow_id, host)
                        result = await self.auto_pair_android(flow_id)
                    elif step_id == "user":
                        # User-initiated flow (from active discovery) — needs a host
                        # Scan subnet for unconfigured Android TVs
                        new_hosts = await self._find_unconfigured_android_tvs()
                        if new_hosts:
                            host_to_use = new_hosts[0]
                            _LOGGER.info("Auto-setup: submitting host %s for androidtv_remote flow %s", host_to_use, flow_id)
                            # Submit the host to the user step
                            host_result = await self.configure_step(flow_id, {"host": host_to_use})
                            if host_result.get("type") == "create_entry":
                                accepted.append({"handler": handler, "flow_id": flow_id, "title": host_result.get("title", handler)})
                                continue
                            elif host_result.get("step_id") == "pair":
                                # Now we're at the pair step — do ADB auto-pair
                                new_flow_id = host_result.get("flow_id", flow_id)
                                _LOGGER.info("Android TV pair step reached for %s, capturing PIN...", host_to_use)
                                # Wait for PIN to render, screenshot, vision, submit
                                await asyncio.sleep(_ADB_SCREENSHOT_DELAY)
                                png = await self._adb_screenshot(host_to_use)
                                if png:
                                    pin = await self._read_pin_from_image(png)
                                    if pin:
                                        pair_result = await self.submit_pin(new_flow_id, pin)
                                        if pair_result.get("type") == "create_entry":
                                            accepted.append({"handler": handler, "flow_id": flow_id, "title": pair_result.get("title", handler)})
                                            await self._adb_power_off(host_to_use)
                                            continue
                                        else:
                                            failed.append({"handler": handler, "flow_id": flow_id, "error": f"PIN submit failed: {pair_result}"})
                                            continue
                                # Couldn't auto-complete pairing
                                skipped.append({"handler": handler, "flow_id": flow_id, "reason": f"pair step — needs manual PIN for {host_to_use}"})
                                continue
                            else:
                                result = host_result
                        else:
                            skipped.append({"handler": handler, "flow_id": flow_id, "reason": "no unconfigured Android TVs found on subnet"})
                            continue
                    else:
                        result = {"error": f"unexpected step: {step_id}"}

                    if result.get("error"):
                        failed.append({"handler": handler, "flow_id": flow_id, "error": result["error"]})
                    else:
                        accepted.append({"handler": handler, "flow_id": flow_id, "title": result.get("title", handler)})
                    continue

                # Try standard single-step accept (works for most auto-discovered devices)
                _LOGGER.info("Auto-setup: trying to accept %s flow %s", handler, flow_id)
                result = await self.accept_flow(flow_id)

                if result.get("type") == "create_entry":
                    accepted.append({"handler": handler, "flow_id": flow_id, "title": result.get("title", handler)})
                    continue

                # Multi-step flow — try config cloning from a similar device
                if result.get("step_id"):
                    clone_data = self._get_existing_config_data(handler)
                    if clone_data:
                        _LOGGER.info(
                            "Auto-setup: cloning config from existing %s entry for flow %s",
                            handler, result.get("flow_id", flow_id),
                        )
                        try:
                            clone_result = await self.configure_step(
                                result.get("flow_id", flow_id), clone_data
                            )
                            if clone_result.get("type") == "create_entry":
                                accepted.append({
                                    "handler": handler,
                                    "flow_id": flow_id,
                                    "title": clone_result.get("title", handler),
                                    "cloned": True,
                                })
                                continue
                        except Exception as clone_exc:
                            _LOGGER.debug("Config clone failed for %s: %s", handler, clone_exc)

                    skipped.append({
                        "handler": handler,
                        "flow_id": result.get("flow_id", flow_id),
                        "reason": f"requires step: {result['step_id']}",
                    })
                else:
                    failed.append({"handler": handler, "flow_id": flow_id, "error": str(result)})

            except Exception as exc:
                _LOGGER.error("Auto-setup failed for %s: %s", handler, exc)
                failed.append({"handler": handler, "flow_id": flow_id, "error": str(exc)})

        _LOGGER.info(
            "Auto-setup complete: %d accepted, %d skipped, %d failed",
            len(accepted), len(skipped), len(failed),
        )

        return {"accepted": accepted, "skipped": skipped, "failed": failed}

    # ── Area auto-assignment ────────────────────────────────────

    async def auto_assign_areas(self) -> dict[str, Any]:
        """Match device names to existing HA areas and assign them.

        Uses case-insensitive substring matching:
          - Device named "basement" → area "basement"
          - Device named "Kitchen" → area "Kitchen"
          - Device named "Living Room Sonos" → area "Living Room"
        Only assigns devices that don't already have an area.
        """
        from homeassistant.helpers import area_registry as ar

        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)

        # Build area lookup: lowercase name → area_id
        areas = {area.name.lower(): area.id for area in area_reg.async_list_areas()}

        assigned: list[dict[str, str]] = []

        from .const import DOMAIN
        hub_id = (DOMAIN, "selora_ai_hub")

        for device in dev_reg.devices.values():
            if device.area_id:
                continue  # already assigned
            # Skip the Selora AI Hub — it's whole-home, not room-specific
            if hub_id in device.identifiers:
                continue
            name = (device.name or "").lower()
            if not name:
                continue

            # Try exact match first, then substring match
            matched_area_id = None
            matched_area_name = None
            for area_name, area_id in areas.items():
                if area_name == name or area_name in name or name in area_name:
                    matched_area_id = area_id
                    matched_area_name = area_name
                    break

            if matched_area_id:
                dev_reg.async_update_device(device.id, area_id=matched_area_id)
                assigned.append({
                    "device": device.name or "",
                    "area": matched_area_name or "",
                })
                _LOGGER.info("Auto-assigned device '%s' to area '%s'", device.name, matched_area_name)

        return {"assigned": assigned}

    async def generate_dashboard(self) -> dict[str, Any]:
        """Auto-generate a Lovelace dashboard config based on discovered devices.

        Builds sections per area, with media player / light / switch cards.
        Writes to HA's .storage/lovelace.lovelace so it becomes the default dashboard.
        """
        from homeassistant.helpers import area_registry as ar, entity_registry as ent_r

        dev_reg = dr.async_get(self.hass)
        ent_reg = ent_r.async_get(self.hass)
        area_reg = ar.async_get(self.hass)

        # Controllable entity domains
        controllable = {"media_player", "light", "switch", "climate", "cover", "fan", "lock", "vacuum"}

        # Build area_id → area_name lookup
        area_names = {a.id: a.name for a in area_reg.async_list_areas()}

        # Group entities by area (inherit area from device if entity has none)
        area_entities: dict[str, list[str]] = {}  # area_name → [entity_ids]
        unassigned: list[str] = []

        for entity in ent_reg.entities.values():
            domain = entity.entity_id.split(".")[0]
            if domain not in controllable:
                continue
            if entity.disabled_by or entity.hidden_by:
                continue

            # Determine area: entity area > device area
            area_id = entity.area_id
            if not area_id and entity.device_id:
                device = dev_reg.async_get(entity.device_id)
                if device:
                    area_id = device.area_id

            if area_id and area_id in area_names:
                area_name = area_names[area_id]
                area_entities.setdefault(area_name, []).append(entity.entity_id)
            else:
                unassigned.append(entity.entity_id)

        # Build Lovelace cards
        cards: list[dict[str, Any]] = []

        # Selora AI Hub summary card
        cards.append({
            "type": "entities",
            "title": "Selora AI Hub",
            "entities": [
                "sensor.selora_ai_hub_devices",
                "sensor.selora_ai_hub_status",
                "sensor.selora_ai_hub_discovery",
            ],
        })

        # Area sections with device cards
        for area_name in sorted(area_entities):
            entity_ids = area_entities[area_name]
            area_cards: list[dict[str, Any]] = []
            for eid in sorted(entity_ids):
                domain = eid.split(".")[0]
                if domain == "media_player":
                    area_cards.append({"type": "media-control", "entity": eid})
                else:
                    area_cards.append({"type": "entity", "entity": eid})
            if area_cards:
                cards.append({
                    "type": "vertical-stack",
                    "title": area_name,
                    "cards": area_cards,
                })

        # Unassigned devices
        if unassigned:
            un_cards = []
            for eid in sorted(unassigned):
                domain = eid.split(".")[0]
                if domain == "media_player":
                    un_cards.append({"type": "media-control", "entity": eid})
                else:
                    un_cards.append({"type": "entity", "entity": eid})
            cards.append({
                "type": "vertical-stack",
                "title": "Other Devices",
                "cards": un_cards,
            })

        # Write the dashboard config
        dashboard = {
            "version": 1,
            "minor_version": 1,
            "key": "lovelace",
            "data": {
                "config": {
                    "title": "Home",
                    "views": [
                        {
                            "path": "default_view",
                            "title": "Overview",
                            "cards": cards,
                        }
                    ],
                },
            },
        }

        import json, os
        storage_path = os.path.join(self.hass.config.path(), ".storage", "lovelace")
        # Only write if no custom dashboard exists yet
        if not os.path.exists(storage_path):
            await self.hass.async_add_executor_job(
                self._write_dashboard, storage_path, dashboard
            )
            _LOGGER.info("Generated Selora AI dashboard with %d cards", len(cards))
            return {"generated": True, "cards": len(cards)}
        else:
            _LOGGER.info("Dashboard already exists — not overwriting")
            return {"generated": False, "reason": "dashboard already exists"}

    @staticmethod
    def _write_dashboard(path: str, data: dict) -> None:
        import json
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    # ── Reset & cleanup ──────────────────────────────────────────

    async def reset_integrations(self) -> dict[str, Any]:
        """Remove all config entries not in PROTECTED_DOMAINS."""
        from .const import PROTECTED_DOMAINS

        removed: list[str] = []
        entries = list(self.hass.config_entries.async_entries())
        for entry in entries:
            if entry.domain not in PROTECTED_DOMAINS:
                try:
                    await self.hass.config_entries.async_remove(entry.entry_id)
                    removed.append(f"{entry.domain}:{entry.title}")
                    _LOGGER.info("Reset removed integration: %s (%s)", entry.domain, entry.title)
                except Exception as exc:
                    _LOGGER.error("Failed to remove %s: %s", entry.domain, exc)

        return {"removed_integrations": removed}

    async def cleanup_mirror_devices(self) -> dict[str, Any]:
        """Remove Selora AI mirror devices + orphaned entities.

        Keeps only the Hub device and its core entities (status sensor + 4 action buttons).
        Removes everything else: stale accept/reject buttons, Turn On/Off, Kitchen Status, etc.
        """
        from .const import DOMAIN

        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)
        hub_id = (DOMAIN, "selora_ai_hub")

        # Unique IDs of entities we want to KEEP on the Hub
        _KEEP_UNIQUE_IDS = {
            "selora_ai_hub_status",            # status sensor
            "selora_ai_hub_device_list",       # device list sensor
            "selora_ai_hub_last_activity",     # last activity sensor
            "selora_ai_hub_discovery",         # discovery sensor
            f"{DOMAIN}_discover",              # discover button
            f"{DOMAIN}_auto_setup",            # auto setup button
            f"{DOMAIN}_cleanup",               # cleanup button
            f"{DOMAIN}_reset",                 # reset button
        }

        removed_devices: list[str] = []
        removed_entities: list[str] = []

        # 1. Remove non-Hub devices owned by our integration
        for device in list(dev_reg.devices.values()):
            if not any(ident[0] == DOMAIN for ident in device.identifiers):
                continue
            if hub_id in device.identifiers:
                continue

            for entity in er.async_entries_for_device(ent_reg, device.id, include_disabled_entities=True):
                ent_reg.async_remove(entity.entity_id)
                removed_entities.append(entity.entity_id)

            dev_reg.async_remove_device(device.id)
            removed_devices.append(device.name or device.id)

        # 2. Remove orphaned entities on the Hub that aren't core
        for entity in list(ent_reg.entities.values()):
            if entity.platform != DOMAIN:
                continue
            if entity.unique_id in _KEEP_UNIQUE_IDS:
                continue
            ent_reg.async_remove(entity.entity_id)
            removed_entities.append(entity.entity_id)

        if removed_devices or removed_entities:
            _LOGGER.info(
                "Cleaned up %d mirror devices, %d stale entities",
                len(removed_devices), len(removed_entities),
            )

        return {
            "removed_devices": removed_devices,
            "removed_entities": removed_entities,
        }

    @staticmethod
    def _normalise_result(result: dict[str, Any]) -> dict[str, Any]:
        """Trim a FlowResult to JSON-safe fields we care about."""
        out: dict[str, Any] = {
            "type": result.get("type", ""),
            "flow_id": result.get("flow_id", ""),
        }
        if result.get("step_id"):
            out["step_id"] = result["step_id"]
        if result.get("title"):
            out["title"] = result["title"]
        if result.get("description_placeholders"):
            out["description_placeholders"] = result["description_placeholders"]
        if result.get("errors"):
            out["errors"] = result["errors"]
        if result.get("result"):
            entry = result["result"]
            if hasattr(entry, "entry_id"):
                out["entry_id"] = entry.entry_id
                out["title"] = entry.title
        return out


def _json(data: Any, status: int = 200) -> Response:
    return Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status,
    )


def _get_flow_handler(hass: HomeAssistant, flow_id: str) -> str:
    """Look up the handler (domain) for a given flow_id."""
    for flow in hass.config_entries.flow.async_progress():
        if flow["flow_id"] == flow_id:
            return flow.get("handler", "")
    return ""


async def handle_devices_webhook(
    hass: HomeAssistant, webhook_id: str, request: Request
) -> Response:
    """Route device-management webhook requests by action."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json({"error": "Invalid JSON"}, 400)

    action = body.get("action", "").strip()
    if not action:
        return _json({"error": "Missing 'action' field"}, 400)

    from .const import DOMAIN  # local import to avoid circular ref

    # Find the DeviceManager stored during setup
    dm: DeviceManager | None = None
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and "device_manager" in entry_data:
            dm = entry_data["device_manager"]
            break

    if dm is None:
        return _json({"error": "DeviceManager not initialised"}, 503)

    try:
        if action == "list":
            devices = await dm.list_discovered()
            return _json({"discovered": devices})

        if action == "add":
            flow_id = body.get("flow_id", "")
            if not flow_id:
                return _json({"error": "Missing 'flow_id'"}, 400)

            # Auto-pair for Android TV devices
            handler = _get_flow_handler(hass, flow_id)
            if handler in _ADB_HANDLERS:
                _LOGGER.info("Auto-pair triggered for %s flow %s", handler, flow_id)
                result = await dm.auto_pair_android(flow_id)
                return _json(result)

            # Default: simple accept for non-Android devices
            result = await dm.accept_flow(flow_id)
            return _json(result)

        if action == "pair":
            flow_id = body.get("flow_id", "")
            pin = body.get("pin", "")
            if not flow_id or not pin:
                return _json({"error": "Missing 'flow_id' or 'pin'"}, 400)
            result = await dm.submit_pin(flow_id, pin)
            return _json(result)

        if action == "discover":
            domain = body.get("domain", "")
            host = body.get("host", "")
            # With domain+host: manual flow start (existing behavior)
            if domain and host:
                result = await dm.start_device_flow(domain, host)
                return _json(result)
            # No params: comprehensive network status
            result = await dm.discover_network_devices()
            return _json(result)

        if action == "auto_setup":
            result = await dm.auto_setup_discovered()
            return _json(result)

        if action == "configure":
            flow_id = body.get("flow_id", "")
            user_input = body.get("user_input", {})
            if not flow_id:
                return _json({"error": "Missing 'flow_id'"}, 400)
            result = await dm.configure_step(flow_id, user_input)
            return _json(result)

        if action == "reset":
            reset_result = await dm.reset_integrations()
            cleanup_result = await dm.cleanup_mirror_devices()
            # Wait for HA's SSDP/mDNS to re-discover devices on the network
            await asyncio.sleep(15)
            auto_result = await dm.auto_setup_discovered()
            return _json({**reset_result, **cleanup_result, "auto_setup": auto_result})

        if action == "cleanup":
            result = await dm.cleanup_mirror_devices()
            return _json(result)

        return _json({"error": f"Unknown action: {action}"}, 400)

    except Exception as exc:
        _LOGGER.error("Device webhook error (%s): %s", action, exc)
        return _json({"error": str(exc)}, 500)

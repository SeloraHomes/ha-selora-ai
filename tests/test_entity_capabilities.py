"""Tests for entity_capabilities -- domain profiles and entity classification."""

from __future__ import annotations

from custom_components.selora_ai.entity_capabilities import (
    COLLECTOR_DOMAINS,
    DOMAIN_PROFILES,
    SCENE_CAPABLE_DOMAINS,
    is_actionable_entity,
    is_scene_capable,
)


# ── Derived sets ────────────────────────────────────────────────────


class TestDerivedSets:
    def test_collector_domains_includes_expected(self) -> None:
        expected = {
            "light", "switch", "media_player", "climate", "fan", "cover",
            "lock", "vacuum", "sensor", "binary_sensor", "water_heater",
            "humidifier", "input_boolean", "input_select", "device_tracker",
            "person",
        }
        assert COLLECTOR_DOMAINS == expected

    def test_scene_capable_domains(self) -> None:
        expected = {"light", "switch", "media_player", "climate", "fan", "cover"}
        assert SCENE_CAPABLE_DOMAINS == expected

    def test_scene_capable_is_subset_of_collector(self) -> None:
        assert SCENE_CAPABLE_DOMAINS <= COLLECTOR_DOMAINS


# ── is_actionable_entity ────────────────────────────────────────────


class TestIsActionableEntity:
    def test_regular_light_passes(self) -> None:
        assert is_actionable_entity("light.kitchen") is True

    def test_status_led_excluded(self) -> None:
        assert is_actionable_entity("light.reolink_status_led") is False

    def test_ir_led_excluded(self) -> None:
        assert is_actionable_entity("light.camera_ir_led") is False

    def test_infrared_excluded(self) -> None:
        assert is_actionable_entity("light.front_door_infrared") is False

    def test_regular_switch_passes(self) -> None:
        assert is_actionable_entity("switch.living_room_outlet") is True

    def test_ftp_upload_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.reolink_kitchen_ftp_upload") is False

    def test_privacy_mode_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.reolink_kitchen_privacy_mode") is False

    def test_auto_tracking_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.reolink_kitchen_auto_tracking") is False

    def test_guard_return_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.reolink_kitchen_guard_return") is False

    def test_push_notifications_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.reolink_kitchen_push_notifications") is False

    def test_record_audio_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.reolink_kitchen_record_audio") is False

    def test_siren_on_event_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.reolink_kitchen_siren_on_event") is False

    def test_express_mode_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.fridge_express_mode") is False

    def test_smart_away_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.smart_bridge_smart_away") is False

    def test_firmware_update_switch_excluded(self) -> None:
        assert is_actionable_entity("switch.device_firmware_update") is False

    def test_sensor_passes_actionable(self) -> None:
        """Sensors have no exclude patterns — actionable check passes."""
        assert is_actionable_entity("sensor.temperature") is True

    def test_unknown_domain_passes(self) -> None:
        assert is_actionable_entity("alarm_control_panel.home") is True


# ── is_scene_capable ────────────────────────────────────────────────


class TestIsSceneCapable:
    def test_light_is_scene_capable(self) -> None:
        assert is_scene_capable("light.kitchen") is True

    def test_switch_is_scene_capable(self) -> None:
        assert is_scene_capable("switch.living_room_outlet") is True

    def test_media_player_is_scene_capable(self) -> None:
        assert is_scene_capable("media_player.tv") is True

    def test_climate_is_scene_capable(self) -> None:
        assert is_scene_capable("climate.thermostat") is True

    def test_fan_is_scene_capable(self) -> None:
        assert is_scene_capable("fan.bedroom") is True

    def test_cover_is_scene_capable(self) -> None:
        assert is_scene_capable("cover.blinds") is True

    def test_sensor_not_scene_capable(self) -> None:
        assert is_scene_capable("sensor.temperature") is False

    def test_binary_sensor_not_scene_capable(self) -> None:
        assert is_scene_capable("binary_sensor.motion") is False

    def test_lock_not_scene_capable(self) -> None:
        assert is_scene_capable("lock.front_door") is False

    def test_vacuum_not_scene_capable(self) -> None:
        assert is_scene_capable("vacuum.roborock") is False

    def test_unknown_domain_not_scene_capable(self) -> None:
        assert is_scene_capable("camera.front_door") is False

    def test_config_switch_not_scene_capable(self) -> None:
        assert is_scene_capable("switch.reolink_kitchen_ftp_upload") is False

    def test_status_led_not_scene_capable(self) -> None:
        assert is_scene_capable("light.reolink_status_led") is False

    def test_appliance_config_not_scene_capable(self) -> None:
        assert is_scene_capable("switch.fridge_express_mode") is False


# ── DomainProfile consistency ───────────────────────────────────────


class TestDomainProfileConsistency:
    def test_all_scene_capable_domains_are_collected(self) -> None:
        for domain in SCENE_CAPABLE_DOMAINS:
            assert DOMAIN_PROFILES[domain].collect, (
                f"Scene-capable domain {domain} must also have collect=True"
            )

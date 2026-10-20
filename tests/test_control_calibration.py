"""The write of the room temperature into the valves."""

from __future__ import annotations

from custom_components.room_climate.control import calibration_values, feed_entity_id
from custom_components.room_climate.models import RoomConfig, TemperatureSource


def room(**overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": "front_room",
        "name": "Front Room",
        "valves": ("climate.trv_front_room",),
        "sources": (TemperatureSource(entity_id="sensor.front_room_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


def test_the_feed_entity_follows_the_valve_object_id() -> None:
    assert (
        feed_entity_id("climate.trv_front_room")
        == "number.trv_front_room_external_measured_room_sensor"
    )
    assert (
        feed_entity_id("climate.0x0011223344556677")
        == "number.0x0011223344556677_external_measured_room_sensor"
    )


def test_the_room_temperature_is_scaled() -> None:
    assert calibration_values(room(), 21.5, {}) == {
        "number.trv_front_room_external_measured_room_sensor": 2150
    }


def test_a_custom_scale_is_honoured() -> None:
    config = room(calibration_scale=10)
    assert calibration_values(config, 21.5, {}) == {
        "number.trv_front_room_external_measured_room_sensor": 215
    }


def test_the_value_clamps_to_the_valve_band() -> None:
    assert calibration_values(room(), 1.0, {}) == {
        "number.trv_front_room_external_measured_room_sensor": 500
    }
    assert calibration_values(room(), 40.0, {}) == {
        "number.trv_front_room_external_measured_room_sensor": 3500
    }


def test_a_small_change_is_not_written() -> None:
    written = {"number.trv_front_room_external_measured_room_sensor": 2150}
    assert calibration_values(room(), 21.55, written) == {}


def test_a_larger_change_is_written() -> None:
    written = {"number.trv_front_room_external_measured_room_sensor": 2100}
    assert calibration_values(room(), 21.5, written) == {
        "number.trv_front_room_external_measured_room_sensor": 2150
    }


def test_a_room_without_a_temperature_writes_nothing() -> None:
    assert calibration_values(room(), None, {}) == {}


def test_a_room_with_the_calibration_off_writes_nothing() -> None:
    config = room(calibration="none", valve_mode="boost")
    assert calibration_values(config, 21.5, {}) == {}


def test_every_valve_of_the_room_receives_the_value() -> None:
    config = room(valves=("climate.trv_left", "climate.trv_right"))
    assert set(calibration_values(config, 21.0, {})) == {
        "number.trv_left_external_measured_room_sensor",
        "number.trv_right_external_measured_room_sensor",
    }

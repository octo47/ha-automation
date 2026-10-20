"""The demand latch, the window suspension and the fallback demand."""

from __future__ import annotations

import pytest

from custom_components.room_climate.control import next_calling
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


@pytest.mark.parametrize(
    ("temperature", "previous", "expected"),
    [(20.7, False, True), (20.8, False, False), (21.0, True, True), (21.21, True, False)],
)
def test_the_latch_band(temperature: float, previous: bool, expected: bool) -> None:
    assert next_calling(previous, room(), 21.0, "heat", temperature, False, False) is expected


@pytest.mark.parametrize(
    ("temperature", "target", "mode"),
    [(21.0, None, "heat"), (21.0, 5.0, "heat"), (21.0, 21.0, "off"), (None, 21.0, "heat")],
)
def test_a_room_that_cannot_heat_never_calls(
    temperature: float | None, target: float | None, mode: str
) -> None:
    assert next_calling(False, room(), target, mode, temperature, False, False) is False


def test_a_window_suspends_the_demand() -> None:
    assert next_calling(True, room(), 21.0, "heat", 19.0, True, False) is False


def test_the_valve_demand_carries_a_room_without_a_temperature() -> None:
    assert next_calling(False, room(), 21.0, "heat", None, False, True) is True


def test_the_valve_demand_does_not_override_a_live_temperature() -> None:
    assert next_calling(False, room(), 21.0, "heat", 21.5, False, True) is False

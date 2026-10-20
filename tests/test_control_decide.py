"""The boiler decision, the timing guards and the cycle status."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.room_climate.control import decide, exponential_average
from custom_components.room_climate.models import (
    ControlInput,
    HouseConfig,
    RoomConfig,
    RuntimeRoom,
    TemperatureSource,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def room(room_id: str, **overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": room_id,
        "name": room_id.title(),
        "valves": (f"climate.trv_{room_id}",),
        "sources": (TemperatureSource(entity_id=f"sensor.{room_id}_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


def house(**overrides: object) -> HouseConfig:
    payload: dict[str, object] = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ("sensor.outdoor",),
        "rooms": (room("front_room"), room("back_room")),
    }
    payload.update(overrides)
    return HouseConfig(**payload)


def runtime(room_id: str, target: float | None = 21.0) -> RuntimeRoom:
    return RuntimeRoom(room_id=room_id, target_temperature=target, hvac_mode="heat", calling=False)


def payload(**overrides: object) -> ControlInput:
    base: dict[str, object] = {
        "now": NOW,
        "house": house(),
        "temperatures": {
            "sensor.front_room_temperature": 20.0,
            "sensor.back_room_temperature": 21.0,
            "sensor.outdoor": 8.0,
        },
        "valve_demands": {},
        "window_states": {},
        "runtimes": (runtime("front_room"), runtime("back_room")),
        "last_actuator_targets": {},
        "last_calibration_values": {},
        "last_command_at": {},
        "outdoor_lockout_active": False,
        "boiler_on": False,
        "boiler_state_since": None,
        "master_enabled": True,
        "override_active": False,
    }
    base.update(overrides)
    return ControlInput(**base)


def test_one_calling_room_turns_the_boiler_on() -> None:
    output = decide(payload())
    assert output.boiler.turn_on is True
    assert output.boiler.reason == "room_demand"
    assert output.boiler.send is True


def test_the_calling_room_commands_its_valve() -> None:
    output = decide(payload())
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.calling is True
    assert front_room.valve_targets == {"climate.trv_front_room": 21.0}
    assert front_room.calibration_values == {"number.trv_front_room_external_measured_room_sensor": 2000}


def test_no_calling_room_keeps_the_boiler_off() -> None:
    output = decide(
        payload(
            temperatures={
                "sensor.front_room_temperature": 22.0,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            }
        )
    )
    assert output.boiler.turn_on is False
    assert output.boiler.reason == "no_demand"
    assert output.boiler.send is False


def test_the_last_room_stopping_turns_the_boiler_off() -> None:
    output = decide(
        payload(
            temperatures={
                "sensor.front_room_temperature": 22.0,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            },
            boiler_on=True,
            boiler_state_since=NOW - timedelta(minutes=10),
        )
    )
    assert output.boiler.turn_on is False
    assert output.boiler.send is True


def test_the_boiler_does_not_switch_off_inside_min_on() -> None:
    output = decide(
        payload(
            temperatures={
                "sensor.front_room_temperature": 22.0,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            },
            boiler_on=True,
            boiler_state_since=NOW - timedelta(seconds=60),
        )
    )
    assert output.boiler.send is False


def test_the_boiler_does_not_switch_on_inside_min_off() -> None:
    output = decide(payload(boiler_on=False, boiler_state_since=NOW - timedelta(seconds=60)))
    assert output.boiler.send is False


def test_a_lockout_stops_the_boiler_at_once_and_closes_the_valves() -> None:
    output = decide(
        payload(
            outdoor_lockout_active=True,
            boiler_on=True,
            boiler_state_since=NOW - timedelta(seconds=5),
        )
    )
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert output.boiler.turn_on is False
    assert output.boiler.reason == "lockout"
    assert output.boiler.send is True
    assert output.status == "locked_out"
    assert front_room.valve_targets == {"climate.trv_front_room": 5.0}


def test_the_override_bypasses_the_lockout() -> None:
    output = decide(payload(outdoor_lockout_active=True, override_active=True))
    assert output.boiler.turn_on is True


def test_the_master_switch_off_stops_the_boiler_at_once() -> None:
    output = decide(
        payload(master_enabled=False, boiler_on=True, boiler_state_since=NOW - timedelta(seconds=5))
    )
    assert output.boiler.send is True
    assert output.boiler.reason == "master_off"
    assert output.status == "disabled"


def test_a_window_open_closes_the_valve_without_waiting() -> None:
    config = house(
        rooms=(room("front_room", window_sensors=("binary_sensor.front_room_window",)), room("back_room"))
    )
    output = decide(
        payload(
            house=config,
            window_states={"binary_sensor.front_room_window": "open"},
            last_command_at={"climate.trv_front_room": NOW - timedelta(seconds=5)},
        )
    )
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.window_open is True
    assert front_room.calling is False
    assert front_room.valve_targets == {"climate.trv_front_room": 5.0}


def test_a_switch_style_window_sensor_also_suspends_the_demand() -> None:
    config = house(
        rooms=(room("front_room", window_sensors=("switch.sim_front_room_window",)), room("back_room"))
    )
    output = decide(payload(house=config, window_states={"switch.sim_front_room_window": "on"}))
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.window_open is True
    assert front_room.calling is False


def test_a_room_without_a_source_is_degraded_and_closed() -> None:
    output = decide(
        payload(
            temperatures={
                "sensor.front_room_temperature": None,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            }
        )
    )
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.calling is False
    assert front_room.degraded is True
    assert front_room.usable_sources == 0
    assert front_room.calibration_values == {}


def test_the_valve_demand_keeps_a_room_without_a_source_warm() -> None:
    config = house(
        rooms=(
            room("front_room", demand_fallback_sensors=("binary_sensor.trv_front_room_heat_required",)),
            room("back_room"),
        )
    )
    output = decide(
        payload(
            house=config,
            temperatures={
                "sensor.front_room_temperature": None,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            },
            valve_demands={"binary_sensor.trv_front_room_heat_required": True},
        )
    )
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.calling is True
    assert output.boiler.turn_on is True


def test_the_house_average_is_the_mean_of_the_room_averages() -> None:
    output = decide(payload())
    assert output.house_ema_temperature == pytest.approx(20.5)


@pytest.mark.parametrize(
    ("previous", "value", "elapsed", "expected"),
    [
        (None, 21.0, 30.0, 21.0),
        (20.0, 22.0, 0.0, 22.0),
        (20.0, 22.0, 1800.0, 20.0 + 2 * (1 - 0.36787944117)),
    ],
)
def test_the_graph_average(
    previous: float | None, value: float, elapsed: float, expected: float
) -> None:
    result = exponential_average(previous, value, elapsed, 1800.0)
    assert result == pytest.approx(expected, abs=1e-6)

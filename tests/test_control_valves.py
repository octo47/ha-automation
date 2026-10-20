"""The valve targets, the repeat rule and the command interval."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.room_climate.control import round_setpoint, valve_targets
from custom_components.room_climate.models import RoomConfig, TemperatureSource

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def room(**overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": "front_room",
        "name": "Front Room",
        "valves": ("climate.trv_front_room",),
        "sources": (TemperatureSource(entity_id="sensor.front_room_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


def targets(config: RoomConfig, **overrides: object) -> dict[str, float]:
    arguments: dict[str, object] = {
        "blocked": False,
        "calling": True,
        "target": 21.0,
        "last_sent": {},
        "last_command_at": {},
        "available": {},
        "now": NOW,
    }
    arguments.update(overrides)
    return dict(valve_targets(config, **arguments))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"), [(20.0, 20.0), (20.24, 20.0), (20.26, 20.5), (20.75, 21.0)]
)
def test_setpoints_round_to_the_half_degree(raw: float, expected: float) -> None:
    assert round_setpoint(raw) == expected


def test_modulate_holds_the_setpoint_even_when_satisfied() -> None:
    assert targets(room(), calling=False) == {"climate.trv_front_room": 21.0}


def test_boost_opens_above_the_setpoint_while_calling() -> None:
    assert targets(room(valve_mode="boost", open_offset=2.0)) == {"climate.trv_front_room": 23.0}


def test_boost_closes_when_satisfied() -> None:
    assert targets(room(valve_mode="boost"), calling=False) == {"climate.trv_front_room": 5.0}


def test_a_blocked_room_closes_its_valves() -> None:
    assert targets(room(), blocked=True) == {"climate.trv_front_room": 5.0}


def test_the_setpoint_clamps_to_the_room_limits() -> None:
    assert targets(room(max_temp=22.0), target=25.0) == {"climate.trv_front_room": 22.0}


def test_a_sent_target_is_not_repeated() -> None:
    assert targets(room(), last_sent={"climate.trv_front_room": 21.0}) == {}


def test_a_due_but_recent_command_waits_for_the_interval() -> None:
    sent_at = {"climate.trv_front_room": NOW - timedelta(seconds=60)}
    assert targets(room(), target=23.0, last_command_at=sent_at) == {}


def test_a_due_command_after_the_interval_is_sent() -> None:
    sent_at = {"climate.trv_front_room": NOW - timedelta(seconds=400)}
    assert targets(room(), target=23.0, last_command_at=sent_at) == {"climate.trv_front_room": 23.0}


def test_closing_a_valve_is_never_deferred() -> None:
    sent_at = {"climate.trv_front_room": NOW - timedelta(seconds=5)}
    assert targets(room(valve_mode="boost"), calling=False, last_command_at=sent_at) == {
        "climate.trv_front_room": 5.0
    }


def test_an_unavailable_valve_is_skipped_and_the_other_is_commanded() -> None:
    config = room(valves=("climate.trv_left", "climate.trv_right"))
    assert targets(config, target=22.0, available={"climate.trv_left": False}) == {
        "climate.trv_right": 22.0
    }


def test_every_valve_of_the_room_receives_the_target() -> None:
    config = room(valves=("climate.trv_left", "climate.trv_right"))
    assert targets(config, target=22.0) == {
        "climate.trv_left": 22.0,
        "climate.trv_right": 22.0,
    }

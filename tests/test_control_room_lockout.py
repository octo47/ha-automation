"""The lockout of one room, and the house lockout beside it."""

from __future__ import annotations

from datetime import timedelta

from custom_components.room_climate.control import (
    decide,
    next_lockout,
    outdoor_temperature,
    room_lockout_temperature,
    room_outdoor_sources,
)

from .test_control_decide import NOW, house, payload, room


def test_a_room_without_its_own_values_uses_the_house_values() -> None:
    config = house()
    front_room = config.rooms[0]
    assert room_outdoor_sources(config, front_room) == config.outdoor_sources
    assert room_lockout_temperature(config, front_room) == config.lockout_temperature


def test_a_room_with_its_own_values_uses_them() -> None:
    config = house(
        rooms=(
            room(
                "front_room",
                outdoor_sources=("sensor.room_outdoor",),
                lockout_temperature=21.5,
            ),
            room("back_room"),
        )
    )
    front_room = config.rooms[0]
    assert room_outdoor_sources(config, front_room) == ("sensor.room_outdoor",)
    assert room_lockout_temperature(config, front_room) == 21.5


def test_an_unusable_room_outdoor_reading_starts_no_lockout() -> None:
    config = house(
        rooms=(
            room(
                "front_room",
                outdoor_sources=("sensor.room_outdoor",),
                lockout_temperature=19.0,
            ),
            room("back_room"),
        )
    )
    front_room = config.rooms[0]
    outdoor = outdoor_temperature(
        room_outdoor_sources(config, front_room), {"sensor.room_outdoor": None}
    )
    assert outdoor is None
    assert (
        next_lockout(
            False,
            outdoor,
            room_lockout_temperature(config, front_room),
            config.lockout_hysteresis,
        )
        is False
    )


def test_a_locked_out_room_closes_every_valve_and_does_not_call() -> None:
    config = house(
        rooms=(
            room(
                "front_room",
                valves=("climate.trv_front_room_a", "climate.trv_front_room_b"),
            ),
            room("back_room"),
        )
    )
    output = decide(payload(house=config, room_lockouts={"front_room": True}))
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.locked_out is True
    assert front_room.calling is False
    assert front_room.valve_targets == {
        "climate.trv_front_room_a": 5.0,
        "climate.trv_front_room_b": 5.0,
    }


def test_a_locked_out_room_does_not_stop_the_boiler_of_the_other_room() -> None:
    output = decide(
        payload(
            room_lockouts={"front_room": True},
            temperatures={
                "sensor.front_room_temperature": 20.0,
                "sensor.back_room_temperature": 19.5,
                "sensor.outdoor": 8.0,
            },
        )
    )
    back_room = next(command for command in output.rooms if command.room_id == "back_room")
    assert back_room.locked_out is False
    assert back_room.calling is True
    assert back_room.valve_targets == {"climate.trv_back_room": 21.0}
    assert output.boiler.turn_on is True
    assert output.boiler.reason == "room_demand"


def test_the_override_bypasses_every_room_lockout() -> None:
    output = decide(
        payload(
            room_lockouts={"front_room": True, "back_room": True},
            override_active=True,
            temperatures={
                "sensor.front_room_temperature": 20.0,
                "sensor.back_room_temperature": 20.0,
                "sensor.outdoor": 8.0,
            },
        )
    )
    assert all(command.locked_out is False for command in output.rooms)
    assert any(command.calling for command in output.rooms)
    assert output.boiler.turn_on is True


def test_the_house_lockout_keeps_its_reason_status_and_immediate_command() -> None:
    output = decide(
        payload(
            outdoor_lockout_active=True,
            boiler_on=True,
            boiler_state_since=NOW - timedelta(seconds=5),
        )
    )
    assert output.boiler.turn_on is False
    assert output.boiler.reason == "lockout"
    assert output.boiler.send is True
    assert output.status == "locked_out"

"""The coordinator reads states, decides, and applies the commands."""

from __future__ import annotations

import os
import pathlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_SERVICE,
    ATTR_SERVICE_DATA,
    EVENT_CALL_SERVICE,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN
from custom_components.room_climate.schedule import schedule_path_name

from .test_sim import set_number, setup_bench

SERVICE_DOMAINS = ("climate", "number", "switch")

SCHEDULE_21 = 'default:\n  - time: "00:00"\n    temperature: 21.0\n'
SCHEDULE_23 = 'default:\n  - time: "00:00"\n    temperature: 23.0\n'
SCHEDULE_TWO_ROOMS = """
default:
  - time: "00:00"
    temperature: 18.0
groups:
  morning:
    - time: "00:00"
      temperature: 22.0
rooms:
  front_room: morning
"""

_SCHEDULE_BUMPS: dict[str, int] = {}


def write_schedule(hass: HomeAssistant, text: str, *, simulation: bool = False) -> pathlib.Path:
    """Write the schedule file of one entry with a strictly newer modification time."""
    path = pathlib.Path(hass.config.path(schedule_path_name(simulation)))
    path.write_text(text, encoding="utf-8")
    bump = _SCHEDULE_BUMPS.get(str(path), 0) + 1
    _SCHEDULE_BUMPS[str(path)] = bump
    stamp = path.stat().st_mtime_ns + bump * 1_000_000_000
    os.utime(path, ns=(stamp, stamp))
    return path


RECORDED: dict[HomeAssistant, list[dict[str, Any]]] = {}


async def enable_services(hass: HomeAssistant) -> None:
    """Load the service domains the coordinator commands, and record every call."""
    for domain in SERVICE_DOMAINS:
        await async_setup_component(hass, domain, {})
    RECORDED[hass] = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, _recorder(hass))


def _recorder(hass: HomeAssistant) -> Callable[[Event[Any]], None]:
    """Return a listener that appends every service call of one instance."""
    calls = RECORDED[hass]

    def record(event: Event[Any]) -> None:
        calls.append(dict(event.data))

    return record


def calls(hass: HomeAssistant, domain: str, service: str) -> list[dict[str, Any]]:
    """Return the service data of every recorded call of one service."""
    return [
        dict(record[ATTR_SERVICE_DATA])
        for record in RECORDED.get(hass, [])
        if record.get(ATTR_DOMAIN) == domain and record.get(ATTR_SERVICE) == service
    ]


def calls_for(hass: HomeAssistant, service: str, entity_id: str) -> list[dict[str, Any]]:
    """Return the recorded climate service calls that name one entity."""
    return [
        service_data
        for service_data in calls(hass, "climate", service)
        if service_data.get("entity_id") == entity_id
    ]


def options(**overrides: object) -> dict[str, object]:
    """The house options, which the options of the entry carry."""
    payload: dict[str, object] = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ["sensor.outdoor"],
    }
    payload.update(overrides)
    return payload


def house(**overrides: object) -> dict[str, object]:
    """The house settings, as the config flow writes them into the data of an entry."""
    payload: dict[str, object] = {
        "name": "Home",
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ["sensor.outdoor"],
    }
    payload.update(overrides)
    return payload


def room_subentry(
    room_id: str = "front_room", name: str = "Front Room", **overrides: object
) -> dict[str, object]:
    """One room subentry of the entry."""
    data: dict[str, object] = {
        "room_id": room_id,
        "name": name,
        "valves": [f"climate.trv_{room_id}"],
        "sources": [{"entity_id": f"sensor.{room_id}_temperature"}],
    }
    data.update(overrides)
    return {"data": data, "subentry_type": "room", "title": name, "unique_id": None}


async def setup_entry(
    hass: HomeAssistant,
    *,
    rooms: list[dict[str, object]] | None = None,
    house_data: dict[str, object] | None = None,
    **overrides: object,
) -> MockConfigEntry:
    """One loaded house entry, with its house in the data, or in its options.

    The config flow writes the first house into the data of the entry, and every later
    change into its options, so a test names the one it means.
    """
    await enable_services(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate",
        data=house_data or {},
        options={} if house_data is not None else options(**overrides),
        subentries_data=rooms if rooms is not None else [room_subentry()],
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.front_room_temperature", "20.0")
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.trv_front_room", "heat", {"temperature": 5.0})
    hass.states.async_set("number.trv_front_room_external_measured_room_sensor", "-8000")
    hass.states.async_set("climate.heating_heat", "off", {"temperature": 15.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def set_room_target(entry: MockConfigEntry, room_id: str, temperature: float) -> None:
    """Set the setpoint of one room by hand, without the climate entity."""
    coordinator = entry.runtime_data
    coordinator.set_manual_target(room_id, temperature)
    await coordinator.async_recompute()


async def test_a_call_for_heat_commands_the_valve_and_the_boiler(hass: HomeAssistant) -> None:
    entry = await setup_entry(
        hass, dry_run=False, rooms=[room_subentry(valve_command_interval_seconds=0)]
    )
    await set_room_target(entry, "front_room", 21.0)
    await hass.async_block_till_done()

    boiler = calls_for(hass, "set_temperature", "climate.heating_heat")
    assert boiler, "the boiler must be commanded"
    assert boiler[-1]["temperature"] == 31.0
    assert boiler[-1]["hvac_mode"] == "heat"

    valve = calls_for(hass, "set_temperature", "climate.trv_front_room")
    assert [call["temperature"] for call in valve][-1] == 21.0


async def test_the_calibration_feed_is_written(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=False)
    writes = calls(hass, "number", "set_value")
    assert any(call.get("value") == 2000 for call in writes)


async def test_dry_run_sends_no_command(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=True)
    await set_room_target(entry, "front_room", 21.0)
    await hass.async_block_till_done()
    assert calls_for(hass, "set_temperature", "climate.trv_front_room") == []
    assert calls_for(hass, "set_temperature", "climate.heating_heat") == []
    assert calls(hass, "number", "set_value") == []
    assert entry.runtime_data.output is not None


async def test_an_unavailable_valve_does_not_stop_the_other_valve(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.side_room_temperature", "19.0")
    hass.states.async_set("climate.trv_side_room_a", "unavailable")
    hass.states.async_set("climate.trv_side_room_b", "heat", {"temperature": 5.0})
    entry = await setup_entry(
        hass,
        dry_run=False,
        rooms=[
            room_subentry(
                "side_room",
                "Side Room",
                valves=["climate.trv_side_room_a", "climate.trv_side_room_b"],
            )
        ],
    )
    await set_room_target(entry, "side_room", 21.0)
    await hass.async_block_till_done()
    assert calls_for(hass, "set_temperature", "climate.trv_side_room_b")
    assert calls_for(hass, "set_temperature", "climate.trv_side_room_a") == []


async def test_a_room_outdoor_source_runs_a_cycle_and_holds_its_latch(
    hass: HomeAssistant,
) -> None:
    entry = await setup_entry(
        hass,
        dry_run=False,
        rooms=[
            room_subentry(
                outdoor_sources=["sensor.front_room_outdoor"],
                lockout_temperature=19.0,
            )
        ],
    )
    assert entry.runtime_data.room_lockouts["front_room"] is False

    hass.states.async_set("sensor.front_room_outdoor", "19.5")
    await hass.async_block_till_done()
    assert entry.runtime_data.room_lockouts["front_room"] is True

    hass.states.async_set("sensor.front_room_outdoor", "18.5")
    await hass.async_block_till_done()
    assert entry.runtime_data.room_lockouts["front_room"] is True


def two_rooms() -> list[dict[str, object]]:
    """One house of two rooms, each with the valve command interval off."""
    return [
        room_subentry("front_room", "Front Room", valve_command_interval_seconds=0),
        room_subentry(
            "back_room",
            "Back Room",
            valves=["climate.trv_back_room"],
            sources=[{"entity_id": "sensor.back_room_temperature"}],
            valve_command_interval_seconds=0,
        ),
    ]


def set_back_room_states(hass: HomeAssistant) -> None:
    """Give the second room of `two_rooms` a state the cycle can read."""
    hass.states.async_set("sensor.back_room_temperature", "20.0")
    hass.states.async_set("climate.trv_back_room", "heat", {"temperature": 5.0})


def last_temperature(hass: HomeAssistant, entity_id: str) -> float | None:
    """Return the temperature of the last command of one valve."""
    valve = calls_for(hass, "set_temperature", entity_id)
    if not valve:
        return None
    return valve[-1].get("temperature")  # type: ignore[return-value]


async def test_the_schedule_file_gives_the_room_its_setpoint(hass: HomeAssistant) -> None:
    write_schedule(hass, SCHEDULE_21)
    await setup_entry(hass, dry_run=False, rooms=[room_subentry(valve_command_interval_seconds=0)])
    await hass.async_block_till_done()
    assert last_temperature(hass, "climate.trv_front_room") == 21.0


async def test_a_saved_schedule_applies_within_one_cycle(hass: HomeAssistant) -> None:
    write_schedule(hass, SCHEDULE_21)
    entry = await setup_entry(
        hass, dry_run=False, rooms=[room_subentry(valve_command_interval_seconds=0)]
    )
    await hass.async_block_till_done()
    assert last_temperature(hass, "climate.trv_front_room") == 21.0

    write_schedule(hass, SCHEDULE_23)
    await hass.services.async_call("room_climate", "recompute", {})
    await hass.async_block_till_done()

    assert entry.runtime_data.runtimes["front_room"].target_temperature == 23.0
    assert last_temperature(hass, "climate.trv_front_room") == 23.0


async def test_a_room_follows_its_group_and_another_follows_the_default(
    hass: HomeAssistant,
) -> None:
    write_schedule(hass, SCHEDULE_TWO_ROOMS)
    set_back_room_states(hass)
    await setup_entry(hass, dry_run=False, rooms=two_rooms())
    await hass.async_block_till_done()

    assert last_temperature(hass, "climate.trv_front_room") == 22.0
    assert last_temperature(hass, "climate.trv_back_room") == 18.0


async def test_a_room_carries_its_own_schedule_in_place_of_its_group(
    hass: HomeAssistant,
) -> None:
    write_schedule(
        hass,
        """
default:
  - time: "00:00"
    temperature: 18.0
groups:
  morning:
    - time: "00:00"
      temperature: 22.0
rooms:
  front_room:
    - time: "00:00"
      temperature: 24.0
  back_room: morning
""",
    )
    set_back_room_states(hass)
    await setup_entry(hass, dry_run=False, rooms=two_rooms())
    await hass.async_block_till_done()

    assert last_temperature(hass, "climate.trv_front_room") == 24.0
    assert last_temperature(hass, "climate.trv_back_room") == 22.0


async def test_a_missing_schedule_file_leaves_every_room_on_the_fallback(
    hass: HomeAssistant,
) -> None:
    """A room always carries a target, so its thermostat offers the operator a control."""
    set_back_room_states(hass)
    entry = await setup_entry(hass, dry_run=True, rooms=two_rooms())
    assert hass.states.get("climate.room_climate_front_room") is not None
    for room_id in ("front_room", "back_room"):
        runtime = entry.runtime_data.runtimes[room_id]
        assert runtime.target_temperature == 20.0
        assert runtime.setpoint_source == "fallback"
    assert entry.runtime_data.schedule_error is None


async def test_the_house_fallback_setpoint_stands_in_for_a_room_with_no_setpoint(
    hass: HomeAssistant,
) -> None:
    entry = await setup_entry(hass, dry_run=True, house_data=house(fallback_setpoint=16.0))
    runtime = entry.runtime_data.runtimes["front_room"]
    assert runtime.target_temperature == 16.0
    assert runtime.setpoint_source == "fallback"


async def test_a_schedule_that_yields_nothing_in_seven_days_takes_the_fallback(
    hass: HomeAssistant, freezer: Any
) -> None:
    """The only block applies on a Wednesday morning, and the instant is before it."""
    freezer.move_to(datetime(2026, 11, 18, 12, 0, tzinfo=UTC))
    write_schedule(hass, 'default:\n  - time: "08:00"\n    temperature: 21.0\n    days: [wed]\n')
    entry = await setup_entry(hass, dry_run=True)
    runtime = entry.runtime_data.runtimes["front_room"]
    assert runtime.target_temperature == 20.0
    assert runtime.setpoint_source == "fallback"
    assert entry.runtime_data.schedule_error is not None


async def test_a_manual_set_holds_to_the_next_block_and_the_schedule_resumes(
    hass: HomeAssistant, freezer: Any
) -> None:
    freezer.move_to(datetime(2026, 11, 18, 12, 0, tzinfo=UTC))
    now = dt_util.now()
    later = (now + timedelta(hours=1)).strftime("%H:%M")
    write_schedule(
        hass,
        f'default:\n  - time: "00:00"\n    temperature: 21.0\n'
        f'  - time: "{later}"\n    temperature: 16.0\n',
    )
    entry = await setup_entry(
        hass, dry_run=False, rooms=[room_subentry(valve_command_interval_seconds=0)]
    )
    await hass.async_block_till_done()
    runtime = entry.runtime_data.runtimes["front_room"]
    assert runtime.target_temperature == 21.0
    assert runtime.setpoint_source == "schedule"
    assert runtime.next_change_at is not None
    assert runtime.next_change_temperature == 16.0

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.room_climate_front_room", "temperature": 24.0},
    )
    await hass.async_block_till_done()
    runtime = entry.runtime_data.runtimes["front_room"]
    assert runtime.target_temperature == 24.0
    assert runtime.setpoint_source == "manual"

    freezer.move_to(now + timedelta(hours=2))
    await hass.services.async_call("room_climate", "recompute", {})
    await hass.async_block_till_done()
    runtime = entry.runtime_data.runtimes["front_room"]
    assert runtime.target_temperature == 16.0
    assert runtime.setpoint_source == "schedule"


async def test_a_malformed_file_keeps_the_last_setpoint_and_reports_schedule_error(
    hass: HomeAssistant,
) -> None:
    write_schedule(hass, SCHEDULE_21)
    entry = await setup_entry(hass, dry_run=True)
    await hass.async_block_till_done()
    assert entry.runtime_data.runtimes["front_room"].target_temperature == 21.0

    write_schedule(hass, "default: [oops\n  - time: ")
    await hass.services.async_call("room_climate", "recompute", {})
    await hass.async_block_till_done()

    assert entry.runtime_data.runtimes["front_room"].target_temperature == 21.0
    assert entry.runtime_data.schedule_error is not None
    state = hass.states.get("sensor.room_climate_schedule_error")
    assert state is not None and state.state == entry.runtime_data.schedule_error


async def test_a_block_outside_the_room_band_is_refused_and_reported(
    hass: HomeAssistant,
) -> None:
    write_schedule(hass, 'default:\n  - time: "00:00"\n    temperature: 40.0\n')
    entry = await setup_entry(hass, dry_run=True)
    runtime = entry.runtime_data.runtimes["front_room"]
    assert runtime.target_temperature == 20.0
    assert runtime.setpoint_source == "fallback"
    error = entry.runtime_data.schedule_error
    assert error is not None and "front_room" in error and "40.0" in error


async def test_a_group_that_no_group_defines_is_reported(hass: HomeAssistant) -> None:
    write_schedule(hass, "rooms:\n  front_room: evening\n")
    entry = await setup_entry(hass, dry_run=True)
    runtime = entry.runtime_data.runtimes["front_room"]
    assert runtime.target_temperature == 20.0
    assert runtime.setpoint_source == "fallback"
    error = entry.runtime_data.schedule_error
    assert error is not None and "front_room" in error and "evening" in error


async def test_a_schedule_change_in_dry_run_sends_no_service_call(
    hass: HomeAssistant,
) -> None:
    write_schedule(hass, SCHEDULE_21)
    entry = await setup_entry(hass, dry_run=True)
    await hass.async_block_till_done()

    write_schedule(hass, 'default:\n  - time: "00:00"\n    temperature: 25.0\n')
    await hass.services.async_call("room_climate", "recompute", {})
    await hass.async_block_till_done()

    assert calls(hass, "climate", "set_temperature") == []
    assert calls(hass, "number", "set_value") == []
    assert entry.runtime_data.runtimes["front_room"].target_temperature == 25.0


async def test_a_simulated_entry_reads_its_own_schedule_file(hass: HomeAssistant) -> None:
    write_schedule(hass, 'default:\n  - time: "00:00"\n    temperature: 18.0\n')
    write_schedule(
        hass,
        'default:\n  - time: "00:00"\n    temperature: 22.0\n',
        simulation=True,
    )
    entry = await setup_bench(hass)
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)

    assert entry.runtime_data.runtimes["front_room"].target_temperature == 22.0
    assert (
        hass.states.get("climate.sim_front_room_valve").attributes["temperature"] == 22.0  # type: ignore[union-attr]
    )

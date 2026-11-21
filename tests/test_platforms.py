"""The entities the operator sees and the Scheduler commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN

from .test_coordinator import (
    calls,
    calls_for,
    options,
    room_subentry,
    setup_entry,
    write_schedule,
)


async def test_the_virtual_thermostat_reports_the_weighted_temperature(
    hass: HomeAssistant,
) -> None:
    await setup_entry(hass, dry_run=True)
    state = hass.states.get("climate.room_climate_front_room")
    assert state is not None
    assert state.attributes["current_temperature"] == 20.0
    assert state.attributes["hvac_action"] == "idle"
    assert state.attributes["hvac_modes"] == ["off", "heat"]
    assert state.attributes["valves"] == ["climate.trv_front_room"]


async def test_the_scheduler_sets_the_setpoint_and_the_room_calls(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.room_climate_front_room", "temperature": 21.0},
    )
    await hass.async_block_till_done()
    state = hass.states.get("climate.room_climate_front_room")
    assert state.attributes["temperature"] == 21.0
    assert state.attributes["hvac_action"] == "heating"
    assert state.attributes["setpoint_source"] == "manual"


async def test_the_thermostat_reports_the_schedule_of_the_room(hass: HomeAssistant) -> None:
    write_schedule(
        hass,
        """
groups:
  morning:
    - time: "00:00"
      temperature: 22.0
rooms:
  front_room: morning
""",
    )
    await setup_entry(hass, dry_run=True)
    state = hass.states.get("climate.room_climate_front_room")
    assert state.attributes["temperature"] == 22.0
    assert state.attributes["setpoint_source"] == "schedule"
    assert state.attributes["schedule_group"] == "morning"
    assert state.attributes["hvac_action"] == "heating"


async def test_the_thermostat_reports_the_next_change(hass: HomeAssistant, freezer: Any) -> None:
    freezer.move_to(datetime(2026, 11, 18, 12, 0, tzinfo=UTC))
    later = (dt_util.now() + timedelta(hours=1)).strftime("%H:%M")
    write_schedule(
        hass,
        f'default:\n  - time: "00:00"\n    temperature: 21.0\n'
        f'  - time: "{later}"\n    temperature: 16.0\n',
    )
    await setup_entry(hass, dry_run=True)
    state = hass.states.get("climate.room_climate_front_room")
    next_change = state.attributes["next_change"]
    assert next_change is not None
    assert next_change["temperature"] == 16.0
    assert next_change["at"][11:16] == later


async def test_a_room_without_a_schedule_reports_the_fallback_target(
    hass: HomeAssistant,
) -> None:
    """A thermostat without a target offers the operator no control at all."""
    entry = await setup_entry(hass, dry_run=False)
    await hass.async_block_till_done()
    state = hass.states.get("climate.room_climate_front_room")
    assert state.attributes["temperature"] == 20.0
    assert state.attributes["setpoint_source"] == "fallback"
    assert state.attributes["hvac_action"] == "idle"
    assert entry.runtime_data.output.rooms[0].calling is False
    assert calls_for(hass, "set_temperature", "climate.heating_heat") == []


async def test_the_fallback_setpoint_calls_for_heat_when_the_room_is_cold(
    hass: HomeAssistant,
) -> None:
    """The fallback is a real setpoint, and not only a value the panel reads."""
    await setup_entry(hass, dry_run=False, rooms=[room_subentry(valve_command_interval_seconds=0)])
    hass.states.async_set("sensor.front_room_temperature", "18.0")
    await hass.async_block_till_done()
    state = hass.states.get("climate.room_climate_front_room")
    assert state.attributes["setpoint_source"] == "fallback"
    assert state.attributes["hvac_action"] == "heating"
    boiler = calls_for(hass, "set_temperature", "climate.heating_heat")
    assert boiler[-1]["temperature"] == 31.0


async def test_the_house_entities_exist(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    for entity_id in (
        "switch.room_climate_enabled",
        "switch.room_climate_lockout_override",
        "binary_sensor.room_climate_boiler_demand",
        "binary_sensor.room_climate_outdoor_lockout",
        "sensor.room_climate_outdoor_temperature",
        "sensor.room_climate_house_temperature",
        "sensor.room_climate_status",
        "sensor.room_climate_schedule_error",
        "sensor.room_climate_front_room_temperature",
        "sensor.room_climate_front_room_temperature_ema",
        "binary_sensor.room_climate_front_room_calling",
        "binary_sensor.room_climate_front_room_degraded",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_a_second_entry_keeps_its_own_title_in_the_entity_ids(
    hass: HomeAssistant,
) -> None:
    await setup_entry(hass, dry_run=True)
    bench = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate Bench",
        options=options(dry_run=True, name="Room Climate Bench"),
        subentries_data=[room_subentry()],
    )
    bench.add_to_hass(hass)
    await hass.config_entries.async_setup(bench.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("climate.room_climate_front_room") is not None
    assert hass.states.get("climate.room_climate_bench_front_room") is not None
    assert hass.states.get("switch.room_climate_enabled") is not None
    assert hass.states.get("switch.room_climate_bench_enabled") is not None
    assert hass.states.get("climate.room_climate_front_room_2") is None


async def test_the_master_switch_releases_the_boiler(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=False)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.room_climate_front_room", "temperature": 21.0},
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.room_climate_enabled"}
    )
    await hass.async_block_till_done()
    boiler = [
        call
        for call in calls(hass, "climate", "set_temperature")
        if call.get("entity_id") == "climate.heating_heat"
    ]
    assert boiler[-1]["temperature"] == 15.0
    assert boiler[-1]["hvac_mode"] == "off"


async def test_the_room_thermostat_answers_turn_off_and_turn_on(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.room_climate_front_room", "temperature": 21.0},
    )
    await hass.async_block_till_done()
    features = hass.states.get("climate.room_climate_front_room").attributes["supported_features"]
    assert features & ClimateEntityFeature.TURN_ON
    assert features & ClimateEntityFeature.TURN_OFF

    await hass.services.async_call(
        "climate", "turn_off", {"entity_id": "climate.room_climate_front_room"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("climate.room_climate_front_room")
    assert state.state == "off"
    assert state.attributes["temperature"] == 21.0

    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": "climate.room_climate_front_room"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("climate.room_climate_front_room")
    assert state.state == "heat"
    assert state.attributes["temperature"] == 21.0


async def test_a_room_with_an_unavailable_valve_reports_degraded(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    sensor = hass.states.get("binary_sensor.room_climate_front_room_degraded")
    assert sensor is not None
    assert sensor.attributes["device_class"] == "problem"
    assert sensor.state == "off"

    hass.states.async_set("climate.trv_front_room", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.room_climate_front_room_degraded").state == "on"
    assert hass.states.get("climate.room_climate_front_room").attributes["degraded"] is True

    hass.states.async_set("climate.trv_front_room", "heat", {"temperature": 5.0})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.room_climate_front_room_degraded").state == "off"


async def test_the_master_switch_is_named_enabled(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    state = hass.states.get("switch.room_climate_enabled")
    assert state is not None
    assert state.attributes["friendly_name"] == "Room Climate Enabled"
    assert hass.states.get("switch.room_climate_boiler_automation") is None

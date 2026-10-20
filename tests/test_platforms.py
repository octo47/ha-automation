"""The entities the operator sees and the Scheduler commands."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .test_coordinator import calls, setup_entry


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


async def test_the_house_entities_exist(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    for entity_id in (
        "switch.room_climate_boiler_automation",
        "switch.room_climate_lockout_override",
        "binary_sensor.room_climate_boiler_demand",
        "binary_sensor.room_climate_outdoor_lockout",
        "sensor.room_climate_outdoor_temperature",
        "sensor.room_climate_house_temperature",
        "sensor.room_climate_status",
        "sensor.room_climate_front_room_temperature",
        "sensor.room_climate_front_room_temperature_ema",
        "binary_sensor.room_climate_front_room_calling",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_the_master_switch_releases_the_boiler(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=False)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.room_climate_front_room", "temperature": 21.0},
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.room_climate_boiler_automation"}
    )
    await hass.async_block_till_done()
    boiler = [
        call
        for call in calls(hass, "climate", "set_temperature")
        if call.get("entity_id") == "climate.heating_heat"
    ]
    assert boiler[-1]["temperature"] == 15.0
    assert boiler[-1]["hvac_mode"] == "off"

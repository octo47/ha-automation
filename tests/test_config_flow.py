"""The config flow creates the house entry and manages the rooms."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN


async def test_user_flow_creates_the_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "boiler_entity": "climate.heating_heat",
            "boiler_on_temperature": 31.0,
            "boiler_off_temperature": 15.0,
            "outdoor_sources": ["sensor.outdoor"],
            "lockout_temperature": 18.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["dry_run"] is True


async def test_a_room_needs_a_valve_and_a_source(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, options={"rooms": [], "outdoor_sources": ["sensor.outdoor"]}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_room"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "room_id": "front_room",
            "name": "Front Room",
            "valves": [],
            "sources": [{"entity_id": "sensor.front_room"}],
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]


async def test_a_room_device_lands_in_its_area(
    hass: HomeAssistant, area_registry: ar.AreaRegistry
) -> None:
    hass.states.async_set("sensor.front_room", "20.0")
    area = area_registry.async_get_or_create("Front Room")
    entry = MockConfigEntry(
        domain=DOMAIN, options={"rooms": [], "outdoor_sources": ["sensor.outdoor"]}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_room"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "room_id": "front_room",
            "name": "Front Room",
            "area_id": area.id,
            "valves": ["climate.trv_front_room"],
            "sources": [{"entity_id": "sensor.front_room"}],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    device = device_registry.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_front_room"), entry.entry_id
    )
    assert device is not None
    assert device.area_id == area.id


async def test_options_flow_rejects_a_shared_valve(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, options={"rooms": [], "outdoor_sources": ["sensor.outdoor"]}
    )
    entry.add_to_hass(hass)
    room = {
        "room_id": "front_room",
        "name": "Front Room",
        "valves": ["climate.trv_front_room"],
        "sources": [{"entity_id": "sensor.front_room"}],
    }
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_room"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], room)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    hass.config_entries.async_update_entry(entry, options=result["data"])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_room"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **room,
            "room_id": "back_room",
            "name": "Back Room",
            "sources": [{"entity_id": "sensor.back_room"}],
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "shared_entity"

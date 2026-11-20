"""The config flow creates the house entry, and the options carry the house settings."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN

from .test_coordinator import setup_entry


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


async def test_the_options_flow_saves_the_house_settings(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={"boiler_entity": "climate.heating_heat", "outdoor_sources": ["sensor.outdoor"]},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"outdoor_sources": ["sensor.outdoor"], "lockout_temperature": 21.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["lockout_temperature"] == 21.0
    assert "rooms" not in result["data"]


async def test_an_options_save_leaves_the_rooms_alone(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)
    assert hass.states.get("climate.room_climate_front_room") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"lockout_temperature": 21.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert len(entry.subentries) == 1
    assert hass.states.get("climate.room_climate_front_room") is not None

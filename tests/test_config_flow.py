"""The config flow creates the house entry, and the entry reads the house it wrote."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN
from custom_components.room_climate.models import HouseConfig

from .test_coordinator import enable_services, house, setup_entry

HOUSE_ANSWER: dict[str, Any] = {
    "name": "Home",
    "boiler_entity": "climate.heating_heat",
    "boiler_on_temperature": 31.0,
    "boiler_off_temperature": 15.0,
    "outdoor_sources": ["sensor.outdoor"],
    "lockout_temperature": 18.0,
    "lockout_hysteresis": 1.5,
    "dry_run": False,
}

SIM_HOUSE_ANSWER: dict[str, Any] = {
    "name": "Bench",
    "boiler_entity": "climate.sim_boiler",
    "boiler_on_temperature": 31.0,
    "boiler_off_temperature": 15.0,
    "outdoor_sources": ["number.sim_outdoor_temperature"],
    "simulation": True,
    "dry_run": False,
}


async def create_house(hass: HomeAssistant, answer: dict[str, Any]) -> ConfigEntry:
    """Create the house entry through the config flow, and return it."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], answer)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return hass.config_entries.async_entries(DOMAIN)[0]


def field(fields: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Return the rendered field of one name."""
    return next(entry for entry in fields if entry["name"] == name)


async def test_the_house_the_flow_creates_is_the_house_the_entry_reads(
    hass: HomeAssistant,
) -> None:
    """Every value the operator wrote comes back out of the entry the flow created."""
    entry = await create_house(hass, HOUSE_ANSWER)
    written = HouseConfig.from_entry(entry).model_dump(mode="json")
    assert {key: written[key] for key in HOUSE_ANSWER} == HOUSE_ANSWER


async def test_a_simulated_house_reads_its_flag_back(hass: HomeAssistant) -> None:
    """The simulation flag of the first form is the flag of the entry."""
    entry = await create_house(hass, SIM_HOUSE_ANSWER)
    config = HouseConfig.from_entry(entry)
    assert config.simulation is True
    assert config.boiler_entity == "climate.sim_boiler"
    assert config.outdoor_sources == ("number.sim_outdoor_temperature",)


async def test_the_options_form_opens_with_the_house_of_the_entry(hass: HomeAssistant) -> None:
    """The options form opens on the house of the entry, and the runtime reads the change."""
    entry = await setup_entry(hass, house_data=house(lockout_temperature=18.0))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    assert field(fields, "lockout_temperature")["description"]["suggested_value"] == 18.0
    assert field(fields, "boiler_entity")["description"]["suggested_value"] == (
        "climate.heating_heat"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"lockout_temperature": 21.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.config.lockout_temperature == 21.0


async def test_the_options_form_carries_the_fallback_setpoint(hass: HomeAssistant) -> None:
    """The fallback setpoint is a plain number field, and it survives the panel."""
    entry = await setup_entry(hass, house_data=house(fallback_setpoint=18.0))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    assert field(fields, "fallback_setpoint")["description"]["suggested_value"] == 18.0

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"fallback_setpoint": 16.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.config.fallback_setpoint == 16.0


async def test_the_house_entry_reads_its_options_over_its_data(hass: HomeAssistant) -> None:
    """A value in the options answers over a different value in the data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=house(lockout_temperature=18.0, dry_run=True),
        options={"lockout_temperature": 21.0, "dry_run": False},
    )
    entry.add_to_hass(hass)
    config = HouseConfig.from_entry(entry)
    assert config.lockout_temperature == 21.0
    assert config.dry_run is False
    assert config.boiler_entity == "climate.heating_heat"
    assert config.outdoor_sources == ("sensor.outdoor",)


async def test_a_simulated_house_creates_the_simulated_boiler(hass: HomeAssistant) -> None:
    """A house created with the simulation flag loads its virtual boiler."""
    await enable_services(hass)
    entry = await create_house(hass, SIM_HOUSE_ANSWER)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("climate.sim_boiler") is not None
    assert hass.states.get("binary_sensor.sim_boiler_firing") is not None


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

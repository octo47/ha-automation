"""An unloaded component must not leave the boiler running."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .test_coordinator import calls_for, setup_entry


async def test_unload_releases_the_boiler(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=False)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.room_climate_front_room", "temperature": 21.0},
    )
    await hass.async_block_till_done()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    boiler = calls_for(hass, "set_temperature", "climate.heating_heat")
    assert boiler[-1]["temperature"] == 15.0
    assert boiler[-1]["hvac_mode"] == "off"


async def test_recompute_runs_one_cycle(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=True)
    entry.runtime_data.output = None
    await hass.services.async_call("room_climate", "recompute", {})
    await hass.async_block_till_done()
    assert entry.runtime_data.output is not None

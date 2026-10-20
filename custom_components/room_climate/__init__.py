"""The Room Climate integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import RoomClimateCoordinator
from .models import HouseConfig

PLATFORMS = (Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH)

RoomClimateConfigEntry = ConfigEntry[RoomClimateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: RoomClimateConfigEntry) -> bool:
    """Start the cycle of one house entry."""
    coordinator = RoomClimateCoordinator(hass, entry, HouseConfig.from_options(entry.options))
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RoomClimateConfigEntry) -> bool:
    """Stop the cycle and return the boiler to its idle values."""
    coordinator = entry.runtime_data
    await coordinator.async_stop()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await coordinator.async_release_boiler()
    return unloaded

"""The Room Climate integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_RECOMPUTE
from .coordinator import RoomClimateCoordinator
from .models import HouseConfig

PLATFORMS = (Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH)

RoomClimateConfigEntry = ConfigEntry[RoomClimateCoordinator]

ServiceHandler = Callable[[ServiceCall], Coroutine[Any, Any, None]]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service that runs one cycle of the loaded houses."""
    _async_register_recompute(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RoomClimateConfigEntry) -> bool:
    """Start the cycle of one house entry."""
    _async_register_recompute(hass)
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
        _async_remove_recompute(hass, entry)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: RoomClimateConfigEntry) -> None:
    """Return the boiler to its idle values when the entry is removed."""
    coordinator = RoomClimateCoordinator(hass, entry, HouseConfig.from_options(entry.options))
    await coordinator.async_release_boiler()


@callback
def _async_register_recompute(hass: HomeAssistant) -> None:
    """Register the service that runs one cycle of every loaded house."""

    async def handle(_call: ServiceCall) -> None:
        for loaded in hass.config_entries.async_loaded_entries(DOMAIN):
            coordinator: RoomClimateCoordinator = loaded.runtime_data
            await coordinator.async_recompute()

    _register_service(hass, handle)


@callback
def _register_service(hass: HomeAssistant, handler: ServiceHandler) -> None:
    """Add the recompute service to the service registry.

    Every service registration of Home Assistant carries the unresolved generic
    of its schema parameter under the strict profile, so this one call is
    suppressed for that rule alone.
    """
    hass.services.async_register(  # pyright: ignore[reportUnknownMemberType]
        DOMAIN, SERVICE_RECOMPUTE, handler
    )


@callback
def _async_remove_recompute(hass: HomeAssistant, entry: RoomClimateConfigEntry) -> None:
    """Remove the service when the last loaded entry goes away."""
    remaining = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id and other.state is ConfigEntryState.LOADED
    ]
    if not remaining:
        hass.services.async_remove(DOMAIN, SERVICE_RECOMPUTE)

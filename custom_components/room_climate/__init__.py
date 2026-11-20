"""The Room Climate integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_RECOMPUTE
from .coordinator import RoomClimateCoordinator
from .entity import room_key
from .models import HouseConfig

PLATFORMS = (
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
)

RoomClimateConfigEntry = ConfigEntry[RoomClimateCoordinator]

ServiceHandler = Callable[[ServiceCall], Coroutine[Any, Any, None]]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service that runs one cycle of the loaded houses."""
    _async_register_recompute(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RoomClimateConfigEntry) -> bool:
    """Start the cycle of one house entry, over the rooms of its subentries."""
    _async_register_recompute(hass)
    coordinator = RoomClimateCoordinator(hass, entry, HouseConfig.from_entry(entry))
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_assign_areas(hass, entry, coordinator.config)
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
    coordinator = RoomClimateCoordinator(hass, entry, HouseConfig.from_entry(entry))
    await coordinator.async_release_boiler()


async def _async_reload_entry(hass: HomeAssistant, entry: RoomClimateConfigEntry) -> None:
    """Reload the entry when its options or one of its rooms changed."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_assign_areas(
    hass: HomeAssistant, entry: RoomClimateConfigEntry, config: HouseConfig
) -> None:
    """Put the device of every room into the area the room names."""
    registry = dr.async_get(hass)
    for room in config.rooms:
        if room.area_id is None:
            continue
        device = registry.async_get_device_by_identifier(
            (DOMAIN, f"{entry.entry_id}_{room_key(room)}"), entry.entry_id
        )
        if device is not None:
            registry.async_update_device(device.id, area_id=room.area_id)


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

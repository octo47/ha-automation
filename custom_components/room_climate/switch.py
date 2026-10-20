"""The master enable switch and the lockout override switch."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RoomClimateConfigEntry
from .const import DOMAIN
from .coordinator import RoomClimateCoordinator
from .entity import house_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the two switches of the house."""
    coordinator = entry.runtime_data
    async_add_entities((BoilerAutomationSwitch(coordinator), LockoutOverrideSwitch(coordinator)))


class HouseSwitch(SwitchEntity):
    """One switch on the house device of the entry."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_is_on = False

    def __init__(self, coordinator: RoomClimateCoordinator, key: str) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = house_device(coordinator)

    @callback
    def _handle_cycle(self) -> None:
        """Publish the state of the switch after one cycle."""
        self._sync()
        self.async_write_ha_state()

    @callback
    @abstractmethod
    def _sync(self) -> None:
        """Copy the state of the coordinator into the switch."""

    async def async_added_to_hass(self) -> None:
        """Follow the cycle of the coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_cycle))
        self._sync()


class BoilerAutomationSwitch(HouseSwitch):
    """The master enable of the component."""

    _attr_name = "Boiler Automation"
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator: RoomClimateCoordinator) -> None:
        super().__init__(coordinator, "boiler_automation")

    @callback
    def _sync(self) -> None:
        """Copy the master enable of the coordinator."""
        self._attr_is_on = self.coordinator.master_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the component and run one cycle."""
        self.coordinator.master_enabled = True
        await self.coordinator.async_recompute()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the component and run one cycle."""
        self.coordinator.master_enabled = False
        await self.coordinator.async_recompute()


class LockoutOverrideSwitch(HouseSwitch):
    """The bypass of the outdoor lockout."""

    _attr_name = "Lockout Override"
    _attr_icon = "mdi:weather-sunny"

    def __init__(self, coordinator: RoomClimateCoordinator) -> None:
        super().__init__(coordinator, "lockout_override")

    @callback
    def _sync(self) -> None:
        """Copy the override of the coordinator."""
        self._attr_is_on = self.coordinator.override_active

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Bypass the lockout and run one cycle."""
        self.coordinator.override_active = True
        await self.coordinator.async_recompute()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the bypass and run one cycle."""
        self.coordinator.override_active = False
        await self.coordinator.async_recompute()

"""The demand and lockout signals of the house, and the latch of every room."""

from __future__ import annotations

from abc import abstractmethod

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RoomClimateConfigEntry
from .const import DOMAIN
from .coordinator import RoomClimateCoordinator
from .entity import house_device, room_command, room_device
from .models import RoomConfig
from .sim import SimBoilerFiringBinary, SimHeatRequiredBinary


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the house signals and the demand latch of every room, and the bench signals."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        BoilerDemandSensor(coordinator),
        OutdoorLockoutSensor(coordinator),
    ]
    entities.extend(RoomCallingSensor(coordinator, room) for room in coordinator.config.rooms)
    if coordinator.config.simulation:
        entities.extend(
            SimHeatRequiredBinary(entry.entry_id, room.room_id) for room in coordinator.config.rooms
        )
        entities.append(SimBoilerFiringBinary(entry.entry_id))
    async_add_entities(entities)


class HouseBinarySensor(BinarySensorEntity):
    """One binary sensor that publishes the output of the last cycle."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_is_on = False

    def __init__(self, coordinator: RoomClimateCoordinator, key: str, device: DeviceInfo) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = device

    @callback
    def _handle_cycle(self) -> None:
        """Publish the state of the sensor after one cycle."""
        self._sync()
        self.async_write_ha_state()

    @callback
    @abstractmethod
    def _sync(self) -> None:
        """Copy the output of the last cycle into the sensor."""

    async def async_added_to_hass(self) -> None:
        """Follow the cycle of the coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_cycle))
        self._sync()


class BoilerDemandSensor(HouseBinarySensor):
    """The wanted boiler state of the last cycle."""

    _attr_name = "Boiler Demand"
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: RoomClimateCoordinator) -> None:
        super().__init__(coordinator, "boiler_demand", house_device(coordinator))

    @callback
    def _sync(self) -> None:
        """Copy the wanted boiler state."""
        output = self.coordinator.output
        self._attr_is_on = output is not None and output.boiler.turn_on


class OutdoorLockoutSensor(HouseBinarySensor):
    """The lockout state of the last cycle."""

    _attr_name = "Outdoor Lockout"
    _attr_icon = "mdi:snowflake"

    def __init__(self, coordinator: RoomClimateCoordinator) -> None:
        super().__init__(coordinator, "outdoor_lockout", house_device(coordinator))

    @callback
    def _sync(self) -> None:
        """Copy the lockout of the coordinator."""
        self._attr_is_on = self.coordinator.lockout_active


class RoomCallingSensor(HouseBinarySensor):
    """The demand latch of one room."""

    _attr_name = "Calling"
    _attr_icon = "mdi:thermometer-chevron-up"

    def __init__(self, coordinator: RoomClimateCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, f"{room.room_id}_calling", room_device(coordinator, room))
        self.room = room

    @callback
    def _sync(self) -> None:
        """Copy the demand latch of the room."""
        command = room_command(self.coordinator, self.room.room_id)
        self._attr_is_on = command is not None and command.calling

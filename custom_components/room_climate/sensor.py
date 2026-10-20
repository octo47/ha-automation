"""The temperatures and the status of the house and of every room."""

from __future__ import annotations

from abc import abstractmethod

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RoomClimateConfigEntry
from .const import (
    DOMAIN,
    STATUS_DEGRADED,
    STATUS_DISABLED,
    STATUS_DRY_RUN,
    STATUS_HEATING,
    STATUS_IDLE,
    STATUS_LOCKED_OUT,
)
from .coordinator import RoomClimateCoordinator
from .entity import house_device, room_command, room_device
from .models import RoomConfig


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the house sensors and the two graph temperatures of every room."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        OutdoorTemperatureSensor(coordinator),
        HouseTemperatureSensor(coordinator),
        StatusSensor(coordinator),
    ]
    for room in coordinator.config.rooms:
        entities.append(RoomTemperatureSensor(coordinator, room))
        entities.append(RoomEmaTemperatureSensor(coordinator, room))
    async_add_entities(entities)


class HouseSensor(SensorEntity):
    """One sensor that publishes the output of the last cycle."""

    _attr_has_entity_name = True
    _attr_should_poll = False

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


class OutdoorTemperatureSensor(HouseSensor):
    """The mean outdoor temperature of the last cycle."""

    _attr_name = "Outdoor Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: RoomClimateCoordinator) -> None:
        super().__init__(coordinator, "outdoor_temperature", house_device(coordinator))

    @callback
    def _sync(self) -> None:
        """Copy the mean outdoor temperature."""
        output = self.coordinator.output
        self._attr_native_value = None if output is None else output.outdoor_temperature


class HouseTemperatureSensor(HouseSensor):
    """The smoothed mean of the room temperatures."""

    _attr_name = "House Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: RoomClimateCoordinator) -> None:
        super().__init__(coordinator, "house_temperature", house_device(coordinator))

    @callback
    def _sync(self) -> None:
        """Copy the smoothed mean of the rooms."""
        output = self.coordinator.output
        self._attr_native_value = None if output is None else output.house_ema_temperature


class StatusSensor(HouseSensor):
    """The status of the house of the last cycle."""

    _attr_name = "Status"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: RoomClimateCoordinator) -> None:
        super().__init__(coordinator, "status", house_device(coordinator))
        self._attr_options = [
            STATUS_HEATING,
            STATUS_IDLE,
            STATUS_LOCKED_OUT,
            STATUS_DISABLED,
            STATUS_DRY_RUN,
            STATUS_DEGRADED,
        ]

    @callback
    def _sync(self) -> None:
        """Copy the status of the house."""
        output = self.coordinator.output
        self._attr_native_value = None if output is None else output.status


class RoomTemperatureSensor(HouseSensor):
    """The weighted temperature of one room."""

    _attr_name = "Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: RoomClimateCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, f"{room.room_id}_temperature", room_device(coordinator, room))
        self.room = room

    @callback
    def _sync(self) -> None:
        """Copy the weighted temperature of the room."""
        command = room_command(self.coordinator, self.room.room_id)
        self._attr_native_value = None if command is None else command.room_temperature


class RoomEmaTemperatureSensor(HouseSensor):
    """The smoothed temperature of one room."""

    _attr_name = "Temperature EMA"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: RoomClimateCoordinator, room: RoomConfig) -> None:
        super().__init__(
            coordinator, f"{room.room_id}_temperature_ema", room_device(coordinator, room)
        )
        self.room = room

    @callback
    def _sync(self) -> None:
        """Copy the smoothed temperature of the room."""
        command = room_command(self.coordinator, self.room.room_id)
        self._attr_native_value = None if command is None else command.ema_temperature

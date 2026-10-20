"""One virtual thermostat per room."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import RoomClimateConfigEntry
from .const import DOMAIN
from .coordinator import RoomClimateCoordinator
from .entity import room_command, room_device
from .models import RoomConfig, RuntimeRoom


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one virtual thermostat per room of the entry."""
    coordinator = entry.runtime_data
    async_add_entities(RoomClimateEntity(coordinator, room) for room in coordinator.config.rooms)


class RoomClimateEntity(RestoreEntity, ClimateEntity):
    """The dashboard surface and the Scheduler target of one room."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_target_temperature_step = 0.5
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_current_temperature: float | None = None
    _attr_hvac_action: HVACAction | None = HVACAction.IDLE

    def __init__(self, coordinator: RoomClimateCoordinator, room: RoomConfig) -> None:
        self.coordinator = coordinator
        self.room = room
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_unique_id = f"{DOMAIN}_{coordinator.entry.entry_id}_{room.room_id}_climate"
        self._attr_device_info = room_device(coordinator, room)
        self._attr_min_temp = room.min_temp
        self._attr_max_temp = room.max_temp
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_target_temperature: float | None = None
        self._sync()

    @callback
    def _handle_cycle(self) -> None:
        """Publish the output of the cycle that just ran."""
        self._sync()
        self.async_write_ha_state()

    @callback
    def _sync(self) -> None:
        """Copy the last cycle of the room into the attributes of the entity."""
        command = room_command(self.coordinator, self.room.room_id)
        self._attr_current_temperature = None if command is None else command.room_temperature
        self._attr_hvac_action = (
            HVACAction.HEATING if command is not None and command.calling else HVACAction.IDLE
        )
        self._attr_extra_state_attributes = {
            "valves": list(self.room.valves),
            "valve_targets": {} if command is None else dict(command.valve_targets),
            "calibration": self.room.calibration,
            "calibration_scale": self.room.calibration_scale,
            "usable_sources": 0 if command is None else command.usable_sources,
            "valves_unavailable": 0 if command is None else command.valves_unavailable,
            "degraded": command is None or command.degraded,
            "window_open": command is not None and command.window_open,
        }

    def _remember(self) -> None:
        """Keep the setpoint and the mode in the runtime state of the room."""
        runtime = self.coordinator.runtimes.get(
            self.room.room_id, RuntimeRoom(room_id=self.room.room_id)
        )
        mode = self._attr_hvac_mode or HVACMode.HEAT
        self.coordinator.runtimes[self.room.room_id] = runtime.model_copy(
            update={
                "target_temperature": self._attr_target_temperature,
                "hvac_mode": mode.value,
            }
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the setpoint of the room and run one cycle."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if isinstance(temperature, (int, float)):
            self._attr_target_temperature = float(temperature)
        self._remember()
        await self.coordinator.async_recompute()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the mode of the room and run one cycle."""
        self._attr_hvac_mode = hvac_mode
        self._remember()
        await self.coordinator.async_recompute()

    async def async_added_to_hass(self) -> None:
        """Follow the cycle, and restore the setpoint and the mode."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_cycle))
        last = await self.async_get_last_state()
        if last is not None:
            temperature = last.attributes.get(ATTR_TEMPERATURE)
            if isinstance(temperature, (int, float)):
                self._attr_target_temperature = float(temperature)
            try:
                self._attr_hvac_mode = HVACMode(last.state)
            except ValueError:
                pass
        self._remember()
        self._sync()

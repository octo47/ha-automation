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
from .entity import room_command, room_device, room_key
from .models import RoomConfig, RuntimeRoom
from .sim import SimBoilerEntity, SimValveEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one virtual thermostat per room, and the virtual valves of a bench."""
    coordinator = entry.runtime_data
    for room in coordinator.config.rooms:
        async_add_entities(
            [RoomClimateEntity(coordinator, room)], config_subentry_id=room.subentry_id
        )
    if coordinator.config.simulation:
        valves: list[ClimateEntity] = [
            SimValveEntity(entry.entry_id, room.room_id) for room in coordinator.config.rooms
        ]
        valves.append(SimBoilerEntity(entry.entry_id))
        async_add_entities(valves)


class RoomClimateEntity(RestoreEntity, ClimateEntity):
    """The dashboard surface and the Scheduler target of one room."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_current_temperature: float | None = None
    _attr_hvac_action: HVACAction | None = HVACAction.IDLE

    def __init__(self, coordinator: RoomClimateCoordinator, room: RoomConfig) -> None:
        self.coordinator = coordinator
        self.room = room
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_unique_id = f"{DOMAIN}_{coordinator.entry.entry_id}_{room_key(room)}_climate"
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
        runtime = self.coordinator.runtimes.get(self.room.room_id)
        self._attr_target_temperature = (
            self.coordinator.config.fallback_setpoint
            if runtime is None
            else runtime.target_temperature
        )
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
            "locked_out": command is not None and command.locked_out,
            "setpoint_source": "fallback" if runtime is None else runtime.setpoint_source,
            "schedule_group": "default" if runtime is None else runtime.schedule_group,
            "next_change": self._next_change_attribute(runtime),
        }

    @staticmethod
    def _next_change_attribute(runtime: RuntimeRoom | None) -> dict[str, object] | None:
        """Return the next block of the schedule as the attribute the dashboard reads."""
        if runtime is None or runtime.next_change_at is None:
            return None
        return {
            "at": runtime.next_change_at.isoformat(),
            "temperature": runtime.next_change_temperature,
        }

    def _remember_mode(self) -> None:
        """Keep the mode in the runtime state of the room."""
        runtime = self.coordinator.runtimes.get(
            self.room.room_id, RuntimeRoom(room_id=self.room.room_id)
        )
        mode = self._attr_hvac_mode or HVACMode.HEAT
        self.coordinator.runtimes[self.room.room_id] = runtime.model_copy(
            update={"hvac_mode": mode.value}
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Hold a manual setpoint on the room and run one cycle."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if isinstance(temperature, (int, float)):
            self.coordinator.set_manual_target(self.room.room_id, float(temperature))
        await self.coordinator.async_recompute()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the mode of the room and run one cycle."""
        self._attr_hvac_mode = hvac_mode
        self._remember_mode()
        await self.coordinator.async_recompute()

    async def async_turn_on(self) -> None:
        """Return the room to heat, keeping the setpoint across the round trip."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Stop the room, keeping the setpoint across the round trip."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_added_to_hass(self) -> None:
        """Follow the cycle, and restore the mode."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_cycle))
        last = await self.async_get_last_state()
        if last is not None:
            try:
                self._attr_hvac_mode = HVACMode(last.state)
            except ValueError:
                pass
        self._remember_mode()
        self._sync()

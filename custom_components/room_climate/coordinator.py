"""The cycle that reads states, decides, and applies the commands."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .control import decide, feed_entity_id, next_lockout, outdoor_temperature
from .models import ControlInput, ControlOutput, HouseConfig, RuntimeRoom

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = "unavailable"
UNUSABLE_STATES = ("unknown", "unavailable", "")


def _numeric_state(hass: HomeAssistant, entity_id: str) -> float | None:
    """Return the temperature of one entity, or None when it carries none."""
    state = hass.states.get(entity_id)
    if state is None or state.state in UNUSABLE_STATES:
        return None
    if state.domain == "weather":
        temperature = state.attributes.get("temperature")
        return float(temperature) if isinstance(temperature, (int, float)) else None
    try:
        return float(state.state)
    except ValueError:
        return None


def _is_available(hass: HomeAssistant, entity_id: str) -> bool:
    """Return whether one entity carries a state the component can command."""
    state = hass.states.get(entity_id)
    return state is not None and state.state != UNAVAILABLE


class RoomClimateCoordinator:
    """Owns the runtime state of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry[Any], config: HouseConfig) -> None:
        self.hass = hass
        self.entry = entry
        self.config = config
        self.runtimes: dict[str, RuntimeRoom] = {}
        self.output: ControlOutput | None = None
        self.boiler_on = False
        self.boiler_state_since: datetime | None = None
        self.lockout_active = False
        self.last_actuator_targets: dict[str, float] = {}
        self.last_calibration_values: dict[str, float] = {}
        self.last_command_at: dict[str, datetime] = {}
        self.master_enabled = config.master_enabled
        self.override_active = False
        self._unsubscribers: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []

    @property
    def data(self) -> ControlOutput | None:
        """The output of the last cycle, for the entities that publish it."""
        return self.output

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity callback that runs after every cycle."""
        self._listeners.append(listener)

        def remove_listener() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove_listener

    @callback
    def async_update_listeners(self) -> None:
        """Let every registered entity publish the state of the last cycle."""
        for listener in list(self._listeners):
            listener()

    @property
    def watched_entities(self) -> tuple[str, ...]:
        """The entities whose state changes trigger a cycle."""
        watched = [
            entity_id
            for room in self.config.rooms
            for entity_id in (
                *room.valves,
                *(feed_entity_id(valve) for valve in room.valves),
                *(source.entity_id for source in room.sources),
                *room.window_sensors,
                *room.demand_fallback_sensors,
            )
        ]
        watched.append(self.config.boiler_entity)
        watched.extend(self.config.outdoor_sources)
        return tuple(watched)

    async def async_start(self) -> None:
        """Run one cycle, then follow the watched entities."""
        await self.async_recompute()
        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass, list(self.watched_entities), self._handle_state_change
            )
        )

    async def async_stop(self) -> None:
        """Detach the listeners."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Run one cycle for one state change of a watched entity."""
        self.hass.async_create_task(self.async_recompute())

    async def async_recompute(self) -> ControlOutput:
        """Run one cycle, and either log the commands or apply them."""
        temperatures = {
            entity_id: _numeric_state(self.hass, entity_id)
            for entity_id in (
                *self.config.outdoor_sources,
                *(source.entity_id for room in self.config.rooms for source in room.sources),
            )
        }
        window_states = {
            entity_id: state.state
            for entity_id in (item for room in self.config.rooms for item in room.window_sensors)
            if (state := self.hass.states.get(entity_id)) is not None
        }
        valve_demands = {
            entity_id: state.state == "on"
            for entity_id in (
                item for room in self.config.rooms for item in room.demand_fallback_sensors
            )
            if (state := self.hass.states.get(entity_id)) is not None
        }
        valve_available = {
            entity_id: _is_available(self.hass, entity_id)
            for entity_id in (valve for room in self.config.rooms for valve in room.valves)
        }
        outdoor = outdoor_temperature(self.config.outdoor_sources, temperatures)
        self.lockout_active = next_lockout(
            self.lockout_active,
            outdoor,
            self.config.lockout_temperature,
            self.config.lockout_hysteresis,
        )
        payload = ControlInput(
            now=dt_util.utcnow(),
            house=self.config,
            temperatures=temperatures,
            valve_demands=valve_demands,
            valve_available=valve_available,
            window_states=window_states,
            runtimes=tuple(self.runtimes.values()),
            last_actuator_targets=self.last_actuator_targets,
            last_calibration_values=self.last_calibration_values,
            last_command_at=self.last_command_at,
            outdoor_lockout_active=self.lockout_active,
            boiler_on=self.boiler_on,
            boiler_state_since=self.boiler_state_since,
            master_enabled=self.master_enabled,
            override_active=self.override_active,
        )
        output = await self.hass.async_add_executor_job(decide, payload)
        if self.config.dry_run:
            _LOGGER.info("dry run %s: %s", self.entry.entry_id, output.model_dump_json())
            self._remember(output)
        else:
            await self._apply(output)
        self.output = output
        self.async_update_listeners()
        return output

    async def _apply(self, output: ControlOutput) -> None:
        """Send the commands of one cycle."""
        now = dt_util.utcnow()
        rooms = {room.room_id: room for room in self.config.rooms}
        for command in output.rooms:
            room = rooms.get(command.room_id)
            off_setpoint = None if room is None else room.off_setpoint
            for entity_id, target in command.valve_targets.items():
                if self.hass.states.get(entity_id) is None:
                    continue
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": entity_id, "temperature": target},
                    blocking=False,
                )
                self.last_actuator_targets[entity_id] = target
                # The interval guards the modulating moves of a valve, so only a
                # command above the off setpoint starts it.
                if target != off_setpoint:
                    self.last_command_at[entity_id] = now
            for entity_id, value in command.calibration_values.items():
                if self.hass.states.get(entity_id) is None:
                    continue
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": value},
                    blocking=False,
                )
                self.last_calibration_values[entity_id] = value
        if output.boiler.send:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {
                    "entity_id": self.config.boiler_entity,
                    "temperature": (
                        self.config.boiler_on_temperature
                        if output.boiler.turn_on
                        else self.config.boiler_off_temperature
                    ),
                    "hvac_mode": (
                        self.config.boiler_on_hvac_mode
                        if output.boiler.turn_on
                        else self.config.boiler_off_hvac_mode
                    ),
                },
                blocking=False,
            )
            self.boiler_on = output.boiler.turn_on
            self.boiler_state_since = now
        self._remember(output)

    def _remember(self, output: ControlOutput) -> None:
        """Keep the latch, the averages and the sent values for the next cycle."""
        now = dt_util.utcnow()
        runtimes: dict[str, RuntimeRoom] = {}
        for command in output.rooms:
            previous = self.runtimes.get(command.room_id, RuntimeRoom(room_id=command.room_id))
            runtimes[command.room_id] = RuntimeRoom(
                room_id=command.room_id,
                target_temperature=previous.target_temperature,
                hvac_mode=previous.hvac_mode,
                calling=command.calling,
                ema_temperature=command.ema_temperature,
                updated_at=now,
            )
        self.runtimes = runtimes

    async def async_release_boiler(self) -> None:
        """Return the boiler to its idle values."""
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {
                "entity_id": self.config.boiler_entity,
                "temperature": self.config.boiler_off_temperature,
                "hvac_mode": self.config.boiler_off_hvac_mode,
            },
            blocking=False,
        )
        self.boiler_on = False
        self.boiler_state_since = dt_util.utcnow()

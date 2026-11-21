"""The cycle that reads states, decides, and applies the commands."""

from __future__ import annotations

import logging
import os
import pathlib
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    SETPOINT_SOURCE_FALLBACK,
    SETPOINT_SOURCE_MANUAL,
    SETPOINT_SOURCE_SCHEDULE,
)
from .control import (
    decide,
    feed_entity_id,
    next_lockout,
    outdoor_temperature,
    room_lockout_temperature,
    room_outdoor_sources,
)
from .models import ControlInput, ControlOutput, HouseConfig, RoomConfig, RuntimeRoom
from .schedule import (
    GROUP_DEFAULT,
    ScheduleError,
    ScheduleFile,
    SetpointResolution,
    parse_schedule_file,
    resolve_setpoint,
    schedule_path_name,
)

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
        self.room_lockouts: dict[str, bool] = {}
        self.last_actuator_targets: dict[str, float] = {}
        self.last_calibration_values: dict[str, float] = {}
        self.last_command_at: dict[str, datetime] = {}
        self.master_enabled = config.master_enabled
        self.override_active = False
        self.schedule: ScheduleFile | None = None
        self.schedule_error: str | None = None
        self._schedule_stamp: tuple[int, int] | None = None
        self._load_error: str | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []

    @property
    def data(self) -> ControlOutput | None:
        """The output of the last cycle, for the entities that publish it."""
        return self.output

    @property
    def schedule_path(self) -> str:
        """The schedule file of this entry: the house file, or the bench file."""
        return self.hass.config.path(schedule_path_name(self.config.simulation))

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
                *room.outdoor_sources,
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

    def _load_schedule(self) -> str | None:
        """Read the schedule file when its modification time changed.

        A missing file is not an error: every room has no setpoint. A file that
        fails to parse keeps the last good schedule. The fault is logged once for
        each change of the file.
        """
        path = self.schedule_path
        try:
            stat = os.stat(path)
        except OSError:
            self.schedule = None
            self._schedule_stamp = None
            self._load_error = None
            return None
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._schedule_stamp:
            return self._load_error
        self._schedule_stamp = stamp
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8")
        except OSError as error:
            self._load_error = f"the schedule file {path} could not be read: {error}"
            _LOGGER.error("%s", self._load_error)
            return self._load_error
        try:
            self.schedule = parse_schedule_file(text)
        except ScheduleError as error:
            self._load_error = str(error)
            _LOGGER.error(
                "the schedule file %s could not be parsed, so the last good schedule "
                "stays in force: %s",
                path,
                error,
            )
            return self._load_error
        self._load_error = None
        return None

    def _resolve(self, room: RoomConfig, at: datetime) -> tuple[SetpointResolution, str | None]:
        """Return the schedule setpoint of one room, and a refusal message when any."""
        try:
            resolution = resolve_setpoint(
                self.schedule,
                room.room_id,
                at,
                minimum=room.min_temp,
                maximum=room.max_temp,
            )
        except ScheduleError as error:
            return SetpointResolution(temperature=None, group=GROUP_DEFAULT), str(error)
        return resolution, "; ".join(resolution.errors) or None

    def _next_runtimes(self, at: datetime) -> tuple[dict[str, RuntimeRoom], list[str]]:
        """Give every room the setpoint of the schedule, or the manual override."""
        runtimes: dict[str, RuntimeRoom] = {}
        errors: list[str] = []
        for room in self.config.rooms:
            previous = self.runtimes.get(room.room_id, RuntimeRoom(room_id=room.room_id))
            resolution, refusal = self._resolve(room, at)
            if refusal is not None:
                errors.append(refusal)
            manual_holds = previous.manual_target is not None and (
                previous.manual_until is None or at < previous.manual_until
            )
            if manual_holds:
                target, source = previous.manual_target, SETPOINT_SOURCE_MANUAL
                manual_target, manual_until = previous.manual_target, previous.manual_until
            elif resolution.temperature is not None:
                target, source = resolution.temperature, SETPOINT_SOURCE_SCHEDULE
                manual_target, manual_until = None, None
            else:
                # A room always carries a target, because a thermostat without one
                # offers the operator no control at all. The house fallback stands in
                # when the file gives the room nothing.
                target = self.config.fallback_setpoint
                source = SETPOINT_SOURCE_FALLBACK
                manual_target, manual_until = None, None
            next_change = resolution.next_change
            runtimes[room.room_id] = previous.model_copy(
                update={
                    "target_temperature": target,
                    "manual_target": manual_target,
                    "manual_until": manual_until,
                    "setpoint_source": source,
                    "schedule_group": resolution.group,
                    "next_change_at": None if next_change is None else next_change.at,
                    "next_change_temperature": (
                        None if next_change is None else next_change.temperature
                    ),
                }
            )
        return runtimes, errors

    def set_manual_target(self, room_id: str, temperature: float) -> None:
        """Hold a manual setpoint on one room until the next block of its schedule."""
        now = dt_util.now()
        room = next((item for item in self.config.rooms if item.room_id == room_id), None)
        until: datetime | None = None
        if room is not None:
            resolution, _ = self._resolve(room, now)
            if resolution.next_change is not None:
                until = resolution.next_change.at
        previous = self.runtimes.get(room_id, RuntimeRoom(room_id=room_id))
        self.runtimes[room_id] = previous.model_copy(
            update={"manual_target": temperature, "manual_until": until}
        )

    async def async_recompute(self) -> ControlOutput:
        """Run one cycle, and either log the commands or apply them."""
        load_error = self._load_schedule()
        runtimes, refusals = self._next_runtimes(dt_util.now())
        self.runtimes = runtimes
        self.schedule_error = (
            "; ".join(dict.fromkeys(message for message in (load_error, *refusals) if message))
            or None
        )
        temperatures = {
            entity_id: _numeric_state(self.hass, entity_id)
            for entity_id in (
                *self.config.outdoor_sources,
                *(
                    entity_id
                    for room in self.config.rooms
                    for entity_id in room_outdoor_sources(self.config, room)
                ),
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
        room_lockouts: dict[str, bool] = {}
        for room in self.config.rooms:
            room_lockouts[room.room_id] = next_lockout(
                self.room_lockouts.get(room.room_id, False),
                outdoor_temperature(room_outdoor_sources(self.config, room), temperatures),
                room_lockout_temperature(self.config, room),
                self.config.lockout_hysteresis,
            )
        self.room_lockouts = room_lockouts
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
            room_lockouts=room_lockouts,
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
        for command in output.rooms:
            previous = self.runtimes.get(command.room_id, RuntimeRoom(room_id=command.room_id))
            self.runtimes[command.room_id] = previous.model_copy(
                update={
                    "calling": command.calling,
                    "ema_temperature": command.ema_temperature,
                    "updated_at": now,
                }
            )

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

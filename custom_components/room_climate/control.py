"""Decision rules for room heating."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from .const import (
    CALIBRATION_MAX_TEMPERATURE,
    CALIBRATION_MIN_TEMPERATURE,
    CALIBRATION_NONE,
    CALIBRATION_TOLERANCE_C,
    MAX_PLAUSIBLE_TEMPERATURE,
    MIN_PLAUSIBLE_TEMPERATURE,
    REASON_LOCKOUT,
    REASON_MASTER_OFF,
    REASON_NO_DEMAND,
    REASON_ROOM_DEMAND,
    REPEAT_THRESHOLD,
    SETPOINT_STEP,
    STATUS_DEGRADED,
    STATUS_DISABLED,
    STATUS_DRY_RUN,
    STATUS_HEATING,
    STATUS_IDLE,
    STATUS_LOCKED_OUT,
    VALVE_MODE_MODULATE,
    WINDOW_OPEN_STATES,
)
from .models import (
    BoilerCommand,
    ControlInput,
    ControlOutput,
    RoomCommand,
    RoomConfig,
    RuntimeRoom,
    TemperatureSource,
)

FEED_SUFFIX = "_external_measured_room_sensor"


def _plausible(value: float | None) -> bool:
    """Return whether a reading is a temperature the component accepts."""
    return value is not None and MIN_PLAUSIBLE_TEMPERATURE <= value <= MAX_PLAUSIBLE_TEMPERATURE


def usable_source_count(
    sources: Sequence[TemperatureSource], temperatures: Mapping[str, float | None]
) -> int:
    """Count the sources that carry a plausible reading."""
    return sum(1 for source in sources if _plausible(temperatures.get(source.entity_id)))


def weighted_temperature(
    sources: Sequence[TemperatureSource], temperatures: Mapping[str, float | None]
) -> float | None:
    """Return the weighted mean of the usable sources, or None when none is usable."""
    total = 0.0
    total_weight = 0.0
    for source in sources:
        value = temperatures.get(source.entity_id)
        if not _plausible(value):
            continue
        assert value is not None
        total += source.weight * value
        total_weight += source.weight
    if total_weight == 0.0:
        return None
    return total / total_weight


def outdoor_temperature(
    sources: Sequence[str], temperatures: Mapping[str, float | None]
) -> float | None:
    """Return the mean of the usable outdoor readings, or None when none is usable."""
    total = 0.0
    count = 0
    for entity_id in sources:
        value = temperatures.get(entity_id)
        if not _plausible(value):
            continue
        assert value is not None
        total += value
        count += 1
    if count == 0:
        return None
    return total / count


def next_lockout(active: bool, outdoor: float | None, threshold: float, hysteresis: float) -> bool:
    """Return the lockout state for the next cycle."""
    if outdoor is None:
        return False
    if active:
        return outdoor > threshold - hysteresis
    return outdoor >= threshold


def next_calling(
    previous: bool,
    room: RoomConfig,
    target: float | None,
    hvac_mode: str,
    temperature: float | None,
    window_open: bool,
    fallback_demand: bool,
) -> bool:
    """Return the demand state of one room for the next cycle."""
    if hvac_mode != "heat" or target is None or target <= room.min_temp:
        return False
    if window_open:
        return False
    if temperature is None:
        return fallback_demand
    if previous:
        return temperature <= target + room.hysteresis
    return temperature < target - room.hysteresis


def round_setpoint(value: float) -> float:
    """Round a target temperature to the half degree step of the valves."""
    return round(value / SETPOINT_STEP) * SETPOINT_STEP


def valve_targets(
    room: RoomConfig,
    *,
    blocked: bool,
    available: Mapping[str, bool],
    calling: bool,
    target: float | None,
    last_sent: Mapping[str, float],
    last_command_at: Mapping[str, datetime],
    now: datetime,
) -> Mapping[str, float]:
    """Return the valve commands, honouring the repeat rule and the command interval."""
    if blocked or target is None:
        wanted = room.off_setpoint
    elif room.valve_mode == VALVE_MODE_MODULATE:
        wanted = target
    else:
        wanted = target + room.open_offset if calling else room.off_setpoint
    rounded = round_setpoint(min(max(wanted, room.min_temp), room.max_temp))
    commands: dict[str, float] = {}
    for entity_id in room.valves:
        if available.get(entity_id) is False:
            continue
        sent = last_sent.get(entity_id)
        if sent is not None and abs(sent - rounded) < REPEAT_THRESHOLD:
            continue
        if rounded != room.off_setpoint:
            since = last_command_at.get(entity_id)
            if (
                since is not None
                and (now - since).total_seconds() < room.valve_command_interval_seconds
            ):
                continue
        commands[entity_id] = rounded
    return commands


def feed_entity_id(valve_entity_id: str) -> str:
    """Return the feed entity of one valve."""
    return f"number.{valve_entity_id.split('.', 1)[1]}{FEED_SUFFIX}"


def calibration_values(
    room: RoomConfig, temperature: float | None, last_written: Mapping[str, float]
) -> Mapping[str, float]:
    """Return the calibration writes of one room."""
    if room.calibration == CALIBRATION_NONE or temperature is None:
        return {}
    clamped = min(max(temperature, CALIBRATION_MIN_TEMPERATURE), CALIBRATION_MAX_TEMPERATURE)
    value = float(round(clamped * room.calibration_scale))
    tolerance = CALIBRATION_TOLERANCE_C * room.calibration_scale
    writes: dict[str, float] = {}
    for valve in room.valves:
        entity_id = feed_entity_id(valve)
        previous = last_written.get(entity_id)
        if previous is not None and abs(previous - value) < tolerance:
            continue
        writes[entity_id] = value
    return writes


def exponential_average(
    previous: float | None, value: float, elapsed_seconds: float, tau_seconds: float
) -> float:
    """Return the smoothed value, and the sample itself when no history exists."""
    if previous is None or elapsed_seconds <= 0.0 or tau_seconds <= 0.0:
        return value
    alpha = 1.0 - math.exp(-elapsed_seconds / tau_seconds)
    return previous + alpha * (value - previous)


def _is_fallback_demand(payload: ControlInput, room: RoomConfig) -> bool:
    """Return whether a demand fallback entity of the room reports heat."""
    return any(
        payload.valve_demands.get(entity_id) is True for entity_id in room.demand_fallback_sensors
    )


def _window_open(payload: ControlInput, room: RoomConfig) -> bool:
    """Return whether a window sensor of the room reports an open window."""
    for entity_id in room.window_sensors:
        state = payload.window_states.get(entity_id)
        if state is not None and state in WINDOW_OPEN_STATES:
            return True
    return False


def _room_command(
    payload: ControlInput, room: RoomConfig, runtime: RuntimeRoom, blocked: bool
) -> RoomCommand:
    """Return the commands and the reported state of one room."""
    temperature = weighted_temperature(room.sources, payload.temperatures)
    usable = usable_source_count(room.sources, payload.temperatures)
    window_open = _window_open(payload, room)
    unavailable_valves = [
        entity_id for entity_id in room.valves if payload.valve_available.get(entity_id) is False
    ]
    calling = next_calling(
        runtime.calling,
        room,
        runtime.target_temperature,
        runtime.hvac_mode,
        temperature,
        window_open,
        _is_fallback_demand(payload, room),
    )
    calling = calling and len(unavailable_valves) < len(room.valves)
    elapsed = (
        (payload.now - runtime.updated_at).total_seconds()
        if runtime.updated_at is not None
        else 0.0
    )
    ema = (
        exponential_average(
            runtime.ema_temperature, temperature, elapsed, payload.house.ema_seconds
        )
        if temperature is not None
        else runtime.ema_temperature
    )
    return RoomCommand(
        room_id=room.room_id,
        calling=calling,
        window_open=window_open,
        room_temperature=temperature,
        ema_temperature=ema,
        usable_sources=usable,
        degraded=usable == 0 or bool(unavailable_valves),
        valves_unavailable=len(unavailable_valves),
        valve_targets=valve_targets(
            room,
            blocked=blocked
            or window_open
            or runtime.hvac_mode == "off"
            or (temperature is None and not calling),
            available=payload.valve_available,
            calling=calling,
            target=runtime.target_temperature,
            last_sent=payload.last_actuator_targets,
            last_command_at=payload.last_command_at,
            now=payload.now,
        ),
        calibration_values=calibration_values(room, temperature, payload.last_calibration_values),
    )


def decide(payload: ControlInput) -> ControlOutput:
    """Return the commands of one cycle."""
    lockout = payload.outdoor_lockout_active and not payload.override_active
    blocked = lockout or not payload.master_enabled
    runtimes = {room.room_id: room for room in payload.runtimes}
    commands = tuple(
        _room_command(
            payload, room, runtimes.get(room.room_id, RuntimeRoom(room_id=room.room_id)), blocked
        )
        for room in payload.house.rooms
    )
    if not payload.master_enabled:
        boiler = _boiler_command(False, REASON_MASTER_OFF, payload, immediate=True)
    elif lockout:
        boiler = _boiler_command(False, REASON_LOCKOUT, payload, immediate=True)
    elif any(command.calling for command in commands):
        boiler = _boiler_command(True, REASON_ROOM_DEMAND, payload)
    else:
        boiler = _boiler_command(False, REASON_NO_DEMAND, payload)
    return ControlOutput(
        rooms=commands,
        boiler=boiler,
        lockout_active=payload.outdoor_lockout_active,
        outdoor_temperature=outdoor_temperature(
            payload.house.outdoor_sources, payload.temperatures
        ),
        house_ema_temperature=_house_average(commands),
        status=_status(payload, lockout, boiler, commands),
    )


def _house_average(commands: Sequence[RoomCommand]) -> float | None:
    """Return the mean of the room averages, or None when no room has one."""
    values = [
        command.ema_temperature for command in commands if command.ema_temperature is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _boiler_command(
    turn_on: bool, reason: str, payload: ControlInput, immediate: bool = False
) -> BoilerCommand:
    """Return the boiler command, honouring the minimum on and off times."""
    if turn_on == payload.boiler_on:
        return BoilerCommand(turn_on=turn_on, reason=reason, send=False)
    if immediate or payload.boiler_state_since is None:
        return BoilerCommand(turn_on=turn_on, reason=reason, send=True)
    limit = timedelta(
        seconds=payload.house.min_off_seconds if turn_on else payload.house.min_on_seconds
    )
    return BoilerCommand(
        turn_on=turn_on, reason=reason, send=payload.now - payload.boiler_state_since >= limit
    )


def _status(
    payload: ControlInput, lockout: bool, boiler: BoilerCommand, commands: Sequence[RoomCommand]
) -> str:
    """Return the status of the house for this cycle."""
    if not payload.master_enabled:
        return STATUS_DISABLED
    if lockout:
        return STATUS_LOCKED_OUT
    if payload.house.dry_run:
        return STATUS_DRY_RUN
    if commands and all(command.degraded for command in commands):
        return STATUS_DEGRADED
    if boiler.turn_on or payload.boiler_on:
        return STATUS_HEATING
    return STATUS_IDLE

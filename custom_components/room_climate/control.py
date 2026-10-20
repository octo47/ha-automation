"""Decision rules for room heating."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from .const import (
    CALIBRATION_MAX_TEMPERATURE,
    CALIBRATION_MIN_TEMPERATURE,
    CALIBRATION_NONE,
    CALIBRATION_TOLERANCE_C,
    MAX_PLAUSIBLE_TEMPERATURE,
    MIN_PLAUSIBLE_TEMPERATURE,
    REPEAT_THRESHOLD,
    SETPOINT_STEP,
    VALVE_MODE_MODULATE,
)
from .models import RoomConfig, TemperatureSource

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

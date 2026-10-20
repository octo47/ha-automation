"""Typed configuration and cycle models of the integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .const import (
    CALIBRATION_EXTERNAL,
    CALIBRATION_NONE,
    DEFAULT_BOILER_ENTITY,
    DEFAULT_BOILER_OFF_TEMPERATURE,
    DEFAULT_BOILER_ON_TEMPERATURE,
    DEFAULT_CALIBRATION_SCALE,
    DEFAULT_EMA_SECONDS,
    DEFAULT_HYSTERESIS,
    DEFAULT_LOCKOUT_HYSTERESIS,
    DEFAULT_LOCKOUT_TEMPERATURE,
    DEFAULT_MIN_OFF_SECONDS,
    DEFAULT_MIN_ON_SECONDS,
    DEFAULT_OFF_SETPOINT,
    DEFAULT_OPEN_OFFSET,
    DEFAULT_OUTDOOR_SOURCES,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DEFAULT_VALVE_COMMAND_INTERVAL_SECONDS,
    SIM_MARKER,
    VALVE_MODE_MODULATE,
)

FROZEN = ConfigDict(frozen=True, extra="forbid")


class TemperatureSource(BaseModel):
    """One temperature entity of a room, with its weight in the mean."""

    model_config = FROZEN

    entity_id: str
    weight: float = Field(default=1.0, gt=0)


class RoomConfig(BaseModel):
    """The valves and the temperature sources of one room."""

    model_config = FROZEN

    room_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1)
    valves: tuple[str, ...] = Field(min_length=1)
    sources: tuple[TemperatureSource, ...] = Field(min_length=1)
    area_id: str | None = None
    calibration: Literal["external_sensor", "none"] = CALIBRATION_EXTERNAL
    calibration_scale: int = Field(default=DEFAULT_CALIBRATION_SCALE, ge=1, le=1000)
    valve_mode: Literal["modulate", "boost"] = VALVE_MODE_MODULATE
    open_offset: float = Field(default=DEFAULT_OPEN_OFFSET, ge=0.0, le=5.0)
    min_temp: float = Field(default=5.0, ge=5.0, le=20.0)
    max_temp: float = Field(default=30.0, ge=15.0, le=35.0)
    hysteresis: float = Field(default=DEFAULT_HYSTERESIS, ge=0.0, le=2.0)
    off_setpoint: float = Field(default=DEFAULT_OFF_SETPOINT, ge=5.0, le=12.0)
    valve_command_interval_seconds: int = Field(
        default=DEFAULT_VALVE_COMMAND_INTERVAL_SECONDS, ge=0, le=3600
    )
    window_sensors: tuple[str, ...] = ()
    demand_fallback_sensors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_room(self) -> Self:
        if self.valve_mode == VALVE_MODE_MODULATE and self.calibration == CALIBRATION_NONE:
            raise ValueError(
                f"room {self.name} cannot use valve_mode modulate without the calibration feed"
            )
        return self


class HouseConfig(BaseModel):
    """The house settings and the room list of one config entry."""

    model_config = FROZEN

    name: str = "Room Climate"
    boiler_entity: str = DEFAULT_BOILER_ENTITY
    boiler_on_temperature: float = Field(default=DEFAULT_BOILER_ON_TEMPERATURE, ge=5.0, le=32.0)
    boiler_off_temperature: float = Field(default=DEFAULT_BOILER_OFF_TEMPERATURE, ge=5.0, le=32.0)
    boiler_on_hvac_mode: Literal["heat", "auto"] = "heat"
    boiler_off_hvac_mode: Literal["off", "auto"] = "off"
    outdoor_sources: tuple[str, ...] = DEFAULT_OUTDOOR_SOURCES
    lockout_temperature: float = Field(default=DEFAULT_LOCKOUT_TEMPERATURE, ge=-20.0, le=40.0)
    lockout_hysteresis: float = Field(default=DEFAULT_LOCKOUT_HYSTERESIS, ge=0.0, le=10.0)
    min_on_seconds: int = Field(default=DEFAULT_MIN_ON_SECONDS, ge=0, le=3600)
    min_off_seconds: int = Field(default=DEFAULT_MIN_OFF_SECONDS, ge=0, le=3600)
    update_interval_seconds: int = Field(default=DEFAULT_UPDATE_INTERVAL_SECONDS, ge=10, le=600)
    dry_run: bool = True
    master_enabled: bool = True
    ema_seconds: int = Field(default=DEFAULT_EMA_SECONDS, ge=60, le=10800)
    simulation: bool = False
    rooms: tuple[RoomConfig, ...] = ()

    @model_validator(mode="after")
    def _validate_house(self) -> Self:
        if self.boiler_on_temperature <= self.boiler_off_temperature:
            raise ValueError("boiler_on_temperature must exceed boiler_off_temperature")
        if not self.outdoor_sources:
            raise ValueError("at least one outdoor source is required")
        if self.simulation:
            for entity_id in (
                self.boiler_entity,
                *self.outdoor_sources,
                *(
                    item
                    for room in self.rooms
                    for item in (*room.valves, *(source.entity_id for source in room.sources))
                ),
            ):
                if SIM_MARKER not in entity_id:
                    raise ValueError(f"a simulated house cannot name {entity_id}")
        room_ids: dict[str, str] = {}
        for room in self.rooms:
            owner = room_ids.get(room.room_id)
            if owner is not None:
                raise ValueError(f"room_id {room.room_id} is used by {owner} and {room.name}")
            room_ids[room.room_id] = room.name
        seen: dict[str, str] = {}
        for room in self.rooms:
            for entity_id in (*room.valves, *(source.entity_id for source in room.sources)):
                self._claim(seen, entity_id, room.name)
        return self

    @staticmethod
    def _claim(seen: dict[str, str], entity_id: str, room_name: str) -> None:
        owner = seen.get(entity_id)
        if owner is not None:
            raise ValueError(f"{entity_id} is claimed by {owner} and {room_name}")
        seen[entity_id] = room_name

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> HouseConfig:
        """Build the house model from the config entry options payload."""
        return cls.model_validate(dict(options))


class RuntimeRoom(BaseModel):
    """The state of one room that survives between cycles."""

    model_config = FROZEN

    room_id: str
    target_temperature: float | None = None
    hvac_mode: Literal["off", "heat"] = "heat"
    calling: bool = False
    ema_temperature: float | None = None
    updated_at: datetime | None = None


class ControlInput(BaseModel):
    """Everything one cycle reads."""

    model_config = FROZEN

    now: datetime
    house: HouseConfig
    temperatures: Mapping[str, float | None]
    valve_demands: Mapping[str, bool]
    window_states: Mapping[str, str]
    runtimes: tuple[RuntimeRoom, ...]
    last_actuator_targets: Mapping[str, float]
    last_calibration_values: Mapping[str, float]
    last_command_at: Mapping[str, datetime]
    valve_available: Mapping[str, bool] = Field(default_factory=dict)
    outdoor_lockout_active: bool
    boiler_on: bool
    boiler_state_since: datetime | None
    master_enabled: bool
    override_active: bool


class RoomCommand(BaseModel):
    """The commands and the reported state of one room."""

    model_config = FROZEN

    room_id: str
    calling: bool
    window_open: bool
    room_temperature: float | None
    ema_temperature: float | None
    usable_sources: int
    degraded: bool
    valves_unavailable: int = 0
    valve_targets: Mapping[str, float]
    calibration_values: Mapping[str, float]


class BoilerCommand(BaseModel):
    """The wanted boiler state of one cycle."""

    model_config = FROZEN

    turn_on: bool
    reason: str
    send: bool


class ControlOutput(BaseModel):
    """The result of one cycle."""

    model_config = FROZEN

    rooms: tuple[RoomCommand, ...]
    boiler: BoilerCommand
    lockout_active: bool
    outdoor_temperature: float | None
    house_ema_temperature: float | None
    status: str

"""The schedule file, its parse, and the lookup of a setpoint.

One YAML file holds the setpoint of a whole house: a `default` schedule, named
`groups`, and a `rooms` map of a room id to a group name or to a schedule. The
module is host independent on purpose, so the parse and the lookup stay unit
testable in the same way as `control.py`: it imports no Home Assistant module.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    field_validator,
)

from .const import SCHEDULE_FILE, SCHEDULE_FILE_SIM

# The day tokens of a block, in the order of `datetime.weekday()`.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# The schedule a room follows when it names neither a group nor its own.
GROUP_DEFAULT = "default"

# The six days before today that the lookup reads back through.
LOOKBACK_DAYS = 6

# The next change reaches one week ahead, so a weekly block is always found.
FORWARD_DAYS = 7

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

FROZEN = ConfigDict(frozen=True, extra="forbid")


class ScheduleError(Exception):
    """A fault in the schedule file, or in the resolution of one room."""


class ScheduleBlock(BaseModel):
    """One block of a schedule: a time, a temperature, and the days it applies."""

    model_config = FROZEN

    time: str
    temperature: float
    days: tuple[str, ...] | None = None

    @field_validator("time")
    @classmethod
    def _check_time(cls, value: str) -> str:
        if _TIME.match(value) is None:
            raise ValueError(f"time {value!r} is not a 24-hour HH:MM time")
        return value

    @field_validator("days")
    @classmethod
    def _check_days(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        days = tuple(day.lower() for day in value)
        unknown = [day for day in days if day not in WEEKDAYS]
        if unknown:
            raise ValueError(f"day {unknown[0]!r} is not one of {', '.join(WEEKDAYS)}")
        return days

    @property
    def minute_of_day(self) -> int:
        """Return the time of the block as minutes since midnight."""
        hour, minute = self.time.split(":")
        return int(hour) * 60 + int(minute)

    def applies_on(self, weekday: int) -> bool:
        """Return whether the block applies on one weekday index."""
        return self.days is None or WEEKDAYS[weekday] in self.days


class Schedule(RootModel[tuple[ScheduleBlock, ...]]):
    """The setpoints of a room over a day, or over a week."""

    model_config = ConfigDict(frozen=True)

    root: tuple[ScheduleBlock, ...] = Field(min_length=1)


class NextChange(BaseModel):
    """The instant and the temperature of the next block of a schedule."""

    model_config = FROZEN

    at: datetime
    temperature: float


class SetpointResolution(BaseModel):
    """What the file answers for one room at one instant."""

    model_config = FROZEN

    temperature: float | None
    group: str
    next_change: NextChange | None = None
    errors: tuple[str, ...] = ()


class ScheduleFile(BaseModel):
    """The whole file: a default schedule, named groups, and the rooms."""

    model_config = FROZEN

    default: Schedule | None = None
    groups: dict[str, Schedule] = Field(default_factory=dict)
    rooms: dict[str, str | Schedule] = Field(default_factory=dict)


def parse_schedule_file(text: str) -> ScheduleFile:
    """Parse the schedule file, and raise `ScheduleError` on any fault."""
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ScheduleError(f"the schedule file is not valid YAML: {error}") from error
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ScheduleError("the schedule file must be a mapping of keys to schedules")
    try:
        return ScheduleFile.model_validate(payload)
    except ValidationError as error:
        raise ScheduleError(f"the schedule file is not valid: {error}") from error


def _schedule_for(schedule_file: ScheduleFile, room_id: str) -> tuple[Schedule | None, str]:
    """Return the schedule of one room and the name it follows."""
    if room_id in schedule_file.rooms:
        value = schedule_file.rooms[room_id]
        if isinstance(value, str):
            group = schedule_file.groups.get(value)
            if group is None:
                raise ScheduleError(
                    f"room {room_id} names the group {value!r}, which the file does not define"
                )
            return group, value
        return value, room_id
    return schedule_file.default, GROUP_DEFAULT


def _usable(
    schedule: Schedule, room_id: str, minimum: float, maximum: float
) -> tuple[list[ScheduleBlock], list[str]]:
    """Split a schedule into the blocks of the room's band and the refused ones."""
    usable: list[ScheduleBlock] = []
    errors: list[str] = []
    for block in schedule.root:
        if minimum <= block.temperature <= maximum:
            usable.append(block)
        else:
            errors.append(
                f"room {room_id}: the block at {block.time} with temperature "
                f"{block.temperature} is outside the band {minimum} to {maximum}"
            )
    return usable, errors


def _latest_before(blocks: list[ScheduleBlock], at: datetime) -> ScheduleBlock | None:
    """Return the last block that applies on or before the instant, over seven days."""
    minute_now = at.hour * 60 + at.minute
    for offset in range(LOOKBACK_DAYS + 1):
        day = at.date() - timedelta(days=offset)
        candidates = [block for block in blocks if block.applies_on(day.weekday())]
        if offset == 0:
            candidates = [block for block in candidates if block.minute_of_day <= minute_now]
        if candidates:
            return max(candidates, key=lambda block: block.minute_of_day)
    return None


def _next_change(blocks: list[ScheduleBlock], at: datetime) -> NextChange | None:
    """Return the next block after the instant, over seven days."""
    minute_now = at.hour * 60 + at.minute
    for offset in range(FORWARD_DAYS + 1):
        day = at.date() + timedelta(days=offset)
        candidates = [block for block in blocks if block.applies_on(day.weekday())]
        if offset == 0:
            candidates = [block for block in candidates if block.minute_of_day > minute_now]
        if candidates:
            block = min(candidates, key=lambda item: item.minute_of_day)
            moment = datetime.combine(
                day,
                time(block.minute_of_day // 60, block.minute_of_day % 60),
                tzinfo=at.tzinfo,
            )
            return NextChange(at=moment, temperature=block.temperature)
    return None


def resolve_setpoint(
    schedule_file: ScheduleFile | None,
    room_id: str,
    at: datetime,
    *,
    minimum: float,
    maximum: float,
) -> SetpointResolution:
    """Return the setpoint of one room at one instant, its group and the next change.

    A missing file leaves every room without a setpoint, and it is not an error. A
    block whose temperature is outside the room's band is refused for that room. A
    room whose schedule yields no setpoint reports the reason and takes no setpoint.
    """
    if schedule_file is None:
        return SetpointResolution(temperature=None, group=GROUP_DEFAULT)
    schedule, group = _schedule_for(schedule_file, room_id)
    if schedule is None:
        return SetpointResolution(
            temperature=None,
            group=group,
            errors=(f"room {room_id}: the file gives it no schedule, and it defines no default",),
        )
    usable, errors = _usable(schedule, room_id, minimum, maximum)
    block = _latest_before(usable, at)
    if block is None:
        errors.append(f"room {room_id}: no block of the schedule applies within the seven days")
    return SetpointResolution(
        temperature=None if block is None else block.temperature,
        group=group,
        next_change=_next_change(usable, at),
        errors=tuple(errors),
    )


def schedule_path_name(simulation: bool) -> str:
    """Return the file name of one entry: the bench reads its own file."""
    return SCHEDULE_FILE_SIM if simulation else SCHEDULE_FILE

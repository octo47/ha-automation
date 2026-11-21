"""The schedule file, its parse, and the lookup of a setpoint.

The lookup is pure: it takes the parsed file, a room id and an instant, and it
answers the setpoint, the schedule it came from and the next change.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.room_climate.schedule import (
    GROUP_DEFAULT,
    Schedule,
    ScheduleBlock,
    ScheduleError,
    ScheduleFile,
    SetpointResolution,
    parse_schedule_file,
    resolve_setpoint,
)

# A Wednesday at noon, and the days around it, so the day rules are visible.
WEDNESDAY_NOON = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
WEDNESDAY_EARLY = datetime(2026, 11, 18, 2, 0, tzinfo=UTC)
SATURDAY_MORNING = datetime(2026, 11, 21, 6, 0, tzinfo=UTC)
SATURDAY_LATE = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)

BAND = {"minimum": 5.0, "maximum": 30.0}


def schedule(*blocks: ScheduleBlock) -> Schedule:
    """One schedule from its blocks, in the order the operator wrote them."""
    return Schedule.model_validate(list(blocks))


def resolve(
    schedule_file: ScheduleFile | Schedule, room_id: str, at: datetime
) -> SetpointResolution:
    """Resolve one room against a file, with the band of a normal room."""
    if isinstance(schedule_file, Schedule):
        schedule_file = ScheduleFile(default=schedule_file)
    return resolve_setpoint(schedule_file, room_id, at, **BAND)


def test_the_setpoint_is_the_last_block_at_or_before_the_instant() -> None:
    blocks = schedule(
        ScheduleBlock(time="06:00", temperature=21.0),
        ScheduleBlock(time="09:00", temperature=19.0),
        ScheduleBlock(time="22:00", temperature=16.0),
    )
    assert resolve(blocks, "front_room", WEDNESDAY_NOON).temperature == 19.0


def test_a_block_before_its_time_does_not_apply() -> None:
    blocks = schedule(ScheduleBlock(time="22:00", temperature=16.0))
    assert resolve(blocks, "front_room", WEDNESDAY_NOON).temperature == 16.0
    assert (
        resolve(blocks, "front_room", datetime(2026, 11, 18, 21, 0, tzinfo=UTC)).temperature == 16.0
    )


def test_a_block_that_names_days_applies_on_those_days_only() -> None:
    blocks = schedule(
        ScheduleBlock(time="06:00", temperature=21.0, days=("mon", "tue", "wed", "thu", "fri")),
        ScheduleBlock(time="06:00", temperature=17.0, days=("sat", "sun")),
    )
    assert resolve(blocks, "front_room", WEDNESDAY_NOON).temperature == 21.0
    assert resolve(blocks, "front_room", SATURDAY_LATE).temperature == 17.0


def test_the_evening_block_holds_through_the_night_to_the_morning_block() -> None:
    """A Friday evening block holds on Saturday until the Saturday morning block."""
    blocks = schedule(
        ScheduleBlock(time="22:00", temperature=17.0, days=("fri",)),
        ScheduleBlock(time="08:00", temperature=21.0, days=("sat",)),
    )
    assert resolve(blocks, "front_room", SATURDAY_MORNING).temperature == 17.0
    assert resolve(blocks, "front_room", SATURDAY_LATE).temperature == 21.0


def test_the_look_back_reaches_the_six_days_before_today() -> None:
    """A Monday block holds on the following Saturday, five days later."""
    blocks = schedule(ScheduleBlock(time="23:00", temperature=18.0, days=("mon",)))
    assert resolve(blocks, "front_room", SATURDAY_MORNING).temperature == 18.0


def test_no_block_in_the_seven_days_gives_no_setpoint_and_not_zero() -> None:
    blocks = schedule(ScheduleBlock(time="08:00", temperature=21.0, days=()))
    resolution = resolve(blocks, "front_room", WEDNESDAY_NOON)
    assert resolution.temperature is None
    assert any("front_room" in message for message in resolution.errors)


def test_a_schedule_that_yields_nothing_reports_the_reason() -> None:
    """The one block is a Wednesday morning block, and the instant is before it."""
    blocks = schedule(ScheduleBlock(time="08:00", temperature=21.0, days=("wed",)))
    resolution = resolve(blocks, "front_room", WEDNESDAY_EARLY)
    assert resolution.temperature is None
    assert any("front_room" in message and "seven days" in message for message in resolution.errors)


def test_a_missing_file_gives_every_room_no_setpoint() -> None:
    resolution = resolve_setpoint(None, "front_room", WEDNESDAY_NOON, **BAND)
    assert resolution.temperature is None
    assert resolution.group == GROUP_DEFAULT
    assert resolution.next_change is None


def test_a_room_that_names_a_group_follows_the_group() -> None:
    schedule_file = parse_schedule_file(
        """
        default:
          - time: "08:00"
            temperature: 18.0
        groups:
          morning:
            - time: "06:00"
              temperature: 21.0
        rooms:
          front_room: morning
        """
    )
    assert resolve(schedule_file, "front_room", WEDNESDAY_NOON).temperature == 21.0
    assert resolve(schedule_file, "front_room", WEDNESDAY_NOON).group == "morning"
    assert resolve(schedule_file, "other_room", WEDNESDAY_NOON).temperature == 18.0
    assert resolve(schedule_file, "other_room", WEDNESDAY_NOON).group == GROUP_DEFAULT


def test_a_room_that_carries_its_own_schedule_follows_it() -> None:
    schedule_file = parse_schedule_file(
        """
        default:
          - time: "08:00"
            temperature: 18.0
        groups:
          morning:
            - time: "06:00"
              temperature: 21.0
        rooms:
          front_room:
            - time: "07:00"
              temperature: 23.0
          back_room: morning
        """
    )
    assert resolve(schedule_file, "front_room", WEDNESDAY_NOON).temperature == 23.0
    assert resolve(schedule_file, "back_room", WEDNESDAY_NOON).temperature == 21.0


def test_a_group_that_no_group_defines_is_an_error_that_names_room_and_group() -> None:
    schedule_file = parse_schedule_file(
        """
        default:
          - time: "08:00"
            temperature: 18.0
        rooms:
          front_room: evening
        """
    )
    with pytest.raises(ScheduleError) as error:
        resolve(schedule_file, "front_room", WEDNESDAY_NOON)
    assert "front_room" in str(error.value)
    assert "evening" in str(error.value)


def test_a_block_outside_the_band_of_a_room_is_refused_and_reported() -> None:
    blocks = schedule(
        ScheduleBlock(time="06:00", temperature=21.0),
        ScheduleBlock(time="09:00", temperature=40.0),
    )
    resolution = resolve(blocks, "front_room", WEDNESDAY_NOON)
    assert resolution.temperature == 21.0
    assert any("front_room" in message and "40.0" in message for message in resolution.errors)


def test_a_block_of_the_evening_outside_the_band_falls_back_to_the_morning() -> None:
    blocks = schedule(
        ScheduleBlock(time="06:00", temperature=21.0),
        ScheduleBlock(time="22:00", temperature=2.0),
    )
    resolution = resolve(blocks, "front_room", WEDNESDAY_EARLY)
    assert resolution.temperature == 21.0


def test_the_next_change_names_the_instant_and_the_temperature_of_the_next_block() -> None:
    blocks = schedule(
        ScheduleBlock(time="06:00", temperature=21.0),
        ScheduleBlock(time="22:00", temperature=16.0),
    )
    next_change = resolve(blocks, "front_room", WEDNESDAY_NOON).next_change
    assert next_change is not None
    assert next_change.at == datetime(2026, 11, 18, 22, 0, tzinfo=UTC)
    assert next_change.temperature == 16.0


def test_the_next_change_reaches_the_next_day_when_the_day_has_no_later_block() -> None:
    blocks = schedule(ScheduleBlock(time="06:00", temperature=21.0))
    next_change = resolve(blocks, "front_room", WEDNESDAY_NOON).next_change
    assert next_change is not None
    assert next_change.at == datetime(2026, 11, 19, 6, 0, tzinfo=UTC)


def test_a_malformed_file_is_an_error() -> None:
    with pytest.raises(ScheduleError):
        parse_schedule_file("default: [oops\n  - time: ")


def test_a_bad_time_is_an_error_and_never_a_silent_default() -> None:
    with pytest.raises(ScheduleError):
        parse_schedule_file('default:\n  - time: "25:00"\n    temperature: 21.0\n')


def test_a_bad_temperature_is_an_error() -> None:
    with pytest.raises(ScheduleError):
        parse_schedule_file('default:\n  - time: "08:00"\n    temperature: warm\n')


def test_a_unknown_day_is_an_error() -> None:
    with pytest.raises(ScheduleError):
        parse_schedule_file(
            'default:\n  - time: "08:00"\n    temperature: 21.0\n    days: [monday]\n'
        )


def test_an_empty_file_leaves_every_room_without_a_setpoint() -> None:
    schedule_file = parse_schedule_file("")
    assert resolve(schedule_file, "front_room", WEDNESDAY_NOON).temperature is None

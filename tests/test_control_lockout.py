"""The lockout starts at the threshold and clears only after the hysteresis."""

from __future__ import annotations

from custom_components.room_climate.control import next_lockout, outdoor_temperature


def test_lockout_starts_at_the_threshold() -> None:
    assert next_lockout(False, 18.0, 18.0, 1.0) is True
    assert next_lockout(False, 17.9, 18.0, 1.0) is False


def test_lockout_holds_inside_the_hysteresis_band() -> None:
    assert next_lockout(True, 17.5, 18.0, 1.0) is True


def test_lockout_clears_below_the_band() -> None:
    assert next_lockout(True, 17.0, 18.0, 1.0) is False


def test_a_missing_outdoor_reading_never_starts_a_lockout() -> None:
    assert next_lockout(False, None, 18.0, 1.0) is False


def test_a_missing_outdoor_reading_does_not_hold_a_lockout() -> None:
    assert next_lockout(True, None, 18.0, 1.0) is False


def test_outdoor_mean_over_the_usable_sources() -> None:
    values = {"sensor.outdoor": 10.0, "weather.home": 12.0}
    assert outdoor_temperature(("sensor.outdoor", "weather.home"), values) == 11.0


def test_outdoor_temperature_ignores_a_missing_reading() -> None:
    values = {"sensor.outdoor": None, "weather.home": 12.0}
    assert outdoor_temperature(("sensor.outdoor", "weather.home"), values) == 12.0


def test_outdoor_temperature_without_a_reading() -> None:
    assert outdoor_temperature(("sensor.outdoor",), {"sensor.outdoor": None}) is None

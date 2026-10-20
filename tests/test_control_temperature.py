"""The weighted mean of the usable temperature sources."""

from __future__ import annotations

from custom_components.room_climate.control import usable_source_count, weighted_temperature
from custom_components.room_climate.models import TemperatureSource


def source(entity_id: str, weight: float = 1.0) -> TemperatureSource:
    return TemperatureSource(entity_id=entity_id, weight=weight)


def test_one_source_is_the_room_temperature() -> None:
    assert weighted_temperature((source("sensor.trv"),), {"sensor.trv": 21.4}) == 21.4


def test_weighted_mean_over_three_sources() -> None:
    sources = (source("sensor.a", 1.0), source("sensor.b", 3.0), source("sensor.c", 1.0))
    values = {"sensor.a": 20.0, "sensor.b": 22.0, "sensor.c": 24.0}
    assert weighted_temperature(sources, values) == 22.0


def test_a_heavy_trv_weight_pulls_the_mean_towards_the_valve() -> None:
    sources = (source("sensor.room", 1.0), source("sensor.trv", 3.0))
    assert weighted_temperature(sources, {"sensor.room": 20.0, "sensor.trv": 24.0}) == 23.0


def test_unusable_sources_are_dropped() -> None:
    sources = (source("sensor.a"), source("sensor.b"), source("sensor.c"))
    values = {"sensor.a": 20.0, "sensor.b": None, "sensor.c": 22.0}
    assert weighted_temperature(sources, values) == 21.0


def test_all_sources_unusable_gives_no_temperature() -> None:
    sources = (source("sensor.a"), source("sensor.b"))
    assert weighted_temperature(sources, {"sensor.a": None, "sensor.b": None}) is None


def test_an_impossible_value_is_dropped() -> None:
    sources = (source("sensor.a"), source("sensor.b"))
    assert weighted_temperature(sources, {"sensor.a": 900.0, "sensor.b": 21.0}) == 21.0


def test_usable_source_count_matches_the_mean() -> None:
    sources = (source("sensor.a"), source("sensor.b"))
    assert usable_source_count(sources, {"sensor.a": 21.0, "sensor.b": None}) == 1
    assert usable_source_count(sources, {"sensor.a": 21.0, "sensor.b": 22.0}) == 2

"""Decision rules for room heating."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .const import MAX_PLAUSIBLE_TEMPERATURE, MIN_PLAUSIBLE_TEMPERATURE
from .models import TemperatureSource


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

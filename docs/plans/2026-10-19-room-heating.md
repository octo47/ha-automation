# Room Climate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Home Assistant custom integration `room_climate` from
`docs/room-heating.spec.md`. The integration groups TRVs and temperature sensors into rooms,
decides the room demand, and decides the Hive boiler state.

**Architecture:** One config entry holds the house, and its options hold the room list. A pure
module `control.py` holds every decision rule and imports no Home Assistant code. A
`DataUpdateCoordinator` reads states, calls `control.decide()`, and applies the returned
commands. One climate entity per room is the dashboard surface and the Scheduler target.

**Tech Stack:** Python 3.13, Home Assistant 2026.10.1, pydantic 2, `uv`, `pytest`,
`pytest-homeassistant-custom-component`, `ruff`, `pyright`.

**Spec:** `docs/room-heating.spec.md`

## Global Constraints

Copy these rules into every task brief. They come from the spec and from the repository rules.

- Every command runs through `uv run` or through a `make` target. Never a bare `python3`.
- `ruff check` and `ruff format --check` must pass. Line length is 100.
- `pyright` must pass with the strict profile on `custom_components/room_climate`.
- Every boundary carries a pydantic model. No open dictionary crosses a function boundary.
  A keyed index over one value type (`Mapping[str, float]`) is allowed, and an open payload
  (`dict[str, Any]`) is not.
- A comment states the current state of the code. No history, no task names, no attribution.
  `TODO:` is the only forward-looking marker.
- The decision logic has one home: `control.py`. No second copy, in an automation or in a
  template.
- Tests run in seconds. No test sleeps, and no test waits for the clock. Use
  `async_fire_time_changed` and a frozen clock.
- No test calls the live Home Assistant instance, and no test calls `climate.heating_heat`.
- The component never commands `climate.heating_water`.
- Default values, in one place: `boiler_on_temperature` 31.0, `boiler_off_temperature` 15.0,
  `lockout_temperature` 18.0, `lockout_hysteresis` 1.0, `min_on_seconds` 300,
  `min_off_seconds` 300, `update_interval_seconds` 30, `hysteresis` 0.2, `off_setpoint` 5.0,
  `dry_run` true.
- The domain is `room_climate`. The manifest requires `"version"`, because HACS updates a
  custom integration by the version.
- The manifest declares `requirements: ["pydantic>=2.11,<3"]`.

## Environment facts that constrain the tasks

- The instance is Home Assistant 2026.10.1. Pin the test harness to the release whose Home
  Assistant version is 2026.10.1: run
  `uv add --dev pytest-homeassistant-custom-component`, then verify with
  `uv run python -c "import homeassistant.const as c; print(c.__version__)"`. If the version
  differs, pin the harness release that carries 2026.10.1.
- The live instance has HACS, Better Thermostat 1.9.3, and an existing
  `automation.heat_on_demand`. Task 11 disables that automation during the live run.
- No write access to the Home Assistant filesystem exists from this host. HACS is the install
  route.

## Review Focus

These inputs are not in the spec's tables. Each one has a test in the task that owns the code.

1. An outdoor source that is `unavailable` or `unknown` must not start a lockout. A failed
   weather station must not stop the heating.
2. A room whose sources are all unusable must not call for heat, must hold its actuators at
   `off_setpoint`, and must report `degraded`.
3. An actuator that is `unavailable` must not raise, and the room's other actuators must still
   receive the setpoint.
4. A setpoint at or below `min_temp`, and a mode of `off`, must both release the room and stop
   the boiler when no other room calls.
5. A lockout that arrives while the boiler runs must stop the boiler at once, inside
   `min_on_seconds`.

---

### Task 1: Repository scaffold and the import guard

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.gitignore`, `hacs.json`
- Create: `custom_components/room_climate/__init__.py`, `const.py`, `manifest.json`
- Create: `custom_components/room_climate/translations/en.json`
- Create: `tests/conftest.py`, `tests/test_package_layout.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the package `custom_components.room_climate`, the constant `DOMAIN`,
  the constant `DEFAULT_*` values, and the test fixture `enable_custom_integrations`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_package_layout.py
"""The layout rules the component must hold."""

from __future__ import annotations

import ast
import pathlib

COMPONENT = pathlib.Path(__file__).parents[1] / "custom_components" / "room_climate"


def test_domain_matches_directory() -> None:
    import json

    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["domain"] == COMPONENT.name
    assert manifest["version"]


def test_control_module_has_no_home_assistant_import() -> None:
    """control.py is host independent, so its rules stay unit testable."""
    tree = ast.parse((COMPONENT / "control.py").read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name.startswith("homeassistant")]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_package_layout.py -v`
Expected: FAIL, because `custom_components/room_climate/manifest.json` does not exist.

- [ ] **Step 3: Write the scaffolding**

```json
// custom_components/room_climate/manifest.json
{
  "domain": "room_climate",
  "name": "Room Climate",
  "version": "0.1.0",
  "documentation": "https://github.com/octo47/ha-automation",
  "issue_tracker": "https://github.com/octo47/ha-automation/issues",
  "codeowners": ["@octo47"],
  "config_flow": true,
  "integration_type": "hub",
  "iot_class": "local_polling",
  "requirements": ["pydantic>=2.11,<3"],
  "loggers": ["custom_components.room_climate"]
}
```

```python
# custom_components/room_climate/control.py
"""Decision rules for room heating."""
```

```python
# custom_components/room_climate/const.py
"""Constants of the integration."""

DOMAIN = "room_climate"

CONF_BOILER_ENTITY = "boiler_entity"
CONF_BOILER_ON_TEMPERATURE = "boiler_on_temperature"
CONF_BOILER_OFF_TEMPERATURE = "boiler_off_temperature"
CONF_BOILER_ON_HVAC_MODE = "boiler_on_hvac_mode"
CONF_BOILER_OFF_HVAC_MODE = "boiler_off_hvac_mode"
CONF_OUTDOOR_SOURCES = "outdoor_sources"
CONF_LOCKOUT_TEMPERATURE = "lockout_temperature"
CONF_LOCKOUT_HYSTERESIS = "lockout_hysteresis"
CONF_MIN_ON_SECONDS = "min_on_seconds"
CONF_MIN_OFF_SECONDS = "min_off_seconds"
CONF_UPDATE_INTERVAL_SECONDS = "update_interval_seconds"
CONF_DRY_RUN = "dry_run"
CONF_MASTER_ENABLED = "master_enabled"
CONF_ROOMS = "rooms"

DEFAULT_BOILER_ENTITY = "climate.heating_heat"
DEFAULT_BOILER_ON_TEMPERATURE = 31.0
DEFAULT_BOILER_OFF_TEMPERATURE = 15.0
DEFAULT_OUTDOOR_SOURCES = ("sensor.outdoor_temperature",)
DEFAULT_LOCKOUT_TEMPERATURE = 18.0
DEFAULT_LOCKOUT_HYSTERESIS = 1.0
DEFAULT_MIN_ON_SECONDS = 300
DEFAULT_MIN_OFF_SECONDS = 300
DEFAULT_UPDATE_INTERVAL_SECONDS = 30
DEFAULT_HYSTERESIS = 0.2
DEFAULT_OFF_SETPOINT = 5.0
DEFAULT_DRY_RUN = True

REASON_MASTER_OFF = "master_off"
REASON_LOCKOUT = "lockout"
REASON_ROOM_DEMAND = "room_demand"
REASON_NO_DEMAND = "no_demand"

STATUS_HEATING = "heating"
STATUS_IDLE = "idle"
STATUS_LOCKED_OUT = "locked_out"
STATUS_DISABLED = "disabled"
STATUS_DRY_RUN = "dry_run"
STATUS_DEGRADED = "degraded"

SERVICE_RECOMPUTE = "recompute"
```

```json
// hacs.json
{
  "name": "Room Climate",
  "render_readme": true,
  "homeassistant": "2026.10.0"
}
```

```toml
# pyproject.toml (excerpt)
[project]
name = "ha-room-climate"
version = "0.1.0"
requires-python = ">=3.13"

[dependency-groups]
dev = [
  "pytest",
  "pytest-asyncio",
  "pytest-homeassistant-custom-component",
  "ruff",
  "pyright",
]

[tool.ruff]
line-length = 100

[tool.pyright]
include = ["custom_components/room_climate"]
typeCheckingMode = "strict"
```

```make
# Makefile
.PHONY: test lint typecheck check
test:
	uv run pytest
lint:
	uv run ruff check . && uv run ruff format --check .
typecheck:
	uv run pyright
check: lint typecheck test
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_package_layout.py -v`
Expected: PASS, two tests.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml Makefile .gitignore hacs.json custom_components tests
git commit -m "scaffold room_climate integration"
```

---

### Task 2: Configuration models and their validation

**Files:**
- Create: `custom_components/room_climate/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TemperatureSource`, `RoomConfig`, `HouseConfig`, `RuntimeRoom`,
  `ControlInput`, `RoomCommand`, `BoilerCommand`, `ControlOutput`, and
  `HouseConfig.from_options(options: Mapping[str, Any]) -> HouseConfig`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
"""Configuration models reject a configuration the control rules cannot honour."""

from __future__ import annotations

import pytest
from custom_components.room_climate.models import HouseConfig, RoomConfig, TemperatureSource
from pydantic import ValidationError


def house(**overrides: object) -> HouseConfig:
    payload: dict[str, object] = {
        "boiler_entity": "climate.heating_heat",
        "boiler_on_temperature": 31.0,
        "boiler_off_temperature": 15.0,
        "outdoor_sources": ("sensor.outdoor_temperature",),
        "lockout_temperature": 18.0,
        "rooms": (
            RoomConfig(
                room_id="front_room",
                name="Front Room",
                actuators=("climate.trv_front_room",),
                sources=(TemperatureSource(entity_id="sensor.trv_front_room_local_temperature"),),
            ),
        ),
    }
    payload.update(overrides)
    return HouseConfig(**payload)


def test_weights_scale_the_mean() -> None:
    source = TemperatureSource(entity_id="sensor.a", weight=2.0)
    assert source.weight == 2.0


@pytest.mark.parametrize("weight", [0.0, -1.0])
def test_weight_must_be_positive(weight: float) -> None:
    with pytest.raises(ValidationError):
        TemperatureSource(entity_id="sensor.a", weight=weight)


def test_room_needs_one_actuator() -> None:
    with pytest.raises(ValidationError):
        RoomConfig(room_id="front_room", name="Front Room", actuators=(), sources=())


def test_house_rejects_one_actuator_in_two_rooms() -> None:
    shared = RoomConfig(
        room_id="a",
        name="A",
        actuators=("climate.trv_front_room",),
        sources=(TemperatureSource(entity_id="sensor.a"),),
    )
    other = RoomConfig(
        room_id="b",
        name="B",
        actuators=("climate.trv_front_room",),
        sources=(TemperatureSource(entity_id="sensor.b"),),
    )
    with pytest.raises(ValidationError) as error:
        house(rooms=(shared, other))
    assert "trv_front_room" in str(error.value)
    assert "A" in str(error.value) and "B" in str(error.value)


def test_house_rejects_one_source_in_two_rooms() -> None:
    first = RoomConfig(
        room_id="a",
        name="A",
        actuators=("climate.trv_a",),
        sources=(TemperatureSource(entity_id="sensor.shared"),),
    )
    second = RoomConfig(
        room_id="b",
        name="B",
        actuators=("climate.trv_b",),
        sources=(TemperatureSource(entity_id="sensor.shared"),),
    )
    with pytest.raises(ValidationError) as error:
        house(rooms=(first, second))
    assert "sensor.shared" in str(error.value)


def test_on_temperature_must_exceed_off_temperature() -> None:
    with pytest.raises(ValidationError):
        house(boiler_on_temperature=15.0, boiler_off_temperature=20.0)


def test_from_options_round_trips_the_entry_payload() -> None:
    payload = {
        "boiler_entity": "climate.heating_heat",
        "boiler_on_temperature": 31.0,
        "boiler_off_temperature": 15.0,
        "outdoor_sources": ["sensor.outdoor_temperature"],
        "lockout_temperature": 18.0,
        "rooms": [
            {
                "room_id": "front_room",
                "name": "Front Room",
                "actuators": ["climate.trv_front_room"],
                "sources": [{"entity_id": "sensor.trv_front_room_local_temperature"}],
            }
        ],
    }
    config = HouseConfig.from_options(payload)
    assert config.rooms[0].actuators == ("climate.trv_front_room",)
    assert config.rooms[0].sources[0].weight == 1.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: custom_components.room_climate.models`.

- [ ] **Step 3: Write the models**

```python
# custom_components/room_climate/models.py
"""Typed configuration and cycle models of the integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Mapping, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .const import (
    DEFAULT_BOILER_ENTITY,
    DEFAULT_BOILER_OFF_TEMPERATURE,
    DEFAULT_BOILER_ON_TEMPERATURE,
    DEFAULT_HYSTERESIS,
    DEFAULT_LOCKOUT_HYSTERESIS,
    DEFAULT_LOCKOUT_TEMPERATURE,
    DEFAULT_MIN_OFF_SECONDS,
    DEFAULT_MIN_ON_SECONDS,
    DEFAULT_OFF_SETPOINT,
    DEFAULT_OUTDOOR_SOURCES,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
)

FROZEN = ConfigDict(frozen=True, extra="forbid")


class TemperatureSource(BaseModel):
    """One temperature entity of a room, with its weight in the mean."""

    model_config = FROZEN

    entity_id: str
    weight: float = Field(default=1.0, gt=0)


class RoomConfig(BaseModel):
    """The actuators and the temperature sources of one room."""

    model_config = FROZEN

    room_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    actuators: tuple[str, ...] = Field(min_length=1)
    sources: tuple[TemperatureSource, ...] = Field(min_length=1)
    area_id: str | None = None
    min_temp: float = Field(default=5.0, ge=5.0, le=20.0)
    max_temp: float = Field(default=30.0, ge=15.0, le=35.0)
    hysteresis: float = Field(default=DEFAULT_HYSTERESIS, ge=0.0, le=2.0)
    off_setpoint: float = Field(default=DEFAULT_OFF_SETPOINT, ge=5.0, le=12.0)
    setpoint_offset: float = Field(default=0.0, ge=-3.0, le=3.0)


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
    rooms: tuple[RoomConfig, ...] = ()

    @model_validator(mode="after")
    def _validate_house(self) -> Self:
        if self.boiler_on_temperature <= self.boiler_off_temperature:
            raise ValueError("boiler_on_temperature must exceed boiler_off_temperature")
        if not self.outdoor_sources:
            raise ValueError("at least one outdoor source is required")
        for kind, extract in (
            ("actuator", lambda room: room.actuators),
            ("source", lambda room: tuple(source.entity_id for source in room.sources)),
        ):
            owners: dict[str, str] = {}
            for room in self.rooms:
                for entity_id in extract(room):
                    if entity_id in owners:
                        raise ValueError(
                            f"{kind} {entity_id} is claimed by {owners[entity_id]} and {room.name}"
                        )
                    owners[entity_id] = room.name
        return self

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> HouseConfig:
        """Build the house model from the config entry options payload."""
        return cls.model_validate(dict(options))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS, eight tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/models.py tests/test_models.py
git commit -m "add room climate configuration models"
```

---

### Task 3: Weighted room temperature

**Files:**
- Create: `custom_components/room_climate/control.py`
- Test: `tests/test_control_temperature.py`

**Interfaces:**
- Consumes: `TemperatureSource` from `models.py`.
- Produces: `weighted_temperature(sources: Sequence[TemperatureSource],
  temperatures: Mapping[str, float | None]) -> float | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_temperature.py
"""The weighted mean of the usable temperature sources."""

from __future__ import annotations

from custom_components.room_climate.control import weighted_temperature
from custom_components.room_climate.models import TemperatureSource


def source(entity_id: str, weight: float = 1.0) -> TemperatureSource:
    return TemperatureSource(entity_id=entity_id, weight=weight)


def test_one_source_is_the_room_temperature() -> None:
    result = weighted_temperature((source("sensor.trv"),), {"sensor.trv": 21.4})
    assert result == 21.4


def test_weighted_mean_over_three_sources() -> None:
    sources = (source("sensor.a", 1.0), source("sensor.b", 3.0), source("sensor.c", 1.0))
    values = {"sensor.a": 20.0, "sensor.b": 22.0, "sensor.c": 24.0}
    assert weighted_temperature(sources, values) == 22.0


def test_unusable_sources_are_dropped() -> None:
    sources = (source("sensor.a"), source("sensor.b"), source("sensor.c"))
    values = {"sensor.a": 20.0, "sensor.b": None, "sensor.c": 22.0}
    assert weighted_temperature(sources, values) == 21.0


def test_all_sources_unusable_gives_no_temperature() -> None:
    sources = (source("sensor.a"), source("sensor.b"))
    assert weighted_temperature(sources, {"sensor.a": None, "sensor.b": None}) is None


def test_impossible_value_is_dropped() -> None:
    sources = (source("sensor.a"), source("sensor.b"))
    assert weighted_temperature(sources, {"sensor.a": 900.0, "sensor.b": 21.0}) == 21.0


def test_one_source_alone_can_be_the_room_temperature() -> None:
    sources = (source("sensor.a", 1.0), source("sensor.b", 3.0))
    assert weighted_temperature(sources, {"sensor.a": None, "sensor.b": 22.5}) == 22.5
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control_temperature.py -v`
Expected: FAIL with `ImportError: cannot import name 'weighted_temperature'`.

- [ ] **Step 3: Write the function**

```python
# custom_components/room_climate/control.py
"""Decision rules for room heating."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .models import TemperatureSource

MIN_PLAUSIBLE_TEMPERATURE = -20.0
MAX_PLAUSIBLE_TEMPERATURE = 60.0


def weighted_temperature(
    sources: Sequence[TemperatureSource], temperatures: Mapping[str, float | None]
) -> float | None:
    """Return the weighted mean of the usable sources, or None when none is usable."""
    total_weight = 0.0
    total = 0.0
    for source in sources:
        value = temperatures.get(source.entity_id)
        if value is None:
            continue
        if not MIN_PLAUSIBLE_TEMPERATURE <= value <= MAX_PLAUSIBLE_TEMPERATURE:
            continue
        total += source.weight * value
        total_weight += source.weight
    if total_weight == 0.0:
        return None
    return total / total_weight
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_control_temperature.py -v`
Expected: PASS, six tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py tests/test_control_temperature.py
git commit -m "add the weighted room temperature"
```

---

### Task 4: The outdoor lockout

**Files:**
- Modify: `custom_components/room_climate/control.py`
- Test: `tests/test_control_lockout.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `outdoor_temperature(sources: Sequence[str], temperatures: Mapping[str, float | None])
  -> float | None` and `next_lockout(active: bool, outdoor: float | None, threshold: float,
  hysteresis: float) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_lockout.py
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


def test_outdoor_temperature_without_a_reading() -> None:
    assert outdoor_temperature(("sensor.outdoor",), {"sensor.outdoor": None}) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control_lockout.py -v`
Expected: FAIL with `ImportError: cannot import name 'next_lockout'`.

- [ ] **Step 3: Write the functions**

```python
# appended to custom_components/room_climate/control.py

def outdoor_temperature(
    sources: Sequence[str], temperatures: Mapping[str, float | None]
) -> float | None:
    """Return the mean of the usable outdoor readings, or None when none is usable."""
    usable = [
        value
        for entity_id in sources
        if (value := temperatures.get(entity_id)) is not None
        and MIN_PLAUSIBLE_TEMPERATURE <= value <= MAX_PLAUSIBLE_TEMPERATURE
    ]
    if not usable:
        return None
    return sum(usable) / len(usable)


def next_lockout(active: bool, outdoor: float | None, threshold: float, hysteresis: float) -> bool:
    """Return the lockout state for the next cycle."""
    if outdoor is None:
        return False
    if active:
        return outdoor > threshold - hysteresis
    return outdoor >= threshold
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_control_lockout.py -v`
Expected: PASS, seven tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py tests/test_control_lockout.py
git commit -m "add the outdoor lockout"
```

---

### Task 5: The room demand latch and the actuator targets

**Files:**
- Modify: `custom_components/room_climate/control.py`
- Test: `tests/test_control_room.py`

**Interfaces:**
- Consumes: `RoomConfig`, `RuntimeRoom`, `Mapping[str, float]`.
- Produces: `next_calling(previous: bool, temperature: float | None, target: float | None,
  hvac_mode: str, room: RoomConfig) -> bool`, `round_setpoint(value: float) -> float`, and
  `actuator_targets(room: RoomConfig, calling: bool, target: float | None,
  last_sent: Mapping[str, float]) -> Mapping[str, float]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_room.py
"""The room demand latch and the actuator command."""

from __future__ import annotations

import pytest
from custom_components.room_climate.control import actuator_targets, next_calling, round_setpoint
from custom_components.room_climate.models import RoomConfig, TemperatureSource


def room(**overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": "front_room",
        "name": "Front Room",
        "actuators": ("climate.trv_front_room",),
        "sources": (TemperatureSource(entity_id="sensor.trv_front_room_local_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


def test_demand_starts_below_the_band() -> None:
    assert next_calling(False, 20.7, 21.0, "heat", room()) is True


def test_demand_does_not_start_inside_the_band() -> None:
    assert next_calling(False, 21.0, 21.0, "heat", room()) is False


def test_demand_holds_inside_the_band() -> None:
    assert next_calling(True, 21.1, 21.0, "heat", room()) is True


def test_demand_stops_above_the_band() -> None:
    assert next_calling(True, 21.3, 21.0, "heat", room()) is False


@pytest.mark.parametrize(
    ("temperature", "target", "mode"),
    [(21.0, None, "heat"), (21.0, 5.0, "heat"), (21.0, 21.0, "off"), (None, 21.0, "heat")],
)
def test_a_room_that_cannot_heat_never_calls(
    temperature: float | None, target: float | None, mode: str
) -> None:
    assert next_calling(False, temperature, target, mode, room()) is False


@pytest.mark.parametrize(("raw", "expected"), [(20.0, 20.0), (20.24, 20.0), (20.26, 20.5), (20.75, 21.0)])
def test_setpoints_round_to_the_half_degree(raw: float, expected: float) -> None:
    assert round_setpoint(raw) == expected


def test_calling_sets_the_actuator_with_the_offset() -> None:
    config = room(
        actuators=("climate.trv_front_room", "climate.bt_front_room"),
        setpoint_offset=0.5,
    )
    assert actuator_targets(config, True, 21.0, {}) == {
        "climate.trv_front_room": 21.5,
        "climate.bt_front_room": 21.5,
    }


def test_not_calling_holds_the_actuator_at_the_off_setpoint() -> None:
    assert actuator_targets(room(), False, 21.0, {}) == {"climate.trv_front_room": 5.0}


def test_a_sent_target_is_not_repeated() -> None:
    sent = {"climate.trv_front_room": 21.5}
    assert actuator_targets(room(setpoint_offset=0.5), True, 21.0, sent) == {}


def test_a_target_is_clamped_to_the_room_limits() -> None:
    config = room(max_temp=22.0, setpoint_offset=0.0)
    assert actuator_targets(config, True, 25.0, {}) == {"climate.trv_front_room": 22.0}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control_room.py -v`
Expected: FAIL with `ImportError: cannot import name 'next_calling'`.

- [ ] **Step 3: Write the functions**

```python
# appended to custom_components/room_climate/control.py

SETPOINT_STEP = 0.5
REPEAT_THRESHOLD = 0.25


def round_setpoint(value: float) -> float:
    """Round a target temperature to the half degree step of the actuators."""
    return round(value / SETPOINT_STEP) * SETPOINT_STEP


def next_calling(
    previous: bool,
    temperature: float | None,
    target: float | None,
    hvac_mode: str,
    room: RoomConfig,
) -> bool:
    """Return the demand state of one room for the next cycle."""
    if hvac_mode != "heat" or target is None or target <= room.min_temp or temperature is None:
        return False
    if previous:
        return temperature <= target + room.hysteresis
    return temperature < target - room.hysteresis


def actuator_targets(
    room: RoomConfig, calling: bool, target: float | None, last_sent: Mapping[str, float]
) -> Mapping[str, float]:
    """Return the actuator commands of one room, omitting a target that is already sent."""
    if calling and target is not None:
        wanted = min(max(target + room.setpoint_offset, room.min_temp), room.max_temp)
    else:
        wanted = room.off_setpoint
    rounded = round_setpoint(wanted)
    commands: dict[str, float] = {}
    for entity_id in room.actuators:
        sent = last_sent.get(entity_id)
        if sent is not None and abs(sent - rounded) < REPEAT_THRESHOLD:
            continue
        commands[entity_id] = rounded
    return commands
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_control_room.py -v`
Expected: PASS, fifteen tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py tests/test_control_room.py
git commit -m "add the room demand latch"
```

---

### Task 6: The boiler decision

**Files:**
- Modify: `custom_components/room_climate/control.py`, `custom_components/room_climate/models.py`
- Test: `tests/test_control_boiler.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `RuntimeRoom`, `ControlInput`, `RoomCommand`, `BoilerCommand`, `ControlOutput` in
  `models.py`, and `decide(payload: ControlInput) -> ControlOutput` in `control.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_boiler.py
"""The boiler decision and its timing guards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.room_climate.control import decide
from custom_components.room_climate.models import (
    ControlInput,
    HouseConfig,
    RoomConfig,
    RuntimeRoom,
    TemperatureSource,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def house(**overrides: object) -> HouseConfig:
    payload: dict[str, object] = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ("sensor.outdoor",),
        "min_on_seconds": 300,
        "min_off_seconds": 300,
        "rooms": (
            RoomConfig(
                room_id="front_room",
                name="Front Room",
                actuators=("climate.trv_front_room",),
                sources=(TemperatureSource(entity_id="sensor.front_room"),),
            ),
            RoomConfig(
                room_id="back_room",
                name="Back Room",
                actuators=("climate.trv_back_room",),
                sources=(TemperatureSource(entity_id="sensor.back_room"),),
            ),
        ),
    }
    payload.update(overrides)
    return HouseConfig(**payload)


def runtime(room_id: str, target: float | None = 21.0) -> RuntimeRoom:
    return RuntimeRoom(
        room_id=room_id, target_temperature=target, hvac_mode="heat", calling=False
    )


def payload(**overrides: object) -> ControlInput:
    base: dict[str, object] = {
        "now": NOW,
        "house": house(),
        "temperatures": {
            "sensor.front_room": 20.0,
            "sensor.back_room": 21.0,
            "sensor.outdoor": 8.0,
        },
        "runtimes": (runtime("front_room"), runtime("back_room")),
        "last_actuator_targets": {},
        "outdoor_lockout_active": False,
        "boiler_on": False,
        "boiler_state_since": None,
        "master_enabled": True,
        "override_active": False,
    }
    base.update(overrides)
    return ControlInput(**base)


def test_one_calling_room_turns_the_boiler_on() -> None:
    output = decide(payload())
    assert output.boiler.turn_on is True
    assert output.boiler.reason == "room_demand"
    assert output.boiler.send is True


def test_no_calling_room_keeps_the_boiler_off() -> None:
    output = decide(payload(temperatures={"sensor.front_room": 22.0, "sensor.back_room": 22.0, "sensor.outdoor": 8.0}))
    assert output.boiler.turn_on is False
    assert output.boiler.reason == "no_demand"
    assert output.boiler.send is False


def test_the_last_room_stopping_turns_the_boiler_off() -> None:
    output = decide(
        payload(
            temperatures={"sensor.front_room": 22.0, "sensor.back_room": 22.0, "sensor.outdoor": 8.0},
            boiler_on=True,
            boiler_state_since=NOW - timedelta(minutes=10),
        )
    )
    assert output.boiler.turn_on is False
    assert output.boiler.send is True


def test_the_boiler_does_not_switch_off_inside_min_on() -> None:
    output = decide(
        payload(
            temperatures={"sensor.front_room": 22.0, "sensor.back_room": 22.0, "sensor.outdoor": 8.0},
            boiler_on=True,
            boiler_state_since=NOW - timedelta(seconds=60),
        )
    )
    assert output.boiler.send is False


def test_the_boiler_does_not_switch_on_inside_min_off() -> None:
    output = decide(
        payload(boiler_on=False, boiler_state_since=NOW - timedelta(seconds=60))
    )
    assert output.boiler.send is False


def test_a_lockout_stops_the_boiler_at_once() -> None:
    output = decide(
        payload(
            outdoor_lockout_active=True,
            boiler_on=True,
            boiler_state_since=NOW - timedelta(seconds=5),
        )
    )
    assert output.boiler.turn_on is False
    assert output.boiler.reason == "lockout"
    assert output.boiler.send is True
    assert output.status == "locked_out"


def test_the_override_bypasses_the_lockout() -> None:
    output = decide(payload(outdoor_lockout_active=True, override_active=True))
    assert output.boiler.turn_on is True


def test_a_locked_out_room_holds_its_actuator_at_the_off_setpoint() -> None:
    output = decide(payload(outdoor_lockout_active=True))
    front_room = next(room for room in output.rooms if room.room_id == "front_room")
    assert front_room.calling is True
    assert front_room.actuator_targets == {"climate.trv_front_room": 5.0}


def test_the_master_switch_off_stops_the_boiler_at_once() -> None:
    output = decide(
        payload(master_enabled=False, boiler_on=True, boiler_state_since=NOW - timedelta(seconds=5))
    )
    assert output.boiler.send is True
    assert output.boiler.reason == "master_off"
    assert output.status == "disabled"


def test_a_missing_room_source_does_not_call_and_is_degraded() -> None:
    output = decide(
        payload(temperatures={"sensor.front_room": None, "sensor.back_room": 22.0, "sensor.outdoor": 8.0})
    )
    front_room = next(room for room in output.rooms if room.room_id == "front_room")
    assert front_room.calling is False
    assert front_room.degraded is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control_boiler.py -v`
Expected: FAIL with `ImportError: cannot import name 'decide'`.

- [ ] **Step 3: Add the cycle models and the decision**

```python
# appended to custom_components/room_climate/models.py


class RuntimeRoom(BaseModel):
    """The demand state of one room, carried between cycles."""

    model_config = FROZEN

    room_id: str
    target_temperature: float | None = None
    hvac_mode: Literal["off", "heat"] = "heat"
    calling: bool = False


class ControlInput(BaseModel):
    """Everything one cycle reads."""

    model_config = FROZEN

    now: datetime
    house: HouseConfig
    temperatures: Mapping[str, float | None]
    runtimes: tuple[RuntimeRoom, ...]
    last_actuator_targets: Mapping[str, float]
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
    room_temperature: float | None
    usable_sources: int
    degraded: bool
    actuator_targets: Mapping[str, float]


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
    status: str
```

```python
# appended to custom_components/room_climate/control.py

from datetime import timedelta

from .const import (
    REASON_LOCKOUT,
    REASON_MASTER_OFF,
    REASON_NO_DEMAND,
    REASON_ROOM_DEMAND,
    STATUS_DEGRADED,
    STATUS_DISABLED,
    STATUS_HEATING,
    STATUS_IDLE,
    STATUS_LOCKED_OUT,
)
from .models import (
    BoilerCommand,
    ControlInput,
    ControlOutput,
    RoomCommand,
    RuntimeRoom,
)


def _usable_source_count(room: RoomConfig, temperatures: Mapping[str, float | None]) -> int:
    return sum(
        1
        for source in room.sources
        if (value := temperatures.get(source.entity_id)) is not None
        and MIN_PLAUSIBLE_TEMPERATURE <= value <= MAX_PLAUSIBLE_TEMPERATURE
    )


def decide(payload: ControlInput) -> ControlOutput:
    """Return the commands of one cycle."""
    lockout = payload.outdoor_lockout_active and not payload.override_active
    runtimes = {room.room_id: room for room in payload.runtimes}
    commands: list[RoomCommand] = []
    for room in payload.house.rooms:
        runtime = runtimes.get(room.room_id, RuntimeRoom(room_id=room.room_id))
        temperature = weighted_temperature(room.sources, payload.temperatures)
        usable = _usable_source_count(room, payload.temperatures)
        calling = next_calling(
            runtime.calling, temperature, runtime.target_temperature, runtime.hvac_mode, room
        )
        blocked = lockout or not payload.master_enabled
        commands.append(
            RoomCommand(
                room_id=room.room_id,
                calling=calling,
                room_temperature=temperature,
                usable_sources=usable,
                degraded=usable == 0,
                actuator_targets=actuator_targets(
                    room, calling and not blocked, runtime.target_temperature, payload.last_actuator_targets
                ),
            )
        )
    if not payload.master_enabled:
        boiler = _boiler_command(False, REASON_MASTER_OFF, payload, immediate=True)
    elif lockout:
        boiler = _boiler_command(False, REASON_LOCKOUT, payload, immediate=True)
    elif any(command.calling for command in commands):
        boiler = _boiler_command(True, REASON_ROOM_DEMAND, payload)
    else:
        boiler = _boiler_command(False, REASON_NO_DEMAND, payload)
    outdoor = outdoor_temperature(
        payload.house.outdoor_sources, payload.temperatures
    )
    return ControlOutput(
        rooms=tuple(commands),
        boiler=boiler,
        lockout_active=payload.outdoor_lockout_active,
        outdoor_temperature=outdoor,
        status=_status(payload, lockout, boiler, commands),
    )


def _boiler_command(
    turn_on: bool, reason: str, payload: ControlInput, immediate: bool = False
) -> BoilerCommand:
    if turn_on == payload.boiler_on:
        return BoilerCommand(turn_on=turn_on, reason=reason, send=False)
    if payload.boiler_state_since is None:
        return BoilerCommand(turn_on=turn_on, reason=reason, send=True)
    if immediate:
        return BoilerCommand(turn_on=turn_on, reason=reason, send=True)
    limit = (
        timedelta(seconds=payload.house.min_off_seconds)
        if turn_on
        else timedelta(seconds=payload.house.min_on_seconds)
    )
    elapsed = payload.now - payload.boiler_state_since
    return BoilerCommand(turn_on=turn_on, reason=reason, send=elapsed >= limit)


def _status(
    payload: ControlInput, lockout: bool, boiler: BoilerCommand, rooms: list[RoomCommand]
) -> str:
    if not payload.master_enabled:
        return STATUS_DISABLED
    if lockout:
        return STATUS_LOCKED_OUT
    if payload.house.dry_run:
        return STATUS_DRY_RUN
    if all(room.degraded for room in rooms) and rooms:
        return STATUS_DEGRADED
    if boiler.turn_on or payload.boiler_on:
        return STATUS_HEATING
    return STATUS_IDLE
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_control_boiler.py -v`
Expected: PASS, eleven tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py custom_components/room_climate/models.py \
  tests/test_control_boiler.py
git commit -m "add the boiler decision"
```

---

### Task 7: The coordinator

**Files:**
- Create: `custom_components/room_climate/coordinator.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `decide`, `HouseConfig.from_options`, `ControlOutput`.
- Produces: `RoomClimateCoordinator(hass, entry, config)` with the attributes `config`,
  `output`, `runtimes` and the method `async_recompute()`. The `data` attribute is a
  `ControlOutput`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coordinator.py
"""The coordinator reads states, decides, and applies the commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN


async def setup_entry(hass: HomeAssistant, **options: object) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate",
        options={
            "boiler_entity": "climate.heating_heat",
            "outdoor_sources": ["sensor.outdoor"],
            "rooms": [
                {
                    "room_id": "front_room",
                    "name": "Front Room",
                    "actuators": ["climate.trv_front_room"],
                    "sources": [{"entity_id": "sensor.front_room"}],
                }
            ],
            **options,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_a_call_for_heat_sets_the_actuator_and_the_boiler(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)
    hass.states.async_set("sensor.front_room", "20.0")
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.trv_front_room", "heat", {"temperature": 5.0})
    hass.states.async_set("climate.heating_heat", "off", {"temperature": 15.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.room_climate_front_room", "temperature": 21.0}
    )
    await hass.async_block_till_done()

    calls = [
        call
        for call in hass.services.async_calls("climate", "set_temperature")
        if call.data.get("temperature") in (31.0, 15.0)
    ]
    assert calls, "the boiler must be commanded"
    assert calls[-1].data["entity_id"] == "climate.heating_heat"


async def test_dry_run_sends_nothing(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=True)
    hass.states.async_set("sensor.front_room", "20.0")
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.trv_front_room", "heat", {"temperature": 5.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with patch("homeassistant.core.ServiceRegistry.async_call", new=AsyncMock()) as call:
        await hass.services.async_call("room_climate", "recompute", {})
        await hass.async_block_till_done()
    assert not [item for item in call.await_args_list if item.args[:1] == ("climate",)]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_coordinator.py -v`
Expected: FAIL, because `custom_components.room_climate` sets up no entry.

- [ ] **Step 3: Write the coordinator and the entry setup**

```python
# custom_components/room_climate/coordinator.py
"""The cycle that reads states, decides, and applies the commands."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .control import decide, outdoor_temperature
from .models import ControlInput, ControlOutput, HouseConfig, RuntimeRoom

_LOGGER = logging.getLogger(__name__)


class RoomClimateCoordinator:
    """Owns the runtime state of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, config: HouseConfig) -> None:
        self.hass = hass
        self.entry = entry
        self.config = config
        self.runtimes: dict[str, RuntimeRoom] = {}
        self.output: ControlOutput | None = None
        self.boiler_on = False
        self.boiler_state_since: datetime | None = None
        self.lockout_active = False
        self.last_actuator_targets: dict[str, float] = {}
        self.master_enabled = config.master_enabled
        self.override_active = False
        self._listeners: list[object] = []

    @property
    def watched_entities(self) -> tuple[str, ...]:
        """The entities whose state changes trigger a cycle."""
        room_entities = [
            entity_id
            for room in self.config.rooms
            for entity_id in (*room.actuators, *(source.entity_id for source in room.sources))
        ]
        return tuple((*room_entities, self.config.boiler_entity, *self.config.outdoor_sources))

    async def async_start(self) -> None:
        """Run one cycle, then follow the watched entities."""
        await self.async_recompute()
        self._listeners.append(
            async_track_state_change_event(
                self.hass, list(self.watched_entities), self._handle_state_change
            )
        )

    async def async_stop(self) -> None:
        """Detach the listeners."""
        for unsub in self._listeners:
            unsub()  # type: ignore[operator]
        self._listeners.clear()

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_recompute())

    async def async_recompute(self) -> ControlOutput:
        """Run one cycle and apply the commands."""
        now = dt_util.utcnow()
        temperatures = {
            entity_id: _numeric_state(self.hass, entity_id)
            for entity_id in (*self.config.outdoor_sources, *(
                source.entity_id for room in self.config.rooms for source in room.sources
            ))
        }
        outdoor = outdoor_temperature(self.config.outdoor_sources, temperatures)
        self.lockout_active = next_lockout(
            self.lockout_active,
            outdoor,
            self.config.lockout_temperature,
            self.config.lockout_hysteresis,
        )
        payload = ControlInput(
            now=now,
            house=self.config,
            temperatures=temperatures,
            runtimes=tuple(self.runtimes.values()),
            last_actuator_targets=self.last_actuator_targets,
            outdoor_lockout_active=self.lockout_active,
            boiler_on=self.boiler_on,
            boiler_state_since=self.boiler_state_since,
            master_enabled=self.master_enabled,
            override_active=self.override_active,
        )
        output = await self.hass.async_add_executor_job(decide, payload)
        if self.config.dry_run:
            _LOGGER.info("dry run %s: %s", self.entry.entry_id, output.model_dump_json())
        else:
            await self._apply(output)
        self._remember(output)
        self.output = output
        self.entry.async_update_listeners()
        return output
```

The remaining methods of the coordinator, and the entry setup, follow the spec's section 6.4
and 6.6:

- `_apply(output)`: for each `RoomCommand`, call `climate.set_temperature` with
  `{"entity_id": entity_id, "temperature": value}` for each target; then, when
  `output.boiler.send` is true, call `climate.set_temperature` with
  `{"entity_id": config.boiler_entity, "temperature": on or off value, "hvac_mode": mode}`.
- `_remember(output)`: set `last_actuator_targets` from the sent targets, `boiler_on`,
  `boiler_state_since`, and the `runtimes` dictionary from `output.rooms`.
- `async_release_boiler(config)`: set the boiler to the OFF values. It runs from
  `async_unload_entry` and from `async_remove_entry`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_coordinator.py -v`
Expected: PASS, two tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/coordinator.py custom_components/room_climate/__init__.py \
  tests/test_coordinator.py
git commit -m "add the room climate coordinator"
```

---

### Task 8: The platforms

**Files:**
- Create: `custom_components/room_climate/climate.py`, `switch.py`, `binary_sensor.py`, `sensor.py`
- Test: `tests/test_platforms.py`

**Interfaces:**
- Consumes: `RoomClimateCoordinator`.
- Produces: `climate.room_climate_<room>`, `switch.room_climate_boiler_automation`,
  `switch.room_climate_lockout_override`, `binary_sensor.room_climate_boiler_demand`,
  `binary_sensor.room_climate_outdoor_lockout`, `sensor.room_climate_outdoor_temperature`,
  `sensor.room_climate_status`, `sensor.room_climate_<room>_temperature`,
  `binary_sensor.room_climate_<room>_calling`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_platforms.py
"""The entities the operator sees and the Scheduler commands."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .test_coordinator import setup_entry


async def test_the_virtual_thermostat_reports_the_weighted_temperature(
    hass: HomeAssistant,
) -> None:
    entry = await setup_entry(hass, dry_run=True)
    hass.states.async_set("sensor.front_room", "20.4")
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.trv_front_room", "heat", {"temperature": 5.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("climate.room_climate_front_room")
    assert state is not None
    assert state.attributes["current_temperature"] == 20.4
    assert state.attributes["hvac_action"] == "heating"
    assert state.attributes["hvac_modes"] == ["off", "heat"]


async def test_the_house_entities_exist(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=True)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in (
        "switch.room_climate_boiler_automation",
        "switch.room_climate_lockout_override",
        "binary_sensor.room_climate_boiler_demand",
        "binary_sensor.room_climate_outdoor_lockout",
        "sensor.room_climate_outdoor_temperature",
        "sensor.room_climate_status",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_platforms.py -v`
Expected: FAIL, because no climate entity exists.

- [ ] **Step 3: Write the platforms**

Each platform subclasses `CoordinatorEntity` and reads `coordinator.output`. The climate
entity:

```python
# custom_components/room_climate/climate.py
"""One virtual thermostat per room."""

from __future__ import annotations

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.components.climate.const import ATTR_TARGET_TEMP_STEP
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .models import RoomConfig


class RoomClimateEntity(ClimateEntity, RestoreEntity):
    """The dashboard surface and the Scheduler target of one room."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_target_temperature_step = 0.5
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(self, coordinator, room: RoomConfig) -> None:
        self.coordinator = coordinator
        self.room = room
        self._attr_unique_id = f"{DOMAIN}_{room.room_id}_climate"
        self._attr_min_temp = room.min_temp
        self._attr_max_temp = room.max_temp
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_target_temperature = None

    @property
    def current_temperature(self) -> float | None:
        command = self._command
        return None if command is None else command.room_temperature

    @property
    def hvac_action(self) -> HVACAction:
        command = self._command
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.HEATING if command is not None and command.calling else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get("temperature")
        if temperature is not None:
            self._attr_target_temperature = temperature
        self._push_runtime()
        await self.coordinator.async_recompute()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        self._push_runtime()
        await self.coordinator.async_recompute()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_target_temperature = last.attributes.get("temperature")
            self._attr_hvac_mode = HVACMode(last.state)
```

`_push_runtime()` writes `RuntimeRoom(room_id=..., target_temperature=..., hvac_mode=...)`
into `coordinator.runtimes`. The switch, binary_sensor and sensor platforms publish
`coordinator.output` and the two switches write `coordinator.master_enabled` and
`coordinator.override_active` and then request a cycle.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_platforms.py -v`
Expected: PASS, two tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/climate.py custom_components/room_climate/switch.py \
  custom_components/room_climate/binary_sensor.py custom_components/room_climate/sensor.py \
  tests/test_platforms.py
git commit -m "add the room climate platforms"
```

---

### Task 9: The config flow

**Files:**
- Create: `custom_components/room_climate/config_flow.py`, `custom_components/room_climate/strings.json`,
  `custom_components/room_climate/translations/en.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `HouseConfig`, `RoomConfig`, `TemperatureSource`.
- Produces: a user flow that creates the house entry, and an options flow with the menu
  `add_room`, `edit_room`, `remove_room`, `house_settings`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_flow.py
"""The config flow creates the house entry and manages the rooms."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN


async def test_user_flow_creates_the_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "boiler_entity": "climate.heating_heat",
            "boiler_on_temperature": 31.0,
            "boiler_off_temperature": 15.0,
            "outdoor_sources": ["sensor.outdoor"],
            "lockout_temperature": 18.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_rejects_a_shared_actuator(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={"rooms": [], "outdoor_sources": ["sensor.outdoor"]},
    )
    entry.add_to_hass(hass)
    room = {
        "room_id": "front_room",
        "name": "Front Room",
        "actuators": ["climate.trv_front_room"],
        "sources": [{"entity_id": "sensor.front_room"}],
    }
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_room"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], room)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry.options = result["data"]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_room"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**room, "room_id": "back_room", "name": "Back Room", "sources": [{"entity_id": "sensor.back_room"}]},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "shared_actuator"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_config_flow.py -v`
Expected: FAIL, because the domain has no flow handler.

- [ ] **Step 3: Write the flows**

The user step collects the house fields with `vol.Schema` selectors
(`EntitySelector` with `domain="climate"` for the boiler, `EntitySelector` with
`device_class="temperature"` for the outdoor sources). The options flow uses
`OptionsFlowWithConfigEntry`, a `menu` step, and one form per room. The room form
rejects a duplicate actuator and a duplicate source with the errors `shared_actuator`
and `shared_source`, and the message names both rooms.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_config_flow.py -v`
Expected: PASS, two tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/config_flow.py custom_components/room_climate/strings.json \
  custom_components/room_climate/translations/en.json tests/test_config_flow.py
git commit -m "add the room climate config flow"
```

---

### Task 10: Unload safety, diagnostics and the recompute service

**Files:**
- Modify: `custom_components/room_climate/__init__.py`, `custom_components/room_climate/coordinator.py`
- Create: `custom_components/room_climate/diagnostics.py`, `custom_components/room_climate/services.yaml`
- Test: `tests/test_unload.py`

**Interfaces:**
- Consumes: `RoomClimateCoordinator`.
- Produces: the service `room_climate.recompute`, the diagnostics payload, and the boiler
  release on unload and on removal.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unload.py
"""An unloaded component must not leave the boiler running."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .test_coordinator import setup_entry


async def test_unload_releases_the_boiler(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=False)
    hass.states.async_set("sensor.front_room", "20.0")
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.trv_front_room", "heat", {"temperature": 5.0})
    hass.states.async_set("climate.heating_heat", "heat", {"temperature": 31.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    calls = [
        call
        for call in hass.services.async_calls("climate", "set_temperature")
        if call.data.get("entity_id") == "climate.heating_heat"
    ]
    assert calls[-1].data["temperature"] == 15.0
    assert calls[-1].data["hvac_mode"] == "off"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_unload.py -v`
Expected: FAIL, because unload sends no boiler command.

- [ ] **Step 3: Write the release path, the service and the diagnostics**

`async_unload_entry` stops the coordinator, then calls
`coordinator.async_release_boiler()`. `async_remove_entry` calls the same release.
`async_setup` registers the service `room_climate.recompute` for every loaded entry,
and it runs one cycle. `diagnostics.py` returns the config, the last `ControlOutput`
and the watched entities.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_unload.py -v`
Expected: PASS, one test.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/__init__.py custom_components/room_climate/coordinator.py \
  custom_components/room_climate/diagnostics.py custom_components/room_climate/services.yaml \
  tests/test_unload.py
git commit -m "release the boiler on unload"
```

---

### Task 11: Packaging, the dashboard example and the live runbook

**Files:**
- Modify: `README.md`, `hacs.json`
- Create: `dashboard/room-climate.yaml`, `docs/room-heating.runbook.md`,
  `scripts/probe_live.py`, `docs/plans/2026-10-19-room-heating.evidence.md`

**Interfaces:**
- Consumes: the whole component.
- Produces: the install route, the dashboard example, and the live evidence.

- [ ] **Step 1: Write the failing check**

Run: `uv run python -c "import json,pathlib; print(json.loads(pathlib.Path('hacs.json').read_text()))"`
Expected: FAIL, because `hacs.json` has no `name` field that matches the manifest domain.

- [ ] **Step 2: Write the packaging**

1. Set `hacs.json` to
   `{"name": "Room Climate", "render_readme": true, "homeassistant": "2026.10.0"}`.
2. Write `README.md` with the install route of the spec's section 8.
3. Write `dashboard/room-climate.yaml` with one thermostat card per room, the house panel
   and one history graph.
4. Write `docs/room-heating.runbook.md` with the numbered install procedure, the
   `dry_run` review step, and the rollback step.
5. Write `scripts/probe_live.py`. It reads the token from `~/.hermes/ha_token`, then prints
   the room climate entities, the boiler entity state, and the status sensor.

- [ ] **Step 3: Run the live regression**

1. Push the repository. Tag `v0.1.0` and push the tag.
2. Add the repository to HACS as a custom repository of category Integration, then
   download it. Restart Home Assistant.
3. Add the integration and configure each room with the table of the spec's section 3.2.
4. Keep `dry_run` on. Watch the log and `sensor.room_climate_status` for one hour.
5. Run `uv run python scripts/probe_live.py` and record the output in
   `docs/plans/2026-10-19-room-heating.evidence.md`.
6. Turn `dry_run` off. Disable `automation.heat_on_demand`.
7. Set one room target above its temperature. Confirm that the boiler reaches 31 °C and
   that the room's TRVs open.
8. Lower the lockout threshold below the outdoor temperature. Confirm that the boiler
   returns to 15 °C and mode `off` within one cycle.
9. Restore the lockout threshold to 18 °C.

- [ ] **Step 4: Run the checks to verify they pass**

Run: `make check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md hacs.json dashboard docs scripts
git commit -m "package room climate for HACS"
```

---

## Self-review

**Spec coverage:** section 5 (architecture) → tasks 1, 7, 8. Section 5.2 (configuration) →
tasks 2, 9. Section 6.1 → task 3. Section 6.2 → task 5. Section 6.3 → task 5. Section 6.4 →
task 6. Section 6.5 → task 4. Section 6.6 → tasks 7, 10. Section 7 → task 8. Section 8 →
task 11. Section 9 → every task.

**Review focus coverage:** item 1 → task 4. Item 2 → tasks 5, 6. Item 3 → task 7. Item 4 →
tasks 5, 6. Item 5 → task 6.

**Type consistency:** `RuntimeRoom` is the name in both `models.py` and the coordinator.
`ControlOutput.rooms` holds `RoomCommand` values. `actuator_targets` returns
`Mapping[str, float]` in the model and in the function signature. The service name is
`room_climate.recompute` in the constants, the service file and the tests.

**Open decisions:** the four rows of the spec's section 10 stay open. Task 9 reads the
defaults from the spec. A change to a default is a change to the spec first.

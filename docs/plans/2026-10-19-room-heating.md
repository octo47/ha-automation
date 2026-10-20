# Room Climate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Home Assistant custom integration `room_climate` from
`docs/room-heating.spec.md`. The integration groups Hive TRVs and temperature sensors into
rooms, drives the valves, and decides the Hive boiler state. It depends on no other custom
integration.

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
- No test calls the live Home Assistant instance. No test writes to a valve.
- The component never commands `climate.heating_water`, `switch.<trv>_heat_available` or
  `select.<trv>_programming_operation_mode`.
- The component takes no code from Better Thermostat (AGPL-3.0). The algorithms are
  reimplemented from the spec.
- Default values, in one place in `const.py`: `boiler_on_temperature` 31.0,
  `boiler_off_temperature` 15.0, `lockout_temperature` 18.0, `lockout_hysteresis` 1.0,
  `min_on_seconds` 300, `min_off_seconds` 300, `update_interval_seconds` 30, `hysteresis` 0.2,
  `off_setpoint` 5.0, `valve_command_interval_seconds` 300, `calibration_scale` 100,
  `open_offset` 2.0, `ema_seconds` 1800, `dry_run` true.
- Markdown that carries Python code blocks is excluded from the formatter through
  `extend-exclude = ["*.md"]`. Ruff parses those blocks, and its rewrite can be invalid Python
  (measured: it turned `except (TypeError, ValueError):` into `except TypeError, ValueError:`).
  Never remove that exclusion to make `make lint` pass.
- The domain is `room_climate`. The manifest requires `"version"`, because HACS updates a
  custom integration by the version.
- The manifest declares `requirements: ["pydantic>=2.11,<3"]` and no custom integration.
- The live regression drives the simulation house of the spec's section 7.4 before it drives a real
  valve. The one live write to a real valve is the feed-scale confirmation of the spec's section 8.2
  step 3, and the operator performs it.

## Environment facts that constrain the tasks

- The instance is Home Assistant 2026.10.1. Pin the test harness to the release whose Home
  Assistant version is 2026.10.1: run
  `uv add --dev pytest-homeassistant-custom-component`, then verify with
  `uv run python -c "import homeassistant.const as c; print(c.__version__)"`. If the version
  differs, pin the harness release that carries 2026.10.1.
- One Hive TRV publishes `climate.<name>`, `sensor.<name>_local_temperature`,
  `binary_sensor.<name>_heat_required`, `sensor.<name>_window_open_internal` and
  `number.<name>_external_measured_room_sensor`. Entity ids in the tests must match that shape.
- The live instance has HACS and Better Thermostat 1.9.3. Task 14 removes Better Thermostat
  after the component works.

## Review Focus

These inputs are not in the spec's tables. Each one has a test in the task that owns the code.

1. An outdoor source that is `unavailable` or `unknown` must not start a lockout. A failed
   weather station must not stop the heating.
2. A room whose sources are all unusable must not call for heat, must hold its valves at
   `off_setpoint`, and must report `degraded` — unless a `demand_fallback_sensors` entity is
   `on`, which is the one case that calls without a temperature.
3. A valve that is `unavailable` must not raise, and the other valves of the room must still
   receive the target.
4. A window sensor in the state `open` must suspend the demand at once, and the valve of the
   room must move to `off_setpoint` without waiting for the command interval.
5. A boiler command that the timing guard defers must not be sent, and a lockout or a master
   switch off must not be deferred.
6. A calibration write must never reach a valve of another room, and it must not repeat while
   the room temperature moves less than 0.1 °C.
7. A simulated entry must reject a room entity without the `sim_` prefix, and it must command
   only virtual entities. On the bench the valve position follows the gap between the target and
   the fed temperature, and the virtual boiler fires only above 30 °C.

---

### Task 1: Repository scaffold, import guard and licence

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.gitignore`, `hacs.json`, `LICENSE`
- Create: `custom_components/room_climate/__init__.py`, `const.py`, `manifest.json`
- Create: `custom_components/room_climate/translations/en.json`
- Create: `tests/conftest.py`, `tests/test_package_layout.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the package `custom_components.room_climate`, the constant `DOMAIN`, the
  `DEFAULT_*` values, the `REASON_*` values and the `STATUS_*` values.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_package_layout.py
"""The layout and the licence rules the component must hold."""

from __future__ import annotations

import ast
import json
import pathlib

ROOT = pathlib.Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "room_climate"


def test_domain_matches_directory() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["domain"] == COMPONENT.name
    assert manifest["version"]
    assert manifest["requirements"] == ["pydantic>=2.11,<3"]


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


def test_repository_carries_a_licence() -> None:
    assert (ROOT / "LICENSE").read_text().strip()
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
CONF_EMA_SECONDS = "ema_seconds"
CONF_ROOMS = "rooms"

CONF_ROOM_ID = "room_id"
CONF_NAME = "name"
CONF_AREA_ID = "area_id"
CONF_VALVES = "valves"
CONF_SOURCES = "sources"
CONF_CALIBRATION = "calibration"
CONF_CALIBRATION_SCALE = "calibration_scale"
CONF_VALVE_MODE = "valve_mode"
CONF_OPEN_OFFSET = "open_offset"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_HYSTERESIS = "hysteresis"
CONF_OFF_SETPOINT = "off_setpoint"
CONF_VALVE_COMMAND_INTERVAL_SECONDS = "valve_command_interval_seconds"
CONF_WINDOW_SENSORS = "window_sensors"
CONF_DEMAND_FALLBACK_SENSORS = "demand_fallback_sensors"

DEFAULT_BOILER_ENTITY = "climate.heating_heat"
DEFAULT_BOILER_ON_TEMPERATURE = 31.0
DEFAULT_BOILER_OFF_TEMPERATURE = 15.0
DEFAULT_OUTDOOR_SOURCES = ("sensor.outdoor_temperature",)
DEFAULT_LOCKOUT_TEMPERATURE = 18.0
DEFAULT_LOCKOUT_HYSTERESIS = 1.0
DEFAULT_MIN_ON_SECONDS = 300
DEFAULT_MIN_OFF_SECONDS = 300
DEFAULT_UPDATE_INTERVAL_SECONDS = 30
DEFAULT_EMA_SECONDS = 1800
DEFAULT_HYSTERESIS = 0.2
DEFAULT_OFF_SETPOINT = 5.0
DEFAULT_OPEN_OFFSET = 2.0
DEFAULT_VALVE_COMMAND_INTERVAL_SECONDS = 300
DEFAULT_CALIBRATION_SCALE = 100
DEFAULT_DRY_RUN = True

CALIBRATION_EXTERNAL = "external_sensor"
CALIBRATION_NONE = "none"
VALVE_MODE_MODULATE = "modulate"
VALVE_MODE_BOOST = "boost"

CALIBRATION_MIN_TEMPERATURE = 5.0
CALIBRATION_MAX_TEMPERATURE = 35.0
CALIBRATION_TOLERANCE_C = 0.1
SETPOINT_STEP = 0.5
WINDOW_OPEN_STATES = ("open", "on")
SIM_MARKER = "sim_"
REPEAT_THRESHOLD = 0.25
MIN_PLAUSIBLE_TEMPERATURE = -20.0
MAX_PLAUSIBLE_TEMPERATURE = 60.0

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

```toml
# pyproject.toml
[project]
name = "ha-room-climate"
version = "0.1.0"
requires-python = ">=3.14.2"

[dependency-groups]
dev = [
  "pytest",
  "pytest-asyncio",
  "pytest-homeassistant-custom-component==0.13.372",
  "ruff",
  "pyright",
]

[tool.uv]
package = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]

[tool.ruff]
line-length = 100
extend-exclude = ["*.md"]

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

`LICENSE` carries the MIT text of the repository. The operator can change it in one file,
because the component copies no AGPL-3.0 code.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_package_layout.py -v`
Expected: PASS, three tests.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml Makefile .gitignore hacs.json LICENSE custom_components tests
git commit -m "scaffold room_climate integration"
```

---

### Task 2: Configuration models and their validation

**Files:**
- Create: `custom_components/room_climate/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `const.py`.
- Produces: `TemperatureSource`, `RoomConfig`, `HouseConfig`, `HouseConfig.from_options`,
  `RuntimeRoom`, `ControlInput`, `RoomCommand`, `BoilerCommand`, `ControlOutput`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
"""Configuration models reject a configuration the control rules cannot honour."""

from __future__ import annotations

import pytest
from custom_components.room_climate.models import HouseConfig, RoomConfig, TemperatureSource
from pydantic import ValidationError


def room(room_id: str = "front_room", **overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": room_id,
        "name": room_id.title(),
        "valves": (f"climate.trv_{room_id}",),
        "sources": (TemperatureSource(entity_id=f"sensor.{room_id}_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


def house(**overrides: object) -> HouseConfig:
    payload: dict[str, object] = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ("sensor.outdoor_temperature",),
        "rooms": (room(),),
    }
    payload.update(overrides)
    return HouseConfig(**payload)


def test_a_room_keeps_its_valves_and_weights() -> None:
    config = room(sources=(TemperatureSource(entity_id="sensor.a", weight=3.0),))
    assert config.valves == ("climate.trv_front_room",)
    assert config.sources[0].weight == 3.0


@pytest.mark.parametrize("weight", [0.0, -1.0])
def test_weight_must_be_positive(weight: float) -> None:
    with pytest.raises(ValidationError):
        TemperatureSource(entity_id="sensor.a", weight=weight)


def test_a_room_id_is_a_slug() -> None:
    with pytest.raises(ValidationError):
        room("Front Room Room")


def test_house_rejects_two_rooms_with_one_room_id() -> None:
    other = room("front_room", name="Front Room Two", valves=("climate.trv_other",))
    with pytest.raises(ValidationError) as error:
        house(rooms=(room(), other))
    assert "room_id front_room" in str(error.value)


def test_a_room_needs_one_valve_and_one_source() -> None:
    with pytest.raises(ValidationError):
        room(valves=())
    with pytest.raises(ValidationError):
        room(sources=())


def test_house_rejects_one_valve_in_two_rooms() -> None:
    other = room("back_room", valves=("climate.trv_front_room",))
    with pytest.raises(ValidationError) as error:
        house(rooms=(room(), other))
    assert "trv_front_room" in str(error.value)
    assert "Front Room" in str(error.value) and "Back Room" in str(error.value)


def test_house_rejects_one_source_in_two_rooms() -> None:
    other = room("back_room", sources=(TemperatureSource(entity_id="sensor.front_room_temperature"),))
    with pytest.raises(ValidationError) as error:
        house(rooms=(room(), other))
    assert "sensor.front_room_temperature" in str(error.value)


def test_modulate_requires_the_calibration_feed() -> None:
    with pytest.raises(ValidationError):
        room(valve_mode="modulate", calibration="none")


def test_boost_works_without_the_calibration_feed() -> None:
    config = room(valve_mode="boost", calibration="none")
    assert config.open_offset == 2.0


def test_on_temperature_must_exceed_off_temperature() -> None:
    with pytest.raises(ValidationError):
        house(boiler_on_temperature=15.0, boiler_off_temperature=20.0)


def test_from_options_round_trips_the_entry_payload() -> None:
    payload = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ["sensor.outdoor_temperature"],
        "rooms": [
            {
                "room_id": "front_room",
                "name": "Front Room",
                "valves": ["climate.trv_front_room"],
                "sources": [{"entity_id": "sensor.front_room_temperature"}],
            }
        ],
    }
    config = HouseConfig.from_options(payload)
    assert config.rooms[0].valves == ("climate.trv_front_room",)
    assert config.rooms[0].calibration == "external_sensor"
    assert config.rooms[0].valve_mode == "modulate"
    assert config.dry_run is True
    assert config.simulation is False


def sim_options(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "simulation": True,
        "boiler_entity": "climate.sim_boiler",
        "outdoor_sources": ["number.sim_outdoor_temperature"],
        "rooms": [
            {
                "room_id": "front_room",
                "name": "Front Room",
                "valves": ["climate.sim_front_room_valve"],
                "sources": [{"entity_id": "number.sim_front_room_room_temperature"}],
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_a_simulated_house_accepts_the_virtual_entities() -> None:
    config = HouseConfig.from_options(sim_options())
    assert config.simulation is True
    assert config.rooms[0].valves == ("climate.sim_front_room_valve",)


def test_a_simulated_house_rejects_a_real_entity() -> None:
    payload = sim_options(
        rooms=[
            {
                "room_id": "front_room",
                "name": "Front Room",
                "valves": ["climate.trv_front_room"],
                "sources": [{"entity_id": "number.sim_front_room_room_temperature"}],
            }
        ]
    )
    with pytest.raises(ValidationError) as error:
        HouseConfig.from_options(payload)
    assert "climate.trv_front_room" in str(error.value)


def test_a_simulated_house_rejects_a_real_boiler() -> None:
    with pytest.raises(ValidationError):
        HouseConfig.from_options(sim_options(boiler_entity="climate.heating_heat"))
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS, eleven tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/models.py tests/test_models.py
git commit -m "add room climate configuration models"
```

---

### Task 3: Weighted room temperature

**Files:**
- Create: `custom_components/room_climate/control.py` (first functions)
- Test: `tests/test_control_temperature.py`

**Interfaces:**
- Consumes: `TemperatureSource`, `MIN_PLAUSIBLE_TEMPERATURE`, `MAX_PLAUSIBLE_TEMPERATURE`.
- Produces: `weighted_temperature(sources: Sequence[TemperatureSource],
  temperatures: Mapping[str, float | None]) -> float | None`,
  `usable_source_count(sources, temperatures) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_temperature.py
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control_temperature.py -v`
Expected: FAIL with `ImportError: cannot import name 'weighted_temperature'`.

- [ ] **Step 3: Write the functions**

```python
# custom_components/room_climate/control.py
"""Decision rules for room heating."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .const import MAX_PLAUSIBLE_TEMPERATURE, MIN_PLAUSIBLE_TEMPERATURE
from .models import TemperatureSource


def _plausible(value: float | None) -> bool:
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_control_temperature.py -v`
Expected: PASS, seven tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py tests/test_control_temperature.py
git commit -m "add the weighted room temperature"
```

---

### Task 4: Outdoor temperature and the lockout

**Files:**
- Modify: `custom_components/room_climate/control.py`
- Test: `tests/test_control_lockout.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `outdoor_temperature(sources: Sequence[str], temperatures: Mapping[str, float | None])
  -> float | None`, `next_lockout(active: bool, outdoor: float | None, threshold: float,
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


def test_outdoor_temperature_ignores_a_missing_reading() -> None:
    values = {"sensor.outdoor": None, "weather.home": 12.0}
    assert outdoor_temperature(("sensor.outdoor", "weather.home"), values) == 12.0


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
    usable = [value for entity_id in sources if _plausible(value := temperatures.get(entity_id))]
    if not usable:
        return None
    return sum(value for value in usable if value is not None) / len(usable)


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
Expected: PASS, eight tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py tests/test_control_lockout.py
git commit -m "add the outdoor lockout"
```

---

### Task 5: The room demand latch

**Files:**
- Modify: `custom_components/room_climate/control.py`
- Test: `tests/test_control_demand.py`

**Interfaces:**
- Consumes: `RoomConfig`.
- Produces: `next_calling(previous: bool, room: RoomConfig, target: float | None,
  hvac_mode: str, temperature: float | None, window_open: bool, fallback_demand: bool) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_demand.py
"""The demand latch, the window suspension and the fallback demand."""

from __future__ import annotations

import pytest
from custom_components.room_climate.control import next_calling
from custom_components.room_climate.models import RoomConfig, TemperatureSource


def room(**overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": "front_room",
        "name": "Front Room",
        "valves": ("climate.trv_front_room",),
        "sources": (TemperatureSource(entity_id="sensor.front_room_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


@pytest.mark.parametrize(
    ("temperature", "previous", "expected"),
    [(20.7, False, True), (20.8, False, False), (21.0, True, True), (21.21, True, False)],
)
def test_the_latch_band(temperature: float, previous: bool, expected: bool) -> None:
    assert next_calling(previous, room(), 21.0, "heat", temperature, False, False) is expected


@pytest.mark.parametrize(
    ("temperature", "target", "mode"),
    [(21.0, None, "heat"), (21.0, 5.0, "heat"), (21.0, 21.0, "off"), (None, 21.0, "heat")],
)
def test_a_room_that_cannot_heat_never_calls(
    temperature: float | None, target: float | None, mode: str
) -> None:
    assert next_calling(False, room(), target, mode, temperature, False, False) is False


def test_a_window_suspends_the_demand() -> None:
    assert next_calling(True, room(), 21.0, "heat", 19.0, True, False) is False


def test_the_valve_demand_carries_a_room_without_a_temperature() -> None:
    assert next_calling(False, room(), 21.0, "heat", None, False, True) is True


def test_the_valve_demand_does_not_override_a_live_temperature() -> None:
    assert next_calling(False, room(), 21.0, "heat", 21.5, False, True) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control_demand.py -v`
Expected: FAIL with `ImportError: cannot import name 'next_calling'`.

- [ ] **Step 3: Write the function**

```python
# appended to custom_components/room_climate/control.py

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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_control_demand.py -v`
Expected: PASS, thirteen tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py tests/test_control_demand.py
git commit -m "add the room demand latch"
```

---

### Task 6: The valve command

**Files:**
- Modify: `custom_components/room_climate/control.py`
- Test: `tests/test_control_valves.py`

**Interfaces:**
- Consumes: `RoomConfig`.
- Produces: `round_setpoint(value: float) -> float`,
  `valve_targets(room: RoomConfig, *, blocked: bool, calling: bool, target: float | None,
  last_sent: Mapping[str, float], last_command_at: Mapping[str, datetime],
  now: datetime) -> Mapping[str, float]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_valves.py
"""The valve targets, the repeat rule and the command interval."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from custom_components.room_climate.control import round_setpoint, valve_targets
from custom_components.room_climate.models import RoomConfig, TemperatureSource

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def room(**overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": "front_room",
        "name": "Front Room",
        "valves": ("climate.trv_front_room",),
        "sources": (TemperatureSource(entity_id="sensor.front_room_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


def targets(config: RoomConfig, **overrides: object) -> dict[str, float]:
    arguments: dict[str, object] = {
        "blocked": False,
        "calling": True,
        "target": 21.0,
        "last_sent": {},
        "last_command_at": {},
        "now": NOW,
    }
    arguments.update(overrides)
    return dict(valve_targets(config, **arguments))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"), [(20.0, 20.0), (20.24, 20.0), (20.26, 20.5), (20.75, 21.0)]
)
def test_setpoints_round_to_the_half_degree(raw: float, expected: float) -> None:
    assert round_setpoint(raw) == expected


def test_modulate_holds_the_setpoint_even_when_satisfied() -> None:
    assert targets(room(), calling=False) == {"climate.trv_front_room": 21.0}


def test_boost_opens_above_the_setpoint_while_calling() -> None:
    assert targets(room(valve_mode="boost", open_offset=2.0)) == {"climate.trv_front_room": 23.0}


def test_boost_closes_when_satisfied() -> None:
    assert targets(room(valve_mode="boost"), calling=False) == {"climate.trv_front_room": 5.0}


def test_a_blocked_room_closes_its_valves() -> None:
    assert targets(room(), blocked=True) == {"climate.trv_front_room": 5.0}


def test_the_setpoint_clamps_to_the_room_limits() -> None:
    assert targets(room(max_temp=22.0), target=25.0) == {"climate.trv_front_room": 22.0}


def test_a_sent_target_is_not_repeated() -> None:
    assert targets(room(), last_sent={"climate.trv_front_room": 21.0}) == {}


def test_a_due_but_recent_command_waits_for_the_interval() -> None:
    sent_at = {"climate.trv_front_room": NOW - timedelta(seconds=60)}
    assert targets(room(), target=23.0, last_command_at=sent_at) == {}


def test_a_due_command_after_the_interval_is_sent() -> None:
    sent_at = {"climate.trv_front_room": NOW - timedelta(seconds=400)}
    assert targets(room(), target=23.0, last_command_at=sent_at) == {"climate.trv_front_room": 23.0}


def test_closing_a_valve_is_never_deferred() -> None:
    sent_at = {"climate.trv_front_room": NOW - timedelta(seconds=5)}
    assert targets(room(valve_mode="boost"), calling=False, last_command_at=sent_at) == {
        "climate.trv_front_room": 5.0
    }


def test_every_valve_of_the_room_receives_the_target() -> None:
    config = room(valves=("climate.trv_left", "climate.trv_right"))
    assert targets(config, target=22.0) == {
        "climate.trv_left": 22.0,
        "climate.trv_right": 22.0,
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control_valves.py -v`
Expected: FAIL with `ImportError: cannot import name 'valve_targets'`.

- [ ] **Step 3: Write the functions**

```python
# appended to custom_components/room_climate/control.py

from datetime import datetime

from .const import REPEAT_THRESHOLD, SETPOINT_STEP, VALVE_MODE_MODULATE
from .models import RoomConfig


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
            if since is not None and (now - since).total_seconds() < room.valve_command_interval_seconds:
                continue
        commands[entity_id] = rounded
    return commands
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_control_valves.py -v`
Expected: PASS, sixteen tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py tests/test_control_valves.py
git commit -m "add the valve command rules"
```

---

### Task 7: The calibration feed

**Files:**
- Modify: `custom_components/room_climate/control.py`
- Test: `tests/test_control_calibration.py`

**Interfaces:**
- Consumes: `RoomConfig`.
- Produces: `feed_entity_id(valve_entity_id: str) -> str`,
  `calibration_values(room: RoomConfig, temperature: float | None,
  last_written: Mapping[str, float]) -> Mapping[str, float]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_calibration.py
"""The write of the room temperature into the valves."""

from __future__ import annotations

from custom_components.room_climate.control import calibration_values, feed_entity_id
from custom_components.room_climate.models import RoomConfig, TemperatureSource


def room(**overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": "front_room",
        "name": "Front Room",
        "valves": ("climate.trv_front_room",),
        "sources": (TemperatureSource(entity_id="sensor.front_room_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


def test_the_feed_entity_follows_the_valve_object_id() -> None:
    assert feed_entity_id("climate.trv_front_room") == "number.trv_front_room_external_measured_room_sensor"
    assert (
        feed_entity_id("climate.0x0011223344556677")
        == "number.0x0011223344556677_external_measured_room_sensor"
    )


def test_the_room_temperature_is_scaled() -> None:
    assert calibration_values(room(), 21.5, {}) == {
        "number.trv_front_room_external_measured_room_sensor": 2150
    }


def test_a_custom_scale_is_honoured() -> None:
    config = room(calibration_scale=10)
    assert calibration_values(config, 21.5, {}) == {
        "number.trv_front_room_external_measured_room_sensor": 215
    }


def test_the_value_clamps_to_the_valve_band() -> None:
    assert calibration_values(room(), 1.0, {}) == {
        "number.trv_front_room_external_measured_room_sensor": 500
    }
    assert calibration_values(room(), 40.0, {}) == {
        "number.trv_front_room_external_measured_room_sensor": 3500
    }


def test_a_small_change_is_not_written() -> None:
    written = {"number.trv_front_room_external_measured_room_sensor": 2150}
    assert calibration_values(room(), 21.55, written) == {}


def test_a_larger_change_is_written() -> None:
    written = {"number.trv_front_room_external_measured_room_sensor": 2100}
    assert calibration_values(room(), 21.5, written) == {
        "number.trv_front_room_external_measured_room_sensor": 2150
    }


def test_a_room_without_a_temperature_writes_nothing() -> None:
    assert calibration_values(room(), None, {}) == {}


def test_a_room_with_the_calibration_off_writes_nothing() -> None:
    config = room(calibration="none", valve_mode="boost")
    assert calibration_values(config, 21.5, {}) == {}


def test_every_valve_of_the_room_receives_the_value() -> None:
    config = room(valves=("climate.trv_left", "climate.trv_right"))
    assert set(calibration_values(config, 21.0, {})) == {
        "number.trv_left_external_measured_room_sensor",
        "number.trv_right_external_measured_room_sensor",
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control_calibration.py -v`
Expected: FAIL with `ImportError: cannot import name 'calibration_values'`.

- [ ] **Step 3: Write the functions**

```python
# appended to custom_components/room_climate/control.py

from .const import (
    CALIBRATION_EXTERNAL,
    CALIBRATION_MAX_TEMPERATURE,
    CALIBRATION_MIN_TEMPERATURE,
    CALIBRATION_NONE,
)

FEED_SUFFIX = "_external_measured_room_sensor"


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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_control_calibration.py -v`
Expected: PASS, nine tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py tests/test_control_calibration.py
git commit -m "add the calibration feed"
```

---

### Task 8: The graph average and the cycle decision

**Files:**
- Modify: `custom_components/room_climate/control.py`
- Test: `tests/test_control_decide.py`

**Interfaces:**
- Consumes: everything above, `ControlInput`, `ControlOutput`.
- Produces: `exponential_average(previous: float | None, value: float, elapsed_seconds: float,
  tau_seconds: float) -> float`, `decide(payload: ControlInput) -> ControlOutput`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_decide.py
"""The boiler decision, the timing guards and the cycle status."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from custom_components.room_climate.control import decide, exponential_average
from custom_components.room_climate.models import (
    ControlInput,
    HouseConfig,
    RoomConfig,
    RuntimeRoom,
    TemperatureSource,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def room(room_id: str, **overrides: object) -> RoomConfig:
    payload: dict[str, object] = {
        "room_id": room_id,
        "name": room_id.title(),
        "valves": (f"climate.trv_{room_id}",),
        "sources": (TemperatureSource(entity_id=f"sensor.{room_id}_temperature"),),
    }
    payload.update(overrides)
    return RoomConfig(**payload)


def house(**overrides: object) -> HouseConfig:
    payload: dict[str, object] = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ("sensor.outdoor",),
        "rooms": (room("front_room"), room("back_room")),
    }
    payload.update(overrides)
    return HouseConfig(**payload)


def runtime(room_id: str, target: float | None = 21.0) -> RuntimeRoom:
    return RuntimeRoom(room_id=room_id, target_temperature=target, hvac_mode="heat", calling=False)


def payload(**overrides: object) -> ControlInput:
    base: dict[str, object] = {
        "now": NOW,
        "house": house(),
        "temperatures": {
            "sensor.front_room_temperature": 20.0,
            "sensor.back_room_temperature": 21.0,
            "sensor.outdoor": 8.0,
        },
        "valve_demands": {},
        "window_states": {},
        "runtimes": (runtime("front_room"), runtime("back_room")),
        "last_actuator_targets": {},
        "last_calibration_values": {},
        "last_command_at": {},
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


def test_the_calling_room_commands_its_valve() -> None:
    output = decide(payload())
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.calling is True
    assert front_room.valve_targets == {"climate.trv_front_room": 21.0}
    assert front_room.calibration_values == {"number.trv_front_room_external_measured_room_sensor": 2000}


def test_no_calling_room_keeps_the_boiler_off() -> None:
    output = decide(
        payload(
            temperatures={
                "sensor.front_room_temperature": 22.0,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            }
        )
    )
    assert output.boiler.turn_on is False
    assert output.boiler.reason == "no_demand"
    assert output.boiler.send is False


def test_the_last_room_stopping_turns_the_boiler_off() -> None:
    output = decide(
        payload(
            temperatures={
                "sensor.front_room_temperature": 22.0,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            },
            boiler_on=True,
            boiler_state_since=NOW - timedelta(minutes=10),
        )
    )
    assert output.boiler.turn_on is False
    assert output.boiler.send is True


def test_the_boiler_does_not_switch_off_inside_min_on() -> None:
    output = decide(
        payload(
            temperatures={
                "sensor.front_room_temperature": 22.0,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            },
            boiler_on=True,
            boiler_state_since=NOW - timedelta(seconds=60),
        )
    )
    assert output.boiler.send is False


def test_the_boiler_does_not_switch_on_inside_min_off() -> None:
    output = decide(payload(boiler_on=False, boiler_state_since=NOW - timedelta(seconds=60)))
    assert output.boiler.send is False


def test_a_lockout_stops_the_boiler_at_once_and_closes_the_valves() -> None:
    output = decide(
        payload(outdoor_lockout_active=True, boiler_on=True, boiler_state_since=NOW - timedelta(seconds=5))
    )
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert output.boiler.turn_on is False
    assert output.boiler.reason == "lockout"
    assert output.boiler.send is True
    assert output.status == "locked_out"
    assert front_room.valve_targets == {"climate.trv_front_room": 5.0}


def test_the_override_bypasses_the_lockout() -> None:
    output = decide(payload(outdoor_lockout_active=True, override_active=True))
    assert output.boiler.turn_on is True


def test_the_master_switch_off_stops_the_boiler_at_once() -> None:
    output = decide(
        payload(master_enabled=False, boiler_on=True, boiler_state_since=NOW - timedelta(seconds=5))
    )
    assert output.boiler.send is True
    assert output.boiler.reason == "master_off"
    assert output.status == "disabled"


def test_a_window_open_closes_the_valve_without_waiting() -> None:
    config = house(rooms=(room("front_room", window_sensors=("binary_sensor.front_room_window",)), room("back_room")))
    output = decide(
        payload(
            house=config,
            window_states={"binary_sensor.front_room_window": "open"},
            last_command_at={"climate.trv_front_room": NOW - timedelta(seconds=5)},
        )
    )
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.window_open is True
    assert front_room.calling is False
    assert front_room.valve_targets == {"climate.trv_front_room": 5.0}


def test_a_switch_style_window_sensor_also_suspends_the_demand() -> None:
    config = house(rooms=(room("front_room", window_sensors=("switch.sim_front_room_window",)), room("back_room")))
    output = decide(
        payload(house=config, window_states={"switch.sim_front_room_window": "on"})
    )
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.window_open is True
    assert front_room.calling is False


def test_a_room_without_a_source_is_degraded_and_closed() -> None:
    output = decide(
        payload(
            temperatures={
                "sensor.front_room_temperature": None,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            }
        )
    )
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.calling is False
    assert front_room.degraded is True
    assert front_room.usable_sources == 0
    assert front_room.calibration_values == {}


def test_the_valve_demand_keeps_a_room_without_a_source_warm() -> None:
    config = house(
        rooms=(room("front_room", demand_fallback_sensors=("binary_sensor.trv_front_room_heat_required",)), room("back_room"))
    )
    output = decide(
        payload(
            house=config,
            temperatures={
                "sensor.front_room_temperature": None,
                "sensor.back_room_temperature": 22.0,
                "sensor.outdoor": 8.0,
            },
            valve_demands={"binary_sensor.trv_front_room_heat_required": True},
        )
    )
    front_room = next(command for command in output.rooms if command.room_id == "front_room")
    assert front_room.calling is True
    assert output.boiler.turn_on is True


def test_the_house_average_is_the_mean_of_the_room_averages() -> None:
    output = decide(payload())
    assert output.house_ema_temperature == pytest.approx(20.5)


@pytest.mark.parametrize(
    ("previous", "value", "elapsed", "expected"),
    [(None, 21.0, 30.0, 21.0), (20.0, 22.0, 0.0, 22.0), (20.0, 22.0, 1800.0, 20.0 + (1 - 0.36787944117))],
)
def test_the_graph_average(previous: float | None, value: float, elapsed: float, expected: float) -> None:
    result = exponential_average(previous, value, elapsed, 1800.0)
    assert result == pytest.approx(expected, abs=1e-6)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control_decide.py -v`
Expected: FAIL with `ImportError: cannot import name 'decide'`.

- [ ] **Step 3: Write the average and the decision**

```python
# appended to custom_components/room_climate/control.py

import math

from .const import (
    REASON_LOCKOUT,
    REASON_MASTER_OFF,
    REASON_NO_DEMAND,
    REASON_ROOM_DEMAND,
    STATUS_DEGRADED,
    STATUS_DISABLED,
    STATUS_DRY_RUN,
    STATUS_HEATING,
    STATUS_IDLE,
    STATUS_LOCKED_OUT,
    WINDOW_OPEN_STATES,
)
from .models import BoilerCommand, ControlInput, ControlOutput, RoomCommand, RuntimeRoom


def exponential_average(
    previous: float | None, value: float, elapsed_seconds: float, tau_seconds: float
) -> float:
    """Return the smoothed value, and the sample itself when no history exists."""
    if previous is None or elapsed_seconds <= 0.0 or tau_seconds <= 0.0:
        return value
    alpha = 1.0 - math.exp(-elapsed_seconds / tau_seconds)
    return previous + alpha * (value - previous)


def _is_fallback_demand(payload: ControlInput, room: RoomConfig) -> bool:
    return any(payload.valve_demands.get(entity_id) is True for entity_id in room.demand_fallback_sensors)


def _room_command(payload: ControlInput, room: RoomConfig, runtime: RuntimeRoom, blocked: bool) -> RoomCommand:
    temperature = weighted_temperature(room.sources, payload.temperatures)
    usable = usable_source_count(room.sources, payload.temperatures)
    window_open = any(
        payload.window_states.get(entity_id) in WINDOW_OPEN_STATES
        for entity_id in room.window_sensors
    )
    calling = next_calling(
        runtime.calling,
        room,
        runtime.target_temperature,
        runtime.hvac_mode,
        temperature,
        window_open,
        _is_fallback_demand(payload, room),
    )
    elapsed = (
        (payload.now - runtime.updated_at).total_seconds() if runtime.updated_at is not None else 0.0
    )
    ema = (
        exponential_average(runtime.ema_temperature, temperature, elapsed, payload.house.ema_seconds)
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
        degraded=usable == 0,
        valve_targets=valve_targets(
            room,
            blocked=blocked or window_open or runtime.hvac_mode == "off",
            calling=calling,
            target=runtime.target_temperature,
            last_sent=payload.last_actuator_targets,
            last_command_at=payload.last_command_at,
            now=payload.now,
        ),
        calibration_values=calibration_values(
            room, temperature, payload.last_calibration_values
        ),
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
        outdoor_temperature=outdoor_temperature(payload.house.outdoor_sources, payload.temperatures),
        house_ema_temperature=_house_average(commands),
        status=_status(payload, lockout, boiler, commands),
    )


def _house_average(commands: Sequence[RoomCommand]) -> float | None:
    values = [
        command.ema_temperature for command in commands if command.ema_temperature is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _boiler_command(
    turn_on: bool, reason: str, payload: ControlInput, immediate: bool = False
) -> BoilerCommand:
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_control_decide.py -v`
Expected: PASS, sixteen tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/control.py tests/test_control_decide.py
git commit -m "add the cycle decision"
```

---

### Task 9: The coordinator

**Files:**
- Create: `custom_components/room_climate/coordinator.py`
- Modify: `custom_components/room_climate/__init__.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `decide`, `HouseConfig.from_options`, `ControlOutput`.
- Produces: `RoomClimateCoordinator(hass, entry, config)` with the attributes `config`,
  `output`, `runtimes`, `last_actuator_targets`, `last_calibration_values`,
  `last_command_at`, `master_enabled`, `override_active`, and the methods
  `async_start()`, `async_stop()`, `async_recompute()`, `async_release_boiler()`. The `data`
  attribute is a `ControlOutput`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coordinator.py
"""The coordinator reads states, decides, and applies the commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN


def options(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "boiler_entity": "climate.heating_heat",
        "outdoor_sources": ["sensor.outdoor"],
        "rooms": [
            {
                "room_id": "front_room",
                "name": "Front Room",
                "valves": ["climate.trv_front_room"],
                "sources": [{"entity_id": "sensor.front_room_temperature"}],
            }
        ],
    }
    payload.update(overrides)
    return payload


async def setup_entry(hass: HomeAssistant, **overrides: object) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Room Climate", options=options(**overrides))
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.front_room_temperature", "20.0")
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.trv_front_room", "heat", {"temperature": 5.0})
    hass.states.async_set("number.trv_front_room_external_measured_room_sensor", "-8000")
    hass.states.async_set("climate.heating_heat", "off", {"temperature": 15.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def calls_for(hass: HomeAssistant, service: str, entity_id: str) -> list[dict]:
    return [
        dict(call.data)
        for call in hass.services.async_calls("climate", service)
        if call.data.get("entity_id") == entity_id
    ]


async def test_a_call_for_heat_commands_the_valve_and_the_boiler(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=False)
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.room_climate_front_room", "temperature": 21.0}
    )
    await hass.async_block_till_done()

    boiler = calls_for(hass, "set_temperature", "climate.heating_heat")
    assert boiler, "the boiler must be commanded"
    assert boiler[-1]["temperature"] == 31.0
    assert boiler[-1]["hvac_mode"] == "heat"

    valve = calls_for(hass, "set_temperature", "climate.trv_front_room")
    assert [call["temperature"] for call in valve][-1] == 21.0


async def test_the_calibration_feed_is_written(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=False)
    writes = hass.services.async_calls("number", "set_value")
    assert any(call.data.get("value") == 2000 for call in writes)


async def test_dry_run_sends_no_command(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    with patch("homeassistant.core.ServiceRegistry.async_call", new=AsyncMock()) as call:
        await hass.services.async_call("room_climate", "recompute", {})
        await hass.async_block_till_done()
    assert not [item for item in call.await_args_list if item.args[:1] == ("climate",)]
    assert not [item for item in call.await_args_list if item.args[:1] == ("number",)]


async def test_an_unavailable_valve_does_not_stop_the_other_valve(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate",
        options=options(
            dry_run=False,
            rooms=[
                {
                    "room_id": "side_room",
                    "name": "Side Room",
                    "valves": ["climate.trv_side_room_a", "climate.trv_side_room_b"],
                    "sources": [{"entity_id": "sensor.side_room_temperature"}],
                }
            ],
        ),
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.side_room_temperature", "19.0")
    hass.states.async_set("sensor.outdoor", "8.0")
    hass.states.async_set("climate.trv_side_room_a", "unavailable")
    hass.states.async_set("climate.trv_side_room_b", "heat", {"temperature": 5.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.room_climate_side_room", "temperature": 21.0},
    )
    await hass.async_block_till_done()
    assert calls_for(hass, "set_temperature", "climate.trv_side_room_b")
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

from .control import decide, feed_entity_id, next_lockout, outdoor_temperature
from .models import ControlInput, ControlOutput, HouseConfig, RuntimeRoom

_LOGGER = logging.getLogger(__name__)


def _numeric_state(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    if state.domain == "weather":
        temperature = state.attributes.get("temperature")
        return float(temperature) if temperature is not None else None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


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
        self.last_calibration_values: dict[str, float] = {}
        self.last_command_at: dict[str, datetime] = {}
        self.master_enabled = config.master_enabled
        self.override_active = False
        self.available = True
        self._listeners: list[object] = []

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
            )
        ]
        return tuple((*watched, self.config.boiler_entity, *self.config.outdoor_sources))

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
        for unsubscribe in self._listeners:
            unsubscribe()  # type: ignore[operator]
        self._listeners.clear()

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_recompute())

    async def async_recompute(self) -> ControlOutput:
        """Run one cycle and apply the commands."""
        temperatures = {
            entity_id: _numeric_state(self.hass, entity_id)
            for entity_id in (
                *self.config.outdoor_sources,
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
        outdoor = outdoor_temperature(self.config.outdoor_sources, temperatures)
        self.lockout_active = next_lockout(
            self.lockout_active,
            outdoor,
            self.config.lockout_temperature,
            self.config.lockout_hysteresis,
        )
        payload = ControlInput(
            now=dt_util.utcnow(),
            house=self.config,
            temperatures=temperatures,
            valve_demands=valve_demands,
            window_states=window_states,
            runtimes=tuple(self.runtimes.values()),
            last_actuator_targets=self.last_actuator_targets,
            last_calibration_values=self.last_calibration_values,
            last_command_at=self.last_command_at,
            outdoor_lockout_active=self.lockout_active,
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
        self.entry.async_update_listeners()
        return output

    async def _apply(self, output: ControlOutput) -> None:
        """Send the commands of one cycle."""
        now = dt_util.utcnow()
        for command in output.rooms:
            for entity_id, value in command.valve_targets.items():
                if self.hass.states.get(entity_id) is None:
                    continue
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": entity_id, "temperature": value},
                    blocking=False,
                )
                self.last_actuator_targets[entity_id] = value
                self.last_command_at[entity_id] = now
            for entity_id, value in command.calibration_values.items():
                if self.hass.states.get(entity_id) is None:
                    continue
                await self.hass.services.async_call(
                    "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=False
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
        self.runtimes = {
            command.room_id: RuntimeRoom(
                room_id=command.room_id,
                target_temperature=self.runtimes.get(
                    command.room_id, RuntimeRoom(room_id=command.room_id)
                ).target_temperature,
                hvac_mode=self.runtimes.get(
                    command.room_id, RuntimeRoom(room_id=command.room_id)
                ).hvac_mode,
                calling=command.calling,
                ema_temperature=command.ema_temperature,
                updated_at=now,
            )
            for command in output.rooms
        }

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
```

`__init__.py` sets the entry up: it builds `HouseConfig.from_options(entry.options)`, creates
the coordinator, forwards the platforms `climate`, `switch`, `binary_sensor` and `sensor`,
starts the coordinator, and registers the `room_climate.recompute` service. `async_unload_entry`
stops the coordinator, releases the boiler and unloads the platforms.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_coordinator.py -v`
Expected: PASS, four tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/coordinator.py custom_components/room_climate/__init__.py \
  tests/test_coordinator.py
git commit -m "add the room climate coordinator"
```

---

### Task 10: The platforms

**Files:**
- Create: `custom_components/room_climate/climate.py`, `switch.py`, `binary_sensor.py`, `sensor.py`
- Test: `tests/test_platforms.py`

**Interfaces:**
- Consumes: `RoomClimateCoordinator`.
- Produces: `climate.room_climate_<room>`, `switch.room_climate_boiler_automation`,
  `switch.room_climate_lockout_override`, `binary_sensor.room_climate_boiler_demand`,
  `binary_sensor.room_climate_outdoor_lockout`, `sensor.room_climate_outdoor_temperature`,
  `sensor.room_climate_house_temperature`, `sensor.room_climate_status`,
  `sensor.room_climate_<room>_temperature`, `sensor.room_climate_<room>_temperature_ema`,
  `binary_sensor.room_climate_<room>_calling`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_platforms.py
"""The entities the operator sees and the Scheduler commands."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .test_coordinator import setup_entry


async def test_the_virtual_thermostat_reports_the_weighted_temperature(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    state = hass.states.get("climate.room_climate_front_room")
    assert state is not None
    assert state.attributes["current_temperature"] == 20.0
    assert state.attributes["hvac_action"] == "idle"
    assert state.attributes["hvac_modes"] == ["off", "heat"]
    assert state.attributes["valves"] == ["climate.trv_front_room"]


async def test_the_scheduler_sets_the_setpoint_and_the_room_calls(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.room_climate_front_room", "temperature": 21.0}
    )
    await hass.async_block_till_done()
    state = hass.states.get("climate.room_climate_front_room")
    assert state.attributes["temperature"] == 21.0
    assert state.attributes["hvac_action"] == "heating"


async def test_the_house_entities_exist(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=True)
    for entity_id in (
        "switch.room_climate_boiler_automation",
        "switch.room_climate_lockout_override",
        "binary_sensor.room_climate_boiler_demand",
        "binary_sensor.room_climate_outdoor_lockout",
        "sensor.room_climate_outdoor_temperature",
        "sensor.room_climate_house_temperature",
        "sensor.room_climate_status",
        "sensor.room_climate_front_room_temperature",
        "sensor.room_climate_front_room_temperature_ema",
        "binary_sensor.room_climate_front_room_calling",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_the_master_switch_releases_the_boiler(hass: HomeAssistant) -> None:
    await setup_entry(hass, dry_run=False)
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.room_climate_front_room", "temperature": 21.0}
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.room_climate_boiler_automation"}
    )
    await hass.async_block_till_done()
    boiler = [
        dict(call.data)
        for call in hass.services.async_calls("climate", "set_temperature")
        if call.data.get("entity_id") == "climate.heating_heat"
    ]
    assert boiler[-1]["temperature"] == 15.0
    assert boiler[-1]["hvac_mode"] == "off"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_platforms.py -v`
Expected: FAIL, because no climate entity exists.

- [ ] **Step 3: Write the platforms**

```python
# custom_components/room_climate/climate.py
"""One virtual thermostat per room."""

from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .models import RoomConfig, RuntimeRoom
from .coordinator import RoomClimateCoordinator


class RoomClimateEntity(ClimateEntity, RestoreEntity):
    """The dashboard surface and the Scheduler target of one room."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_target_temperature_step = 0.5
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_should_poll = False

    def __init__(self, coordinator: RoomClimateCoordinator, room: RoomConfig) -> None:
        self.coordinator = coordinator
        self.room = room
        self._attr_unique_id = f"{DOMAIN}_{room.room_id}_climate"
        self._attr_min_temp = room.min_temp
        self._attr_max_temp = room.max_temp
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_target_temperature: float | None = None

    @property
    def _command(self):  # type: ignore[no-untyped-def]
        output = self.coordinator.output
        if output is None:
            return None
        return next((command for command in output.rooms if command.room_id == self.room.room_id), None)

    @property
    def current_temperature(self) -> float | None:
        command = self._command
        return None if command is None else command.room_temperature

    @property
    def hvac_action(self) -> HVACAction:
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        command = self._command
        return HVACAction.HEATING if command is not None and command.calling else HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        command = self._command
        return {
            "valves": list(self.room.valves),
            "valve_targets": dict(command.valve_targets) if command else {},
            "calibration": self.room.calibration if command else None,
            "calibration_scale": self.room.calibration_scale,
            "usable_sources": command.usable_sources if command else 0,
            "degraded": command.degraded if command else True,
            "window_open": command.window_open if command else False,
        }

    def _remember(self) -> None:
        runtime = self.coordinator.runtimes.get(self.room.room_id, RuntimeRoom(room_id=self.room.room_id))
        self.coordinator.runtimes[self.room.room_id] = runtime.model_copy(
            update={
                "target_temperature": self._attr_target_temperature,
                "hvac_mode": str(self._attr_hvac_mode),
            }
        )

    async def async_set_temperature(self, **kwargs: object) -> None:
        temperature = kwargs.get("temperature")
        if isinstance(temperature, (int, float)):
            self._attr_target_temperature = float(temperature)
        self._remember()
        await self.coordinator.async_recompute()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        self._remember()
        await self.coordinator.async_recompute()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_target_temperature = last.attributes.get("temperature")
            self._attr_hvac_mode = HVACMode(last.state)
        self._remember()
```

`switch.py` publishes the two switches and writes `coordinator.master_enabled` and
`coordinator.override_active`, then requests a cycle. `binary_sensor.py` and `sensor.py`
publish the values of `coordinator.output`: the calling latch, the room temperature, the room
EMA (`command.ema_temperature`), the house EMA, the outdoor temperature, the boiler demand,
the lockout and the status. `sensor.room_climate_status` uses `SensorDeviceClass.ENUM` with
the six values.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_platforms.py -v`
Expected: PASS, four tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/climate.py custom_components/room_climate/switch.py \
  custom_components/room_climate/binary_sensor.py custom_components/room_climate/sensor.py \
  tests/test_platforms.py
git commit -m "add the room climate platforms"
```

---

### Task 11: The config flow

**Files:**
- Create: `custom_components/room_climate/config_flow.py`, `strings.json`
- Modify: `custom_components/room_climate/translations/en.json`
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
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
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
    assert result["data"]["dry_run"] is True


async def test_a_room_needs_a_valve_and_a_source(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options={"rooms": [], "outdoor_sources": ["sensor.outdoor"]})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_room"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"room_id": "front_room", "name": "Front Room", "valves": [], "sources": [{"entity_id": "sensor.front_room"}]},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]


async def test_options_flow_rejects_a_shared_valve(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options={"rooms": [], "outdoor_sources": ["sensor.outdoor"]})
    entry.add_to_hass(hass)
    room = {
        "room_id": "front_room",
        "name": "Front Room",
        "valves": ["climate.trv_front_room"],
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
    assert result["errors"]["base"] == "shared_entity"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_config_flow.py -v`
Expected: FAIL, because the domain has no flow handler.

- [ ] **Step 3: Write the flows**

The room form derives `room_id` from the name with `slugify` and never asks for it, because a
room id must be a slug for the simulation entity ids to be valid.

The user step collects the house fields with `vol.Schema` selectors: `EntitySelector` with
`domain="climate"` for the boiler, `EntitySelector` with `device_class="temperature"` for the
outdoor sources, `NumberSelector` for the thresholds, and `BooleanSelector` for `dry_run`. The
options flow uses `OptionsFlowWithConfigEntry`, a `menu` step, and one form per room. The room
form collects `valves` and `sources` with `EntitySelector(multiple=True)`, `calibration`,
`calibration_scale`, `valve_mode`, `open_offset`, the temperature limits, `hysteresis`,
`off_setpoint`, `valve_command_interval_seconds`, `window_sensors` and
`demand_fallback_sensors`. It validates the room with `HouseConfig.model_validate` over the
room list plus the new room, and it maps `ValidationError` to the errors `shared_entity` and
`invalid_room`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_config_flow.py -v`
Expected: PASS, three tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/config_flow.py custom_components/room_climate/strings.json \
  custom_components/room_climate/translations/en.json tests/test_config_flow.py
git commit -m "add the room climate config flow"
```

---

### Task 12: Unload safety, diagnostics and the recompute service

**Files:**
- Modify: `custom_components/room_climate/__init__.py`, `custom_components/room_climate/coordinator.py`
- Create: `custom_components/room_climate/diagnostics.py`, `services.yaml`
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
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.room_climate_front_room", "temperature": 21.0}
    )
    await hass.async_block_till_done()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    boiler = [
        dict(call.data)
        for call in hass.services.async_calls("climate", "set_temperature")
        if call.data.get("entity_id") == "climate.heating_heat"
    ]
    assert boiler[-1]["temperature"] == 15.0
    assert boiler[-1]["hvac_mode"] == "off"


async def test_recompute_runs_one_cycle(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass, dry_run=True)
    entry.runtime_data.output = None
    await hass.services.async_call("room_climate", "recompute", {})
    await hass.async_block_till_done()
    assert entry.runtime_data.output is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_unload.py -v`
Expected: FAIL, because unload sends no boiler command.

- [ ] **Step 3: Write the release path, the service and the diagnostics**

`async_unload_entry` stops the coordinator, calls `coordinator.async_release_boiler()`, then
unloads the four platforms and removes the service when the last entry goes away.
`async_remove_entry` releases the boiler as well. `async_setup` registers
`room_climate.recompute` with no fields and one handler per loaded entry.
`diagnostics.py` returns the config model dump, the last `ControlOutput`, the watched entities
and the timestamps of the boiler transitions.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_unload.py -v`
Expected: PASS, two tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/__init__.py custom_components/room_climate/coordinator.py \
  custom_components/room_climate/diagnostics.py custom_components/room_climate/services.yaml \
  tests/test_unload.py
git commit -m "release the boiler on unload"
```

---

### Task 13: The simulation house

**Files:**
- Create: `custom_components/room_climate/sim.py`, `custom_components/room_climate/number.py`
- Modify: `custom_components/room_climate/climate.py`, `switch.py`, `binary_sensor.py`, `sensor.py`,
  `__init__.py`
- Test: `tests/test_sim.py`

**Interfaces:**
- Consumes: `HouseConfig.simulation`, the coordinator, `SIM_MARKER`.
- Produces: `sim_valve_entity`, `sim_source_entity`, `sim_local_temperature_entity`,
  `sim_feed_entity`, `sim_window_entity`, `sim_offline_entity`, `sim_position_entity`,
  `sim_heat_required_entity`, `SIM_BOILER`, `SIM_BOILER_FIRING`, `SIM_OUTDOOR`,
  `measured_temperature(hass, room_id)`, `valve_position(hass, room_id)`, and the sim entity
  classes.

A platform module cannot add entities of another domain. Each platform therefore branches on
`config.simulation`: `climate.py` adds the virtual valves and the virtual boiler, `number.py`
adds the knobs, `switch.py` adds the window and offline knobs, `binary_sensor.py` adds the
heat demand and the boiler firing signal, and `sensor.py` adds the valve position and the
outdoor temperature. The entity classes live only in `sim.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sim.py
"""The bench: virtual valves, a virtual boiler and the knobs."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN


def bench_room(**overrides: object) -> dict[str, object]:
    room: dict[str, object] = {
        "room_id": "front_room",
        "name": "Front Room",
        "valves": ["climate.sim_front_room_valve"],
        "sources": [{"entity_id": "number.sim_front_room_room_temperature"}],
    }
    room.update(overrides)
    return room


def sim_options(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "simulation": True,
        "boiler_entity": "climate.sim_boiler",
        "outdoor_sources": ["number.sim_outdoor_temperature"],
        "dry_run": False,
        "rooms": [bench_room()],
    }
    payload.update(overrides)
    return payload


async def setup_bench(hass: HomeAssistant, **overrides: object) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Room Climate Bench", options=sim_options(**overrides)
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": value}
    )
    await hass.async_block_till_done()


async def set_target(hass: HomeAssistant, entity_id: str, temperature: float) -> None:
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": entity_id, "temperature": temperature}
    )
    await hass.async_block_till_done()


async def test_the_bench_creates_its_entities(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    for entity_id in (
        "climate.sim_front_room_valve",
        "number.sim_front_room_room_temperature",
        "number.sim_front_room_valve_local_temperature",
        "number.sim_front_room_valve_external_measured_room_sensor",
        "sensor.sim_front_room_valve_position",
        "binary_sensor.sim_front_room_heat_required",
        "switch.sim_front_room_window",
        "switch.sim_front_room_offline",
        "climate.sim_boiler",
        "binary_sensor.sim_boiler_firing",
        "number.sim_outdoor_temperature",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_a_real_entity_is_rejected_in_a_simulated_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate Bench",
        options=sim_options(rooms=[bench_room(valves=["climate.trv_front_room"])]),
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_a_cold_room_fires_the_virtual_boiler(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)
    await set_target(hass, "climate.room_climate_front_room", 21.0)

    assert hass.states.get("climate.sim_boiler").attributes["temperature"] == 31.0
    assert hass.states.get("binary_sensor.sim_boiler_firing").state == "on"
    assert hass.states.get("climate.sim_front_room_valve").attributes["temperature"] == 21.0
    assert hass.states.get("binary_sensor.sim_front_room_heat_required").state == "on"


async def test_a_warm_room_releases_the_virtual_boiler(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)
    await set_target(hass, "climate.room_climate_front_room", 21.0)
    await set_number(hass, "number.sim_front_room_room_temperature", 22.0)

    assert hass.states.get("climate.sim_boiler").attributes["temperature"] == 15.0
    assert hass.states.get("binary_sensor.sim_boiler_firing").state == "off"


async def test_the_valve_position_follows_the_gap(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)
    await set_target(hass, "climate.sim_front_room_valve", 21.0)
    assert float(hass.states.get("sensor.sim_front_room_valve_position").state) == 100.0

    await set_number(hass, "number.sim_front_room_valve_external_measured_room_sensor", 2050)
    assert float(hass.states.get("sensor.sim_front_room_valve_position").state) == 25.0


async def test_the_feed_overrides_the_warm_local_sensor(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await set_number(hass, "number.sim_front_room_valve_local_temperature", 25.0)
    await set_number(hass, "number.sim_front_room_room_temperature", 21.0)
    await set_target(hass, "climate.sim_front_room_valve", 21.0)

    assert hass.states.get("climate.sim_front_room_valve").attributes["current_temperature"] == 21.0
    assert float(hass.states.get("sensor.sim_front_room_valve_position").state) == 0.0


async def test_the_local_sensor_is_the_measurement_without_the_calibration(
    hass: HomeAssistant,
) -> None:
    await setup_bench(
        hass,
        rooms=[bench_room(valve_mode="boost", calibration="none")],
    )
    await set_number(hass, "number.sim_front_room_valve_local_temperature", 24.0)
    assert hass.states.get("climate.sim_front_room_valve").attributes["current_temperature"] == 24.0


async def test_an_offline_valve_reports_unavailable(hass: HomeAssistant) -> None:
    await setup_bench(hass)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.sim_front_room_offline"})
    await hass.async_block_till_done()
    assert hass.states.get("climate.sim_front_room_valve").state == "unavailable"


async def test_an_open_window_closes_the_valve(hass: HomeAssistant) -> None:
    await setup_bench(hass, rooms=[bench_room(window_sensors=["switch.sim_front_room_window"])])
    await set_number(hass, "number.sim_front_room_room_temperature", 19.0)
    await set_target(hass, "climate.room_climate_front_room", 21.0)
    assert hass.states.get("climate.sim_front_room_valve").attributes["temperature"] == 21.0

    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.sim_front_room_window"})
    await hass.async_block_till_done()

    assert hass.states.get("climate.sim_front_room_valve").attributes["temperature"] == 5.0
    assert hass.states.get("binary_sensor.room_climate_front_room_calling").state == "off"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_sim.py -v`
Expected: FAIL, because `sim.py` does not exist and no sim entity is created.

- [ ] **Step 3: Write the simulation house**

```python
# custom_components/room_climate/sim.py
"""The simulation house: virtual valves, a virtual boiler, and the knobs."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback, State
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, SIM_MARKER

FEED_DISABLED = -8000.0
FEED_SCALE = 100.0
VALVE_POSITION_GAIN = 50.0
BOILER_FIRING_THRESHOLD = 30.0
SIM_MIN_TEMPERATURE = 5.0
SIM_MAX_TEMPERATURE = 35.0


def sim_valve_entity(room_id: str) -> str:
    """Return the virtual valve of one room."""
    return f"climate.{SIM_MARKER}{room_id}_valve"


def sim_source_entity(room_id: str) -> str:
    """Return the virtual room temperature of one room."""
    return f"number.{SIM_MARKER}{room_id}_room_temperature"


def sim_local_temperature_entity(room_id: str) -> str:
    """Return the local sensor knob of one virtual valve."""
    return f"number.{SIM_MARKER}{room_id}_valve_local_temperature"


def sim_feed_entity(room_id: str) -> str:
    """Return the feed entity of one virtual valve."""
    return f"number.{SIM_MARKER}{room_id}_valve_external_measured_room_sensor"


def sim_window_entity(room_id: str) -> str:
    """Return the window knob of one room."""
    return f"switch.{SIM_MARKER}{room_id}_window"


def sim_offline_entity(room_id: str) -> str:
    """Return the offline knob of one room."""
    return f"switch.{SIM_MARKER}{room_id}_offline"


def sim_position_entity(room_id: str) -> str:
    """Return the position sensor of one virtual valve."""
    return f"sensor.{SIM_MARKER}{room_id}_valve_position"


def sim_heat_required_entity(room_id: str) -> str:
    """Return the heat demand of one virtual valve."""
    return f"binary_sensor.{SIM_MARKER}{room_id}_heat_required"


SIM_BOILER = "climate.sim_boiler"
SIM_BOILER_FIRING = "binary_sensor.sim_boiler_firing"
SIM_OUTDOOR = "number.sim_outdoor_temperature"


def _number(hass: HomeAssistant, entity_id: str) -> float | None:
    state: State | None = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def measured_temperature(hass: HomeAssistant, room_id: str) -> float | None:
    """Return what the virtual valve measures: the feed when set, else its local sensor."""
    feed = _number(hass, sim_feed_entity(room_id))
    if feed is not None and feed != FEED_DISABLED:
        return feed / FEED_SCALE
    return _number(hass, sim_local_temperature_entity(room_id))


def valve_position(hass: HomeAssistant, room_id: str) -> float:
    """Return the valve position in percent."""
    state = hass.states.get(sim_valve_entity(room_id))
    if state is None or state.state in ("off", "unavailable"):
        return 0.0
    target = state.attributes.get("temperature")
    measured = measured_temperature(hass, room_id)
    if target is None or measured is None:
        return 0.0
    return max(0.0, min(100.0, (float(target) - measured) * VALVE_POSITION_GAIN))


def boiler_firing(hass: HomeAssistant) -> bool:
    """Return whether the virtual boiler fires."""
    state = hass.states.get(SIM_BOILER)
    if state is None or state.state != "heat":
        return False
    target = state.attributes.get("temperature")
    return target is not None and float(target) >= BOILER_FIRING_THRESHOLD


def _room_offline(hass: HomeAssistant, room_id: str) -> bool:
    state = hass.states.get(sim_offline_entity(room_id))
    return state is not None and state.state == "on"


class SimValveEntity(ClimateEntity):
    """A valve that computes its position from the gap of target and measurement."""

    _attr_has_entity_name = True
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_target_temperature_step = 0.5
    _attr_min_temp = SIM_MIN_TEMPERATURE
    _attr_max_temp = SIM_MAX_TEMPERATURE

    def __init__(self, room_id: str) -> None:
        self.room_id = room_id
        self._attr_unique_id = f"{DOMAIN}_{SIM_MARKER}{room_id}_valve"
        self._attr_name = f"Sim {room_id.replace('_', ' ')} valve"
        self._attr_target_temperature = SIM_MIN_TEMPERATURE

    @property
    def available(self) -> bool:
        return not _room_offline(self.hass, self.room_id)

    @property
    def current_temperature(self) -> float | None:
        return measured_temperature(self.hass, self.room_id)

    @property
    def hvac_action(self) -> HVACAction:
        return HVACAction.HEATING if valve_position(self.hass, self.room_id) > 0 else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: object) -> None:
        temperature = kwargs.get("temperature")
        if isinstance(temperature, (int, float)):
            self._attr_target_temperature = float(temperature)
        self.async_write_ha_state()


class SimBoilerEntity(ClimateEntity):
    """A boiler that fires on a high target, like the Hive pair."""

    _attr_has_entity_name = True
    _attr_name = "Sim boiler"
    _attr_unique_id = f"{DOMAIN}_sim_boiler"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_target_temperature_step = 0.5
    _attr_min_temp = SIM_MIN_TEMPERATURE
    _attr_max_temp = 32.0
    _attr_target_temperature = 15.0

    @property
    def hvac_action(self) -> HVACAction:
        return HVACAction.HEATING if boiler_firing(self.hass) else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: object) -> None:
        temperature = kwargs.get("temperature")
        if isinstance(temperature, (int, float)):
            self._attr_target_temperature = float(temperature)
        mode = kwargs.get("hvac_mode")
        if isinstance(mode, HVACMode):
            self._attr_hvac_mode = mode
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()


class SimNumberEntity(NumberEntity):
    """A temperature knob of the bench."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_step = 0.1
    _attr_native_min_value = SIM_MIN_TEMPERATURE
    _attr_native_max_value = SIM_MAX_TEMPERATURE
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self, key: str, name: str, initial: float, offline_room: str | None = None
    ) -> None:
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name
        self._attr_native_value = initial
        self._offline_room = offline_room

    @property
    def available(self) -> bool:
        if self._offline_room is None:
            return True
        return not _room_offline(self.hass, self._offline_room)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()


class SimSwitchEntity(SwitchEntity):
    """A two state knob of the bench."""

    _attr_has_entity_name = True

    def __init__(self, key: str, name: str, initial: bool = False) -> None:
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name
        self._attr_is_on = initial

    async def async_turn_on(self, **kwargs: object) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: object) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()


class _DerivedSimEntity(Entity):
    """An entity that recomputes when one of the entities it reads changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _watch: tuple[str, ...] = ()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_track_state_change_event(self.hass, list(self._watch), self._handle_change)
        )

    @callback
    def _handle_change(self, event: Event) -> None:
        self.async_write_ha_state()


class SimValvePositionSensor(_DerivedSimEntity, SensorEntity):
    """The position of one virtual valve."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, room_id: str) -> None:
        self.room_id = room_id
        self._attr_unique_id = f"{DOMAIN}_{SIM_MARKER}{room_id}_valve_position"
        self._attr_name = f"Sim {room_id.replace('_', ' ')} valve position"
        self._watch = (
            sim_valve_entity(room_id),
            sim_feed_entity(room_id),
            sim_local_temperature_entity(room_id),
        )

    @property
    def native_value(self) -> float:
        return valve_position(self.hass, self.room_id)


class SimHeatRequiredBinary(_DerivedSimEntity, BinarySensorEntity):
    """The heat demand of one virtual valve."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, room_id: str) -> None:
        self.room_id = room_id
        self._attr_unique_id = f"{DOMAIN}_{SIM_MARKER}{room_id}_heat_required"
        self._attr_name = f"Sim {room_id.replace('_', ' ')} heat required"
        self._watch = (sim_valve_entity(room_id), sim_position_entity(room_id))

    @property
    def is_on(self) -> bool:
        return valve_position(self.hass, self.room_id) > 0


class SimBoilerFiringBinary(_DerivedSimEntity, BinarySensorEntity):
    """Whether the virtual boiler fires."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_name = "Sim boiler firing"
    _attr_unique_id = f"{DOMAIN}_sim_boiler_firing"

    def __init__(self) -> None:
        self._watch = (SIM_BOILER,)

    @property
    def is_on(self) -> bool:
        return boiler_firing(self.hass)
```

The platforms add the sim entities when `config.simulation` is true:

```python
# the branch every platform takes
if config.simulation:
    async_add_entities(SimValveEntity(room.room_id) for room in config.rooms)
```
`climate.py` also adds `SimBoilerEntity()`. `number.py` adds three knobs per room and the
outdoor knob:

```python
async_add_entities(
    [
        SimNumberEntity(
            f"{SIM_MARKER}{room.room_id}_room_temperature",
            f"Sim {room.room_id.replace('_', ' ')} room temperature",
            20.0,
        ),
        SimNumberEntity(
            f"{SIM_MARKER}{room.room_id}_valve_local_temperature",
            f"Sim {room.room_id.replace('_', ' ')} local temperature",
            20.0,
            offline_room=room.room_id,
        ),
        SimNumberEntity(
            f"{SIM_MARKER}{room.room_id}_valve_external_measured_room_sensor",
            f"Sim {room.room_id.replace('_', ' ')} external sensor",
            FEED_DISABLED,
            offline_room=room.room_id,
        ),
    ]
    for room in config.rooms
)
```
plus `SimSwitchEntity(f"{SIM_MARKER}{room.room_id}_window", ...)` and `..._offline` in
`switch.py`, `SimBoilerFiringBinary()` in `binary_sensor.py`, and the position sensors in
`sensor.py`. `number.py` is a new platform, so `PLATFORMS` gains `Platform.NUMBER`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_sim.py -v`
Expected: PASS, ten tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/room_climate/sim.py custom_components/room_climate/number.py \
  custom_components/room_climate/climate.py custom_components/room_climate/switch.py \
  custom_components/room_climate/binary_sensor.py custom_components/room_climate/sensor.py \
  custom_components/room_climate/__init__.py tests/test_sim.py
git commit -m "add the simulation house"
```

---

### Task 14: Packaging, the bench dashboard and the migration runbook

**Files:**
- Modify: `README.md`, `hacs.json`
- Create: `dashboard/room-climate.yaml`, `docs/room-heating.runbook.md`,
  `scripts/probe_live.py`, `docs/plans/2026-10-19-room-heating.evidence.md`

**Interfaces:**
- Consumes: the whole component.
- Produces: the install route, the dashboard example, the migration record and the live
  evidence.

- [ ] **Step 1: Write the failing check**

Run: `uv run python -c "import json,pathlib; json.loads(pathlib.Path('hacs.json').read_text())['name']"`
Expected: FAIL with `KeyError: 'name'`, because `hacs.json` has no `name` field yet.

- [ ] **Step 2: Write the packaging**

1. Set `hacs.json` to
   `{"name": "Room Climate", "render_readme": true, "homeassistant": "2026.10.0"}`.
2. Write `README.md` with the install route, the room table of the spec's section 3.3, and
   the eight dashboard cards that change.
3. Write `dashboard/room-climate.yaml` with one thermostat card per room, the house panel and
   one history graph per room.
4. Write `docs/room-heating.runbook.md` with the fourteen steps of the spec's section 8, the
   rollback steps, and the feed-scale confirmation of steps 6 and 7 as its own section.
5. Write `scripts/probe_live.py`. It reads the token from `~/.hermes/ha_token`, prints the
   room climate entities with their attributes, the boiler state, the status sensor, and the
   current value of every feed entity.
6. Write `dashboard/room-climate-bench.yaml` with the knobs of the bench, the virtual boiler,
   the virtual valves and their positions.

- [ ] **Step 3: Run the live regression, bench first**

Bench, which is phase A of the spec's section 8.1:

1. Push the repository to `main`, then tag `v0.1.0` and push the tag.
2. Add `octo47/ha-automation` to HACS as a custom repository of category Integration, then
   download it. Restart Home Assistant.
3. Add the integration with `simulation` on and `dry_run` on. Add each room of the spec's
   section 3.3 by name.
4. Run `uv run python scripts/probe_live.py`, and read the computed commands in the log.
5. Turn `dry_run` off.
6. Walk the knob table of the spec's section 8.1 step 8. Confirm every row.
7. Tune the weights, the hysteresis, `open_offset`, `min_on_seconds` and
   `lockout_hysteresis` until every row behaves as its right column states.

Real house, which is phase B of the spec's section 8.2:

8. Add a second integration entry with `simulation` off. Configure each room with the real
   valve and sensor ids of the spec's section 3.3.
9. Confirm the feed scale on one valve. Write 2150 into its feed entity, read the value back,
   and watch `sensor.<trv>_pi_heating_demand` for one hour. This is the one live write to a
   real valve, and the operator performs it.
10. Keep `dry_run` on for one day. Watch the log and `sensor.room_climate_status`.
11. Turn `dry_run` off, then disable `automation.heat_on_demand`.
12. Watch one full heating cycle per room. Confirm that the boiler reaches 31 °C when a room
    calls, and 15 °C when no room calls.
13. Lower the lockout threshold below the outdoor temperature. Confirm that the boiler returns
    to 15 °C and the valves to 5 °C within one cycle. Restore the threshold to 18 °C.
14. Repoint the eight dashboard cards to `climate.room_climate_*`.
15. Remove the eight Better Thermostat entries, then remove Better Thermostat from HACS.
16. Record every measurement in `docs/plans/2026-10-19-room-heating.evidence.md`: the feed
    scale, the valve targets, the boiler transitions, the cycle timings and the bench rows.

- [ ] **Step 4: Run the checks to verify they pass**

Run: `make check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md hacs.json dashboard docs scripts
git commit -m "package room climate for HACS and record the migration"
```

---

## Self-review

**Spec coverage:** section 5 (architecture) → tasks 1, 9, 10. Section 5.2 (configuration) →
tasks 2, 11. Section 6.1 → task 3. Section 6.2 → task 5. Section 6.3 → task 6. Section 6.4 →
task 7. Section 6.5 → task 8. Section 6.6 → task 4. Section 6.7 → tasks 9, 12. Section 7.1 to
7.3 → tasks 8, 10. Section 7.4 → task 13. Section 8.1 → tasks 13, 14. Section 8.2 → task 14.
Section 9 → every task.

**Review focus coverage:** item 1 → task 4. Item 2 → tasks 5, 8. Item 3 → task 9. Item 4 →
tasks 6, 8. Item 5 → tasks 8, 12. Item 6 → tasks 7, 9. Item 7 → tasks 2, 13.

**Type consistency:** `RuntimeRoom` is the name in `models.py`, the coordinator and the
climate entity. `valve_targets` returns `Mapping[str, float]` in the model and in the
function. `calibration_values` returns raw feed units in both. `feed_entity_id` is the one
place that derives the feed entity. The service name is `room_climate.recompute` in the
constants, the service file and the tests.

**Decisions:** the operator accepted every default of the spec's section 10 on 2026-10-19.
The tasks read those defaults from the spec. A change to a default is a change to the spec
first.
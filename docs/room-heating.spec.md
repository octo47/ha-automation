# Room Climate — design and requirements

Status: draft for review. Date: 2026-10-19.
Instance: Home Assistant 2026.10.1 at `http://192.168.1.7:8123`, HACS loaded.
Target repository: `github.com/octo47/ha-automation`.

## 1. Purpose

The house has Hive TRVs in several rooms and a Hive boiler thermostat. This document
specifies one Home Assistant custom integration, `room_climate`. The integration groups
TRVs and temperature sensors into rooms. It decides when a room calls for heat, and it
decides when the boiler runs.

## 2. Terms

| Term | Meaning |
| --- | --- |
| Room | A named group of actuators and temperature sources. |
| Actuator | A `climate.*` entity that accepts a target temperature. |
| Source | A temperature entity that the component reads. |
| Room temperature | The weighted mean of the usable sources of a room. |
| Calling | The room needs heat now, because its temperature is below the setpoint. |
| Setpoint | The target temperature of a room. The Scheduler sets this value. |
| Boiler | The Hive central-heating entity `climate.heating_heat`. |
| Boiler ON | The component sets the boiler target to 31 °C and the mode to `heat`. |
| Boiler OFF | The component sets the boiler target to 15 °C and the mode to `off`. |
| Lockout | Heating is blocked because the outdoor temperature is high. |
| Cycle | One evaluation of every room and of the boiler. |
| Dry run | The component computes commands and logs them, and sends no service call. |

Boiler ON and Boiler OFF are the only two boiler commands in this document. The Hive
thermostat has no separate "call for heat" input.

## 3. Measured environment

The component must work with the installation that exists now. A probe on 2026-10-19
measured the following facts.

### 3.1 Heating chain

- The boiler is the Hive dual-channel thermostat, model `Dual channel heating and hot
  water thermostat`, over Zigbee2MQTT.
- `climate.heating_heat` is the central heating entity. Its range is 5 °C to 32 °C. Its
  step is 0.5 °C.
- `climate.heating_water` is the hot water entity. This component must not control it.
- A Hive TRV turns the boiler on only through a high setpoint. The measured workaround
  sets the target to 31 °C. The reverse sets the target to 15 °C.
- Ten TRVs are present, all `Hive Radiator valve` over Zigbee2MQTT.
- Each TRV publishes `binary_sensor.<trv>_heat_required`. The group entity
  `binary_sensor.some_trv_wants_heat` is the OR of those signals.
- The existing automation `automation.heat_on_demand` (id `1731526329445`) reads that
  group entity and sets the boiler to 31 °C or to 18 °C. This component replaces it.

### 3.2 Rooms measured today

| Area | Actuators | Temperature sources | Better Thermostat |
| --- | --- | --- | --- |
| Study | `climate.trv_study` | `sensor.trv_study_local_temperature`, `sensor.0x8899aabbccddeeff_temperature` | `climate.bt_study_bt_study` |
| Bedroom | `climate.trv_bedroom` | `sensor.sensor_bedroom_temperature`, `sensor.trv_bedroom_local_temperature` | `climate.bt_bedroom_bt_bedroom` |
| Guest Room | `climate.trv_guest_wall`, `climate.trv_guest_window` | `sensor.sensor_guest_room_temperature` | `climate.bt_guest_room_bt_guest_room` |
| Front Room | `climate.trv_front_room` | `sensor.trv_front_room_local_temperature` | none |
| Back Room | not measured | `sensor.sensor_back_room_temperature` | `climate.bt_back_room` |
| Side Room | `climate.trv_side_room_a`, `climate.trv_side_room_b` | `sensor.sensor_side_room_temperature` | `climate.bt_side_room_bt_side_room` |
| Dining Room | `climate.trv_dining_room` | `sensor.sensor_dining_room_temperature` | `climate.bt_dining_room` |
| Office | `climate.trv_office` | `sensor.sensor_office_temperature`, `sensor.trv_office_local_temperature` | `climate.bt_office` |
| Store Room | `climate.0x0011223344556677` | `sensor.sensor_store_room_room_temperature` | `climate.bt_store_room_room` |

No actuator is measured for the Back Room room. The probe cannot tell which TRV the
Back Room Better Thermostat entry drives. The operator must confirm the Back Room actuator
in the config flow.

`climate.trv_dining_room` and `climate.bt_dining_room` are `unavailable` today.

Rooms without an actuator: Sun Room, Workshop, Family Bathroom, Plant Room,
Wet Room. Each has a temperature sensor. They can become rooms later.

### 3.3 Outdoor temperature sources

- `sensor.outdoor_temperature` (Ecowitt, 10.7 °C at probe time).
- `weather.forecast_home` (met.no, 10.6 °C at probe time). `weather.forecast_home`
  is `unavailable`.

### 3.4 Installed software

- Better Thermostat 1.9.3, eight entries, over HACS.
- HACS is installed and loaded.
- Only port 8123 answers on the HA host. No SSH add-on and no Samba add-on exist. The
  REST and websocket APIs are the only agent paths into the instance.

## 4. Scope

### 4.1 In scope

1. Group one or more actuators and one or more temperature sources into a room.
2. Weight each source. A weight of zero excludes the source.
3. Compute the room temperature as the weighted mean of the usable sources.
4. Publish one virtual thermostat entity per room for the dashboard and for the
   Scheduler.
5. Decide room demand from the setpoint and a hysteresis band.
6. Decide boiler demand from all rooms.
7. Block heating when the outdoor temperature is at or above a threshold, with
   hysteresis.
8. Hold a minimum on time and a minimum off time for the boiler.
9. Provide a dry-run mode, a master enable switch, and a lockout override switch.
10. Provide diagnostic entities for the dashboard.

### 4.2 Out of scope

1. Hot water control. The Hive schedule keeps that duty.
2. Weather compensation of the flow temperature. The boiler is on or off.
3. Cooling.
4. Window detection and valve calibration. Better Thermostat keeps those duties.
5. Per-room weekly schedules inside this component. The Scheduler integration sets the
   setpoints.
6. Control of the trapezoid of the boiler. The Hive thermostat owns that.

## 5. Architecture

```
 custom_components/room_climate/
   control.py       pure decision logic, no Home Assistant import
   models.py        pydantic models for configuration and for cycle input and output
   coordinator.py   reads entity states, calls control.py, applies the commands
   climate.py       one virtual thermostat entity per room
   switch.py        master enable, lockout override
   binary_sensor.py boiler demand, lockout, room calling
   sensor.py        room temperature, outdoor temperature, house status
   config_flow.py   house entry, room add, room edit, room remove
   __init__.py      setup, unload, boiler release
```

The decision logic has one home: `control.py`. The coordinator imports it. The tests
import it. No second copy of the logic may exist in an automation or in a template.

`control.py` must not import `homeassistant`. A test enforces that rule.

### 5.1 Data flow of one cycle

1. The coordinator collects the states of every configured entity.
2. The coordinator builds a `ControlInput` model.
3. `control.decide()` returns a `ControlOutput` model.
4. In dry-run mode the coordinator logs the output and stops.
5. Otherwise the coordinator calls `climate.set_temperature` for each changed actuator
   and for the boiler.
6. The coordinator stores the output for the entities to publish.

### 5.2 Configuration model

One config entry holds the house. The entry options hold the room list. The options
flow adds, edits and removes rooms.

Reason for one entry, and not one entry per room: the component must reject one actuator
or one source that two rooms claim. That check needs every room at once.

| Field | Type | Default | Rule |
| --- | --- | --- | --- |
| `name` | string | `Room Climate` | Entry title. |
| `boiler_entity` | entity id, climate domain | `climate.heating_heat` | Required. |
| `boiler_on_temperature` | float | `31.0` | 5 to 32. |
| `boiler_off_temperature` | float | `15.0` | 5 to 32. |
| `boiler_on_hvac_mode` | select | `heat` | `heat` or `auto`. |
| `boiler_off_hvac_mode` | select | `off` | `off` or `auto`. |
| `outdoor_sources` | list of entity ids | `["sensor.outdoor_temperature"]` | One or more. |
| `lockout_temperature` | float | `18.0` | −20 to 40. |
| `lockout_hysteresis` | float | `1.0` | 0 to 10. |
| `min_on_seconds` | int | `300` | 0 to 3600. |
| `min_off_seconds` | int | `300` | 0 to 3600. |
| `update_interval_seconds` | int | `30` | 10 to 600. |
| `dry_run` | bool | `true` | Safety default. |
| `master_enabled` | bool | `true` | Starting state of the master switch. |
| `rooms` | list of `RoomConfig` | `[]` | See the next table. |

| Room field | Type | Default | Rule |
| --- | --- | --- | --- |
| `room_id` | slug | from the name | Unique in the entry. |
| `name` | string | — | Required. |
| `area_id` | area id | none | Optional. The component assigns the device to this area. |
| `actuators` | list of entity ids, climate domain | — | One or more. |
| `sources` | list of `{entity_id, weight}` | — | One or more. |
| `weight` | float | `1.0` | Greater than 0. |
| `min_temp` | float | `5.0` | 5 to 20. |
| `max_temp` | float | `30.0` | 15 to 35. |
| `hysteresis` | float | `0.2` | 0 to 2. |
| `off_setpoint` | float | `5.0` | 5 to 12. |
| `setpoint_offset` | float | `0.0` | −3 to 3. Added to an actuator target. |

Two rooms must not share an actuator. Two rooms must not share a source. The options
flow rejects both cases and names the two rooms.

## 6. Control rules

### 6.1 Room temperature

1. Read every source of the room.
2. Discard a source with the state `unavailable`, `unknown` or `None`.
3. Discard a source whose value is outside −20 °C to 60 °C.
4. Compute the weighted mean of the remaining values.
5. If no value remains, the room temperature is `None`.

An untyped average is not acceptable. The mean is `sum(weight × value) / sum(weight)`
over the usable sources only.

### 6.2 Room demand

1. The room cannot call for heat when its mode is `off`.
2. The room cannot call for heat when its setpoint is at or below `min_temp`.
3. The room cannot call for heat when its room temperature is `None`.
4. The room starts to call when `room_temperature < setpoint − hysteresis`.
5. The room stops calling when `room_temperature > setpoint + hysteresis`.
6. Between those two values the room keeps its previous state.

Rule 6 makes the demand a latch. The latch stops valve chatter.

### 6.3 Actuator command

1. When the room calls, the component sets each actuator to `setpoint + setpoint_offset`.
2. When the room does not call, the component sets each actuator to `off_setpoint`.
3. The component rounds every sent value to 0.5 °C.
4. The component clamps every sent value to the `min_temp` and `max_temp` of the room.
5. The component repeats a command only when the wanted value differs from the last
   sent value. The step size of the actuator is 0.5 °C, so a difference below 0.25 °C
   is no change.
6. An actuator in the state `unavailable` does not stop the cycle. The component skips
   it, keeps the other actuators of the room, and reports the room as degraded.

### 6.4 Boiler demand

The component computes a wanted boiler state in this order.

1. Master switch off → Boiler OFF. Reason `master_off`.
2. Lockout active and override off → Boiler OFF. Reason `lockout`.
3. At least one room calls → Boiler ON. Reason `room_demand`.
4. No room calls → Boiler OFF. Reason `no_demand`.

Timing rules (1 minute more precisely):

1. A change from Boiler OFF to Boiler ON requires `now − last_off ≥ min_off_seconds`.
   This rule holds when no Boiler OFF has happened yet.
2. A change from Boiler ON to Boiler OFF requires `now − last_on ≥ min_on_seconds`.
   The reasons `lockout` and `master_off` are exempt: those two stop the boiler at
   once.
3. The component sends no boiler command when the wanted state equals the last sent
   state.

### 6.5 Outdoor lockout

1. The component reads every configured outdoor source and takes the mean of the
   usable values.
2. The lockout starts when the mean is at or above `lockout_temperature`.
3. The lockout stops when the mean is at or below
   `lockout_temperature − lockout_hysteresis`.
4. An unavailable outdoor reading must not start a lockout. Rule 4 keeps the house
   warm when the weather station fails.
5. The lockout overrides room demand. Rooms may still call, and the TRVs go to
   `off_setpoint` while the lockout holds.

### 6.6 Safety and failure behaviour

1. `dry_run` is `true` at installation. The operator turns it off after the review of
   the computed commands.
2. When the integration unloads or the entry is removed, the component sets the boiler
   to the OFF values. A removed component must not leave the boiler running.
3. When the component starts and the boiler state is unknown, the component treats
   `last_on` and `last_off` as unknown and allows the first command.
4. `boiler_on_temperature` must be greater than `boiler_off_temperature`.
5. The component must not command `climate.heating_water`.

## 7. Entities

### 7.1 Room device: `Room Climate <Room Name>`

One device per room, assigned to the configured area. The climate entity carries the
name of the device.

| Entity | Kind | Purpose |
| --- | --- | --- |
| `climate.room_climate_<room>` | climate | Virtual thermostat. Setpoint in, weighted temperature out. |

Climate entity rules:

1. `hvac_modes` are `off` and `heat`.
2. `current_temperature` is the weighted room temperature.
3. `target_temperature` is the setpoint. The entity restores it after a restart.
4. `hvac_action` is `heating` when the room calls, `idle` otherwise.
5. The entity accepts `set_temperature` and `set_hvac_mode`.
6. Attributes: the usable source count, the weighted temperature, the calling state,
   the actuator list, and the degradation flag.

### 7.2 House device: the config entry

| Entity | Kind | Default state | Purpose |
| --- | --- | --- | --- |
| `switch.room_climate_boiler_automation` | switch | on | Master enable. |
| `switch.room_climate_lockout_override` | switch | off | Bypass of the outdoor lockout. |
| `binary_sensor.room_climate_boiler_demand` | binary sensor | off | Wanted boiler state. |
| `binary_sensor.room_climate_outdoor_lockout` | binary sensor | off | Lockout state. |
| `sensor.room_climate_outdoor_temperature` | sensor, temperature | — | Mean outdoor temperature. |
| `sensor.room_climate_status` | sensor, enum | `idle` | One of `heating`, `idle`, `locked_out`, `disabled`, `dry_run`, `degraded`. |

Per room, in addition to the climate entity:

| Entity | Kind | Purpose |
| --- | --- | --- |
| `sensor.room_climate_<room>_temperature` | sensor, diagnostic | Weighted temperature for a graph. |
| `binary_sensor.room_climate_<room>_calling` | binary sensor | Demand latch for a graph. |

### 7.3 Dashboard

The virtual thermostats carry the area of their room. The area dashboard of Home
Assistant shows them without extra work.

The plan adds a `dashboard/room-climate.yaml` example with one thermostat card per
room, one house panel, and one history graph per room.

The Scheduler integration sets the setpoint of a virtual thermostat through
`climate.set_temperature`. No separate bridge entity is necessary.

## 8. Installation

The repository is a HACS custom repository. The integration directory sits at the
repository root, under `custom_components/room_climate`.

Procedure:

1. Open HACS, then Integrations.
2. Add `octo47/ha-automation` as a custom repository of category Integration.
3. Download Room Climate. Restart Home Assistant.
4. Add the integration from Settings, then Devices and Services.
5. Fill the house form. Add one room per radiator group.
6. Review the computed commands in the log for one hour with `dry_run` on.
7. Turn `dry_run` off.
8. Disable `automation.heat_on_demand`.

Step 8 is mandatory. Two controllers must not command `climate.heating_heat`.

## 9. Requirements of the development process

1. Every command runs through `uv run` or through a `make` target.
2. `ruff check` and `ruff format --check` must pass.
3. `pyright` must pass with the strict profile on `custom_components/room_climate`.
4. Every boundary carries a pydantic model. No open dictionary crosses a function
   boundary of this component.
5. The tests must run in seconds. No test may sleep or wait for the clock.
6. A test must prove each rule of section 6. A test that only proves a function exists
   is not acceptable.
7. The regression on the live instance is manual. The tests must not call the live
   instance.

## 10. Decisions owed to the operator

The following decisions took a default. The operator can change each default before
implementation starts.

| Decision | Default taken | Alternative |
| --- | --- | --- |
| Relation to Better Thermostat | The room actuator is the `climate.bt_*` entity where one exists, and the raw TRV elsewhere. Better Thermostat keeps valve calibration. | Remove Better Thermostat and drive the raw TRVs. |
| Source of the setpoint | The Scheduler integration sets the setpoint. This component holds no schedule. | A weekly schedule inside the component. |
| Installation route | HACS custom repository. | A file copy with an SSH or file-editor add-on. |
| Lockout effect | Whole house. The boiler stops, and every TRV goes to `off_setpoint`. | Lockout blocks the boiler only, and the TRVs keep their target. |

## 11. Acceptance criteria

1. The integration installs over HACS on Home Assistant 2026.10.1 and loads with no
   error in the log.
2. One room with two TRVs and three sources reports the weighted mean of the usable
   sources as `current_temperature`.
3. A room call for heat sets every actuator of that room and sets the boiler to
   31 °C with mode `heat`.
4. The last room that stops calling sets the boiler to 15 °C with mode `off`.
5. An outdoor temperature at or above the threshold stops the boiler. The lockout
   clears only after the outdoor temperature falls by the hysteresis value.
6. The boiler does not switch on again within `min_off_seconds`, and does not switch
   off within `min_on_seconds`, except for the reasons `lockout` and `master_off`.
7. Two rooms that claim one actuator fail the options flow with a message that names
   both rooms.
8. An unavailable source is ignored. An unavailable actuator is skipped and the room
   reports degraded.
9. `dry_run` sends no service call. The log shows the command that the component would
   send.
10. Removing the entry sets the boiler to the OFF values.
11. The virtual thermostat appears as a device in the area of its room, and a
    thermostat card renders it.
12. `make test`, `make lint` and `make typecheck` pass from a clean checkout.

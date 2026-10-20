# Room Climate — design and requirements

Status: draft for review. Date: 2026-10-19. Revision 2: Better Thermostat is removed.
Instance: Home Assistant 2026.10.1 at `http://192.168.1.7:8123`, HACS loaded.
Target repository: `github.com/octo47/ha-automation`.

## 1. Purpose

The house has Hive TRVs in several rooms and a Hive boiler thermostat. This document
specifies one Home Assistant custom integration, `room_climate`. The integration groups
TRVs and temperature sensors into rooms. It decides when a room calls for heat. It drives
the valves of the room. It decides when the boiler runs.

Better Thermostat leaves the installation. The integration must not depend on it, on any
other custom integration, or on any HACS repository.

## 2. Terms

| Term | Meaning |
| --- | --- |
| Room | A named group of valves and temperature sources. |
| Valve | A `climate.*` TRV entity that accepts a target temperature. |
| Source | A temperature entity that the component reads. |
| Room temperature | The weighted mean of the usable sources of a room. |
| Calling | The room needs heat now, because its temperature is below the setpoint. |
| Setpoint | The target temperature of a room. The Scheduler sets this value. |
| Valve target | The target temperature the component writes to a valve. |
| Calibration | The write of the room temperature into a valve, so the valve regulates on the room temperature. |
| Feed entity | The `number.*_external_measured_room_sensor` entity of one valve. |
| Boiler | The Hive central-heating entity `climate.heating_heat`. |
| Boiler ON | The component sets the boiler target to 31 °C and the mode to `heat`. |
| Boiler OFF | The component sets the boiler target to 15 °C and the mode to `off`. |
| Lockout | Heating is blocked because the outdoor temperature is high. |
| Cycle | One evaluation of every room and of the boiler. |
| Dry run | The component computes commands and logs them, and sends no service call. |

Boiler ON and Boiler OFF are the only two boiler commands in this document. The Hive
thermostat has no separate call-for-heat input.

## 3. Measured environment

A probe on 2026-10-19 measured the facts below. The component must work with this
installation.

### 3.1 Heating chain

- The boiler is the Hive dual-channel thermostat, model `Dual channel heating and hot
  water thermostat`, over Zigbee2MQTT.
- `climate.heating_heat` is the central heating entity. Its range is 5 °C to 32 °C. Its
  step is 0.5 °C.
- `climate.heating_water` is the hot water entity. This component must not control it.
- A Hive boiler turns on only through a high setpoint. The measured workaround sets the
  target to 31 °C. The reverse sets the target to 15 °C.
- Ten Hive TRVs are present, all `Hive Radiator valve` over Zigbee2MQTT.
- Each TRV publishes `binary_sensor.<trv>_heat_required`. The group entity
  `binary_sensor.some_trv_wants_heat` is the OR of those signals.
- The automation `automation.heat_on_demand` (id `1731526329445`) reads that group entity
  and sets the boiler to 31 °C or to 18 °C. This component replaces it.

### 3.2 The valve interface

One Hive TRV publishes the entities below over Zigbee2MQTT. The component uses the marked
entries.

| Entity | State at probe | Use |
| --- | --- | --- |
| `climate.trv_front_room` | `heat` | The valve target. |
| `sensor.trv_front_room_local_temperature` | 22.12 °C | A room source, or a fallback source. |
| `binary_sensor.trv_front_room_heat_required` | `on` | Optional demand fallback. |
| `sensor.trv_front_room_pi_heating_demand` | 100 % | The valve position the valve itself computes. |
| `sensor.trv_front_room_window_open_internal` | `closed` | Optional window signal. |
| `number.trv_front_room_external_measured_room_sensor` | −8000.0 | The calibration feed. |
| `number.trv_front_room_regulation_setpoint_offset` | unknown | A per-valve nudge, −2.5 to 2.5. |
| `number.trv_front_room_occupied_heating_setpoint_scheduled` | 23.5 | The setpoint slot of the valve. |
| `number.trv_front_room_max_heat_setpoint_limit` | unknown | The ceiling of the valve, 5 to 35. |
| `switch.trv_front_room_heat_available` | `on` | A per-valve heat disable. |
| `switch.trv_front_room_window_open_feature` | `off` | The internal window detection of the valve. |
| `sensor.trv_front_room_battery` | 74 % | Operator information. |

The feed entity has a range of −8000 to 3500, and a step of 1. The value −8000 means
"no external measurement". If the scale is 0.01 °C, the value 2150 means 21.5 °C, and the
usable band is 500 to 3500, which is 5 °C to 35 °C. **The scale is not confirmed.** Steps
6 and 7 of section 8 confirm it on one valve before the component writes to ten valves.

Every Hive TRV exposes the feed entity. The probe read nine of them in the state −8000,
which means "no external measurement". The tenth is `unavailable` with its valve. The
`regulation_setpoint_offset` entity is present as well, and this component does not use
it.

The valve regulates its own position. It publishes the result as
`sensor.<trv>_pi_heating_demand`. The component does not compute a valve position.

Some valves carry `switch.<trv>_heat_available` in the state `off`, which means the valve
refuses heat. The component must not write that switch.

### 3.3 Rooms measured today

| Area | Valves | Temperature sources |
| --- | --- | --- |
| Study | `climate.trv_study` | `sensor.study_sensor_temperature`, `sensor.trv_study_local_temperature` |
| Bedroom | `climate.trv_bedroom` | `sensor.sensor_bedroom_temperature`, `sensor.trv_bedroom_local_temperature` |
| Guest Room | `climate.trv_guest_wall`, `climate.trv_guest_window` | `sensor.sensor_guest_room_temperature`, `sensor.trv_guest_wall_local_temperature`, `sensor.trv_guest_window_local_temperature` |
| Front Room | `climate.trv_front_room` | `sensor.trv_front_room_local_temperature` |
| Back Room | no valve measured | `sensor.sensor_back_room_temperature` |
| Side Room | `climate.trv_side_room_a`, `climate.trv_side_room_b` | `sensor.sensor_side_room_temperature`, `sensor.trv_side_room_a_local_temperature`, `sensor.trv_side_room_b_local_temperature` |
| Office | `climate.trv_office` | `sensor.sensor_office_temperature`, `sensor.trv_office_local_temperature` |
| Store Room | `climate.0x0011223344556677` | `sensor.sensor_store_room_room_temperature`, `sensor.0x0011223344556677_local_temperature` |

The probe cannot measure which valve the Back Room Better Thermostat entry drives. The
Back Room row has no valve. The operator must confirm the Back Room valve. The runbook of
section 8 removes Better Thermostat, so a room without a valve heats nothing.

`climate.trv_dining_room` is `unavailable` today. The Dining Room has one valve and the
source `sensor.sensor_dining_room_temperature`.

Rooms without a valve: Sun Room, Workshop, Family Bathroom, Plant Room, Wet Room.
Each has a temperature sensor.

### 3.4 Outdoor temperature sources

- `sensor.outdoor_temperature` (Ecowitt, 10.7 °C at probe time).
- `weather.forecast_home` (met.no, 10.6 °C at probe time). `weather.forecast_home` is
  `unavailable`.

### 3.5 Software and dependencies

- Better Thermostat 1.9.3 holds eight entries, over HACS.
- HACS is installed and loaded.
- Only port 8123 and port 4357 answer on the HA host. No SSH add-on and no Samba add-on
  exist. The HACS user interface is the only install route.
- The integration must not depend on Better Thermostat or on any other custom
  integration.
- The integration declares one runtime requirement, `pydantic>=2.11,<3`. Home Assistant
  installs a declared requirement at setup. `pydantic` is the house model for every
  boundary, so the integration keeps it. Section 10 lists the alternative.

### 3.6 The removal of Better Thermostat

Better Thermostat is licensed AGPL-3.0. This repository must not copy its code, because
AGPL-3.0 would then govern this repository. The component reimplements the room logic. It
takes no code from Better Thermostat. Section 10 lists the alternative.

The removal has this measured blast radius.

- 122 entities carry the platform `better_thermostat`. They disappear: 8 climate entities,
  56 number entities, 48 sensor entities and 10 switch entities.
- The one storage dashboard, `lovelace`, references eight `climate.bt_*` entities and no
  other Better Thermostat entity.
- No automation and no script references a Better Thermostat entity. The 13 automations
  are clean.

The history of the Better Thermostat sensors has no replacement. The component adds one
graph sensor per room, and section 7.3 states it.

## 4. Scope

### 4.1 In scope

1. Group one or more valves and one or more temperature sources into a room.
2. Weight each source. A weight of zero excludes the source.
3. Compute the room temperature as the weighted mean of the usable sources.
4. Publish one virtual thermostat entity per room for the dashboard and for the Scheduler.
5. Decide room demand from the setpoint and a hysteresis band.
6. Drive the valves of a room, and rate limit the commands to a valve.
7. Feed the room temperature into a valve, so the valve regulates on the room temperature.
8. Decide boiler demand from all rooms.
9. Block heating when the outdoor temperature is at or above a threshold, with
   hysteresis.
10. Hold a minimum on time and a minimum off time for the boiler.
11. Stop a room when one of its window sensors reports an open window.
12. Provide a dry-run mode, a master enable switch and a lockout override switch.
13. Provide diagnostic entities for the dashboard, including one graph temperature per
    room and one for the house.

### 4.2 Out of scope

1. Hot water control. The Hive schedule keeps that duty.
2. Weather compensation of the flow temperature. The boiler is on or off.
3. Cooling.
4. A computed valve position. The valve regulates its own position.
5. Valve families other than the Hive TRV.
6. Per-room weekly schedules inside this component. The Scheduler integration sets the
   setpoints.
7. The presets Activity, Away, Boost, Comfort, Eco, Home and Sleep of Better Thermostat.
   The component has one setpoint per room.

## 5. Architecture

```
 custom_components/room_climate/
   control.py       pure decision logic, no Home Assistant import
   models.py        pydantic models for configuration and for cycle input and output
   coordinator.py   reads entity states, calls control.py, applies the commands
   climate.py       one virtual thermostat entity per room
   switch.py        master enable, lockout override
   binary_sensor.py boiler demand, lockout, room calling
   sensor.py        room temperature, room EMA, outdoor temperature, house status
   config_flow.py   house entry, room add, room edit, room remove
   __init__.py      setup, unload, boiler release
```

The decision logic has one home: `control.py`. The coordinator imports it and the tests
import it. No second copy of the logic may exist in an automation or in a template.

`control.py` must not import `homeassistant`. A test enforces that rule.

### 5.1 Data flow of one cycle

1. The coordinator collects the states of every configured entity.
2. The coordinator builds a `ControlInput` model.
3. `control.decide()` returns a `ControlOutput` model.
4. In dry-run mode the coordinator logs the output and stops.
5. Otherwise the coordinator calls `climate.set_temperature` for each valve target, calls
   `number.set_value` for each calibration value, and calls `climate.set_temperature` for
   the boiler.
6. The coordinator stores the output for the entities to publish.

### 5.2 Configuration model

One config entry holds the house. The entry options hold the room list. The options flow
adds, edits and removes rooms.

Reason for one entry, and not one entry per room: the component must reject one valve or
one source that two rooms claim. That check needs every room at once.

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
| `ema_seconds` | int | `1800` | 60 to 10800. The time constant of the graph temperatures. |
| `rooms` | list of `RoomConfig` | `[]` | See the next table. |

| Room field | Type | Default | Rule |
| --- | --- | --- | --- |
| `room_id` | slug | from the name | Unique in the entry. |
| `name` | string | — | Required. |
| `area_id` | area id | none | Optional. The component assigns the device to this area. |
| `valves` | list of entity ids, climate domain | — | One or more. |
| `sources` | list of `{entity_id, weight}` | — | One or more. |
| `weight` | float | `1.0` | Greater than 0. |
| `calibration` | select | `external_sensor` | `external_sensor` or `none`. |
| `calibration_scale` | int | `100` | 1 to 1000. Feed units for 1 °C. |
| `valve_mode` | select | `modulate` | `modulate` or `boost`. |
| `open_offset` | float | `2.0` | 0 to 5. Only `boost` uses it. |
| `min_temp` | float | `5.0` | 5 to 20. |
| `max_temp` | float | `30.0` | 15 to 35. |
| `hysteresis` | float | `0.2` | 0 to 2. |
| `off_setpoint` | float | `5.0` | 5 to 12. |
| `valve_command_interval_seconds` | int | `300` | 0 to 3600. |
| `window_sensors` | list of entity ids | `[]` | Optional. |
| `demand_fallback_sensors` | list of entity ids | `[]` | Optional. The `heat_required` entities. |

Rules:

1. Two rooms must not share a valve. Two rooms must not share a source.
2. The `valve_mode` of `modulate` requires the `calibration` of `external_sensor`.
   Reason: the valve and the component must agree on the room temperature.
3. A room with an empty `valves` list is invalid.
4. The options flow rejects a broken rule and names the two rooms.

## 6. Control rules

### 6.1 Room temperature

1. Read every source of the room.
2. Discard a source with the state `unavailable`, `unknown` or `None`.
3. Discard a source whose value is outside −20 °C to 60 °C.
4. Compute the weighted mean of the remaining values.
5. If no value remains, the room temperature is `None`.

The mean is `sum(weight × value) / sum(weight)` over the usable sources only.

### 6.2 Room demand

1. The room cannot call for heat when its mode is `off`.
2. The room cannot call for heat when its setpoint is at or below `min_temp`.
3. The room cannot call for heat when its room temperature is `None`, unless rule 8
   applies.
4. The room starts to call when `room_temperature < setpoint − hysteresis`.
5. The room stops calling when `room_temperature > setpoint + hysteresis`.
6. Between those two values the room keeps its previous state.
7. A window sensor of the room in the state `open` suspends the demand at once.
8. When the room temperature is `None`, the room calls when at least one
   `demand_fallback_sensors` entity is in the state `on`. This rule keeps a room warm
   after a source fails. Rule 8 is the only case that calls without a temperature. The
   room reports `degraded` in this state.

### 6.3 Valve command

1. In `modulate` mode, the valve target is the setpoint, clamped to `min_temp` and
   `max_temp`, and rounded to 0.5 °C.
2. In `boost` mode, the valve target is `setpoint + open_offset` while the room calls, and
   `off_setpoint` while the room does not call. The value is clamped and rounded as in
   rule 1.
3. A valve of a room with the mode `off` receives `off_setpoint`.
4. The lockout, the master switch off, and a window sensor in the state `open` put the
   valves of the room at `off_setpoint`.
5. The component sends a command only when the wanted target differs from the last sent
   target by 0.25 °C or more.
6. The component defers a due command while
   `now − last_command_at < valve_command_interval_seconds`. A command to `off_setpoint`
   is never deferred. The valve motor and the Zigbee network both need the limit.
7. A valve in the state `unavailable` does not stop the cycle. The component skips it,
   keeps the other valves of the room, and reports the room as degraded.
8. The component sits outside the internal control loop of the valve. The valve owns its
   position.

### 6.4 Calibration

1. With the `calibration` of `external_sensor`, the component writes the room temperature
   into the feed entity of every valve of the room. The written value is
   `round(room_temperature × calibration_scale)`.
2. With the `calibration` of `none`, the component writes nothing.
3. The component skips rule 1 when the room temperature is `None`.
4. The component skips rule 1 when the feed entity is missing.
5. The component writes a value only when it differs from the last written value by 0.1 °C
   or more. A valve needs one write per change, not one write per cycle.
6. The valid band of the feed value is 500 to 3500 at the default scale. A value outside
   the band clamps to the band. Section 3.2 records why the band needs confirmation.

### 6.5 Boiler demand

The component computes a wanted boiler state in this order.

1. Master switch off → Boiler OFF. Reason `master_off`.
2. Lockout active and override off → Boiler OFF. Reason `lockout`.
3. At least one room calls → Boiler ON. Reason `room_demand`.
4. No room calls → Boiler OFF. Reason `no_demand`.

Timing rules:

1. A change from Boiler OFF to Boiler ON requires `now − last_off ≥ min_off_seconds`.
   Rule 1 holds when no Boiler OFF has happened yet.
2. A change from Boiler ON to Boiler OFF requires `now − last_on ≥ min_on_seconds`. The
   reasons `lockout` and `master_off` are exempt: those two stop the boiler at once.
3. The component sends no boiler command when the wanted state equals the last sent
   state.

### 6.6 Outdoor lockout

1. The component reads every configured outdoor source, and it takes the mean of the
   usable values.
2. The lockout starts when the mean is at or above `lockout_temperature`.
3. The lockout stops when the mean is at or below `lockout_temperature −
   lockout_hysteresis`.
4. An unavailable outdoor reading must not start a lockout. Rule 4 keeps the house warm
   when the weather station fails.
5. The lockout overrides room demand. The valves go to `off_setpoint` while the lockout
   holds.

### 6.7 Safety and failure behaviour

1. `dry_run` is `true` at installation. The operator turns it off after the review of the
   computed commands.
2. When the integration unloads or the entry is removed, the component sets the boiler to
   the OFF values. A removed component must not leave the boiler running.
3. When the component starts and the boiler state is unknown, the component treats
   `last_on` and `last_off` as unknown, and it allows the first command.
4. `boiler_on_temperature` must be greater than `boiler_off_temperature`.
5. The component must not command `climate.heating_water`, `switch.<trv>_heat_available`
   or `select.<trv>_programming_operation_mode`.
6. Every valve command and every calibration write carries the entity id of the target. A
   write to a wrong valve is a heating fault, so a test must prove the target of every
   command.

## 7. Entities

### 7.1 Room device: `Room Climate <Room Name>`

One device per room, assigned to the configured area.

| Entity | Kind | Purpose |
| --- | --- | --- |
| `climate.room_climate_<room>` | climate | Virtual thermostat. Setpoint in, weighted temperature out. |

Climate entity rules:

1. `hvac_modes` are `off` and `heat`.
2. `current_temperature` is the weighted room temperature.
3. `target_temperature` is the setpoint. The entity restores it after a restart.
4. `hvac_action` is `heating` when the room calls, and `idle` otherwise.
5. The entity accepts `set_temperature` and `set_hvac_mode`.
6. Attributes: the usable source count, the room temperature, the calling state, the
   valve list, the valve targets, the calibration value, and the degradation flag.

### 7.2 House device: the config entry

| Entity | Kind | Default state | Purpose |
| --- | --- | --- | --- |
| `switch.room_climate_boiler_automation` | switch | on | Master enable. |
| `switch.room_climate_lockout_override` | switch | off | Bypass of the outdoor lockout. |
| `binary_sensor.room_climate_boiler_demand` | binary sensor | off | Wanted boiler state. |
| `binary_sensor.room_climate_outdoor_lockout` | binary sensor | off | Lockout state. |
| `sensor.room_climate_outdoor_temperature` | sensor, temperature | — | Mean outdoor temperature. |
| `sensor.room_climate_house_temperature` | sensor, temperature | — | EMA of the mean of the room temperatures. |
| `sensor.room_climate_status` | sensor, enum | `idle` | One of `heating`, `idle`, `locked_out`, `disabled`, `dry_run`, `degraded`. |

Per room, in addition to the climate entity:

| Entity | Kind | Purpose |
| --- | --- | --- |
| `sensor.room_climate_<room>_temperature` | sensor, temperature | The room temperature for a graph. |
| `sensor.room_climate_<room>_temperature_ema` | sensor, temperature | The smoothed room temperature for a graph. |
| `binary_sensor.room_climate_<room>_calling` | binary sensor | The demand latch for a graph. |

### 7.3 Dashboard

The virtual thermostats carry the area of their room. The area dashboard shows them
without extra work.

The removal of Better Thermostat breaks eight dashboard cards. Those eight cards point at
`climate.bt_*` today. They must point at `climate.room_climate_*` after the removal. The
runbook of section 8 holds the step.

Better Thermostat also provides the graph sensors `..._temperature_ema` and
`..._temperature_ema_1h` per room. The component replaces the first one, per room. The
history of the old sensors does not transfer.

The Scheduler integration sets the setpoint of a virtual thermostat through
`climate.set_temperature`. No separate bridge entity is necessary.

## 8. Install, migrate and roll back

Procedure:

1. Open HACS, then Integrations.
2. Add `octo47/ha-automation` as a custom repository of category Integration.
3. Download Room Climate, then restart Home Assistant.
4. Add the integration from Settings, then Devices and Services.
5. Fill the house form. Add one room per radiator group, with the table of section 3.3.
6. Confirm the scale of the feed entity on one valve. Write the value 2150 into
   `number.<trv>_external_measured_room_sensor`. Read the value back. Watch
   `sensor.<trv>_pi_heating_demand` and the room temperature for one hour.
7. If the valve regulates on the value, keep the `calibration` of `external_sensor` and
   the `calibration_scale` of 100. If not, set the `calibration` to `none` and the
   `valve_mode` to `boost`.
8. Keep `dry_run` on. Watch the log and `sensor.room_climate_status` for one day.
9. Turn `dry_run` off.
10. Disable `automation.heat_on_demand`. Two controllers must not command
    `climate.heating_heat`.
11. Watch one full heating cycle per room.
12. Repoint the eight dashboard cards to the new climate entities.
13. Remove the eight Better Thermostat entries from Settings, then Devices and Services.
14. Remove Better Thermostat from HACS.

Roll back:

1. Stop Room Climate, then remove its entries. The component sets the boiler to the OFF
   values at the removal.
2. Add the Better Thermostat entries again.
3. Enable `automation.heat_on_demand`.

Step 12 must happen before step 13. The dashboard and the removed entities share no
history, so a card points at a missing entity between the two steps.

## 9. Requirements of the development process

1. Every command runs through `uv run` or through a `make` target.
2. `ruff check` and `ruff format --check` must pass.
3. `pyright` must pass with the strict profile on `custom_components/room_climate`.
4. Every boundary carries a pydantic model. No open dictionary crosses a function
   boundary of this component.
5. The tests must run in seconds. No test may sleep or wait for the clock.
6. A test must prove each rule of section 6. A test that only proves a function exists is
   not acceptable.
7. The live regression is manual. No test may call the live instance, and no test may
   write to a valve.
8. The repository must carry a licence file. The component takes no code from Better
   Thermostat, which is AGPL-3.0.

## 10. Decisions owed to the operator

| Decision | Default taken | Alternative |
| --- | --- | --- |
| Source of the setpoint | The Scheduler integration sets the setpoint. This component holds no schedule. | A weekly schedule inside the component. |
| Lockout effect | Whole house. The boiler stops, and every valve goes to `off_setpoint`. | The lockout blocks the boiler only, and the valves keep their target. |
| Valve mode | `modulate`, with the calibration feed. The valve regulates its own position on the measured room temperature. | `boost`. The valve opens fully while the room calls. |
| Runtime dependency | Keep `pydantic`, declared in the manifest. The repository rule requires a typed model at every boundary. | Replace the models with frozen dataclasses, and keep the integration free of third-party requirements. |
| Code from Better Thermostat | Reimplement the logic, and keep this repository outside AGPL-3.0. | Copy the calibration logic, and license this repository AGPL-3.0. |
| Repository licence | MIT. The component copies no AGPL-3.0 code, so the licence is free. | Another licence of the operator's choice. |

## 11. Acceptance criteria

1. The integration installs over HACS on Home Assistant 2026.10.1, and it loads with no
   error in the log. It has no dependency on another custom integration.
2. One room with two valves and three sources reports the weighted mean of the usable
   sources as `current_temperature`.
3. A room call for heat sets every valve of that room, and sets the boiler to 31 °C with
   mode `heat`.
4. The last room that stops calling sets the boiler to 15 °C with mode `off`.
5. An outdoor temperature at or above the threshold stops the boiler. The lockout clears
   only after the outdoor temperature falls by the hysteresis value.
6. The boiler does not switch on again within `min_off_seconds`, and it does not switch
   off within `min_on_seconds`, except for the reasons `lockout` and `master_off`.
7. Two rooms that claim one valve fail the options flow with a message that names both
   rooms.
8. An unavailable source is ignored. An unavailable valve is skipped, and the room reports
   degraded.
9. A window sensor in the state `open` suspends the demand, and the valve of the room
   moves to `off_setpoint`.
10. `dry_run` sends no service call. The log shows the command that the component would
    send.
11. Removing the entry sets the boiler to the OFF values.
12. The virtual thermostat appears as a device in the area of its room, and a thermostat
    card renders it.
13. `make test`, `make lint` and `make typecheck` pass from a clean checkout.

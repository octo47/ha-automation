# ha-automation

Home Assistant custom integrations for this house.

## Room Climate

Room Climate groups the Hive TRVs and the temperature sensors of this house into rooms. It
computes the weighted mean of the usable sources of a room, drives the valves of that room,
writes the room temperature into every valve as its external measurement, and decides the Hive
boiler state. It replaces Better Thermostat, carries a simulation house for a bench, and depends
on no other custom integration.

### Install

1. Open HACS, then **Integrations**, then the three-dot menu, then **Custom repositories**.
2. Add `octo47/ha-automation` with the category **Integration**.
3. Download **Room Climate** from HACS.
4. Restart Home Assistant.

The integration declares one requirement, `pydantic>=2.11,<3`. Home Assistant installs it at
setup.

### Procedure

The move has two phases. Phase A runs on the bench and touches no hardware. Phase B moves the
control to the real valves.

**Phase A, the bench.** Add the integration with `simulation` on and `dry_run` on, and set
`min_on_seconds` and `min_off_seconds` to 0, so every row of the runbook's knob table takes
effect at once. Add one room per row of the table below, by name only: a simulated entry derives
the virtual valve and the virtual source from the room id. Drive the bench from
`dashboard/room-climate-bench.yaml`, then tune the weights, the hysteresis, `open_offset` and
`lockout_hysteresis` until every row behaves as its expected column states.

**Phase B, the real house.** Add the real entry with `simulation` off and the real valve and
sensor ids of the table below. Confirm the feed scale on one valve first. Keep `dry_run` on for
one day, then turn it off and disable `automation.heat_on_demand`. Repoint the eight dashboard
cards below, then remove the eight Better Thermostat entries and Better Thermostat itself.

The step-by-step procedure, the knob table, the feed-scale confirmation, the rollback and the
fault list are in [docs/room-heating.runbook.md](docs/room-heating.runbook.md).

### Rooms

The measured rooms of the spec's section 3.3. Each row is one room of the integration and one
`climate.room_climate_<room>` thermostat, plus `sensor.room_climate_<room>_temperature` and
`sensor.room_climate_<room>_temperature_ema` for a graph.

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

The Dining Room is a ninth room: one valve `climate.trv_dining_room` and one source
`sensor.sensor_dining_room_temperature`. The valve is `unavailable` today.

A room needs at least one valve, so the Back Room room is added after the operator confirms which
valve the Back Room Better Thermostat entry drives.

`dashboard/room-climate.yaml` is a starting view with one thermostat card and one history graph
per row of the table.

### Dashboard cards

Better Thermostat leaves eight cards on the `lovelace` dashboard, and every one of them points
at a `climate.bt_*` entity. Repoint each card at the Room Climate thermostat of the same room
before the Better Thermostat entries are removed.

| Better Thermostat card | Room Climate card |
| --- | --- |
| `climate.bt_study_bt_study` | `climate.room_climate_study` |
| `climate.bt_bedroom_bt_bedroom` | `climate.room_climate_bedroom` |
| `climate.bt_guest_room_bt_guest_room` | `climate.room_climate_guest_room` |
| `climate.bt_back_room` | `climate.room_climate_back_room` |
| `climate.bt_side_room_bt_side_room` | `climate.room_climate_side_room` |
| `climate.bt_office` | `climate.room_climate_office` |
| `climate.bt_store_room_room` | `climate.room_climate_store_room` |
| `climate.bt_dining_room` | `climate.room_climate_dining_room` |

The Front Room has no Better Thermostat card, because Better Thermostat holds no Front Room entry; the Front Room
card is new. The Back Room and the Dining Room cards point at a room whose valve is unconfirmed
or `unavailable` today, so those two rooms heat nothing until the operator confirms the valve.
The runbook carries the step.

## Design records

1. [Room heating spec](docs/room-heating.spec.md) — requirements for the `room_climate`
   integration (rooms, weighted temperature, outdoor lockout, Hive boiler control).
2. [Room heating plan](docs/plans/2026-10-19-room-heating.md) — the task-by-task
   implementation plan for that spec.
3. [Room heating runbook](docs/room-heating.runbook.md) — the bench, the real house, the
   feed-scale confirmation, the rollback and the fault list.

## Links

1. https://better-thermostat.org

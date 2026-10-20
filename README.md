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

The example rooms of the spec's section 3.3. Each row is one room of the integration and one
`climate.room_climate_<room>` thermostat, plus `sensor.room_climate_<room>_temperature` and
`sensor.room_climate_<room>_temperature_ema` for a graph.

| Room | Valves | Temperature sources |
| --- | --- | --- |
| Front Room | `climate.trv_front_room` | `sensor.front_room_temperature`, `sensor.trv_front_room_local_temperature` |
| Back Room | `climate.trv_back_room` | `sensor.back_room_temperature` |
| Side Room | `climate.trv_side_room_a`, `climate.trv_side_room_b` | `sensor.side_room_temperature`, `sensor.trv_side_room_a_local_temperature`, `sensor.trv_side_room_b_local_temperature` |

These three rooms are an example. The operator's own room list, its entity ids and its sensor
ids are private, and the config flow holds the real ones. A room needs at least one valve, and
the operator confirms each valve in the config flow.

`dashboard/room-climate.yaml` is a starting view with one thermostat card and one history graph
per room.

### Dashboard cards

Better Thermostat leaves one card per Better Thermostat room on the dashboard, and each card
points at a `climate.bt_*` entity. Repoint each card at the Room Climate thermostat of the same
room before the Better Thermostat entries are removed.

| Better Thermostat card | Room Climate card |
| --- | --- |
| `climate.bt_front_room` | `climate.room_climate_front_room` |
| `climate.bt_back_room` | `climate.room_climate_back_room` |

A room can exist without a Better Thermostat entry, and then its card is new. A room whose
valve is unconfirmed or `unavailable` heats nothing until the operator confirms the valve. The
runbook carries the step.

## Design records

1. [Room heating spec](docs/room-heating.spec.md) — requirements for the `room_climate`
   integration (rooms, weighted temperature, outdoor lockout, Hive boiler control).
2. [Room heating plan](docs/plans/2026-10-19-room-heating.md) — the task-by-task
   implementation plan for that spec.
3. [Room heating runbook](docs/room-heating.runbook.md) — the bench, the real house, the
   feed-scale confirmation, the rollback and the fault list.

## Links

1. https://better-thermostat.org

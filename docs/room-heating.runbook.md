# Room Climate runbook

The operator procedure for moving the house from Better Thermostat to `room_climate`. Two
phases: phase A runs on the bench and touches no hardware, phase B moves the control to the real
valves. The requirements are in [room-heating.spec.md](room-heating.spec.md), section 8.

Instance: Home Assistant 2026.10.1 at `http://192.168.1.7:8123`, HACS loaded, no SSH add-on and
no Samba add-on. The HACS user interface is the only install route.

## Phase A — the bench

The bench runs the same coordinator, the same control rules and the same service calls as the
real house, against virtual valves and a virtual boiler. It touches no real valve and it cannot
start the real boiler, because a simulated entry names only `sim_` entities and the component
rejects any other entity at setup.

1. Open HACS, then **Integrations**.
2. Add `octo47/ha-automation` as a custom repository of category **Integration**.
3. Download **Room Climate**, then restart Home Assistant.
4. Add the integration. Turn `simulation` on. Keep `dry_run` on. Set `min_on_seconds` and
   `min_off_seconds` to 0.

   The bench holds `min_on_seconds` and `min_off_seconds` at 0, so every row of the table below
   takes effect at once. Row 2 of the table expects the boiler at 15 °C in the same cycle that
   the room stops calling, and the default of 300 seconds would defer that transition. Raise
   both values later to watch the guard rules of the spec's section 6.5.
5. Add one room per row of the spec's section 3.3, and give the room its name only. A simulated
   entry derives the virtual valve and the virtual source from the room id, so the form asks for
   the name and nothing else.
6. Read the computed commands in the log for one cycle.
7. Turn `dry_run` off.
8. Drive the bench from `dashboard/room-climate-bench.yaml` with this table. Read the state after
   every row.

| Knob | Value | Expected |
| --- | --- | --- |
| `number.sim_front_room_room_temperature` | 19.0 | The room calls. The valve reaches the setpoint. The boiler reaches 31 with `hvac_action` heating. |
| the same | 21.5 | The room stops. The boiler reaches 15 with mode `off`, because the bench holds the minimum on time at 0. |
| `number.sim_outdoor_temperature` | 19.0 | Status `locked_out`. The boiler at 15. The valves at `off_setpoint`. |
| `switch.room_climate_lockout_override` | on | The lockout stops, and the room calls again. |
| `switch.sim_front_room_window` | on | The demand stops, and the valve closes in the same cycle. |
| `switch.sim_front_room_offline` | on | The room reports degraded, and the cycle continues. |
| `number.sim_front_room_valve_external_measured_room_sensor` | 2150 | The virtual valve measures 21.5, and the feed removes the bias of the local sensor. |

9. Correct the weights, the hysteresis, `open_offset`, `min_on_seconds` and
   `lockout_hysteresis` until each row behaves as the right column states. This is the purpose of
   the bench: a parameter changes here and no real valve moves.
10. Record the result in `docs/plans/2026-10-19-room-heating.evidence.md`.

A setpoint on the bench comes from `climate.room_climate_<room>`; the virtual source is set by
hand, because the component runs no thermal model.

## Phase B — the real house

1. Add the real entry, with `simulation` off. Fill the house form: `climate.heating_heat`, the
   outdoor sources `sensor.outdoor_temperature` and `weather.forecast_home`,
   and the defaults of the spec's section 5.2.
2. Add one room per radiator group, with the valves and the sources of the spec's section 3.3.
3. Confirm the scale of the feed entity on one valve. See the next section.
4. Keep `dry_run` on. Watch the log and `sensor.room_climate_status` for one day.
5. Turn `dry_run` off.
6. Disable `automation.heat_on_demand` (id `1731526329445`). Two controllers must not command
   `climate.heating_heat`.
7. Watch one full heating cycle per room. The boiler reaches 31 °C when a room calls, and 15 °C
   when the last room stops.
8. Repoint the eight dashboard cards from `climate.bt_*` to `climate.room_climate_*`. The table
   is in the repository [README](../README.md), and `dashboard/room-climate.yaml` is a starting
   view.
9. Remove the eight Better Thermostat entries from **Settings**, then **Devices and services**.
10. Remove Better Thermostat from HACS.

Step 8 must happen before step 9. The dashboard and the removed entities share no history, so a
card points at a missing entity between the two steps.

A room needs at least one valve, so the Back Room room is added only after the operator confirms
which valve the Back Room Better Thermostat entry drives. A room whose only valve is `unavailable`
holds its valves closed and reports degraded, so an unconfirmed valve heats nothing.

### Entity ids of the second entry

Both entries create a device named `Room Climate <Room Name>`, and both create the house device
`Room Climate`. Home Assistant suffixes the entity ids of the second entry with `_2`, so the
second entry produces `climate.room_climate_front_room_2`, `sensor.room_climate_status_2` and the
rest. Add the real entry first, or remove the bench entry before adding it, so the real house
owns the plain entity ids of the spec's sections 7.1 to 7.3, which are the ids in the two
dashboard views and in the card table.

## The feed-scale confirmation

The feed entity `number.<trv>_external_measured_room_sensor` has a range of −8000 to 3500 and a
step of 1. The value −8000 means "no external measurement". If the scale is 0.01 °C, the value
2150 means 21.5 °C and the usable band is 500 to 3500, which is 5 °C to 35 °C. The scale is not
confirmed, so confirm it on one valve before the component writes to ten.

This is the one live write to a real valve, and the operator performs it.

1. Pick one valve. `climate.trv_front_room` is the one to use.
2. Write the value 2150 into `number.trv_front_room_external_measured_room_sensor`.
3. Read the value back.
4. Watch `sensor.trv_front_room_pi_heating_demand` and `sensor.trv_front_room_local_temperature` for one
   hour.
5. If the valve regulates on the written value, keep the `calibration` of `external_sensor` and
   the `calibration_scale` of 100 for every room. The component then writes
   `round(room_temperature × 100)` into every valve of the room.
6. If it does not, set the `calibration` of every room to `none` and the `valve_mode` to
   `boost`. In `boost` mode the valve target is the setpoint plus `open_offset` while the room
   calls, and `off_setpoint` while it does not, so the valve regulates on its own local sensor
   instead of the written room temperature.

`modulate` requires the `external_sensor` calibration, because the valve and the component must
agree on the room temperature. `boost` is the fallback for a valve that ignores the feed.

## Rollback

1. Stop Room Climate, then remove its entries. The component sets the boiler to the OFF values
   at the removal, so a removed component does not leave the boiler running.
2. Add the Better Thermostat entries again.
3. Enable `automation.heat_on_demand`.
4. Repoint the eight dashboard cards back to `climate.bt_*`.

The bench entry is harmless to keep: it names only `sim_` entities, so it cannot move a real
valve or start the real boiler.

## What to look at when nothing happens

- `sensor.room_climate_status`. `dry_run` means the component logs its commands and sends no
  service call. `disabled` means the master switch `switch.room_climate_boiler_automation` is
  off. `locked_out` means the mean outdoor temperature is at or above `lockout_temperature`.
  `degraded` means every room reports a failure. `idle` and `heating` are the two working
  states.
- Degraded rooms. The attributes of `climate.room_climate_<room>` carry `degraded`,
  `usable_sources`, `valves_unavailable`, `valve_targets` and `window_open`. A room with no
  usable source and no `demand_fallback_sensors` entity that is on holds its valves at
  `off_setpoint`, so it heats nothing. `binary_sensor.room_climate_<room>_calling` is the
  demand latch.
- Dry run. `dry_run` is an option of the house entry and not a switch. Turn it off in the
  options flow, at the **House settings** step, before the component commands a valve.
- The boiler's minimum on and off times. `min_on_seconds` and `min_off_seconds` default to 300.
  A boiler that should stop but does not is inside `min_on_seconds`; a boiler that should start
  but does not is inside `min_off_seconds`. The reasons `lockout` and `master_off` are exempt
  and stop the boiler at once.
- The lockout override. `switch.room_climate_lockout_override` bypasses the lockout. A house
  that heats on a warm day has the override on, or the outdoor sources are unreadable and the
  lockout never started.

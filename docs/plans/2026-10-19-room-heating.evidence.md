# Room Climate, live evidence

Measurements of the instance on 2026-11-20 and 2026-11-21, Home Assistant 2026.10.1. Each row was
read from the instance over its own API, and the command that read it is named. The house's own
entity ids are private and are not repeated here.

## The faults that only the instance could show

| Fault | Measurement |
| --- | --- |
| The room form could not open on the panel | `POST /api/config/config_entries/subentries/flow` answered **HTTP 500** with `ValueError: unable to serialize schema` from `probatio/codecs/fields.py`. Home Assistant serializes every flow form with `probatio.to_field_list`, and the form declared its sources as a list of nested schemas. |
| The house of an entry was thrown away | An entry created with `simulation: true` created **no** simulated entity: `climate.sim_boiler` answered **HTTP 404**. An entry created with two live outdoor sources read `unknown` while both sources carried a value. No error was logged; the entry reported `loaded`. Cause: the flow wrote the house into `entry.data`, every reader read `entry.options`. |
| The source picker of the house was empty | `config/entity_registry/list` holds **0** temperature device classes for this house, while `/api/states` holds **44**. The picker read the registry. Four of the 44 are entities of the component itself, so a room could have read the component's own output. |

## The install of 0.2.1

| Step | Result |
| --- | --- |
| `gh release create v0.2.1` while `manifest.json` read `0.2.0` | `update/install` answered `The version 0.2.1 for this integration can not be used with HACS.` HACS refuses a release whose version does not match the code. The release was rebuilt on a commit whose manifest reads `0.2.1`. |
| `hacs/repository/refresh` with the repository name | `AttributeError: 'NoneType' object has no attribute 'update_repository'` — the command takes the numeric id. |
| `hacs/repository/refresh` with the GitHub repository id | `success: true`; the update entity offered `v0.2.1` within 5 seconds. |
| `update/install` | Installed `v0.2.1`, and the entity asked for a restart. |

Every step above ran over the API: no browser, no user interface.

## The behaviour after 0.2.1

| Reading | Value |
| --- | --- |
| The house outdoor temperature | `2.6 °C`, the mean of its two live sources, where it read `unknown` before |
| The simulated house of the bench | `climate.sim_boiler`, `number.sim_outdoor_temperature` and `binary_sensor.sim_boiler_firing`, where none existed before |
| The room form | `type: form`, `step_id: user`, 15 fields, and no HTTP error |
| The rooms | Eight rooms of the house, each with its own room temperature, and the mean of the rooms as the house temperature |
| A room whose valve is `unavailable` | Reports `degraded` and holds no valve, as the spec's section 6.4 requires |
| The status | `dry_run` |
| A valve under dry run | Its target stands at the value the old controller left, and its feed entity still reads `-8000`, so dry run wrote nothing |

## Open items

- **The setpoint source.** The spec gives the setpoint to the Scheduler integration, in its
  comparison table of the two controllers, and `/api/config` holds no such component. Every valve's
  `occupied_heating_setpoint_scheduled` reads `5` in nine rooms, so the schedule of the boiler's own
  app says off. A room cannot call for heat without a setpoint, and `climate.room_climate_<room>`
  reads no setpoint until one is set.
- **The feed scale.** Unconfirmed. The runbook's section "The feed-scale confirmation" gives the one
  live write to a real valve to the operator.
- **The picker labels.** Ten rows of the source picker read `Temperature` (Task 16e).
- **The old controller.** Seven entries are installed and every one of their climate entities reads
  `unavailable`, so nothing commands the valves. The whole house stands unheated except one valve
  that carries its own value.

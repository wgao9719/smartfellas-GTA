# Provisional G0 action capability sheet

Status: **UNVERIFIED ON GTA**. Compile reference: SHVDN v3.6.0. Supported game build: none established. These executors are available only in Debug with `EnableUnverifiedActions=true`.

| Item | face_player | wait_here |
|---|---|---|
| API mapping | `Ped.Task.TurnTo(player, duration)` | `Ped.Task.StandStill(duration)` |
| Observation | `GET_SCRIPT_TASK_STATUS`, sampled heading versus player direction | `GET_SCRIPT_TASK_STATUS`, sampled displacement and elapsed duration |
| Duration | 0.1–5 seconds | 0.1–5 seconds |
| Completion condition | Task observed active and heading error ≤20° after 250 ms | Task observed active and displacement ≤1.5 m through requested duration |
| Failure | Task ends without observed heading, timeout, invalid binding | Movement beyond tolerance, task takeover, timeout, invalid binding |
| Stop | Clear only while the same script task reports active and all current guards pass | Same |
| Safety | Live civilian, current binding, within 15 m, ambient/mod-owned, no protected policy, combat, busy, mission, cutscene, random event, pause, loading, or Online | Same |

`GET_SCRIPT_TASK_STATUS` values, task hashes, completion tolerances, and task-clear ownership are provisional mappings. Compilation is not runtime evidence. A task reporting active can still fail to change the ped. Both actions require observed completion after dispatch.

The additional development executors are documented in the [task catalog](development-task-catalog.md). The G0 limits above still apply to facing and waiting.

1. Record the exact GTA, Script Hook V, and SHVDN versions.
2. Test a valid ambient ped at 2 m and 8 m.
3. Test a mod-owned persistent ped once curated spawning exists.
4. Test a game-script-owned ped and verify rejection.
5. Remove the target during dispatch and verify rejection.
6. Recycle a handle and verify marker/instance rejection.
7. Start a mission or random event during the task.
8. Verify the adapter does not clear the replacement engine task.
9. Cancel before dispatch and during execution.
10. Replay the same command and verify one native effect.
11. Record started, completed, failed, canceled, and expired examples.
12. Complete 20 trials for each mapped action.

Do not add an action to release capabilities until this sheet contains actual results and an approved runtime build.

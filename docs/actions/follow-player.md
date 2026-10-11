# Follow player (P0, Debug only, UNVERIFIED)

`follow_player` walks a selected ambient or mod-owned pedestrian in short `TASK_GO_STRAIGHT_TO_COORD` segments, keeping within the registry's 15 m bound for a bounded duration (1–30 s). The controller samples position and player distance continuously. It stops on cancellation, range loss, unsafe world, combat, protection/ownership/binding change, route failure, takeover, or timeout. Completion requires observed task activity and the bounded interval; the adapter reports elapsed, path distance, and player distance facts.

The route checker uses `GET_SAFE_COORD_FOR_PED` with sidewalk mode, `IS_POINT_ON_ROAD`, `GET_INTERIOR_AT_COORDS`, `World.Raycast`, and nearby vehicle/ped/prop mission-entity checks. The current positive trial area is Legion Square; unknown areas fail closed. Natives and walk behavior remain unverified on the pinned game build.

## In-game test

1. Story Mode, no mission/cutscene/random event; enable `Development/EnableUnverifiedActions=true`.
2. Select an ordinary pedestrian in Legion Square, ask “follow me”, and capture the started ACK.
3. Walk 5–10 m around the plaza for the requested duration. Confirm the ped walks, remains within 15 m, and only then receives a completed ACK with facts.
4. Walk outside the bound, enter a mission/cutscene, or press Backspace. Confirm cancellation and that an engine takeover task is never cleared.
5. Ask “follow me” again, then “stop following”; confirm only the mod follow task is replaced and the stop completion is observed.

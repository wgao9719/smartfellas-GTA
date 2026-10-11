# Go to safe point (P0, Debug only, UNVERIFIED)

`go_to_safe_point` chooses or validates a nearby Legion Square pavement coordinate, checks a short corridor for sidewalks, roads, interiors, collision, vehicles, and mission-owned peds/props, and walks there in short straight segments. Completion requires observed task activity and arrival within 0.75 m. Any failed check aborts safely.

## In-game test

In a quiet Story Mode Legion Square, ask “go somewhere safe”. Confirm the adapter chooses pavement, the ped never enters a road/interior/restricted area, and completion follows an observed arrival. Repeat near a road or during a mission and confirm fail-closed cancellation.

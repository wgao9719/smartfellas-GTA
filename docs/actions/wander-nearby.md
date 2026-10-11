# Wander nearby (P0, Debug only, UNVERIFIED)

`wander_nearby` walks short checked pavement segments inside the registry radius (1–20 m) for a bounded duration (1–30 s). It revalidates the corridor and radius before each leg and stops on danger, takeover, cancellation, protection/binding change, or timeout. Completion reports observed movement and elapsed facts.

## In-game test

In Legion Square ask “wander nearby”. Confirm every leg stays inside the requested radius and on pavement, then confirm observed completion. Trigger a mission/cutscene or move the ped toward a road and verify immediate cancellation/fail-closed behavior.

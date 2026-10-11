# Flee from threat (P0, Debug only, UNVERIFIED)

`flee_from_threat` walks directly away from the supplied nearby threat position, or the player when the request is “get out of here”. It uses checked pavement walking rather than panic behavior, so it does not deliberately attack or run into traffic. Completion requires observed task activity and the configured distance increase (2–15 m), bounded by duration. Unsafe routes, combat, takeover, protection, binding loss, and cancellation latch a stop.

When `threat_actor_id` is supplied, its position is captured at dispatch and used as the threat position for that bounded action.

## In-game test

In Legion Square aim at or approach the selected ped and ask “get out of here”. Confirm it walks away on pavement, never attacks, and reports completion only after the measured distance increases. Try a supplied threat outside the local bound and a mission state; both must reject/cancel without clearing engine behavior.

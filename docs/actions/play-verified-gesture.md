# Play verified gesture (P0 conditional, Debug only, UNVERIFIED)

The allowlist is `wave`, `shrug`, and `point`. The adapter loads fixed animation dictionaries, starts `TASK_PLAY_ANIM`, keeps the ped stationary, and reports completion only after playback is observed for at least one second. Missing or interrupted animation, combat, vehicle state, takeover, cancellation, or binding/protection changes fail closed.

## In-game test

In a quiet Legion Square conversation ask “wave”, then repeat with “shrug” and “point”. Confirm the animation is visible, harmless, stationary, and followed by an observed completion ACK. Repeat in a vehicle, combat, mission, or with an unknown gesture and confirm rejection/cancellation.

# Stop following (P0, Debug only, UNVERIFIED)

`stop_following` is accepted only when this adapter owns the actor's active `follow_player` task. It replaces that task with a bounded pause and completes only after the replacement and absence of the follow task are observed. It cannot clear wait, wander, engine, mission, protected, or rebound tasks.

## In-game test

Start a bounded follow in Legion Square, ask “stop following”, and verify the ped stops and the completion ACK contains `follow_task_absent`. Repeat after mission/cutscene takeover and confirm rejection without clearing the engine task.

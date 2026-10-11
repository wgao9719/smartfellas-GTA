# Development NPC social embodiment

Status: implemented and compiled on macOS; **not observed in GTA**. G0–G4 remain open.
Debug builds require `[Development] EnableUnverifiedSocial=true` in `LivingNPC.ini`.
The service requires `LLS_RELEASE=0`, `LLS_DEV_SOCIAL=1`, and, for automatic initiation,
`LLS_DEV_SOCIAL_AUTONOMY=1`. Release builds cannot enable this path.

| Operation | Native mapping | Bounds | Observed on rig |
| --- | --- | --- | --- |
| Discover civilians | SHVDN `World.GetNearbyPeds`, zone/interior natives, runtime decorators | 8 nearby identities, 35 m, one candidate per sample | — |
| Approach partner | `TASK_GO_TO_ENTITY` | Initial separation ≤6 m; 4 s task; cumulative movement ≤6 m | — |
| Face partner | `TASK_TURN_PED_TO_FACE_ENTITY` via SHVDN | 600 ms | — |
| Chat/look | `TASK_CHAT_TO_PED`, `TASK_LOOK_AT_ENTITY` | 2–3 bound civilians, ≤45 s scene | — |
| Gesture | `TASK_PLAY_ANIM`, `STOP_ANIM_TASK` | Whitelisted casual greeting, upper-body flags, 1 s | — |
| Ambient speech | `PLAY_PED_AMBIENT_SPEECH_NATIVE` | Whitelisted greeting/chat contexts, ≥6 s between calls | — |
| Overhead text | `GET_SCREEN_COORD_FROM_WORLD_COORD`, text natives | ≤12 m, visible speaker, separate from foreground subtitle | — |
| Listener observation | `HAS_ENTITY_CLEAR_LOS_TO_ENTITY`, interior/state/distance checks | Scene members only, ≤10 m hearing | — |
| Release | Script-task status check, then clear owned tasks | Cancel on danger, state/binding change, distance or duration | — |

The adapter advertises `development:social-embodiment:v1`. The service then queues a
`social.scene` setup command and marks dialogue `presentation=overhead`. Setup completion
means dispatch only, with `native_completion_confirmed=false`.

Display receipts are captured on the first rendered frame. The transport sends that frame's
world/actor snapshots, then the display acknowledgment, then listener evidence, in that
order. Transient retries reuse the same evidence. Missing hearing cannot create memories.
A 409 hearing rejection is discarded without reconnecting the whole adapter.

Within 12 m, one service-directed conversation can run; without AI it uses authored fallback
lines. From 12–35 m, a local director uses native chat/voice without model calls or memories.
There are at most two adapter scenes total. The native director starts no more than one
scene per 30 s, with 120 s participant cooldowns. Approaching a native-only scene releases
it so the service may form a generated scene; it does not preserve that scene's turn history.
The service retains its development caps of four attempts and 24 reserved calls per session.

Run the cross-language check with:

```sh
uv run --frozen python tests/live_bridge_check.py --embodied
```

It exercises production social-controller dispatch and real HTTP with simulated game
observations. Core tests additionally check snapshot/ack/hearing ordering and immutable
retry bodies. Neither substitutes for rendering, task takeover, voice availability, or
frame-time measurements on the Windows rig.

Native signatures: [chat task](https://github.com/citizenfx/natives/blob/master/TASK/TaskChatToPed.md),
[approach task](https://github.com/citizenfx/natives/blob/master/TASK/TaskGoToEntity.md),
[ambient speech](https://github.com/citizenfx/natives/blob/master/AUDIO/PlayPedAmbientSpeechNative.md),
[screen coordinates](https://github.com/citizenfx/natives/blob/master/GRAPHICS/GetScreenCoordFromWorldCoord.md),
[line of sight](https://github.com/citizenfx/natives/blob/master/ENTITY/HasEntityClearLosToEntity.md).

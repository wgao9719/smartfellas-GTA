# NPC-to-NPC interaction plan

Date: 2026-10-11. Status: service/bridge slice and Debug native integration implemented; GTA verification pending.

## Integration prerequisite for GTA testing

This feature checkpoint was validated on base `77086c8`. When pushing it, remote `main`
was at `ee464d3` and included newer Windows runtime fixes and game test reports. Integrate
those changes before deploying this branch to GTA, particularly per-tick ped binding
continuity (which replaced decorators), script-thread initialization, bridge negotiation,
and cancellation. Reconcile the overlapping native action implementations as well.
The Mac simulation results below do not validate the integrated Windows build.

The goal is a street that feels lively. Pedestrians should visibly stop and talk to each other, the player should be able to overhear them, and what they say should reach the player later. This plan builds on the existing development social protocol described in [social scenes](social-development.md) and [the social scheduler](social-autonomy.md). It follows the post-v1 milestone in `references/SPECS.md` §19 and the behavior design in `references/SPECS_agentdesign.md`.

## Implemented first slice

The service accepts session-scoped ambient pedestrians. Their invitation and continuation
decisions are deterministic and owned by each recipient; curated and pinned actors retain
model decisions. A four-line ambient scene reserves four dialogue calls, excluding optional
semantic-memory HTTP work. Hearing is still required before every subsequent turn.

Social generation has a dedicated system prompt, observable partner appearance/activity,
and optional observed weather plus the session's game time. It does not share a partner's
biography or hidden name. Chatter uses a separate durable daily budget and rolling minute
cap, editable through provider controls. Disabled/exhausted AI uses an authored small-talk
bank. Legacy adapters retain queue-time receipts; new subtitle receipts attest display time
bounded by command creation and acknowledgment arrival.

Player input interrupts the addressed participant's scene. Social revalidation cleanup is
scene-scoped. The scheduler permits one automatic generated scene per session. The Debug adapter now
implements nearby scanning, overhead rendering, and a second native-only scene in the
12–35 m band. See [native integration](actions/npc-social-embodiment.md) for exact bounds.

Run `uv run --frozen python tests/live_bridge_check.py --social` for the C# fake street:
eight generated ambient identities, a four-line/two-speaker autonomous conversation, explicit
hearing, four reserved dialogue calls, and turn-limit completion. It uses the production
command controller and real Python HTTP boundary, with a fake provider and no GTA natives.
Focused Python tests also cover refusals, private context, session cleanup, missing hearing,
display-time validation, scoped interruption, and zero-request fallback.

## Baseline before this slice

The service already implements the hard parts. Each participant decides for itself, scenes have turn ownership, only confirmed hearing creates listener memories, retellings keep their lineage, and repeated encounters change partner and topic selection.

Before these changes, the real adapter had the following gaps (historical baseline):

- The adapter snapshots only the ped selected with F6 (`adapter/LivingNPC.GameAdapter/LivingNpcScript.cs:162-170`). It never forms a pair.
- Snapshots carry no `area_id` or `interior_id`. The scheduler requires both (`service/lls/social_scheduler.py:170-171`).
- The adapter never advertises `development:social:v1` and never posts `/v1/bridge/social/delivery`. The scheduler stalls waiting for hearing evidence.
- Only `curated_persistent`, `spawned_persistent`, and `anchored_service` actors qualify (`service/lls/social.py:41`). Ambient pedestrians do not.
- A 4-line scene costs about 10 model calls: two invitation decisions, then one review and one turn per line. The background allowance defaults to zero (`service/lls/gateway_config.py:83`).
- NPC lines share the single bottom-of-screen subtitle slot with the player's conversation (`adapter/LivingNPC.Core/CommandController.cs:118`, `:177`).
- Receipt freshness uses the command's queue time and a 2-second window (`service/lls/runtime.py:1575`, `service/lls/social.py:546`). A line that waits behind another subtitle can never count as heard.
- The only native actions are `face_player` and `wait_here`. No ped-to-ped task exists.
- Any player utterance cancels every scene. A scene that fails revalidation also cancels foreground work (`service/lls/runtime_social.py:247-257`).
- Social turns reuse the player-facing system prompt. The partner appears only as an ID, and the world state is hard-coded (`service/lls/social_storage.py:244`).

## Approach: detail by distance

Most visible liveliness costs nothing. Model calls are spent only where the player can overhear.

| Distance from player | Behavior | Model calls |
| --- | --- | --- |
| Up to 12 m | Generated lines, one call per speaker with that speaker's own context. Text above the speaker's head. Memories only from confirmed hearing. | About 1 per line |
| 12–35 m | Peds stop, face each other, gesture, and play GTA ambient voice lines. No text and no memories. | 0 |
| Beyond 35 m | GTA's own behavior | 0 |

A scene upgrades when the player approaches. Its next line becomes generated text.
When AI is disabled or the chatter budget is exhausted, an authored small-talk bank supplies lines. Those lines reference only observed facts such as weather, time of day, and activity.

## Phase A: service groundwork

Mac-testable now.

1. NPC-to-NPC prompt. Add a dedicated system prompt. Every ambient ped carries the game label `Passerby`, so describe the partner by persona appearance and current activity. Use a name only after the speaker has heard it. Pass real weather and time instead of the hard-coded world.
2. Chatter budget. Add a budget class for unprompted NPC chatter with daily and per-minute caps and a dashboard control. Memory consolidation keeps the `background` class.
3. Display-time attestation. The adapter reports when each line appeared on screen. The freshness check uses that time, bounded by acknowledgment arrival, instead of queue time.
4. Scoped interruption. Addressing one participant ends only that scene. A scene that fails revalidation cancels only itself, never foreground work.
5. Ambient participants. Allow `ambient_ephemeral` actors in scenes. Their memories last until the session ends unless the ped is pinned. A reconnect ends the session and its scenes.
6. Rule-based ambient decisions. Each ambient ped accepts or refuses from its own persona, mood, activity, refusal history, and familiarity. The decision creates the existing `I chose to …` memory, so adaptation still works. Curated and pinned actors keep model decisions. A 4-line ambient scene drops from about 10 calls to 4.
7. Scene director. Rework the scheduler to use the nearby set and distance bands. Run at most two scenes at once. Start a new scene at most every 20–40 seconds. Prefer pairs that already stand together. Generate the reply once hearing is attested at display start, so lines follow without gaps.
8. Small-talk bank. Add `data/ambient_smalltalk.json` for the zero-spend fallback.

## Phase B: nearby peds in the adapter

1. Nearby scan. Bind up to 8 eligible civilians within 35 m. Snapshot one ped per tick to stay under the 20 ms `slow_tick` threshold. Use movement and `IS_PED_USING_ANY_SCENARIO` for nearby peds, and the full scenario lookup only for scene candidates.
   - `BindingRetention` releases bindings only on despawn, model or marker change, script takeover, or a new runtime generation. Distance alone never releases one.
   - Exclude scene members from the releasable set at the `PruneBindings` call site (`adapter/LivingNPC.GameAdapter/GtaGameAdapter.cs:121`).
   - Raise `BindingRetention.Headroom` from 4 to about 10, so one scan pass cannot reach the 36-binding cap.
2. Snapshot fields. Add heading, `area_id` from the zone name, and `interior_id`.
3. Capability. Advertise `development:social:v1` in Debug builds behind an INI opt-in.
4. Fake street. Simulate 8 peds in `LivingNPC.BridgeProbe` so the full loop runs on a Mac.

## Phase C: visible conversations

Needs the Windows rig to verify.

1. Ped-to-ped operations: face another ped, look at it, approach within 6 m, hold a chat idle with `TASK_CHAT_TO_PED`, play whitelisted contexts through `PLAY_PED_AMBIENT_SPEECH_NATIVE`, play whitelisted gestures, and release.
2. Safety rules. Both peds must be ambient civilians. Limit movement to 6 m and a scene to 45 seconds. Abort on ragdoll, fleeing, or takeover. Do not block GTA's own event reactions, so danger ends a scene natively.
3. Overhead text. Draw several lines at once above speakers' heads, faded by distance, only within 12 m with line of sight. The bottom subtitle remains for the player's conversation.
4. Native hearing evidence. When a line appears, the adapter checks each listener's distance, interior, line of sight, and state, then posts `/v1/bridge/social/delivery`.
5. Capability sheet. Add `docs/actions/npc-social-embodiment.md` with an empty "Observed on rig" column.

## Phase D: the world reacts to the player

- Implemented: the first automatic turn may repeat the speaker's retained player conversation or confirmed third-party quote. Selection excludes whispers, other actors' private memories, forgotten sources, and current-scene quotes. Delivery retains exact words and lineage.
- A listener's `told me:` memory surfaces when the player later talks to that listener.
- A ped learns a name only after hearing its owner say it.
- A refusing ped waves the other off and plays a native goodbye line.

## Phase E: later

- The player joins an NPC conversation, and three-person scenes with deterministic legal speaker pairs.
- Pinned regulars are re-embodied in later sessions. This needs spawn reconciliation.
- The neighborhood `seek_company` routine produces implemented off-screen encounters.
- Spatial voice audio for NPC-to-NPC lines.

## Decisions

| Decision | Reason |
| --- | --- |
| Ambient pedestrians participate | Without them, a street has at most 8 eligible actors. |
| Ambient accept and refuse are rule-based but owned by the recipient | The specification requires a real acceptance step, not a model call. |
| One call per speaker, not one call for the whole scene | A single scene call would be about four times cheaper but would expose each NPC's private persona and memories to the other's lines. |
| Overhead text and GTA ambient voices | The bottom subtitle stays free for the player. Service audio is not spatial. |

## Risks

- G0 has not passed. Every native operation above is unverified until the rig runs it. Phase A and B4 are Mac-testable.
- Ambient speech contexts can be missing for some ped voices. `TASK_CHAT_TO_PED` behavior is unknown.
- Eight snapshots plus line-of-sight checks need frame-time measurement on the rig.
- At most 4 lines every 30 seconds gives 8 calls per minute while the player stays nearby. This matches the scheduler's existing per-minute cap.

## Current delivery and remaining work

Phases B and C are implemented behind the Debug INI opt-in, including actual render-time
observations and ordered HTTP delivery. Run `tests/live_bridge_check.py --embodied` for the
production social controller against the real service with simulated game observations.
The adapter and service enforce the 6 m pairing and 12 m generated-dialogue bounds.

Windows G0 and gameplay checks remain required. Refusal animation, seamless scene upgrades, continuous scheduling beyond the development session
caps, two concurrent generated scenes, and Phase E remain unfinished. Automatic retelling now selects bounded heard sources from
the speaker's own retained memory; it does not turn inferred events into facts. Native voice contexts,
chat animation, line wrapping, interruption behavior, and frame cost need rig observations.

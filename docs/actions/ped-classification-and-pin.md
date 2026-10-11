# Ped classification and pinning capability sheet

Status: **UNVERIFIED ON GTA**. Compile reference: SHVDN v3.6.0. Supported game build: none established. Role table: `data/ped_roles.json`, version 1, `verification: "unverified"`.

The adapter samples facts for each candidate ped and classifies them with the shared table. The service recomputes the role from the same facts and keeps the stricter result. A ped becomes a target only when its model, ped type, and relationship group all indicate a civilian and its ownership is ambient. Every other role, and any unlisted value, is **Protected**.

## Reported in-game failure and decorator-free replacement

Targeting **failed** with adapter `8df3db5`, GTA V Legacy 1.0.3889.0, Script Hook V v3889.0, and SHVDN 3.7.0-nightly.193, with Franklin in free roam and the session open. F6 near three standing ambient pedestrians, apparently 4–7 m away and centered in view, repeatedly showed `No nearby resident is available.` It never showed a nearest-role refusal. The console returned `false` for `Function.Call<bool>(Hash.DECOR_IS_REGISTERED_AS_TYPE, "lls_runtime_binding_v1", 3)`. GTA's startup registration lock prevented the constructor's `DECOR_REGISTER` from registering the private integer marker; a subsequent failed `DECOR_SET_INT` returned no target without explaining the technical failure.

The attempted registration fix in `a98b0bd` is withdrawn. It loaded but logged `target_decorator_unavailable`. Brian reported that manually unlocking, registering, and relocking in the SHVDN console also left the lock true and integer registration false. About two minutes later, GTA crashed: Windows Application Event 1000 at 05:08:06 named `GTA5.exe` version `1.0.3889.0`, exception `0xc0000005`, faulting module unknown; Windows Error Reporting Event 1001 was `BEX64`. These are supplied QA observations, not an automated crash reproduction.

**Safety finding: decorators are not used; post-startup decorator registration crashed GTA 1.0.3889.0 with SHVDN 3.7.0-nightly.193 in QA.** Post-startup unlocking/registration is unsafe on this build. The adapter removes registration, reflection on `DecoratorInterface`, and all decorator reads/writes for bindings. It does not write game memory for identity. Do not retry the withdrawn console registration procedure.

The replacement binds an actor UUID/runtime instance to the handle and model hash plus a per-binding continuity record. Every tick samples position, sample time, alive/dead state, health, ownership, and policy. Missing/dead entities, changed handle/model, implausible displacement for elapsed time, ownership/policy changes, or uncertain samples permanently invalidate the binding and queue `actor.removed`. A dead binding cannot revive. Reacquiring a recycled handle creates a fresh actor UUID/runtime instance with no old history or pending native actions, as required by FR-ID-01/FR-ID-04. Pinning, action dispatch/release, and persistence release use the same guard. This continuity record is a conservative guard, not an engine identity token: a replacement indistinguishable between observations cannot be proved absent. Native recycling behavior and guard tuning remain unverified.

The maximum sample gap is 2,000 ms. Movement receives an elapsed-time budget at 15 m/s on foot, including vehicle entry/exit; the 120 m/s vehicle bound applies only when both samples report being in a vehicle. Each binding also has a bounded 1.5 m position reserve that replenishes only from unused speed budget, rather than renewing on every tick. This rejects a series of small jumps that together exceed plausible speed. Samples at the same timestamp must have identical positions. Slow walking and plausible vehicle entry/travel retain continuity. Health loss while still alive is allowed; health increases and maximum-health changes invalidate the binding. These thresholds fail closed on uncertain movement or healing and have not been calibrated in GTA. The only accepted ownership transition is an authorized pin, with unchanged role, health, and vehicle facts and continuous position/time samples.

Candidate discovery now enumerates `World.GetAllPeds()` and applies an explicit Core filter for player exclusion, existence, life, and distance at most 8 m before the unchanged 45° look-cone scorer. The failed-session console counts were 0 for `World.GetNearbyPeds(player, 8f)`, 5 at 20 m, 1 for `World.GetNearbyPeds(position, 8f)`, and 66 for `World.GetAllPeds()`. Both [SHVDN nearby overloads](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/4afee05937377d33d87930821e922bb2d3987dae/source/scripting_v3/GTA/World.cs#L470-L516) already use the ped pool; the position overload includes the player, so its extra result does not establish a defect in the ped overload. Apparent camera distance also does not establish player distance. `GameplayCamera.Direction` returned the valid unit vector `(0.923, -0.376, -0.079)`. The fix's native behavior remains **unverified**.

## Sampled facts

| Fact | API | Cached | Expected value | Observed on rig |
|---|---|---|---|---|
| Model hash | `Ped.Model.Hash` | Per frame; compared with binding model | Listed civilian model, such as `a_m_y_business_01` = `0xC99F21C4` | |
| Human | `Ped.IsHuman` | Per frame | `true` for people, `false` for animals | |
| Animal model | `Model.IsAnimalPed` | Per frame | `true` only for `a_c_*` models | |
| Ped type | `Function.Call<int>(Hash.GET_PED_TYPE, ped)` | Per frame | 4 civilian male, 5 civilian female | |
| Relationship group | `Ped.RelationshipGroup.Hash` | Per frame | `CIVMALE` = `0x02B8FA80`, `CIVFEMALE` | |
| Cuffed | `Ped.IsCuffed` | Per frame | `false` for free pedestrians | |
| In police vehicle | `Ped.IsInPoliceVehicle` | Per frame | `true` for officers and detainees in cruisers | |
| Player group | `IS_PED_GROUP_MEMBER(ped, GET_PED_GROUP_INDEX(player))` | Per frame | `true` for companions and recruits | |
| Mission entity | `Entity.IsPersistent` | Per frame | `false` for ambient pedestrians | |
| Owning script | `GET_ENTITY_SCRIPT` (Debug builds only) | Per frame | Empty for ambient pedestrians | |
| Owned by this script | `DOES_ENTITY_BELONG_TO_THIS_SCRIPT(ped, false)` | Per frame | `true` only after the mod takes persistence | |
| Position and sample time | `Ped.Position`, monotonic adapter clock | Per guard check, including every tick | Finite position and continuous motion within the guard's elapsed-time bound | |
| Life and health | `Ped.Exists()`, `Ped.IsDead`, `Ped.Health`, `Ped.MaxHealth` | Per guard check, including every tick | Existing, alive ped with valid health facts | |
| Vehicle state | `Ped.IsInVehicle()` | Per guard check, including every tick | Plausible entry and travel retain identity | |

`Ped.PedType` does not exist in SHVDN v3.6.0. The adapter uses the native. A sampling exception yields no facts, which classifies the ped as unknown and protected.

## Role table

| Role | Policy | Ped types | Relationship groups | Model examples | Observed on rig |
|---|---|---|---|---|---|
| civilian | Ambient | 4, 5 | `CIVMALE`, `CIVFEMALE` | Residents, workers, tourists, shoppers, beach, business, hipsters, rural | |
| service_worker | Protected | 24 | None | `mp_m_shopkeep_01`, shop staff, bartenders | |
| police | Protected | 6, 27 | `COP` | `s_m_y_cop_01`, `s_m_y_sheriff_01`, `s_m_y_swat_01` | |
| military | Protected | 29 | `ARMY` | `s_m_y_armymech_01`, marines | |
| security | Protected | None | `SECURITY_GUARD`, `PRIVATE_SECURITY` | `s_m_m_security_01` | |
| emergency | Protected | 20, 21 | `FIREMAN`, `MEDIC` | `s_m_m_paramedic_01`, `s_m_y_fireman_01` | |
| gang | Protected | 7–19, 22 | Ambient gang groups, `GANG_*`, `DEALER` | `g_m_*`, `g_f_*` | |
| prisoner | Protected | None | `PRISONER` | `s_m_y_prisoner_01`; also any cuffed ped | |
| animal | Protected | 28 | Animal groups | `a_c_*` | |
| protagonist | Protected | 0–3 | `PLAYER` | `player_zero`, `player_one`, `player_two` | |
| story_character | Protected | None | None | `ig_*`, `cs_*`, `csb_*`; also player-group members | |
| unknown | Protected | Any other | Any other | Any unlisted model | |

The civilian model list is a conservative Story Mode allowlist. It excludes cult, thug, hillbilly, tramp, street-hustler, performer, and prologue models. The other model lists only improve labels. They are incomplete by design, because an unlisted model already classifies as unknown.

Police, security, emergency, and gang roles stay Protected. The agent design's conversation-only tier for these roles is deferred until a tested integration exists.

## Classification order

1. Missing facts: unknown, `facts_unavailable`.
2. A registered ped remains mod-owned while it is still the mod's: owned by this script after taking persistence, otherwise not a mission entity. A taken ped is game-script-owned with `registered_ped_taken_by_script`.
3. An unregistered mission entity, or one with an owning script, is game-script-owned.
4. Animal model or non-human: animal.
5. Cuffed: prisoner.
6. Player-group member: story character.
7. Any non-civilian model, ped type, or relationship group, in that order.
8. In a police vehicle: prisoner. This follows step 7 so an officer in a cruiser keeps the police label.
9. Any unlisted model, ped type, or relationship group: unknown, with a `*_unlisted` reason.
10. Otherwise civilian, `civilian_evidence`.

Game-script-owned or unknown ownership forces Protected. Otherwise the role policy applies. `tests/fixtures/ped_role_vectors.json` holds the shared cases for C# and Python.

## Status text

F6 shows `Target: <name> (Available)`, `(Busy)`, `(Out of range)`, or `(Not available)`. When no eligible ped is nearby, it names the nearest refused role, for example `Nearest: police officer — not available`. Debug builds log `target_classified role= reason= ped_type= relgroup=0x… model=0x…`.

While the selected binding remains valid, the script thread also draws a small blue cone above that pedestrian's head and refreshes `Target (blue marker): <name> (<state>)` each frame. The indicator follows the resolved binding, including seated pedestrians; cancel, despawn, or continuity invalidation removes it. Missing or nonfinite bone positions skip the marker, show `marker unavailable`, and log `target_indicator_unavailable` once. **The native marker and head anchor remain provisional and unverified in GTA.** Core tests and builds establish only the presentation contract and native API compatibility.

Transient notices expire after five seconds. Opening typed entry, sending a turn, and rendering a reply replace an old cancellation notice with typing, thinking, and talking status; recording and the current target remain visible independently. While typed entry is open, phone/accept/frontend navigation controls are disabled every tick in all three GTA input groups. Enter, arrow, Escape, and Backspace presses remain suppressed through their release frame, even when submitting or canceling already closed the field. If a phone call or phone task is active, Enter keeps the draft and asks the player to retry after the call. These control and phone natives also require an in-game recheck.

Technical failures show distinct targeting-unavailable feedback with the fixed codes `target_sampling_failed`, `target_camera_unavailable`, `target_binding_limit`, or `target_binding_lost`, logged once per code per adapter lifetime. These failures must not be mistaken for an empty candidate pool or a role refusal. The retired `target_decorator_unavailable` and `target_marker_failed` statuses are no longer part of binding.

## Rig test script

Use a Debug build. Save the game first. Record the adapter log for each step.

1. Record the GTA, Script Hook V, and SHVDN versions.
2. Press F6 beside an ordinary pedestrian downtown. Expect `Target: Passerby (Available)` and `role=civilian reason=civilian_evidence`.
3. Repeat beside a patrol officer on foot. Expect `Nearest: police officer — not available` and `ped_type=6`.
4. Repeat beside an officer seated in a cruiser. Expect the police label, not detained person.
5. Repeat beside a gang member in Grove Street. Expect the gang label.
6. Repeat beside Chop or a stray dog. Expect the animal label.
7. Repeat beside Lamar while he follows the player. Expect story character.
8. Repeat beside a shop clerk. Expect service worker.
9. Watch an arrest and target the cuffed ped. Expect detained person.
10. Target a random-event participant during the event. Expect `Not available` or a refusal.
11. Record each observed ped type, relationship group, and model hash in the tables above.
12. Record any ordinary pedestrian that shows `*_unlisted`, with its model hash.

### Continuity and identity regression checks

These checks are pending on the reference rig; pure tests and bridge probes cannot verify GTA's handle lifecycle or native sampling. Do not register, unlock, set, or inspect binding decorators as part of this procedure.

1. Load the replacement build and record its version and log. Confirm startup and targeting do not report either retired decorator error. Observe stability through ordinary free-roam targeting; a successful load alone does not establish crash freedom.
2. Use measured player distances to verify ordinary pedestrians at most 8 m away in the 45° cone select as `Target: <name> (Available)`. Verify the player cannot be selected and distant or outside-cone pedestrians cannot bind. Repeat the refused-role checks above.
3. Follow a bound civilian walking slowly and entering an ordinary vehicle. Confirm plausible movement retains identity. Exercise death/despawn, a teleport-like jump, changed model, and script ownership/policy takeover. Confirm permanent invalidation and `actor.removed`; pending face/wait/pin/cancel work or persistence release must not affect a replacement pedestrian. A later alive or matching sample must not revive the old binding.
4. Reacquire a recycled handle, including a replacement with the same model elsewhere. Confirm it receives a new actor UUID/runtime instance and cannot retrieve the old actor's conversation history.
5. Exercise failed native sampling and binding-capacity failure in a controlled validation build. Confirm distinct technical feedback, one fixed log entry per code, no selection, and no native action dispatch.
6. Repeat the pin checks below and the provisional face/wait capability checks with the continuity guard active. Record results without marking native capabilities supported until their full sheets pass.

### First-conversation UI regression checks (pending)

1. With Franklin at Legion Square, select the intended seated pedestrian in a crowd. Confirm the blue cone identifies exactly the selected ped, including while sitting or walking, and that the persistent label tracks availability. If F6 chooses a passer-by, the marker must expose that before F7 sends a message. Reselecting the same still-bound ped must keep its history; selecting another must not inherit it.
2. Open F7 while a story phone call is ringing. Type a draft, hold/release Enter, and use the arrow keys. Confirm those keys cannot answer or navigate the phone and the utterance is sent only once. Repeat with an already active call: Enter must keep the full draft and display the phone notice; after the call, Enter must submit it. Escape cancels typed entry and normal phone/game controls must return after release.
3. Cancel, reselect, and start another successful turn. Observe typing, thinking, then talking instead of the previous canceled notice. Wait five seconds after a transient notice: the notice must expire while the valid target label and marker remain. Confirm a recording indicator does not disappear merely because a transient notice expires.
4. Cancel, despawn the target, change ownership, and exercise an invalidated/recycled handle. Confirm the indicator disappears and never follows the replacement ped. Repeat at differing resolutions and with the pause menu; record screenshots and the loaded build identity before marking native UI behavior verified.

## Pinning

FR-ID-03 lets the player keep an ordinary pedestrian. Pinning promotes the session identity in place: the actor keeps its id, its own conversation memories, relationship updates, and owned knowledge. Nothing promises that the engine entity survives.

1. The player selects a pedestrian with F6, then presses F8 twice within 5 seconds on the same actor and runtime instance. One request waits at a time, for at most 10 seconds.
2. `PinPolicy` requires a safe world, ambient ownership, the civilian role, the ambient policy, no mission flag, not busy or in combat, and at most 8 m.
3. The adapter sends `actor.pin_requested` on the event lane. The service requires the `actor_pin` manifest flag, a fresh civilian ambient snapshot, and a passing conversation policy. It allows one pin per 10 seconds and 8 per session.
4. The service cancels the scene, promotes the actor in one transaction, and queues `actor.pin` with the display name. The default name is the [generated persona's](ambient-persona-and-activity.md) name, or `Resident <4 hex>` for an actor without one. The dashboard can supply another name.
5. The adapter rechecks the same continuity guard before pinning, caps registered pins at 8, and marks the binding mod-owned under the same actor id. Only this authorized pin transition may update the binding's expected ownership facts. Any refusal or discontinuity drops the binding and sends `actor.removed`.
6. A completed acknowledgment shows `Pinned as <name>. This conversation will be remembered. They may not return after you leave.` Any other outcome shows `Saved as <name>, but this pedestrian is no longer available.` The saved identity stays in the database without an embodiment.

| Item | API or behavior | Expected value | Observed on rig |
|---|---|---|---|
| Pin key | `[Input] PinKey`, default F8 | No conflict with the game or installed mods | |
| Registered pin | Binding marked mod-owned | Same actor id; snapshot reports `spawned_persistent` and `mod_owned` | |
| Ownership after pin | `IsPersistent` false without persistence | `mod_owned`, policy ambient | |
| Script takeover | Mission flag set by another script | `game_script_owned`, `registered_ped_taken_by_script`, binding pruned | |
| Despawn | Player leaves the area | Binding pruned, `actor.removed`, identity kept | |
| Persistence opt-in | `Ped.SetIsPersistentNoClearTask(true)`, Debug and `EnableUnverifiedPinPersistence=true` only | Ped keeps its current task and stays loaded | |
| Persistence ownership | `DOES_ENTITY_BELONG_TO_THIS_SCRIPT(ped, false)` | `true` after the opt-in | |
| Release | `Entity.MarkAsNoLongerNeeded` on prune, reset, or abort | Only when this script still owns the ped | |

`Entity.IsPersistent = true` would clear the ped's tasks, so the opt-in uses `SetIsPersistentNoClearTask`. This native call remains gated behind both Debug and `EnableUnverifiedPinPersistence=true`; the decorator-free guard does not enable persistence. The manifest then carries `pin_persistence_unverified`. Guard invalidation prevents persistence release from touching a possible replacement entity.

### Pin rig test script

1. Use a Debug build with `EnableUnverifiedPinPersistence=false`. Save the game first.
2. Talk to an ordinary pedestrian with F6 and F7.
3. Press F8 once. Expect the confirmation prompt. Wait 6 seconds and press F8 once more. Expect the prompt again, not a pin.
4. Press F8 twice quickly. Expect `Pinned as <persona name>`, for example `Pinned as Dana Whitfield`.
5. Type another message. Confirm the reply uses the pinned name and remembers the earlier exchange.
6. Walk away until the ped despawns. Confirm `actor.removed` in the service diagnostics and the pinned character in the dashboard.
7. Close the session. Confirm the dashboard still lists the pinned pedestrian and its memories.
8. Try F8 near a police officer, a story character, and a shop clerk. Expect a refusal and no new dashboard character.
9. Pin 8 pedestrians in one session. Expect the ninth to be refused.
10. Repeat steps 2–6 with `EnableUnverifiedPinPersistence=true`. Record whether the ped keeps its task, stays loaded, and is released after reset.
11. Start a mission while a pinned ped is nearby. Record whether a script takes it and whether the binding is pruned without release.
12. Forget the pinned character from the dashboard. Confirm its memories are gone.

Do not mark this sheet verified until every row has an observed value from the reference rig.

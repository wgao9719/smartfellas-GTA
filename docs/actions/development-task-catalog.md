# LLM-controlled development tasks

Status: implemented for the SHVDN compile reference; **unverified in GTA**.
Release builds still advertise zero action capabilities. A Debug build with
`[Development] EnableUnverifiedActions=true` advertises the explicit 18-action
catalog. That setting also grants action consent on typed and push-to-talk
turns, including movement, vehicle tasks, and combat. AI provider configuration
is separate. Backspace cancels the current conversation and its owned tasks.

The service offers only actions supported by the adapter, permitted for the
actor, and eligible in the latest snapshot. A separate LLM request chooses one
action or none after the dialogue. It receives argument schemas, descriptions,
the actor's coordinates, and up to eight nearby target records. Target records
identify the player, already bound civilian NPCs, and nearby ordinary cars.
They do not identify every world pedestrian or map destination.

The adapter resolves opaque target IDs again on its game thread. Ped targets
must still be visible, alive, and within 15 metres. Vehicle identities use bounded per-tick continuity checks; missing or implausible
observations retire the binding. Unknown model-supplied IDs and
native hashes cannot become game calls. Target loss, seat changes, movement
limits, task takeover, and cancellation end the request without claiming its
intended result happened.

| Action | Native task / observation |
|---|---|
| `face_player` | Turn toward player; observe heading |
| `wait_here` | Stand still; observe displacement and elapsed time |
| `follow_player` | Follow player at a short offset; observe proximity and bounded travel |
| `stop_following` | Withdraw this adapter's active follow task; observe task inactive |
| `go_to_safe_point` | Validate a nearby pedestrian coordinate, then walk; observe arrival |
| `run_to_point` | Validate a nearby pedestrian coordinate, then run; observe arrival |
| `wander_nearby` | Wander within a small area; observe movement within a travel budget |
| `flee_from_threat` | Flee a listed ped currently fighting this NPC; observe increased separation |
| `play_verified_gesture` | Authored `wave` animation only; observe animation playback |
| `look_at` | Look at a listed ped; observe head tracking |
| `hands_up` | Raise hands; observe active task over requested interval |
| `cower` | Cower; observe active task over requested interval |
| `enter_vehicle` | Enter a listed car in a free driver/passenger seat; observe that seat |
| `leave_vehicle` | Leave current nearly stationary car; observe exit |
| `drive_to_point` | Drive current car slowly toward a nearby road node; observe stopped arrival |
| `cruise_vehicle` | Cruise current car slowly for a bounded interval; observe movement |
| `fight_target` | Fight a listed ped briefly; observe combat, without claiming damage or defeat |
| `shoot_at_target` | Shoot at a listed ped using an equipped firearm; observe shooting, without claiming hits |

Facing and waiting retain their 0.1–5 second limit. P0 movement actions keep the shared registry bounds and local-navigation controller
from main (including the approved-area checks). Additional timed actions accept
0.1–10 seconds and default to three seconds. `stop_following` has no duration.
Movement is local: point destinations must be within 15 metres and must pass a
native coordinate check. The merged P0 walk/follow/wander/flee paths additionally retain
main's approved-area and pavement-corridor checks. Driving uses a 4 m/s target speed. Actions stop when
sampled travel exceeds their bound (normally 15 metres; following accepts an
explicit `max_follow_meters`; wandering also bounds travel by its radius).
Additional actions require the actor to remain within 15 metres of the player. P0 movement
retains main's observed distance bounds (follow uses `max_follow_meters`, other local
movement allows up to 30 metres before withdrawal).

Vehicle entry never requests carjacking or warping. Driving requires the NPC to
already occupy the driver's seat of a non-mission car. Shooting does not spawn,
equip, or replenish a weapon. Wave is an authored dictionary/clip mapping, not
an arbitrary animation name from the model; the historical action name does
not mean the mapping has passed game validation.

The existing world, ownership, mission, and actor binding checks remain in
force. Combat caused by an owned combat task can be observed and canceled;
unrelated combat still blocks dispatch. Ongoing tasks yield to engine takeover.
The adapter releases its still-owned task after observed completion or failure.
Native task status mappings and withdrawal are provisional until rig testing.

## Rig validation

Build Debug, enable the development setting, and configure an AI provider.
Try short requests such as “follow me,” “wave,” “look at me,” and “get in the
passenger seat of that car.” Coordinate actions need a resolvable local point;
the model has no map search or landmark navigation. To drive, first enter an
empty driver's seat, then request a short driving action.

For each task, record dispatch and terminal receipts alongside actual gameplay.
Exercise cancellation, target removal, handle reuse, occupied seats, task
takeover, blocked navigation, unloaded animation assets, moving vehicles, and
missing weapons. Verify service disconnection and radius/time limits withdraw
owned tasks. Do not enable release capabilities until native mappings,
observations, and cleanup have been verified on the supported Windows build.

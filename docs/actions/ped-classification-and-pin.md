# Ped classification capability sheet

Status: **UNVERIFIED ON GTA**. Compile reference: SHVDN v3.6.0. Supported game build: none established. Role table: `data/ped_roles.json`, version 1, `verification: "unverified"`.

The adapter samples facts for each candidate ped and classifies them with the shared table. The service recomputes the role from the same facts and keeps the stricter result. A ped becomes a target only when its model, ped type, and relationship group all indicate a civilian and its ownership is ambient. Every other role, and any unlisted value, is **Protected**.

## Sampled facts

| Fact | API | Cached | Expected value | Observed on rig |
|---|---|---|---|---|
| Model hash | `Ped.Model.Hash` | Per binding | Listed civilian model, such as `a_m_y_business_01` = `0xC99F21C4` | |
| Human | `Ped.IsHuman` | Per binding | `true` for people, `false` for animals | |
| Animal model | `Model.IsAnimalPed` | Per binding | `true` only for `a_c_*` models | |
| Ped type | `Function.Call<int>(Hash.GET_PED_TYPE, ped)` | Per binding | 4 civilian male, 5 civilian female | |
| Relationship group | `Ped.RelationshipGroup.Hash` | Per frame | `CIVMALE` = `0x02B8FA80`, `CIVFEMALE` | |
| Cuffed | `Ped.IsCuffed` | Per frame | `false` for free pedestrians | |
| In police vehicle | `Ped.IsInPoliceVehicle` | Per frame | `true` for officers and detainees in cruisers | |
| Player group | `IS_PED_GROUP_MEMBER(ped, GET_PED_GROUP_INDEX(player))` | Per frame | `true` for companions and recruits | |
| Mission entity | `Entity.IsPersistent` | Per frame | `false` for ambient pedestrians | |
| Owning script | `GET_ENTITY_SCRIPT` (Debug builds only) | Per frame | Empty for ambient pedestrians | |
| Owned by this script | `DOES_ENTITY_BELONG_TO_THIS_SCRIPT(ped, false)` | Per frame | `true` only after the mod takes persistence | |

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

## Rig test script

Use a Debug build. Save the game first. Record the adapter log for each step.

1. Record the GTA, Script Hook V, and SHVDN versions.
2. Press F6 beside an ordinary pedestrian downtown. Expect `Target: Nearby resident (Available)` and `role=civilian reason=civilian_evidence`.
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

Do not mark this sheet verified until every row has an observed value from the reference rig.

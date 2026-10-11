# Ambient persona and activity capability sheet

Status: **UNVERIFIED ON GTA**. Compile reference: SHVDN v3.6.0. Activity table: `data/ped_activity.json`, version 1, `verification: "unverified"`. Persona table: `data/ambient_personas.json`, version 2.

Both features add narrative context only. Neither grants a capability, changes eligibility, or establishes a world fact.

## Generated personas

The service gives each new ambient pedestrian a persona when it first registers the actor. A persona contains a name, occupation, personality, speech style, two motivations, a quirk, a mood, and a short bio. The draw is a pure function of the actor id and the names already in use, so replay and re-registration over the same stored actors produce the same persona.

The name never repeats one that a stored actor uses: a pack character, a pin, or another pedestrian in the session. Only the name is redrawn on a collision. If every combination for that presentation is taken, the original draw stands as a cosmetic duplicate.

The civilian model name supplies only presentation, age band, clothing or uniform, and setting. For example, `a_f_y_runner_01` yields `young woman in workout clothes` and a fitness occupation. Model names that encode ethnicity, body type, or orientation supply no traits. Unknown models produce a persona with gender-neutral names and no appearance. Retirement occupations are drawn only for older models.

| Setting | Models | Bio opens with | Trait changes |
|---|---|---|---|
| Local | Default | `A Los Santos local` | None |
| County | Rural (`salton`, `farmer`) | `A Blaine County local` | Drops city-only traits such as the bus schedule and the taco trucks |
| Visitor | Tourist | `A visitor to Los Santos on a short stay` | Drops rent, apartments, long shifts, neighborhood gossip, and local knowledge. Adds trip motivations and traveler quirks |

The table lists clashing pairs, such as `Suspicious of anyone who starts a conversation` with the mood `Curious and talkative`, or `Personal trainer` with `Get back in shape before summer`. A later draw skips any trait that clashes with an earlier one.

Every ambient actor shows the game label `Passerby`, which fits locals and visitors alike. The persona tells the model that the player knows only that label until the character shares its name. Pinning uses the persona's name as the default pin name. Actors stored before this change keep the `Resident <4 hex>` fallback.

The adapter keeps an ambient binding, and so its actor id and persona, until the game releases the ped. Despawn, a model or decorator marker change, a script taking the ped, or a new runtime generation releases it. Distance does not. Walking away and returning to the same ped finds the same person. Near 36 bindings the adapter releases the farthest ones first. It never releases the selected actor or a pin. A released ped receives a new identity if targeted again. Every reconnect clears the adapter's bindings and starts a new runtime generation and service session. Pause, mission, cutscene, random event, loading, save load, protagonist switch, and service loss all reconnect. The service deletes a session's ambient actors when the session ends, so only a pin's saved identity outlives these boundaries.

## Sampled activity

The adapter samples the selected actor's activity with its snapshot. `Resolve` runs every tick, so each binding reuses a sample for 250 ms.

| Fact | API | Expected value | Observed on rig |
|---|---|---|---|
| Movement | `Ped.IsSprinting`, `IsRunning`, `IsWalking`, `IsStopped`, fastest first | `walking` for a strolling pedestrian, `running` for a jogger | |
| Scenario in use | `IS_PED_USING_ANY_SCENARIO`, then `IS_PED_USING_SCENARIO` per table row | First matching row's label; `other` when none matches | |
| On phone | `IS_PED_RUNNING_MOBILE_PHONE_TASK` | `true` while a ped walks and talks on a phone | |
| Swimming | `Ped.IsSwimming` | `true` in water | |
| Injured | `Ped.IsInjured` | `true` below the injured health threshold | |

A sampling exception sends no activity. The snapshot field is optional, so older adapters remain compatible. The service maps an unknown movement to `unknown` and an unknown scenario label to `other`.

The service turns the sample into phrases such as `running` or `sitting on a bench` under `actor.current_activity` in the dialogue context. Busy, fleeing, ragdoll, burning, and in-vehicle peds are already refused for conversation, so the table does not describe them.

## Rig test script

Use a Debug build. Save the game first. Enable the fake provider or a local provider, and record the dashboard diagnostics for each step.

1. Target a pedestrian walking along a sidewalk. Ask what they are doing. Expect a reply consistent with walking, and a persona in the dashboard character profile.
2. Target a jogger. Expect `running` in the model context.
3. Target a pedestrian smoking, drinking coffee, or sitting on a bench. Record the scenario label. If it shows `other`, record the scenario from a native trainer.
4. Target a pedestrian talking on a phone while walking. Record `on_phone`.
5. Target a pedestrian standing still with no scenario. Expect `standing still`.
6. Watch the adapter log for `slow_tick` while a scenario ped stays selected for 2 minutes.
7. Talk to three pedestrians of different models. Confirm that names, occupations, and speech styles differ, and that appearance matches the model.
8. Talk to a tourist. Expect a visitor bio in the dashboard and replies about a short stay, not a home or job in Los Santos.
9. Talk to a pedestrian, walk at least 80 m away for 30 seconds, then return and target the same ped. Expect the same actor id and persona name, and a reply that remembers the earlier exchange. If the ped despawned, expect a new identity.
10. Pin one of them. Expect `Pinned as <persona name>`.

Do not mark this sheet verified until every row has an observed value from the reference rig.

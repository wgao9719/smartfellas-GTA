# GTA V NPC Agent Design

This document maps NPC roles in GTA V Story Mode to proposed agent actions, initiatives, and memories that preserve the existing storyline. NPCs can create social continuity and small everyday interactions, while GTA retains control of missions, character availability, canonical relationships, services, and consequential world events.

NPCs talking to each other is a core feature. They should initiate conversations, recognize acquaintances, develop opinions, remember shared experiences, and choose whom to spend time with, even when the player is merely observing.

> **v1 scope.** v1 covers the player talking with one NPC at a time. NPC-to-NPC conversation, social initiatives between NPCs, and learning between NPCs are the first milestone after v1 (`SPECS.md` §19). The role taxonomy, control policies, memory rules, and storyline protections below already apply to v1.

The agents' action repertoire should evolve through experience. Agents can generate reusable behavior code, test it, improve it, and organize successful behaviors into new action classes. The initial classes below are a starting vocabulary. The storyline protections remain fixed as the repertoire grows.

Classify each NPC along two independent dimensions:

- **Their role:** shopkeeper, civilian, police officer, story character, and so on.
- **Their current involvement in scripted content:** available for ambient behavior, providing a service, participating in an encounter, or controlled by a mission.

An ordinary-looking pedestrian might currently be essential to a scripted event. The taxonomy below covers types of NPCs rather than every named character or appearance variant. The roles and character examples come from the game; the agent behaviors are proposed designs.

## Initial agent action classes

These classes describe the starting repertoire and its permitted effects. They are not an exhaustive list of future behaviors. New behaviors can combine existing capabilities, and agents can create, split, or merge higher-level classes to organize what they learn.

| Class | What it permits | Examples | Boundary |
|---|---|---|---|
| **Conversation** | Speak, listen, ask, answer, joke, acknowledge, decline | Discuss the weather; ask another NPC about their afternoon | No invented missions, plot revelations, or claims about events that never happened |
| **Expression** | Communicate through compatible gestures and animations | Wave, nod, look toward the speaker, show impatience | Cannot interrupt scripted animations or essential tasks |
| **Local movement** | Move within an approved nearby area | Approach an acquaintance; move aside; walk to an available meeting spot | No entering restricted areas, obstructing objectives, or taking mission vehicles |
| **Routine** | Choose among small, supported everyday activities | Rest, look at a view, exercise, resume an idle activity | No implied working economy or elaborate off-screen simulation |
| **Social coordination** | Propose and negotiate a shared interaction | Invite someone to chat; introduce two acquaintances; suggest a short walk | Other NPCs can refuse; no forced participation or permanent commitments |
| **Service assistance** | Explain or recommend an existing game service | Recommend an outfit or discuss vehicle customization | Transactions, prices, unlocks, and outcomes remain with the original game |
| **Memory and adaptation** | Record experiences and adjust future ambient behavior | Remember a preferred topic, familiar face, previous interruption | Does not change native faction allegiance, mission cooperation, or service eligibility |
| **Disengagement** | End or suspend agent behavior | Stop talking when danger starts; yield when a script needs the NPC | Always available; game control takes priority |

An initiative is a short sequence of these actions. For example:

> A dockworker notices a familiar colleague, approaches, asks about their last conversation, listens, suggests a nearby break, and remembers whether the invitation was welcome.

That creates meaningful agency without requiring a new quest.

## Evolving actions and action classes

Evolution means that the available reusable behaviors change over time. An agent can discover a useful response to a situation, implement it as code, evaluate the outcome, and make an improved version available for later decisions. This design does not require retraining the underlying LLM; persistent code, records of outcomes, and retrieval of relevant skills can provide the adaptation.

Separate three layers so that a new behavior does not silently acquire new permissions:

| Layer | What it contains | How it evolves |
|---|---|---|
| **Game capabilities** | Developer-provided operations for speech, observation, animation, movement, memory, and existing services | New engine operations require an explicit toolkit implementation and validation. Agents cannot create engine support merely by naming it. |
| **Learned actions or skills** | Executable procedures that combine available operations, with inputs, preconditions, time limits, and stop conditions | Agents can create, revise, specialize, combine, share, or retire these procedures within enforced permissions. |
| **Action classes** | Categories that group related skills by purpose, such as hosting conversations or maintaining acquaintances | Agents can propose new categories and reorganize them as useful patterns emerge. Category labels never grant authority. |

For example, an NPC first learns to greet a familiar person. After several encounters, it develops a procedure for resuming unfinished conversations. It later combines that procedure with introductions and turn-taking to host a small group conversation. These skills can become a new class called **conversation hosting**, with its own variants for different group sizes and personalities.

The progression is driven by actual experience and need. NPCs should not unlock abilities simply because a fixed number of days has passed.

| Starting behavior | Learned action | Possible emerging class | Evidence that would justify keeping it |
|---|---|---|---|
| Greet and ask a question | Resume an unfinished topic at a suitable moment | **Conversational continuity** | Correct recall, a relevant topic, and the other person's willingness to continue |
| Introduce two people | Include a newcomer without interrupting the current speaker | **Conversation hosting** | Accepted introduction, coherent turns, and an opportunity for each participant to speak |
| Invite someone for a short walk | Choose a nearby route and pace that both participants accept | **Shared leisure coordination** | Explicit acceptance, feasible movement, and completion or graceful cancellation |
| Listen and apologize | Clarify a misunderstanding and offer an appropriate repair | **Social repair** | The other participant's actual response; the initiating agent cannot declare forgiveness |
| Discuss a customer's preferences | Give a contextually relevant recommendation through an existing service | **Personalized assistance** | Accurate use of preferences and a valid native service option, without invented benefits |
| Notice and remember a local hazard | Warn a nearby acquaintance about something currently observed | **Local information sharing** | Grounded observation, relevant recipient, and preserved source and time information |

These are examples of classes that could emerge, not a required progression or a fixed list of unlocks. Two NPCs with the same initial toolkit can develop different repertoires through different experiences.

## Learning and retaining new actions

Use the following lifecycle for a candidate skill:

1. **Observe a need.** Identify a repeated situation, an unsuccessful interaction, or a useful variation supported by actual observations.
2. **Generate a candidate.** Write a reusable procedure and describe its intended effect, inputs, required capabilities, eligible roles, and stopping conditions.
3. **Validate before execution.** Check that the procedure can call only permitted operations and stays within execution limits. Exercise it against simulated inputs before an eligible live trial. A model's own review is insufficient to enforce permissions.
4. **Try it in a bounded context.** Each primitive operation passes through the execution layer, which checks the current game state and every affected participant. Approval of the overall procedure does not bypass those checks.
5. **Evaluate observed results.** Record completion, refusal, interruption, relevance, repetition, and resource use. Treat a respected refusal or clean cancellation as a potentially correct outcome. Do not reward forcing participation or prolonging conversations.
6. **Retain, revise, or retire.** Successful candidates become retrievable skills after sufficient evidence for their context. Failed or redundant candidates can be revised, disabled, or removed. Preserve earlier versions for rollback.

Every retained skill should store its identity and version, executable code, parameters, required capabilities, preconditions, permitted effects, duration and resource limits, cancellation behavior, dependency versions, trial outcomes, and provenance. Replacing a dependency requires revalidation of affected skills.

The agent can also learn **when** to use a skill. A technically valid introduction may be unwelcome during another conversation; a previously successful joke may become repetitive. Selection policies should adapt alongside the skill code.

## Learning between NPCs

NPC-to-NPC interaction can support the spread of skills as well as information. An NPC might observe another successfully including a newcomer, remember that interaction, and later construct its own version. NPCs can also explain a technique to each other through an actual conversation.

Keep reusable software separate from personal knowledge. A shared implementation of an introduction routine does not transfer names, memories, relationships, private dialogue, or story knowledge. A receiving NPC must meet the skill's prerequisites and validate it in its own context. One NPC's successful trial does not establish that the behavior is appropriate for every role.

Maintain a per-NPC repertoire over a shared implementation library. If NPCs are intended to learn through experience, discovering a skill should follow an observed or communicated event rather than automatic worldwide distribution. Store skill availability and adoption history with the save timeline so reloading an earlier save does not give an NPC future experiences.

## Boundaries on action evolution

An evolving repertoire must preserve the original control policies. A protected story character remains protected even if it learns a new conversational technique. A clerk can improve recommendations but cannot learn to change prices. A security guard can learn a more natural greeting but cannot learn to grant access outside GTA's rules.

Generated code executes in a restricted runtime that can reach the game only through the validated bridge. It cannot modify the permission checks, invoke arbitrary native functions, or treat a friendly-sounding skill name as evidence that its effects are allowed. Compound skills inherit the requirements of their constituent actions, and multi-NPC actions require each participant's eligibility and acceptance.

The role mappings below therefore define initial examples and effect boundaries. Future action classes are welcome when their actual effects fit those boundaries. New engine mechanics, quest objectives, rewards, canonical relationships, or story events remain separate development decisions outside autonomous repertoire growth.

## Control policies

The role mappings use three control policies:

- **Ambient:** agent behavior can operate when the actor and location are eligible.
- **Service-limited:** flavor interaction only around the original service.
- **Protected:** autonomous control is off by default. Any extra interaction requires a specifically tested window.

## Story characters and scripted encounters

Story characters and scripted encounter participants need the narrowest permissions. GTA V includes the three protagonists, family and associates, mission contacts, antagonists, recruitable heist specialists, and Strangers & Freaks. These are distinct narrative responsibilities, even when their character models resemble ordinary pedestrians. [GTA Base character roster](https://www.gtabase.com/grand-theft-auto-v/characters/)

| NPC type and examples | Policy and action classes | Suitable initiatives | Suitable memory |
|---|---|---|---|
| **Inactive protagonists:** Michael, Franklin, Trevor when not being played | **Protected.** Optional conversation/expression in verified windows | Respond to a brief greeting or discuss a neutral topic | Extra conversations, separately from canonical relationships; never knowledge from another protagonist's private experiences |
| **Family, household members, partners:** Amanda, Jimmy, Tracey; Denise, Tanisha | **Protected.** Optional conversation/expression | Brief household chatter; acknowledge an earlier harmless conversation | Topic preferences, interruptions, conversational tone; no changes to family or romantic story arcs |
| **Friends and close associates:** Lamar, Ron, Wade | **Protected.** Optional conversation/expression | Casual banter or a callback to a permitted shared experience | Conversational familiarity; no new criminal plans, errands, or obligations |
| **Mission planners, employers, professional contacts:** Lester, Simeon, Solomon | **Protected.** Optional tightly bounded conversation | Discuss a neutral personal interest when explicitly eligible | Small-talk history; no invented assignments, tips affecting missions, or new appointments |
| **Power brokers, government contacts, adversaries:** Dave, Steve, Devin, Martin, Wei Cheng | **Protected.** Default to disengagement | Usually none; their interactions have substantial narrative implications | Read-only story context appropriate to the current save; no autonomous alliances or negotiations |
| **Recruitable heist specialists:** drivers, gunmen, hackers | **Protected.** Optional non-operational conversation | Harmless discussion of general interests, outside recruitment and heists | Conversation history; cannot affect availability, skill progression, cuts, or mission performance |
| **Strangers & Freaks contacts:** Tonya, Barry, Beverly, Cletus, Maude, Omega, Dom, Mary-Ann, Epsilon contacts, and others | **Protected.** Optional flavor only after checking each encounter | Discuss their established interests without adding another task | Only unlocked knowledge and permitted interactions; no extending their quest chains |
| **Mission-specific targets, escorts, hostages, witnesses, guards, crowds, and accomplices** | **Protected.** Disengagement | No agent initiative while participating | No additional actionable memory that could redirect their scripted role |
| **Random-event participants:** people needing help, robbers, victims, hitchhikers, potential recruits | **Protected** during the event and its resolution | At most, optional acknowledgment afterward if the actor remains legitimately available | Actual completed outcome; no invented follow-up event, debt, reward, or obligation |
| **Special named street encounters and performers** | **Protected** whenever an original interaction or unlock is involved | Additional ambient conversation only outside that interaction | Which conversations actually occurred; no changing unlock conditions |

The supporting cast also includes minor encounter characters and activity participants; being a minor character does not make an actor safe to commandeer. [Grand Theft Wiki character list](https://www.grandtheftwiki.com/Characters_in_GTA_V)

For a first implementation, leave these protected groups under GTA's control. Most of the project's value can come from the following groups.

## Service NPCs

Service NPCs can gain personality while keeping their original function. All initiatives below are proposals. They require an implementation for the specific service and must stop when its original interaction takes control.

| NPC type | Action classes | Player-facing and NPC-to-NPC initiatives | Memories that can matter |
|---|---|---|---|
| **Convenience-store clerks** | Conversation, expression, service assistance, memory | Greet a returning customer; exchange a brief remark with another customer | Familiar customers, preferred conversation style, previous courtesy |
| **Clothing-store staff** | Conversation, expression, service assistance, memory | Discuss style preferences; comment on an outfit; chat with a nearby customer | Stated preferences and observed purchases, if available to the mod |
| **Ammu-Nation staff** | Conversation, service assistance, memory | Explain existing offerings; recognize a returning customer | Previous topics and purchases; no new inventory, discounts, or eligibility |
| **Barbers and tattoo artists** | Conversation, expression, service assistance, memory | Resume a previous topic; discuss the player's stated aesthetic preferences | Style preferences and prior interactions; existing menus determine changes |
| **Mechanics and customization staff** | Conversation, expression, service assistance, memory | Comment on a familiar vehicle; discuss color preferences; trade remarks with a colleague | Recognized vehicles, observed modifications, preferences; no invented repair missions |
| **Taxi drivers** | Conversation, service assistance, memory | Start optional conversation during an existing ride; respect requests for quiet | Conversation preferences and destinations actually encountered; routing remains native |
| **Venue and hospitality staff:** bartenders, attendants, club workers, sex workers | Conversation, expression, service assistance, memory | Greet regulars; make small talk; exchange non-graphic workplace chatter | Names if introduced, conversational boundaries, previous topics; native services unchanged |
| **Activity instructors and opponents:** tennis, golf, flight school, shooting-range participants | Conversation, expression, service assistance, memory | Offer encouragement; discuss a previous recorded result; converse before or after an activity | Actual results and stated preferences; no invented opponents, rewards, or competitions |
| **Property staff and business contacts** | Conversation, service assistance, memory | Recognize the owner; discuss ordinary, non-actionable workplace matters | Ambient interactions; existing management requests remain scripted |

A mechanic remembering that Franklin likes understated paint is useful. A mechanic inventing a parts shortage and sending Franklin on an errand would extend the game's content.

## Ambient civilians

Ambient civilians provide the broadest space for initiative. GTA's pedestrian catalog contains many civilian, leisure, and occupational appearances. A model's appearance can suggest a persona, but does not prove that the game implements a corresponding job or service. The catalog also includes content beyond Story Mode, so the mod needs its own Story Mode allowlist. [Cfx pedestrian model reference](https://docs.fivem.net/docs/game-references/ped-models/)

For every row below, **conversation, expression, social coordination, and memory** are available when eligible. Local movement and routines require suitable locations and implemented animations.

| NPC type | Suitable initiatives including NPC-to-NPC interaction | Useful memories | Main constraint |
|---|---|---|---|
| **Neighborhood residents and ordinary pedestrians** | Greet acquaintances; introduce themselves; stop for a short conversation; invite someone to stand nearby | Familiar faces, names learned, previous topics, preferred company | Do not invent a persistent home, household, or occupation as an established game fact |
| **Commuters, office workers, businesspeople** | Brief small talk; complain about observed traffic; politely end a conversation | Who prefers short exchanges; repeatedly discussed interests | Avoid creating actual work schedules or appointments the mod cannot fulfill |
| **Affluent residents and shoppers** | Discuss fashion, cars, architecture, or a nearby view; ask another NPC's opinion | Taste, conversational compatibility, observed possessions | No access privileges, purchases, or status benefits created by dialogue |
| **Tourists and sightseers** | Ask a local about a nearby landmark; compare impressions; invite another tourist to look at something | Places actually visited, information received, helpful acquaintances | Directions must use known locations; no fictional attractions or tours promised |
| **Rural residents and farm-area workers** | Discuss weather or surroundings; greet a neighbor; take a short social break | Local acquaintances, observed conditions, preferred meeting spots | Do not simulate crop production, deliveries, or property ownership without implementing them |
| **Homeless or transient pedestrians** | Exchange greetings; discuss surroundings; seek company or quiet | Respectful versus unpleasant encounters, trusted acquaintances | Give them individual personalities; avoid treating all as beggars or criminals |
| **Joggers, cyclists, hikers** | Greet a familiar participant; ask about a route; suggest a short shared stretch or rest | Preferred pace, previous route discussions, invitations accepted or declined | Shared movement stays short and within approved paths |
| **Beachgoers, sunbathers, swimmers, surfers** | Comment on conditions; invite conversation; regroup at a nearby safe spot | Preferred locations and companions, recent conversations | Avoid controlling water activities until supported safely |
| **Fitness enthusiasts, bodybuilders, yoga participants** | Encourage someone; discuss routines; invite participation in an available animation | Encouragement preferences, familiar exercise partners | No invented progression, training effects, or unsupported synchronized activity |
| **Leisure groups, couples, friends, spectators** | Include a newcomer; continue group banter; disagree about a harmless topic | Group membership established through interaction, inside jokes, conversational turns | Do not invent major relationships or reorganize scripted groups |
| **Nightlife patrons and partygoers** | Introduce acquaintances; discuss music; invite nearby socializing | Who enjoys company, conversation boundaries, shared jokes | No new romance system, intoxication simulation, or consequential commitments |
| **Street performers, buskers, mimes, impersonators** | Address an audience; react to attention; exchange remarks with other performers | Familiar spectators, preferred performance spots, audience response | Protect special scripted encounters; use only supported performances |
| **Preachers, protesters, enthusiasts, ordinary cult-associated pedestrians** | Debate a bounded topic; invite someone to listen; accept disagreement | Expressed opinions, previous debates, willingness to engage | No recruitment quests, donations, faction conversion, or story-related revelations |
| **Media workers, paparazzi, film-set extras** | Discuss the immediate scene; trade observations; converse during an idle period | What they actually witnessed or were told | No invented news reports, scandals, stalking missions, or plot investigations |
| **Construction, dock, warehouse, industrial workers** | Invite a nearby colleague to take a break; discuss surroundings; resume a shared topic | Colleague familiarity, break preferences, previous conversations | Keep machinery, cargo, schedules, and mission areas under native control |
| **Gardeners, cleaners, sanitation and maintenance workers** | Greet regular passersby; exchange workplace chatter; choose an approved idle routine | Familiar people and locally observed conditions | Do not promise actual changes to vegetation, cleanliness, or infrastructure |
| **Delivery, postal, transport and hospitality workers** | Brief exchanges while idle; acknowledge a familiar colleague | Conversational history, observed visits | Appearance does not authorize generating deliveries, orders, or service transactions |
| **Drivers, passengers, pilots and other vehicle occupants** | Optional passenger conversation; parked-driver small talk | Shared ride conversations and directly observed destinations | Keep vehicle operation native initially; autonomous driving has much greater impact |

These categories can overlap. A tourist who is also jogging should combine relevant preferences, rather than requiring a separate "jogging tourist" agent implementation.

## Public order emergency and faction NPCs

Public-order, emergency, and faction NPCs need additional limits. Criminal groups include the Families, Ballas, Vagos, Aztecas, Marabunta Grande, Lost MC, and organized-crime groups. Membership should inform characterization without giving agents permission to change the game's conflict systems. [GTA V faction categories](https://www.grandtheftwiki.com/Category%3AGangs_in_GTA_V)

| NPC type | Permitted initiatives when confirmed idle and eligible | Useful memory | What stays native |
|---|---|---|---|
| **Police, sheriff deputies, highway officers, rangers** | Greet a civilian; answer a basic local question; exchange brief colleague chatter | Non-enforcement conversation history | Suspicion, investigations, pursuit, arrest, wanted levels, dispatch |
| **Tactical units, federal agents, military personnel** | Prefer no takeover; possibly background conversation in specifically tested contexts | Very limited ambient interaction history | Orders, access control, deployments, combat, mission behavior |
| **Private security, guards, bouncers** | Acknowledge a familiar face; make brief small talk while remaining at their post | Familiarity and prior conversations | Who may enter, searches, hostility, alarms, security response |
| **Paramedics, firefighters, medical personnel** | Conversation only when idle; brief colleague socializing | Ordinary acquaintance memory | Emergency response, treatment, rescue, scene management |
| **Street-gang members** | In-group banter; discuss ordinary interests; recognize a previously encountered person | Individual conversational familiarity and preferences | Territory, faction allegiance, hostility, recruitment, retaliation |
| **Bikers and organized-crime associates** | Discuss bikes, surroundings, or neutral personal interests with eligible companions | Shared topics, individual acquaintances | Deals, theft, extortion, jobs, faction leadership, violence |
| **Cult members or hostile enclave inhabitants** | Generally protected where behavior is encounter-related; limited ambient conversation elsewhere | Permitted dialogue history | Recruitment events, attacks, sacrifices, mission outcomes |
| **Prisoners, detainees, escorted people, people under guard** | Protected while in that situation | No agent memory that changes the encounter | Custody, escape, escorting, transfers, access |

A guard recognizing someone can produce a nod. It should not grant entry. A gang member remembering a pleasant conversation can change their wording; it should not overwrite the game's faction relationships.

## Animals and non embodied characters

| Type | Proposed treatment |
|---|---|
| **Ordinary domestic animals** | Optional familiarity, approach, attention, avoidance, and simple routine selection. No human conversation; an LLM may be unnecessary. |
| **Chop and other scripted animal participants** | Protected whenever existing companion, activity, or mission behavior applies. Any extra behavior needs a dedicated integration. |
| **Wildlife and hunting-related animals** | Leave native initially. Their behavior benefits less from conversational reasoning and can intersect with activities. |
| **Phone-only, radio, television, mentioned, flashback-only, or otherwise unavailable characters** | No autonomous embodied agent. Do not spawn them or invent communications to make them available. |
| **Dead, missing, unavailable, or special scripted entities** | Respect the current save state. No resurrection, replacement, or relocation to support agent interactions. |

## NPC conversations and social initiatives

NPC-to-NPC interaction should allow more than two agents alternately producing lines of text.

| Social initiative | Example | What each agent can remember |
|---|---|---|
| **Initiate contact** | "You're here again. How's your afternoon?" | Recognition, whether the approach was welcome |
| **Resume a conversation** | "You were telling me about that view yesterday." | Unfinished topic and who introduced it |
| **Ask and learn** | A tourist asks a resident about a nearby landmark | The question, answer, and source |
| **Share an observation** | "That car nearly hit the curb." | What was seen, when, and by whom |
| **Introduce acquaintances** | "You both keep talking about cars." | Names learned and the introduction event |
| **Invite a small shared activity** | Suggest a nearby walk, rest, or available exercise routine | Acceptance, refusal, preferred activities |
| **Coordinate a group conversation** | Invite a third person to join and make room for them | Participants, turns, shared topics |
| **Express agreement or disagreement** | Debate a song, outfit, vehicle, or landmark | Stated opinions; avoid turning one exchange into a permanent personality label |
| **Repair an interaction** | Apologize for interrupting; clarify a misunderstood joke | Apology, response, whether tension eased |
| **Respect boundaries** | Accept "I'd rather be alone" | Reduced approach frequency and topic avoidance |
| **Develop familiarity** | Use a previously welcomed joke or greeting | Relationship history supported by actual exchanges |
| **End gracefully** | "I'll let you get back to it." | Why the conversation ended; whether to resume later |

Information should travel through actual interactions. If A tells B something, C does not automatically learn it. If B later tells C, C remembers it as something B said, not as something C witnessed.

For example:

> A sees Franklin drive past in a red car. A tells B. B can later say, "Someone told me they saw Franklin in a red car."

B should not acquire Franklin's location, vehicle history, or identity certainty beyond what A actually conveyed.

Every social invitation needs a real acceptance step. One agent cannot decide that another has become its friend, joined its walk, or forgiven an insult.

## Memory classes

| Memory class | Examples | How it may affect behavior |
|---|---|---|
| **Identity and recognition** | Stable identity, learned name, recognizable acquaintance | Whether to greet as a stranger or familiar person |
| **Episodic experience** | "We talked near the pier"; "You interrupted me" | Specific callbacks and contextual reactions |
| **Preferences** | Likes quiet conversation; dislikes repeated invitations | Topic choice, approach frequency, activity suggestions |
| **Relationship state** | Familiarity, comfort, mild irritation, conversational trust | Tone, voluntary social approach, willingness to chat |
| **Conversation state** | Current topic, pending question, last speaker | Coherent turn-taking and resumable discussions |
| **Local place knowledge** | Visited locations, observed meeting spots | Grounded directions and nearby suggestions |
| **Social knowledge with provenance** | "B said C enjoys hiking" | Qualified references and questions, rather than presumed facts |
| **Short-lived intentions** | Finish this conversation; walk to that nearby spot | Small plans that expire or cancel cleanly |
| **Procedural memory** | Learned skill versions, successful contexts, failed attempts, adoption history | Retrieve, improve, select, or retire reusable actions without transferring private knowledge between NPCs |
| **Read-only canonical context** | Current character availability; facts the character legitimately knows | Prevent contradictions; never modified through generated dialogue |

Apply the following memory rules:

- **Remember Michael, Franklin, and Trevor separately.** Meeting one does not imply knowing the other two.
- **Distinguish observation, hearsay, and interpretation.** "He sounded annoyed" is an inference.
- **Use stable mod identities for persistent NPCs.** Reused appearances or temporary game handles do not establish that two encounters involved the same individual.
- **Keep ordinary transient pedestrians' memories temporary unless persistent identity is implemented.**
- **Tie memories to the save timeline.** Loading an earlier save should not leave characters remembering future events.
- **Do not fabricate completed off-screen experiences.** A proposed meeting is not a meeting that happened.
- **Let weak memories fade.** One declined invitation should not produce permanent resentment.

For generic ambient characters, author lightweight personalities and harmless background flavor. Keep those separate from claims that a job, home, relationship, or event actually exists in the simulation.

## Runtime permissions and storyline protection

Enforce permissions outside the LLM. Conceptually:

**Preservation rules stay fixed; permissions change with game state.** The rule "never override a mission-controlled actor" stays stable while actors enter or leave missions. A learned skill may therefore be permitted now and unavailable a moment later.

Agents cannot rewrite these rules or their enforcement. Developers can improve both through tested updates. "Fixed" means outside the agents' learning authority. Individually harmless operations can combine into disruptive sequences, so checks must also consider cumulative movement, duration, participants, and interference.

`Available actions = candidate skills whose required capabilities and effects are allowed for the role and current context`

The agent proposes actions from its evolving repertoire or generates a new candidate; the mod validates them before execution. Validation applies to the operations inside a skill, regardless of its name or class. Successful learning cannot expand the mod's underlying permissions.

ScriptHookVDotNet exposes mission, random-event, and cutscene state checks, which are useful inputs. They are not sufficient on their own to establish that every nearby actor is available. [ScriptHookVDotNet game-state API source](https://github.com/scripthookvdotnet/scripthookvdotnet/blob/main/source/scripting_v3/GTA/Game.cs)

The execution layer should:

1. **Check the actor and location**, including original scripts, services, encounters, and mission involvement.
2. **Recheck when executing.** A model response may arrive after a mission starts.
3. **Reject consequential actions** outside the project's scope: creating objectives, issuing rewards, changing faction relationships, modifying mission state, or initiating crimes and enforcement.
4. **Cancel social activity when necessary**, including danger, scripted interaction, despawning, or loss of a valid participant.
5. **Yield cleanly to GTA**, without restoring an old task over a newly started mission task.
6. **Bound interactions** by duration, distance, participant count, and frequency so autonomous chatter does not become constant interruption.

Even ordinary movement can have knock-on effects in an open world, so preserving the storyline is an engineering constraint to test, not something a prompt alone guarantees.

## Recommended first NPC-to-NPC milestone (post-v1)

v1 itself is the player-facing alpha defined in `SPECS.md`; this is the first milestone after it. Start with persistent acquaintances in a few locations: two dockworkers, beach regulars, neighborhood pedestrians, and one service NPC. Let them remember each other, initiate conversations, invite nearby company, disagree mildly, and adapt through repeated encounters, while GTA retains control of the storyline.

Demonstrate one complete learning cycle: an NPC creates a reusable interaction, tries it in an eligible context, records the outcome, improves the code, and later selects the revised skill appropriately. Log versions and observed results to show that the repertoire changed while storyline protections held.



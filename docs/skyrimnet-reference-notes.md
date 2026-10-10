# SkyrimNet reference review

Reviewed on 2026-10-10. The repository pins
`MinLL/SkyrimNet-GamePlugin` at
`7274247d312e2a7641e6bc4dff82455128a731c1`.
The original checkout contained an empty gitlink directory and no `.gitmodules`.
The coordinator restored that exact revision and added its upstream URL.
`references/SPECS_skyrimnet.md` is empty and supplies no requirements.

## Evidence boundary

The public reference contains the README, prompt templates, Papyrus scripts,
configuration, and serialized game records. It does not provide enough native
runtime implementation to verify the README's concurrency or performance claims.
Those claims are design context, not measured GTA acceptance evidence.
GTA implementation code remains original. Reference assets are not release inputs.

## Patterns used

| Reference file, relative to the pinned checkout | Finding | GTA implementation decision |
| --- | --- | --- |
| `README.md` | Dialogue, memory, perception, speech, triggers, and diagnostics form one player experience. | Track the full parity matrix, including failures and administrative controls. |
| `plugins/skyrimnet/base/prompts/dialogue_response.prompt` | Dialogue combines identity, event history, and final speaking instructions. | Assemble bounded actor-specific context. Treat dialogue and stored narrative as untrusted data. |
| `plugins/skyrimnet/base/prompts/native_action_selector.prompt` | A separate selector receives the exchange and eligible action schemas. | Use a separate structured action stage. Validate on the service and again at game dispatch. |
| `plugins/skyrimnet/base/prompts/memory/memory_builder.prompt` | First-person memory formation uses events, relevance, importance, and emotional context. | Preserve source event IDs and provenance outside generated prose. Separate proposed effects from observed outcomes. |
| `plugins/skyrimnet/base/prompts/memory/generate_memory.prompt` | Dense memories preserve names, locations, outcomes, and importance. | Keep short searchable episodes with bounded retrieval and deletion semantics. |
| `plugins/skyrimnet/base/prompts/submodules/character_bio/7100_memories.prompt` | Prompt memory access is actor-specific and includes temporal context. | Filter profile, protagonist, actor, and timeline before ranking memories. |
| `plugins/skyrimnet/base/prompts/decisions/scene_speaker_selector.prompt` | The runtime supplies legal speaker-target pairs and limits outsider interjections. | For post-v1 scenes, generate legal pairs deterministically before model selection. Require acceptance for shared activities. |
| `plugins/skyrimnet/base/dialogue_actions/nff_follower_controller.yaml` | Integration can explicitly suppress actions that conflict with another controller. | Entity ownership and scripted activity restrict actions independently from an actor's appearance. |

## Deliberate differences

The reference action prompt strongly prefers an action over `None` and accepts
implicit agreement. GTA proposals must preserve refusal, consent, and current
eligibility. A conversational suggestion does not establish consent or completion.

The reference allows Skyrim quest and Papyrus integrations. GTA's initial
capability registry excludes story changes, arbitrary natives, arbitrary scripts,
financial effects, and combat control. New learned skill labels do not grant new
engine permissions.

Skyrim actors and GTA ambient pedestrians have different lifetimes. GTA identity
uses a durable character ID plus a separate session, handle, and generation binding.
Save rollback must hide subsequent experiences and later skill adoptions.

The reference describes native in-process speech. GTA uses service-owned system
audio until a separate game-native audio experiment proves an integration.

The full user goal extends past v1. It requires actual NPC-to-NPC interaction,
observed learning, persistent changes, and later neighborhood simulation. A text
reply with a fixed action selector does not prove that goal.

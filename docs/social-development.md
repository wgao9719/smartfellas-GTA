# Development NPC social scenes

The service can run bounded scenes between two or three persistent NPCs through explicit development endpoints. Each participant receives its own character context. Invitations require recipient-owned acceptance. Scenes have a turn owner, expiry, turn limit, and interruption checks.

An optional [development scheduler](social-autonomy.md) starts two-NPC scenes and requests turns without caller invitation or turn requests. It requires separate fresh hearing evidence before another turn. Native hearing, lasting NPC-to-NPC behavior changes, and game integration remain unfinished. This implementation does not prove autonomous GTA activity.

## Gates

Social scenes require all of these conditions:

- The service runs with `LLS_RELEASE=0`.
- The service runs with `LLS_DEV_SOCIAL=1`.
- The authenticated bridge manifest advertises `development:social:v1`.
- Every participant has a registered persistent actor and a current runtime binding.
- Fresh actor and world snapshots show an eligible shared area and interior.

Release adapters do not advertise this capability. The social gate is independent of the development perception gate. Social requests cannot add native action capabilities.

The four endpoints are `/v1/bridge/social/invite`, `/turn`, `/review`, and `/delivery`. Each request binds the session, generation, protagonist, scene, and game time. The runtime derives the profile and timeline branch from the authenticated session. Exact request fixtures appear in `tests/test_runtime_social.py`.

## Decisions and delivery

The engine asks every participant, including the initiator, through that participant's private context. The initiator cannot accept for another participant. Invalid decision output fails closed. The fake provider refuses unless the participant's authored fixture explicitly enables invitations.

Hosted or local model work uses the gateway's background allowance. Its default is zero. Setting up foreground conversations does not authorize background spending.

Each generated utterance belongs to one participant and one pending turn. A subtitle acknowledgment alone does not give another NPC knowledge. The development adapter must separately submit fresh listener evidence for the exact delivered quote. Unknown hearing, stale evidence, identity changes, different interiors, or nonparticipants cannot receive that quote.

Listener memories record the delivered words as reported information, with teller, time, confidence, and retelling lineage. A retelling requires a retained memory visible in the current branch and protagonist scope. The engine does not copy hidden source-event payloads into recipients' context. It does not grant friendship, forgiveness, shared commitments, or native movement authority.

Foreground input, danger, protected game state, participant departure, actor replacement, expiry, reset, shutdown, and provider changes stop affected work. Cleanup joins active generation and storage before erasure proceeds.

## Real HTTP simulation

Run the isolated social probe:

```sh
uv run --frozen python tests/live_social_check.py
```

The probe starts a temporary Python service with three authored fake NPCs. It exercises six turns through real loopback HTTP, confirms delivery to two listeners per turn, and checks twelve reported memories. It checks distinct opinions and refusal of a seventh turn. It makes zero model requests and performs no native calls.

The report keeps `game_verified=false` and `native_verified=false`. These results establish the service protocol and simulated state transitions. The separate automatic probe establishes development scheduler initiative. Neither probe establishes native perception or Windows acceptance.

## Retained social experience

Valid invitation and participation decisions now create private source-linked memories for their owners. Invalid model output closes the attempt without creating a decision memory. Confirmed hearing can change later partner selection and topic suggestions. The [scheduler documentation](social-autonomy.md#lasting-social-adaptation) defines the bounds, erasure rules, and restart probe. These development simulations do not pass AUTO-05 native acceptance.

## Forgetting derived lines

Each spoken line records the lines its prompt explicitly carried: a retold source, a topic suggestion, or lines heard earlier in the same scene. Memories that retrieval happened to select are background, not carriers. When the runtime queues a line, it links the line's subtitle copy, its social copy, and every carrier, before any poll or acknowledgment can see the command. Delivery repeats the link and refuses a line whose speaker no longer remembers a carrier. A runtime turn that fails this check cancels its command and scene with HTTP 409.

Deleting a memory also deletes that character's later lines whose prompts carried it, including both copies of each line, transitively. Every deleted memory gets its own content-free scrub marker, and replay cannot restore it. Deletion stays scoped to one character. Other characters' memories of hearing those lines are their own experiences, and the canonical speaker event remains their proof. The administrative delete response lists the derived memory IDs it removed. Links contain identifiers only, and actor reset and profile deletion remove them.

Schema version 4 recovers links only from what earlier speaker events prove: each line's two copies and retold sources. Topic and in-scene carriers from before version 4 were never recorded. Paraphrases that a model produces from general recall are not tracked, and canonical events keep the delivered words. Forgetting a whole conversation is not implemented.

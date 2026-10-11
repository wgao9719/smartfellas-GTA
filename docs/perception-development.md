# Development perception and triggers

The service accepts typed development sensor input at `POST /v1/bridge/perception`. The GTA adapter does not emit this input yet. These tests establish backend behavior only.

The endpoint requires all three conditions:

- The service has `dev_perception=True`.
- The service has `release=False`.
- The session manifest includes `development:perception:v1`.

Default and Release configurations reject this endpoint. `LLS_DEV_PERCEPTION=1` maps to the explicit development setting. The ordinary `world.observed` endpoint still rejects grounded truth claims.

## Evidence checks

`PerceptionBatch` supplies one incident and at most 36 candidate witnesses. The runtime computes the accepted witnesses. It does not accept a caller-approved evidence list.

The runtime checks the session, protagonist, generation, sequence, and game date. Every witness must match a current actor instance and snapshot position. Area, interior, existence, and life state must also match. UTC samples older than two seconds or from the future are rejected. Unsafe world states reject perception.

Visual evidence requires positive line-of-sight and field-of-view attestations. Audible evidence requires positive hearing evidence and the configured distance limit. Unknown sensor values fail closed. These attestations remain development inputs until native sensor checks pass on the Windows rig.

Each accepted witness gets a separate canonical event and private memory. Other actors cannot retrieve that event as their own observation. Repeated incident identifiers require identical content and do not create duplicate effects.

## Delivered reports

Reported speech requires a matching delivery receipt from the service's acknowledgment path. The teller identity, current instance, exact words, and quote event identifier must match. The listener must also pass the hearing checks. Receipts quote the display-rule text that the subtitle showed, including the `speaker` and `display_rule` fields of `dialogue.delivered`.

The confirmed source is the delivered quote. `reported_event_id` preserves the claimed topic identifier. It does not verify the original incident. Canonical report records explicitly set `reported_lineage_verified=false`.

Receipt freshness uses command creation time. A delayed acknowledgment cannot make old words eligible for a new report. This conservative rule can reject delayed speech. Trusted adapter delivery timestamps remain future work.

## Trigger integration

The runtime can receive a validated `TriggerEngine` through dependency injection. It does not enable the pilot trigger pack automatically. Rules can retain thoughts, write private knowledge, suggest eligible actions, or queue authored remarks.

The trigger engine rechecks actor bindings, scene epochs, age, and budgets before dispatch. Player dialogue takes priority over autonomous remarks. Suggested actions pass the normal policy again. No rule grants a native capability.

Reported hearsay can produce private memory or knowledge. It cannot trigger speech forwarding or action suggestions. This prevents trigger rules from distributing rumors without a delivered conversation.

The integration tests cover isolation, expiry, duplicate rejection, delivery receipts, speech priority, and action reauthorization. They do not establish native visibility, hearing, navigation, or in-game behavior.

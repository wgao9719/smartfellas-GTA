# Deterministic local replay

The replay tool runs recorded events through the real HTTP boundary and service runtime. It uses a temporary database and recorded model responses. It never calls GTA, microphone devices, or model endpoints. Environment provider settings cannot enable cloud calls.

1. Run the synthetic conversation fixture.

   ```sh
   uv run --frozen python tools/replay_trace.py tests/fixtures/replay_conversation.json
   ```

The JSON report contains command states, policy decisions, acknowledgment results, and text hashes. It omits raw dialogue and authentication credentials. Repeated runs of the same complete bundle produce the same report.

Command text hashes describe queued output. Acknowledgment steps separately record submitted delivery hashes and character counts. Action outcomes come from observed command states and survive session closure in the report. A timed-out turn produces a failure count and a nonzero CLI exit status.

## Bundle contract

`tests/fixtures/replay_conversation.json` shows the complete format. Bundles are limited to 1 MiB, 256 steps, and 64 recorded turns. Unknown fields and duplicate JSON keys are rejected.

| Field | Meaning |
|---|---|
| `schema_version` | Must be `1`. |
| `sanitized` | Must be `true`. This is the author's assertion, not an automatic privacy check. |
| `start_utc` | UTC origin for the virtual service clock. |
| `session` | Bridge session contract. Replay creates a fresh isolated profile. |
| `turns` | Recorded text chunks and optional action proposals, in request order. |
| `steps` | Ordered events, waits, clock advances, acknowledgments, and session closure. |

| Step | Behavior |
|---|---|
| `event` | Submits the nested event through the real bridge endpoint. Replay substitutes its temporary session identifier. |
| `wait_turn` | Waits for the recorded model turn to finish. This step must immediately follow each player utterance. |
| `advance` | Advances the service clock by `seconds`, then checks command and session expiry. It does not sleep. |
| `ack` | Submits a receipt for `command_seq`. Simulated text delivery requires the actual recorded text. |
| `close` | Closes the temporary session through the real bridge endpoint. |

Replay preserves recorded actor identities, generations, event identifiers, sequence numbers, and game dates. The runtime therefore detects mismatches and duplicate collisions. Runtime-generated identifiers use a deterministic factory. Authentication tokens remain random and never appear in the report.

The recorded provider returns its action proposal to the normal policy. A recorded proposal cannot bypass consent, actor ownership, or capability checks. Missing recorded responses produce a failure count and a nonzero CLI exit status.

## Evidence limits

Current replay covers sequential turns, cancellation after generation, delivery receipts, policy rejection, and virtual-time expiry. It does not reproduce arbitrary midstream thread scheduling, real model behavior, speech devices, native perception, or GTA actions.

The service does not automatically export complete replay bundles yet. Prepare and review sanitized bundles explicitly before sharing them. Remove secrets, personal dialogue, and raw audio. Keep private traces outside the public repository. The included fixture contains synthetic text only.

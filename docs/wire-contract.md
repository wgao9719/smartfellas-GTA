# Bridge wire contract

The executable schema-v1 examples live in
[`tests/fixtures/wire_contract.json`](../tests/fixtures/wire_contract.json).
Both pytest and xUnit load the same valid and invalid messages for session open,
session response, event envelope, action proposal, command, and acknowledgment.
They exercise structural validation; a valid fixture does not authorize a native
action or prove game behavior.
The corpus is representative, not exhaustive semantic equivalence of every
outbound C# DTO and Pydantic model. The command cases also exercise the adapter's
existing authorization checks with a fixed fake actor and clock.

Every queued command includes a required `schema_version: 1`. Python supplies it
at the shared command producer, and the C# DTO requires it on decode. A missing
or null version is a protocol fault; the adapter stops AI and releases its own
active work through the controller's safety stop. A different major version also
latches the controller's stop. Neither a subsequent valid command nor a transient
HTTP recovery clears that stop.

Session opening with an unsupported integer major version returns HTTP **426**
and an instruction to install matching adapter and service versions and reload.
The adapter displays its own bounded, actionable version-mismatch notice without
displaying arbitrary HTTP response bodies. Other malformed session fields remain
HTTP 422 validation errors.

The response's `capabilities` contains the intersection of offered action IDs and
the service action registry. `capability_flags` contains the intersection of
offered optional feature names and service-supported features. Unknown optional
offers are ignored, duplicates collapse, and absent offers never appear in the
negotiated lists.
Negotiation describes protocol support, not native capability verification.

The service supports `typed_input`, `subtitles`, `actor_pin`,
`role_classifier_v1`, and its matching `role_table_<version>`. `ptt_control`
requires enabled speech. Development perception/social/skills/neighborhood flags
require their corresponding setting and a non-release service. A `service:<id>`
flag requires an authored anchored-service actor with that integration and
`service_limited` policy. Descriptive adapter claims such as
`pin_persistence_unverified` are not returned as service-supported features.
The adapter further intersects the returned actions with its own original offer;
a response cannot enable an unoffered executor.

## Deliberate differences from the illustrative spec examples

The fixtures preserve the exact JSON in SPECS.md sections 7.2 and 7.3 as rejected
examples, with valid counterparts. A pytest check ties those fixtures to the
original fenced JSON so a spec edit requires reviewing the contract expectations.

| Example | Difference and reason |
|---|---|
| Section 7.2 event | The original omits `protagonist_id` and `payload.scene_id`, and includes unsupported `payload.observed_by`. The implementation requires protagonist and scene identity to isolate player context and route cancellation. Hearing evidence is recorded through the observation/perception paths; an utterance cannot grant arbitrary actors witness status. The valid counterpart includes those identities and removes `observed_by`. |
| Section 7.3 proposal | The model-facing `ActionProposal` contains only `action_id`, bounded `arguments`, and `reason_for_player`. The illustrated `proposal_id`, `actor_id`, and `conversation_turn_id` are forbidden in that model output. Runtime code owns actor/scene/turn correlation and creates the authorization; model-supplied identity must not become execution authority. The valid counterpart omits the wrapper fields. A durable proposal-wrapper contract is outside this fix. |

Acknowledgments retain the existing v1 endpoint body (`session_id`,
`runtime_generation`, `status`, optional `reason`/`observed`); the command ID is in
the URL. The fixtures cover that implemented body. This change adds the missing
version to independently queued commands and does not redesign the auxiliary
endpoint bodies.

See [development validation](development-validation.md) for commands, results,
and the boundary between fake-game transport tests and GTA acceptance.

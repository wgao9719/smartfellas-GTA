# Provider controls and request budgets

AI starts disabled. The local dashboard can save a provider selection without restarting the service. Saved provider configuration overrides provider environment values. Credentials remain in the service environment. The dashboard does not accept or store API keys.

One Runtime owns AI control for each database. A second Runtime fails before readiness. The owner keeps an operating-system lock across provider changes. Ordinary Repository readers and exports remain available. The `<database>-ai-owner.lock` sidecar persists after shutdown. Keep that file while a service is running. macOS process-lock tests pass. Windows process locking remains unverified.

## Local AI

1. Start an existing OpenAI-compatible server on the service computer.
2. Select **Local OpenAI-compatible server** in the dashboard.
3. Enter its literal loopback endpoint.
4. Enter the server's model identifier.
5. Select **Save provider settings**.

For example, a local endpoint can be `http://127.0.0.1:1234/v1`. The service refuses non-loopback local endpoints, embedded credentials, query strings, and fragments. It disables environment proxies and redirects. Local requests never receive the hosted API key. This feature does not install models or establish their hardware performance.

## Hosted AI

1. Set `LLM_API_KEY` in the service environment.
2. Select **Hosted OpenAI-compatible server** in the dashboard.
3. Enter its HTTPS endpoint.
4. Enter the dialogue model identifier.
5. Enter the action model identifier, if it differs.
6. Enter current input and output prices for each model.
7. Set the daily foreground limits.
8. Set the daily background limits.
9. Select **Save provider settings**.
10. Review the saved configuration.
11. Select **Enable cloud for saved endpoint**.

Saving configuration always revokes cloud consent. Enabling cloud sends selected character context, relevant memories, and dialogue to the saved endpoint. The consent request binds the full saved configuration, including models, prices, and budgets. A stale dashboard cannot approve a changed configuration.

Select **Revoke cloud consent** to stop remote requests. The runtime cancels active turns and speech, joins cleanup, and persists revoked consent. This state survives restart even when `LLS_CLOUD_OPT_IN=1` remains in the environment. Ordinary shutdown does not revoke stored consent.

## Reservations and estimates

Every HTTP model attempt first reserves a request and its maximum configured estimated cost in SQLite. Action-schema repair requires a separate reservation. Foreground and background requests have separate limits. The default background allowance is zero. Local attempts also consume request allowances, with no hosted monetary charge.

The gateway uses configured USD prices per million tokens. It reserves the configured input ceiling plus the output limit. It performs monetary arithmetic with decimal values and stores integer micro-USD. This is an estimate from configured prices, not the provider's invoice.

Complete, unambiguous provider usage can settle the reservation. Missing, conflicting, duplicated, malformed, interrupted, or failed usage retains the full reservation. Reported usage above the reservation remains recorded and can exceed the estimate. Subsequent requests must still fit the remaining budget.

Limits and pending reservations survive restart and profile erasure. Profile export does not expose the global accounting ledger. The ledger stores content-free request records. It does not store prompts, response text, profiles, or credentials.

## Local fallback

The dashboard can configure one explicit local fallback endpoint and its models. The gateway tries the selected provider, then the configured local fallback, then a neutral reply. Each HTTP attempt must pass the request gate. Repeated provider failures open a temporary circuit that skips hosted retries.

The gateway does not combine a partial hosted response with another model's answer. A failed action stage returns no action. Fallback does not expand native capabilities or bypass cloud consent. The dashboard exposes the latest fallback reason and current daily usage.

An incomplete dialogue stream cannot authorize the action stage. Cancellation remains cancellation when a worker later fails. A committed reservation from canceled admission keeps its estimated charge and becomes uncertain after successful cleanup. A storage cleanup failure can leave the reservation pending, but cannot dispatch a new request.

## Validation boundary

Controlled HTTP fixtures test authorization, reservations, revocation, settlement, restart behavior, and fallback. The installed Chrome dashboard passed local selection, hosted configuration, explicit consent, revocation, and reload checks. Those browser checks made zero model requests. Real hosted and local model execution remains unverified.

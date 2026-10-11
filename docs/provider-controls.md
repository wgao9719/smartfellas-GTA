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

Every hosted model request passes a shared scrubber immediately before HTTP dispatch. It replaces recognizable API keys/tokens, configured bridge and provider credential values, authorization strings, private-key blocks, e-mail addresses, telephone and card-like numbers, IP addresses, Windows user-profile paths, and associated usernames with `[REDACTED]`. Filtering includes nested persona/context JSON, action-repair messages, social dialogue, skill candidates, and embeddings. Request bodies are not logged; HTTP failures retain the filtered request. The provider credential is still sent in its required authentication header.

The filter preserves ordinary game dialogue and leaves local inference and stored memories unchanged. It recognizes patterns and configured values; it is not a guarantee of anonymity or detection of every personal fact, encoding, or novel secret format. Cloud consent is still required.

Select **Revoke cloud consent** to stop remote requests. The runtime cancels active turns and speech, joins cleanup, and persists revoked consent. This state survives restart even when `LLS_CLOUD_OPT_IN=1` remains in the environment. Ordinary shutdown does not revoke stored consent.

## Claude

The **Claude (Anthropic Messages API)** provider (`LLS_PROVIDER=anthropic`) calls `https://api.anthropic.com/v1/messages` directly rather than through Anthropic's OpenAI-compatibility layer. That layer ignores `response_format`, rejects `temperature` below 1 on current models, and cannot set effort. The consent, reservation, scrubber, redirect and fallback rules above apply unchanged. `LLM_API_KEY` holds the Anthropic key, which is sent only in the `x-api-key` header. When that variable is unset, `python -m service.lls` loads it at startup from the git-ignored `secrets/LLM_API_KEY.txt`, or from `LLS_API_KEY_FILE`. With a hosted provider selected and no key anywhere, it creates that file as a commented template and names it in the startup error. See `secrets/README.md`.

- **Effort.** Every request sets `output_config.effort` from `LLS_ANTHROPIC_EFFORT` (default `medium`). Current Claude models think adaptively; Claude Opus 5.5 cannot turn it off. Thinking is never spoken or stored, but it counts toward each request's output limit and is billed as output, so give dialogue and action limits room for it. `tools/windows-rig/claude-gateway.json` uses 2,048 and 1,024 tokens. Set the variable to an empty value for a model without effort support.
- **Actions.** The action stage uses structured outputs. The JSON grammar cannot express numeric or string bounds, so the request schema omits `minimum`, `maximum`, `minLength` and `maxLength`. The prompt still states them, and the service validates every proposal against the full registry schema. A refusal or an exhausted output limit selects no action.
- **Grounding checks.** The sentence classifier returns a `yes`/`no` enum through structured outputs, sends no `temperature`, and has 512 output tokens and a 3-second deadline. A timeout or invalid verdict withholds the sentence, as for other providers.
- **Usage.** Streamed `message_start` and `message_delta` counts settle each reservation. Input includes any cache reads and writes, at the configured input price.
- **Configuration without the dashboard.** `LLS_AI_GATEWAY_FILE` loads prices and limits from a JSON `GatewayConfig`. Saved dashboard settings still override the environment and this file. The rig's `start-service.ps1 -Provider claude` uses Claude Sonnet 5 (`claude-sonnet-5`) and `claude-gateway.json`. That file lists Anthropic's published prices per million tokens: $2 input and $10 output for Sonnet 5 and 5.5, $4 and $20 for Opus 5.5. The ledger uses them as estimates, not invoices.

### Claude through the Claude Code CLI

`LLS_PROVIDER=claude_cli` sends the same requests through the **Claude Code CLI you are signed in to** (`claude auth login` once on the service computer) instead of an API key. It is meant for development and demos on a machine that already uses Claude Code. The prompts, consent, reservations, scrubber and response parsing are the Messages API provider's. Only the transport differs:

- Each request runs `claude -p` once in an empty temporary folder with `--tools ""`, `--strict-mcp-config` and `--no-session-persistence`, so the CLI can't use tools, MCP servers or project instructions, and it keeps no transcript. Requests travel on stdin and replies return as `stream-json`; the prompt is never on the command line. Dialogue streams with `--include-partial-messages`, and grounding and action checks use `--json-schema`. Model names must be ones the CLI's route accepts; through a proxy that can mean dated IDs such as `claude-sonnet-4-5-20250929`.
- The child process gets the service's environment minus every `ANTHROPIC_*`, `CLAUDE_*`, `CLAUDECODE`, `LLM_*` and `LLS_*` variable, so a surrounding Claude Code session can't lend it a token and service secrets never reach it. No API key is read.
- The CLI takes about a second to start, so one process per prompt shape (dialogue, grounding) is started ahead of time and waits for its single request. Who is speaking travels with the request: the system prompt holds the constant rules, and the character's identity starts the user message.
- User settings stay loaded, because they can hold how the CLI signs in (for example a local proxy), but `--settings` turns off every hook and always-on thinking. The CLI still adds a short environment note (working folder, OS and today's real date) to the system prompt; no user memory, CLAUDE.md or project files reach the model.
- Grounding checks get 8 seconds instead of 3, because even a warm CLI adds about a second before the model starts.
- A signed-out CLI fails the turn with its own message (for example "Not logged in"), which appears in the provider's last failure.
- `LLS_CLAUDE_CLI` names the executable; by default the service uses `claude` on `PATH`, then `~/.local/bin/claude(.exe)`. Prices in `tools/windows-rig/claude-cli-gateway.json` are zero because a subscription isn't billed per token. The request limits still apply.

## Reservations and estimates

Every HTTP model attempt first reserves a request and its maximum configured estimated cost in SQLite. Action-schema repair requires a separate reservation. Foreground, background maintenance, and NPC chatter requests have separate daily limits. Background and chatter daily allowances default to zero. Chatter additionally has a durable rolling-minute request cap, defaulting to eight, shared across sessions and providers. Social dialogue and its optional semantic retrieval use chatter; memory consolidation retains background. Configure chatter daily requests, estimated USD, and requests per minute in the dashboard. Local attempts also consume request allowances, with no hosted monetary charge.

The gateway uses configured USD prices per million tokens. It reserves the configured input ceiling plus the output limit. It performs monetary arithmetic with decimal values and stores integer micro-USD. This is an estimate from configured prices, not the provider's invoice.

Complete, unambiguous provider usage can settle the reservation. Missing, conflicting, duplicated, malformed, interrupted, or failed usage retains the full reservation. Reported usage above the reservation remains recorded and can exceed the estimate. Subsequent requests must still fit the remaining budget.

Limits and pending reservations survive restart and profile erasure. Profile export does not expose the global accounting ledger. The ledger stores content-free request records. It does not store prompts, response text, profiles, or credentials.

## Local fallback

The dashboard can configure one explicit local fallback endpoint and its models. The gateway tries the selected provider, then the configured local fallback, then a neutral reply. Each HTTP attempt must pass the request gate. Repeated provider failures open a temporary circuit that skips hosted retries.

The gateway does not combine a partial hosted response with another model's answer. A failed action stage returns no action. Fallback does not expand native capabilities or bypass cloud consent. The dashboard exposes the latest fallback reason and current daily usage.

An incomplete dialogue stream cannot authorize the action stage. Cancellation remains cancellation when a worker later fails. A committed reservation from canceled admission keeps its estimated charge and becomes uncertain after successful cleanup. A storage cleanup failure can leave the reservation pending, but cannot dispatch a new request.

## Validation boundary

Controlled HTTP fixtures test authorization, reservations, revocation, settlement, restart behavior, and fallback. The installed Chrome dashboard passed local selection, hosted configuration, explicit consent, revocation, and reload checks. Those browser checks made zero model requests. Real hosted and local model execution remains unverified.

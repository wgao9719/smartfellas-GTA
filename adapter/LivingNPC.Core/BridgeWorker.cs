using System;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace LivingNPC.Core
{
    // Owns HTTP only. No game API, disk operation, or continuation calls the script.
    public sealed class BridgeWorker : IDisposable
    {
        private sealed class PermanentFailure : Exception { public int Status; public PermanentFailure(int status) { Status = status; } }
        public readonly BoundedQueue<string> Notices = new BoundedQueue<string>(16);
        private readonly HttpClient controlClient, eventClient, pollClient;
        private readonly CancellationTokenSource stop = new CancellationTokenSource();
        private readonly SessionRequest request;
        private readonly Task run;
        private readonly string bootstrapToken;
        private readonly Uri endpoint;
        private long cursor;
        public volatile bool RequiresReconnect;
        public volatile bool ProtocolFault;
        public float Fps;
        public int ActorCount;
        private int failures;
        private volatile SessionResponse? session;
        private volatile bool online;
        public readonly BoundedQueue<EventEnvelope> Controls = new BoundedQueue<EventEnvelope>(128);
        public readonly BoundedQueue<EventEnvelope> Events = new BoundedQueue<EventEnvelope>(128);
        public readonly BoundedQueue<EventEnvelope> Snapshots = new BoundedQueue<EventEnvelope>(36);
        public readonly BoundedQueue<Command> Commands = new BoundedQueue<Command>(64);
        public readonly BoundedQueue<Ack> Acks = new BoundedQueue<Ack>(512);
        public SessionResponse? Session => session;
        public bool Online => online;
        public string LastFailure { get; private set; } = "";
        public BridgeWorker(Uri endpoint, SessionRequest request, string bootstrapToken, HttpMessageHandler? controlHandler = null, HttpMessageHandler? eventHandler = null, HttpMessageHandler? pollHandler = null)
        {
            this.endpoint = endpoint; this.request = request; this.bootstrapToken = bootstrapToken;
            controlClient = Client(endpoint, controlHandler); eventClient = Client(endpoint, eventHandler); pollClient = Client(endpoint, pollHandler);
            run = Task.Run(Run);
        }
        private static HttpClient Client(Uri endpoint, HttpMessageHandler? handler)
        {
            var client = handler == null ? new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false }) : new HttpClient(handler);
            client.BaseAddress = endpoint; client.Timeout = TimeSpan.FromSeconds(7); return client;
        }
        private async Task<string> Send(HttpClient client, HttpMethod method, string path, object? body, string token)
        {
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(stop.Token))
            using (var message = new HttpRequestMessage(method, path))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(7));
                if (token.Length > 0) message.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
                if (body != null)
                {
                    var json = Wire.Encode(body);
                    if (Encoding.UTF8.GetByteCount(json) > 256 * 1024) throw new InvalidDataException("bridge_body_too_large");
                    message.Content = new StringContent(json, Encoding.UTF8, "application/json");
                }
                using (var response = await client.SendAsync(message, HttpCompletionOption.ResponseHeadersRead, deadline.Token).ConfigureAwait(false))
                {
                    if ((int)response.StatusCode == 401 && session != null) { RequiresReconnect = true; online = false; }
                    if ((int)response.StatusCode >= 400 && (int)response.StatusCode < 500 && (int)response.StatusCode != 408 && (int)response.StatusCode != 429) throw new PermanentFailure((int)response.StatusCode);
                    response.EnsureSuccessStatusCode();
                    using (var stream = await response.Content.ReadAsStreamAsync().ConfigureAwait(false))
                    using (var output = new MemoryStream())
                    {
                        var buffer = new byte[8192]; int count;
                        while ((count = await stream.ReadAsync(buffer, 0, buffer.Length, deadline.Token).ConfigureAwait(false)) > 0)
                        {
                            if (output.Length + count > 256 * 1024) throw new InvalidDataException("bridge_response_too_large");
                            output.Write(buffer, 0, count);
                        }
                        return Encoding.UTF8.GetString(output.ToArray());
                    }
                }
            }
        }
        private async Task Run()
        {
            while (!stop.IsCancellationRequested && session == null)
            {
                try
                {
                    var value = Wire.Decode<SessionResponse>(await Send(eventClient, HttpMethod.Post, "/v1/bridge/sessions", request, bootstrapToken).ConfigureAwait(false));
                    if (value.SchemaVersion != 1 || value.SessionId.Length == 0 || value.Token.Length == 0 || value.PolicyVersion.Length == 0) throw new InvalidDataException("invalid_session");
                    value.Capabilities = value.Capabilities.Intersect(request.Capabilities).ToArray();
                    session = value; online = true;
                }
                catch (OperationCanceledException) when (stop.IsCancellationRequested) { return; }
                catch (PermanentFailure e)
                {
                    LastFailure = "session_rejected_" + e.Status;
                    Notices.ReplaceOldest(e.Status == 409
                        ? "Save/profile association refused. Verify ProfileId, SaveSlot and ParentBranchId in LivingNPC.ini, then reload and confirm. Use a new profile for a different save."
                        : e.Status == 426 ? "Protocol version mismatch. Update the Living Los Santos adapter and service together, then reload the adapter."
                        : LastFailure);
                    return;
                }
                catch (Exception e) { Fail(e); await Delay(1000).ConfigureAwait(false); }
            }
            if (session != null) await Task.WhenAll(ControlLoop(), EventLoop(), PollLoop()).ConfigureAwait(false);
        }
        private void Fail(Exception error)
        {
            // No URL, response body, token, or player text enters diagnostics.
            LastFailure = error is HttpRequestException ? "bridge_http_failure" : error is OperationCanceledException ? "bridge_timeout" : "bridge_protocol_failure";
            if (Interlocked.Increment(ref failures) >= 2) online = false;
        }
        private void Success() { Interlocked.Exchange(ref failures, 0); online = !RequiresReconnect && !ProtocolFault; }
        private async Task Delay(int ms) { try { await Task.Delay(ms, stop.Token).ConfigureAwait(false); } catch (OperationCanceledException) { } }
        private async Task ControlLoop()
        {
            while (!stop.IsCancellationRequested)
            {
                if (!Controls.TryPeek(out var item)) { await Delay(10).ConfigureAwait(false); continue; }
                try { item.SessionId = session!.SessionId; await Send(controlClient, HttpMethod.Post, "/v1/bridge/control", item, session.Token).ConfigureAwait(false); Controls.TryTake(out _); Success(); }
                catch (OperationCanceledException) when (stop.IsCancellationRequested) { return; }
                catch (PermanentFailure e) { Controls.TryRemove(item); LastFailure = "control_rejected_" + e.Status; Notices.ReplaceOldest(LastFailure); if (item.Kind != "voice.ptt_started") RequiresReconnect = true; }
                catch (Exception e) { Fail(e); await Delay(200).ConfigureAwait(false); }
            }
        }
        private async Task EventLoop()
        {
            var heartbeatAt = DateTimeOffset.UtcNow;
            while (!stop.IsCancellationRequested)
            {
                try
                {
                    if (Acks.TryPeek(out var ack))
                    {
                        try { await Send(eventClient, HttpMethod.Post, "/v1/bridge/commands/" + Uri.EscapeDataString(ack.CommandId) + "/ack", ack, session!.Token).ConfigureAwait(false); }
                        catch (PermanentFailure e) { LastFailure = "ack_rejected_" + e.Status; Notices.ReplaceOldest(LastFailure); RequiresReconnect = true; online = false; return; }
                        Acks.TryTake(out _); Success(); continue;
                    }
                    var queue = Events.Count > 0 ? Events : Snapshots;
                    if (queue.TryPeek(out var item))
                    {
                        item.SessionId = session!.SessionId;
                        try { await Send(eventClient, HttpMethod.Post, "/v1/bridge/events:batch", new { session_id = session.SessionId, events = new[] { item } }, session.Token).ConfigureAwait(false); }
                        catch (PermanentFailure e) { queue.TryRemove(item); LastFailure = "event_rejected_" + e.Status; Notices.ReplaceOldest(LastFailure); continue; }
                        item.Accepted = true; queue.TryRemove(item); Success(); continue;
                    }
                    if (DateTimeOffset.UtcNow >= heartbeatAt)
                    {
                        await Send(eventClient, HttpMethod.Post, "/v1/bridge/heartbeat", new { session_id = session!.SessionId, runtime_generation = request.RuntimeGeneration, queue_depth = Commands.Count, fps = Fps, actor_count = ActorCount }, session.Token).ConfigureAwait(false);
                        heartbeatAt = DateTimeOffset.UtcNow.AddSeconds(3); Success();
                    }
                    await Delay(15).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (stop.IsCancellationRequested) { return; }
                catch (Exception e) { Fail(e); await Delay(300).ConfigureAwait(false); }
            }
        }
        private async Task PollLoop()
        {
            while (!stop.IsCancellationRequested)
            {
                if (Commands.Remaining < 32 || Acks.Remaining < 128) { await Delay(30).ConfigureAwait(false); continue; }
                try
                {
                    var batch = Wire.Decode<CommandBatch>(await Send(pollClient, HttpMethod.Get, "/v1/bridge/commands?session_id=" + Uri.EscapeDataString(session!.SessionId) + "&after=" + cursor, null, session.Token).ConfigureAwait(false));
                    if (batch.Commands.Count > Commands.Remaining) throw new InvalidDataException("command_batch_overflow");
                    foreach (var command in batch.Commands)
                    {
                        if (!Commands.TryAdd(command)) throw new InvalidDataException("command_queue_full");
                        cursor = Math.Max(cursor, command.Seq);
                    }
                    // Cursor must not skip a command that was not queued.
                    Success(); await Delay(30).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (stop.IsCancellationRequested) { return; }
                catch (Newtonsoft.Json.JsonException)
                {
                    ProtocolFault = true; online = false; LastFailure = "bridge_protocol_failure";
                    Notices.ReplaceOldest("Command schema mismatch. AI stopped. Update the adapter and service together, then reload the adapter.");
                    return;
                }
                catch (Exception e) { Fail(e); await Delay(300).ConfigureAwait(false); }
            }
        }
        public void Dispose()
        {
            stop.Cancel(); online = false;
            var closing = session;
            if (closing != null) _ = Task.Run(async () => {
                try { using (var client = Client(endpoint, null)) { client.Timeout = TimeSpan.FromSeconds(2); client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", closing.Token);
                    using (var content = new StringContent(Wire.Encode(new { runtime_generation = request.RuntimeGeneration }), Encoding.UTF8, "application/json"))
                    using (await client.PostAsync("/v1/bridge/sessions/" + Uri.EscapeDataString(closing.SessionId) + "/close", content).ConfigureAwait(false)) { }
                } } catch (Exception) { /* Abrupt game shutdown may prevent the best-effort close. */ }
            });
            // Never wait for network on the SHVDN script thread.
            _ = run.ContinueWith(_ => { controlClient.Dispose(); eventClient.Dispose(); pollClient.Dispose(); stop.Dispose(); }, TaskScheduler.Default);
        }
    }
}

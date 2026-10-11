using System;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using LivingNPC.Core;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public class BridgeTests
{
    private sealed class Handler : HttpMessageHandler
    {
        private readonly Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send;
        public Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send) { this.send = send; }
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => send(request, token);
    }
    private static HttpResponseMessage Json(string text) => new(HttpStatusCode.OK) { Content = new StringContent(text) };
    [Theory][InlineData(false)][InlineData(true)]
    public void CommandSchemaVersionMustBePresentAndNonNull(bool nullVersion)
    {
        var command = JObject.Parse(Wire.Encode(ControllerTests.Action()));
        if (nullVersion) command["schema_version"] = null; else command.Remove("schema_version");
        Assert.Throws<JsonSerializationException>(() => Wire.Decode<Command>(command.ToString()));
    }
    [Fact] public async Task SchemaMismatchGivesPlayerAnActionableNotice()
    {
        var calls = 0;
        var rejected = new Handler((_, _) => {
            Interlocked.Increment(ref calls);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.UpgradeRequired) { Content = new StringContent("private response body") });
        });
        using var worker = new BridgeWorker(new Uri("http://127.0.0.1:8765"), new SessionRequest { SchemaVersion = 2 }, "", eventHandler: rejected);
        var deadline = DateTime.UtcNow.AddSeconds(2);
        while (worker.Notices.Count == 0 && DateTime.UtcNow < deadline) await Task.Delay(5);
        Assert.True(worker.Notices.TryTake(out var notice));
        Assert.Equal("Protocol version mismatch. Update the Living Los Santos adapter and service together, then reload the adapter.", notice);
        Assert.Equal("session_rejected_426", worker.LastFailure);
        Assert.Null(worker.Session); Assert.False(worker.Online); Assert.Equal(1, calls);
    }
    [Fact] public async Task MissingCommandVersionLatchesSafetyStopAcrossSuccessfulHeartbeat()
    {
        var command = JObject.Parse(Wire.Encode(ControllerTests.Action("unversioned"))); command.Remove("schema_version");
        var heartbeatStarted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var heartbeatRelease = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var heartbeatFinished = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var calls = 0;
        var events = new Handler(async (request, token) => {
            if (request.RequestUri!.AbsolutePath.EndsWith("sessions")) return Json(Wire.Encode(new SessionResponse { SessionId = "ses", Token = "token", PolicyVersion = "p1", SchemaVersion = 1 }));
            heartbeatStarted.TrySetResult(); await heartbeatRelease.Task.WaitAsync(token); heartbeatFinished.TrySetResult(); return Json("{}");
        });
        var poll = new Handler(async (_, _) => {
            await heartbeatStarted.Task; Interlocked.Increment(ref calls);
            return Json(new JObject { ["commands"] = new JArray(command), ["cursor"] = 1 }.ToString());
        });
        var game = new FakeGame(); var controller = ControllerTests.Controller(game);
        controller.Commands.TryAdd(ControllerTests.Action()); controller.Tick(ControllerTests.Now);
        Assert.True(controller.Acks.TryTake(out var started)); Assert.Equal("started", started.Status);
        controller.Commands.TryAdd(ControllerTests.Action("pending"));
        using var worker = new BridgeWorker(new Uri("http://127.0.0.1:8765"), new SessionRequest(), "", eventHandler: events, pollHandler: poll);
        var deadline = DateTime.UtcNow.AddSeconds(2);
        while (!worker.ProtocolFault && DateTime.UtcNow < deadline) await Task.Delay(5);
        Assert.True(worker.ProtocolFault); Assert.False(worker.Online); Assert.Equal(0, worker.Commands.Count);
        // The script forwards the worker fault on its tick, even while offline.
        controller.StopForProtocolFault();
        Assert.True(controller.FailedClosed); Assert.Equal(1, game.Releases); Assert.Equal("error", game.Status);
        for (var n = 0; n < 2; n++) { Assert.True(controller.Acks.TryTake(out var canceled)); Assert.Equal("safety_stop", canceled.Reason); }
        heartbeatRelease.TrySetResult(); await heartbeatFinished.Task.WaitAsync(TimeSpan.FromSeconds(2)); await Task.Delay(50);
        Assert.False(worker.Online); Assert.True(worker.ProtocolFault); Assert.Equal(1, calls);
        controller.Commands.TryAdd(ControllerTests.Action("later")); controller.Tick(ControllerTests.Now.AddSeconds(1));
        Assert.True(controller.Acks.TryTake(out var later)); Assert.Equal("safety_stop", later.Reason); Assert.Equal(1, game.Starts);
    }
    [Fact] public async Task SessionCapabilitiesCannotExceedTheAdaptersOffer()
    {
        var events = new Handler((request, _) => Task.FromResult(request.RequestUri!.AbsolutePath.EndsWith("sessions")
            ? Json(Wire.Encode(new SessionResponse { SessionId = "ses", Token = "token", PolicyVersion = "p1", SchemaVersion = 1, Capabilities = new[] { "face_player", "wait_here" }, CapabilityFlags = new[] { "subtitles" } }))
            : Json("{}")));
        var poll = new Handler(async (_, token) => { await Task.Delay(Timeout.Infinite, token); return Json("{}"); });
        using var worker = new BridgeWorker(new Uri("http://127.0.0.1:8765"), new SessionRequest { Capabilities = new[] { "face_player", "follow_player" } }, "", eventHandler: events, pollHandler: poll);
        var deadline = DateTime.UtcNow.AddSeconds(2);
        while (worker.Session == null && DateTime.UtcNow < deadline) await Task.Delay(5);
        Assert.NotNull(worker.Session);
        Assert.Equal(new[] { "face_player" }, worker.Session.Capabilities);
        Assert.Equal(new[] { "subtitles" }, worker.Session.CapabilityFlags);
    }
    [Fact]
    public async Task PriorityControlDoesNotWaitForEventOrCommandPoll()
    {
        var eventBlocked = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var pollBlocked = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var controlSent = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        var eventHandler = new Handler(async (request, token) => {
            if (request.RequestUri!.AbsolutePath.EndsWith("sessions")) return Json("{\"session_id\":\"ses\",\"token\":\"token\",\"policy_version\":\"p1\",\"schema_version\":1,\"branch_id\":\"branch\",\"capabilities\":[]}");
            eventBlocked.TrySetResult(); await Task.Delay(Timeout.Infinite, token); return Json("{}");
        });
        var pollHandler = new Handler(async (_, token) => { pollBlocked.TrySetResult(); await Task.Delay(Timeout.Infinite, token); return Json("{}"); });
        var controlHandler = new Handler(async (request, _) => { controlSent.TrySetResult(await request.Content!.ReadAsStringAsync()); return Json("{}"); });
        using var worker = new BridgeWorker(new Uri("http://127.0.0.1:8765"), new SessionRequest(), "", controlHandler, eventHandler, pollHandler);
        worker.Events.TryAdd(new EventEnvelope { Kind = "actor.snapshot" });
        await Task.WhenAll(eventBlocked.Task, pollBlocked.Task).WaitAsync(TimeSpan.FromSeconds(3));
        worker.Controls.TryAdd(new EventEnvelope { Kind = "voice.ptt_started" });
        var body = await controlSent.Task.WaitAsync(TimeSpan.FromSeconds(1)); Assert.Contains("voice.ptt_started", body); Assert.Contains("\"session_id\":\"ses\"", body);
    }
    [Fact] public async Task PermanentEventRejectionDoesNotBlockLaterObservations()
    {
        var goodSent = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var rejected = 0;
        var events = new Handler(async (request, _) => {
            if (request.RequestUri!.AbsolutePath.EndsWith("sessions")) return Json("{\"session_id\":\"ses\",\"token\":\"token\",\"policy_version\":\"p1\",\"schema_version\":1,\"branch_id\":\"branch\",\"capabilities\":[]}");
            var body = await request.Content!.ReadAsStringAsync();
            if (body.Contains("invalid-event")) { Interlocked.Increment(ref rejected); return new HttpResponseMessage(HttpStatusCode.UnprocessableEntity) { Content = new StringContent("{}") }; }
            if (body.Contains("good-event")) goodSent.TrySetResult();
            return Json("{}");
        });
        var poll = new Handler(async (_, token) => { await Task.Delay(Timeout.Infinite, token); return Json("{}"); });
        using var worker = new BridgeWorker(new Uri("http://127.0.0.1:8765"), new SessionRequest(), "", new Handler((_, _) => Task.FromResult(Json("{}"))), events, poll);
        var bad = new EventEnvelope { EventId = "invalid-event" }; var good = new EventEnvelope { EventId = "good-event" };
        worker.Events.TryAdd(bad); worker.Events.TryAdd(good);
        await goodSent.Task.WaitAsync(TimeSpan.FromSeconds(2)); Assert.Equal(1, rejected); Assert.True(worker.Notices.TryTake(out var notice)); Assert.Equal("event_rejected_422", notice);
        var deadline = DateTime.UtcNow.AddSeconds(2);
        while (!good.Accepted && DateTime.UtcNow < deadline) await Task.Delay(5);
        Assert.True(good.Accepted); Assert.False(bad.Accepted);
        Assert.DoesNotContain("accepted", Wire.Encode(good));
    }
    [Fact] public void PythonWireFixtureDecodesActorBindingAndUtcExpiry()
    {
        var batch = Wire.Decode<CommandBatch>("{\"commands\":[{\"schema_version\":1,\"id\":\"cmd\",\"seq\":1,\"kind\":\"action.execute\",\"session_id\":\"ses\",\"actor_id\":\"npc\",\"runtime_instance_id\":\"instance-1\",\"runtime_generation\":7,\"expires_at\":\"2026-10-10T12:00:10+00:00\",\"idempotency_key\":\"effect\",\"payload\":{}}],\"cursor\":1}");
        Assert.Equal("instance-1", Assert.Single(batch.Commands).RuntimeInstanceId);
        Assert.Equal(TimeSpan.Zero, batch.Commands[0].ExpiresAt.Offset);
    }
    [Fact] public async Task AssociationConflictStopsWithoutExposingResponseBody()
    {
        var calls = 0;
        var rejected = new Handler((_, _) => {
            Interlocked.Increment(ref calls);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("private response body") });
        });
        using var worker = new BridgeWorker(new Uri("http://127.0.0.1:8765"), new SessionRequest(), "", eventHandler: rejected);
        var deadline = DateTime.UtcNow.AddSeconds(2);
        while (worker.Notices.Count == 0 && DateTime.UtcNow < deadline) await Task.Delay(5);
        Assert.True(worker.Notices.TryTake(out var notice));
        Assert.Contains("Save/profile association refused", notice);
        Assert.Contains("ProfileId, SaveSlot and ParentBranchId", notice);
        Assert.DoesNotContain("private response body", notice);
        Assert.Equal("session_rejected_409", worker.LastFailure);
        Assert.Null(worker.Session); Assert.False(worker.Online); Assert.False(worker.RequiresReconnect);
        Assert.Equal(1, calls);
    }
}

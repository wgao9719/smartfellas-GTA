using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using LivingNPC.Core;
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
    [Fact] public void SharedBridgeLimitsMatchTheWireAndQueueBudget()
    {
        Assert.Equal(256 * 1024, BridgeLimits.MaxBodyBytes);
        Assert.Equal(64, BridgeLimits.MaxCommands);
    }
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task HeartbeatDoesNotWaitForEventOrAcknowledgment(bool acknowledgment)
    {
        var start = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var eventBlocked = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var heartbeatSent = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        var events = new Handler(async (request, token) => {
            if (request.RequestUri!.AbsolutePath.EndsWith("sessions"))
            {
                await start.Task.WaitAsync(token);
                return Json("{\"session_id\":\"ses\",\"token\":\"token\",\"policy_version\":\"p1\",\"schema_version\":1,\"branch_id\":\"branch\",\"capabilities\":[]}");
            }
            if (request.RequestUri.AbsolutePath.EndsWith("heartbeat"))
            {
                await eventBlocked.Task.WaitAsync(token);
                heartbeatSent.TrySetResult(await request.Content!.ReadAsStringAsync(token));
                return Json("{}");
            }
            eventBlocked.TrySetResult(); await Task.Delay(Timeout.Infinite, token); return Json("{}");
        });
        var poll = new Handler(async (_, token) => { await Task.Delay(Timeout.Infinite, token); return Json("{}"); });
        using var worker = new BridgeWorker(new Uri("http://127.0.0.1:8765"), new SessionRequest { RuntimeGeneration = 7 }, "", new Handler((_, _) => Task.FromResult(Json("{}"))), events, poll);
        if (acknowledgment) worker.Acks.TryAdd(new Ack { CommandId = "cmd" });
        else worker.Events.TryAdd(new EventEnvelope { Kind = "actor.snapshot" });
        start.SetResult();
        await eventBlocked.Task.WaitAsync(TimeSpan.FromSeconds(2));
        var body = await heartbeatSent.Task.WaitAsync(TimeSpan.FromSeconds(2));
        Assert.Contains("\"session_id\":\"ses\"", body); Assert.Contains("\"runtime_generation\":7", body);
        Assert.Equal(1, acknowledgment ? worker.Acks.Count : worker.Events.Count);
    }
    [Fact] public async Task PaginatedBacklogWaitsForCapacityAndPreservesCommandCursor()
    {
        var secondPoll = new TaskCompletionSource<(string Query, int Remaining)>(TaskCreationOptions.RunContinuationsAsynchronously);
        var finalPoll = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        var start = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var events = new Handler(async (request, token) => {
            if (request.RequestUri!.AbsolutePath.EndsWith("sessions"))
            {
                await start.Task.WaitAsync(token);
                return Json("{\"session_id\":\"ses\",\"token\":\"token\",\"policy_version\":\"p1\",\"schema_version\":1,\"branch_id\":\"branch\",\"capabilities\":[]}");
            }
            return Json("{}");
        });
        BridgeWorker? worker = null;
        var polls = 0;
        var poll = new Handler(async (request, token) => {
            switch (Interlocked.Increment(ref polls))
            {
                case 1: return Json(Wire.Encode(new CommandBatch { Commands = Enumerable.Range(1, 32).Select(seq => new Command { Id = "cmd" + seq, Seq = seq }).ToList(), Cursor = 96 }));
                case 2:
                    secondPoll.TrySetResult((request.RequestUri!.Query, worker!.Commands.Remaining));
                    return Json(Wire.Encode(new CommandBatch { Commands = Enumerable.Range(33, 64).Select(seq => new Command { Id = "cmd" + seq, Seq = seq }).ToList(), Cursor = 96 }));
                default: finalPoll.TrySetResult(request.RequestUri!.Query); await Task.Delay(Timeout.Infinite, token); return Json("{}");
            }
        });
        using (worker = new BridgeWorker(new Uri("http://127.0.0.1:8765"), new SessionRequest(), "", new Handler((_, _) => Task.FromResult(Json("{}"))), events, poll))
        {
            start.SetResult();
            var deadline = DateTime.UtcNow.AddSeconds(2);
            while (worker.Commands.Count != 32 && DateTime.UtcNow < deadline) await Task.Delay(5);
            Assert.Equal(32, worker.Commands.Count);
            await Task.Delay(200);
            Assert.False(secondPoll.Task.IsCompleted, "A poll must reserve space for the largest valid page.");
            var delivered = new List<long>();
            for (var i = 0; i < 32; i++)
            {
                Assert.True(worker.Commands.TryTake(out var command)); delivered.Add(command.Seq);
            }
            var next = await secondPoll.Task.WaitAsync(TimeSpan.FromSeconds(2));
            Assert.Contains("after=32", next.Query); Assert.Equal(64, next.Remaining);
            deadline = DateTime.UtcNow.AddSeconds(2);
            while (worker.Commands.Count != 64 && DateTime.UtcNow < deadline) await Task.Delay(5);
            Assert.Equal(64, worker.Commands.Count);
            while (worker.Commands.TryTake(out var command)) delivered.Add(command.Seq);
            Assert.Equal(Enumerable.Range(1, 96).Select(seq => (long)seq), delivered);
            Assert.Contains("after=96", await finalPoll.Task.WaitAsync(TimeSpan.FromSeconds(2)));
            Assert.Equal("", worker.LastFailure);
        }
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
        var batch = Wire.Decode<CommandBatch>("{\"commands\":[{\"id\":\"cmd\",\"seq\":1,\"kind\":\"action.execute\",\"session_id\":\"ses\",\"actor_id\":\"npc\",\"runtime_instance_id\":\"instance-1\",\"runtime_generation\":7,\"expires_at\":\"2026-10-10T12:00:10+00:00\",\"idempotency_key\":\"effect\",\"payload\":{}}],\"cursor\":1}");
        Assert.Equal("instance-1", Assert.Single(batch.Commands).RuntimeInstanceId);
        Assert.Equal(TimeSpan.Zero, batch.Commands[0].ExpiresAt.Offset);
    }
}

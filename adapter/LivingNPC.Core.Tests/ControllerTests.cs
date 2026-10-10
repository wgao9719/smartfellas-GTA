using System;
using System.Collections.Generic;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class FakeGame : IGameAdapter
{
    public WorldState World = new() { ProtagonistId = "franklin", GameDatetime = new DateTime(2013, 9, 17) };
    public ActorState Actor = new() { ActorId = "npc", RuntimeInstanceId = "instance-1", Generation = 7, Exists = true, Alive = true, Distance = 3, Ownership = Ownership.Ambient, Policy = ControlPolicy.Ambient, CanMove = true };
    public int Starts, Releases, Subtitles;
    public int SubtitleClears;
    public string LastSubtitle = "";
    public bool SubtitleRendered { get; set; } = true;
    public void Forget(Command command) { }
    public string Status = "";
    public EffectObservation Observation = new() { Status = EffectStatus.Running };
    public WorldState ReadWorld() => World;
    public ActorState? Resolve(string actorId) => actorId == Actor.ActorId ? Actor : null;
    public bool Start(Command command, ActorState actor, string actionId, JObject arguments) { Starts++; return true; }
    public EffectObservation Observe(Command command, ActorState actor, string actionId) => Observation;
    public void Release(Command command, ActorState actor, string actionId) => Releases++;
    public void ShowSubtitle(string speaker, string text) { Subtitles++; LastSubtitle = text; }
    public void ClearSubtitle() { SubtitleClears++; LastSubtitle = ""; }
    public void ShowStatus(string state, string message) => Status = state;
}

public class ControllerTests
{
    public static readonly DateTimeOffset Now = DateTimeOffset.Parse("2026-10-10T12:00:00Z");
    public static Command Action(string id = "cmd1") => new() { Id = id, Seq = 1, SessionId = "ses", ActorId = "npc", RuntimeInstanceId = "instance-1", RuntimeGeneration = 7, Kind = "action.execute", IdempotencyKey = id, ExpiresAt = Now.AddSeconds(10), Payload = JObject.FromObject(new { action_id = "face_player", arguments = new { duration_seconds = 1.5 }, policy_version = "p1", scene_id = "scene-1", consent = false }) };
    public static CommandController Controller(FakeGame game) => new(game, "ses", 7, "p1", new[] { "face_player", "wait_here" });
    private static List<Ack> Drain(CommandController controller) { var acks = new List<Ack>(); while (controller.Acks.TryTake(out var ack)) acks.Add(ack); return acks; }

    [Fact] public void DispatchIsNotCompletion()
    {
        var game = new FakeGame(); var controller = Controller(game); controller.Commands.TryAdd(Action()); controller.Tick(Now);
        Assert.Equal("started", Assert.Single(Drain(controller)).Status);
        controller.Tick(Now.AddMilliseconds(100)); Assert.Empty(Drain(controller));
        game.Observation = new EffectObservation { Status = EffectStatus.Completed, Reason = "heading_observed", Facts = new() { ["heading_error_degrees"] = 3 } };
        controller.Tick(Now.AddMilliseconds(500)); var completed = Assert.Single(Drain(controller));
        Assert.Equal("completed", completed.Status); Assert.Equal(3, completed.Observed!["heading_error_degrees"]);
    }
    [Theory]
    [InlineData("online")][InlineData("mission")][InlineData("cutscene")][InlineData("random_event")][InlineData("paused")][InlineData("unknown_world")]
    [InlineData("dead")][InlineData("missing")][InlineData("far")][InlineData("nan_distance")][InlineData("busy")][InlineData("combat")][InlineData("protected")][InlineData("script_owned")][InlineData("unknown_owner")][InlineData("rebound")]
    public void CurrentStateBlocksDispatch(string unsafeState)
    {
        var game = new FakeGame(); var controller = Controller(game);
        switch (unsafeState)
        {
            case "online": game.World.Online = true; break;
            case "mission": game.World.MissionActive = true; break;
            case "cutscene": game.World.CutsceneActive = true; break;
            case "random_event": game.World.RandomEventActive = true; break;
            case "paused": game.World.Paused = true; break;
            case "unknown_world": game.World.Known = false; break;
            case "dead": game.Actor.Alive = false; break;
            case "missing": game.Actor.Exists = false; break;
            case "far": game.Actor.Distance = 20; break;
            case "nan_distance": game.Actor.Distance = float.NaN; break;
            case "busy": game.Actor.Busy = true; break;
            case "combat": game.Actor.InCombat = true; break;
            case "protected": game.Actor.Policy = ControlPolicy.Protected; break;
            case "script_owned": game.Actor.Ownership = Ownership.GameScriptOwned; break;
            case "unknown_owner": game.Actor.Ownership = Ownership.Unknown; break;
            case "rebound": game.Actor.RuntimeInstanceId = "recycled-handle"; break;
        }
        controller.Commands.TryAdd(Action()); controller.Tick(Now);
        Assert.Equal(0, game.Starts); Assert.Equal("rejected", Assert.Single(Drain(controller)).Status);
    }
    [Fact] public void ModOwnedPersistentEntityRemainsEligible()
    {
        var game = new FakeGame(); game.Actor.Ownership = Ownership.ModOwned; game.Actor.MissionEntity = true;
        var controller = Controller(game); controller.Commands.TryAdd(Action()); controller.Tick(Now); Assert.Equal(1, game.Starts);
    }
    [Theory][InlineData("generation")][InlineData("session")][InlineData("expired")][InlineData("policy")][InlineData("arbitrary_native")][InlineData("extra_argument")][InlineData("unbounded_duration")]
    public void InvalidAuthorizationCannotExecute(string invalid)
    {
        var game = new FakeGame(); var controller = Controller(game); var command = Action();
        switch (invalid)
        {
            case "generation": command.RuntimeGeneration = 6; break;
            case "session": command.SessionId = "other"; break;
            case "expired": command.ExpiresAt = Now; break;
            case "policy": command.Payload["policy_version"] = "other"; break;
            case "arbitrary_native": command.Payload["action_id"] = "0xDEADBEEF"; break;
            case "extra_argument": ((JObject)command.Payload["arguments"]!)["native_hash"] = "0x123"; break;
            case "unbounded_duration": ((JObject)command.Payload["arguments"]!)["duration_seconds"] = 10000; break;
        }
        controller.Commands.TryAdd(command); controller.Tick(Now); Assert.Equal(0, game.Starts); Assert.NotEqual("started", Assert.Single(Drain(controller)).Status);
    }
    [Fact] public void ReplayReturnsPreviousResultWithoutNativeExecution()
    {
        var game = new FakeGame(); var controller = Controller(game); controller.Commands.TryAdd(Action()); controller.Tick(Now); Drain(controller);
        var duplicate = Action("cmd-duplicate"); duplicate.IdempotencyKey = "cmd1";
        controller.Commands.TryAdd(duplicate); controller.Tick(Now.AddMilliseconds(100));
        Assert.Equal(1, game.Starts); Assert.Equal("started", Assert.Single(Drain(controller)).Status);
    }
    [Fact] public void MissionTakeoverDoesNotClearEngineTask()
    {
        var game = new FakeGame(); var controller = Controller(game); controller.Commands.TryAdd(Action()); controller.Tick(Now); Drain(controller);
        game.World.MissionActive = true; controller.Tick(Now.AddSeconds(1));
        Assert.Equal(0, game.Releases); Assert.Equal("canceled", Assert.Single(Drain(controller)).Status);
    }
    [Fact] public void HandleRebindDoesNotClearReplacementTask()
    {
        var game = new FakeGame(); var controller = Controller(game); controller.Commands.TryAdd(Action()); controller.Tick(Now); Drain(controller);
        game.Actor.RuntimeInstanceId = "instance-2"; controller.Tick(Now.AddSeconds(1));
        Assert.Equal(0, game.Releases); Assert.Equal("canceled", Assert.Single(Drain(controller)).Status);
    }
    [Fact] public void CancelBlocksLateActionButAllowsNewScene()
    {
        var game = new FakeGame(); var controller = Controller(game); controller.Cancel("scene-1");
        controller.Commands.TryAdd(Action()); controller.Tick(Now); Assert.Equal(0, game.Starts); Assert.Equal("canceled", Assert.Single(Drain(controller)).Status);
        var next = Action("new"); next.Payload["scene_id"] = "scene-2"; controller.Commands.TryAdd(next); controller.Tick(Now); Assert.Equal(1, game.Starts);
    }
    [Fact] public void FullAckQueueBackpressuresNativeDispatch()
    {
        var game = new FakeGame(); var controller = Controller(game);
        for (var n = 0; n < controller.Acks.Capacity; n++) Assert.True(controller.Acks.TryAdd(new Ack()));
        controller.Commands.TryAdd(Action()); controller.Tick(Now); Assert.Equal(0, game.Starts); Assert.Equal(1, controller.Commands.Count);
    }
    [Fact] public void ConflictingTasksAreRefused()
    {
        var game = new FakeGame(); var controller = Controller(game); controller.Commands.TryAdd(Action()); controller.Tick(Now); Drain(controller);
        var second = Action("second"); second.Payload["action_id"] = "wait_here"; controller.Commands.TryAdd(second); controller.Tick(Now.AddMilliseconds(200));
        Assert.Equal(1, game.Starts); Assert.Equal("actor_task_conflict", Assert.Single(Drain(controller)).Reason);
    }
    [Fact] public void SubtitleChunksWaitForActualRenderAndTheirOwnDisplayInterval()
    {
        var game = new FakeGame { SubtitleRendered = false }; var controller = Controller(game);
        Command Chunk(int n) { var command = Action("text-" + n); command.Kind = "dialogue.chunk"; command.Payload = JObject.FromObject(new { text = "Sentence " + n, speaker = "Resident", turn_id = "turn", chunk_index = n, scene_id = "scene-1", final = false }); return command; }
        controller.Commands.TryAdd(Chunk(0)); controller.Commands.TryAdd(Chunk(1)); controller.Tick(Now);
        Assert.Equal(1, game.Subtitles); Assert.Empty(Drain(controller));
        controller.Tick(Now.AddMilliseconds(100)); Assert.Empty(Drain(controller));
        game.SubtitleRendered = true; controller.Tick(Now.AddMilliseconds(200));
        Assert.Equal("Sentence 0", Assert.Single(Drain(controller)).Observed!["text"]);
        Assert.Equal(1, game.Subtitles);
        game.SubtitleRendered = false; controller.Tick(Now.AddSeconds(3)); Assert.Equal(2, game.Subtitles); Assert.Empty(Drain(controller));
        controller.Cancel("scene-1"); Assert.Equal("canceled", Assert.Single(Drain(controller)).Status);
    }
    [Fact] public void LongSubtitleQueueCompletesWithinSeparateSpeechWindow()
    {
        var game = new FakeGame { SubtitleRendered = true }; var controller = Controller(game);
        for (var n = 0; n < 3; n++)
        {
            var command = Action("long-text-" + n); command.Kind = "dialogue.chunk"; command.ExpiresAt = Now.AddSeconds(120);
            command.Payload = JObject.FromObject(new { text = new string((char)('a' + n), 180), speaker = "Resident", turn_id = "long-turn", chunk_index = n, scene_id = "long-scene" });
            controller.Commands.TryAdd(command);
        }
        var delivered = new List<Ack>();
        for (var second = 0; second <= 46; second++) { controller.Tick(Now.AddSeconds(second)); delivered.AddRange(Drain(controller)); }
        Assert.Equal(3, game.Subtitles); Assert.Equal(3, delivered.Count); Assert.All(delivered, a => Assert.Equal("completed", a.Status));
        var native = Action("native-too-long"); native.ExpiresAt = Now.AddSeconds(120); controller.Commands.TryAdd(native); controller.Tick(Now);
        Assert.Equal("authorization_too_long", Assert.Single(Drain(controller)).Reason);
    }
    [Fact] public void CancelImmediatelyAfterRenderPreservesOnlyShownChunk()
    {
        var game = new FakeGame { SubtitleRendered = true }; var controller = Controller(game);
        for (var n = 0; n < 2; n++)
        {
            var command = Action("cancel-text-" + n); command.Kind = "dialogue.chunk";
            command.Payload = JObject.FromObject(new { text = "Chunk " + n, speaker = "Resident", turn_id = "turn", chunk_index = n, scene_id = "scene-1" }); controller.Commands.TryAdd(command);
        }
        controller.Tick(Now); controller.Cancel("scene-1"); var acks = Drain(controller);
        Assert.Equal(1, game.Subtitles); Assert.Equal(2, acks.Count);
        Assert.Equal("Chunk 0", acks[0].Observed!["text"]); Assert.Null(acks[1].Observed);
        Assert.All(acks, a => Assert.Equal("canceled", a.Status));
    }
    [Fact] public void CancellationTombstonesOutliveAllValidOldSpeechThenExpire()
    {
        var game = new FakeGame(); var controller = Controller(game); controller.Cancel("scene-1", Now);
        var command = Action("late-speech"); command.Kind = "dialogue.chunk"; command.ExpiresAt = Now.AddSeconds(120);
        command.Payload = JObject.FromObject(new { text = "Late", speaker = "Resident", turn_id = "turn", chunk_index = 0, scene_id = "scene-1" });
        controller.Commands.TryAdd(command); controller.Tick(Now.AddSeconds(119));
        Assert.Equal("canceled", Assert.Single(Drain(controller)).Status); Assert.Equal(1, controller.CanceledSceneCount); Assert.Equal(0, game.Subtitles);
        controller.Tick(Now.AddSeconds(126)); Assert.Equal(0, controller.CanceledSceneCount);
        controller.Commands.TryAdd(command); controller.Tick(Now.AddSeconds(127));
        Assert.Equal(0, game.Subtitles); Assert.Equal("canceled", Assert.Single(Drain(controller)).Status);
    }
    [Fact] public void ChunkHistoryExpiresAfterItsCommandWindow()
    {
        var game = new FakeGame(); var controller = Controller(game); var command = Action("speech-history"); command.Kind = "dialogue.chunk";
        command.Payload = JObject.FromObject(new { text = "Hello", speaker = "Resident", turn_id = "turn", chunk_index = 0, scene_id = "scene-1" });
        controller.Commands.TryAdd(command); controller.Tick(Now); Assert.Equal(1, controller.TrackedTurnCount);
        controller.Tick(Now.AddSeconds(11)); Assert.Equal(0, controller.TrackedTurnCount);
    }
    [Fact] public void CancellationStateSaturationFailsClosedInsteadOfEvictingLiveTombstones()
    {
        var game = new FakeGame(); var controller = Controller(game);
        for (var n = 0; n < 4097; n++) controller.Cancel("scene" + n, Now);
        Assert.Equal(4096, controller.CanceledSceneCount); Assert.True(controller.FailedClosed);
        controller.Commands.TryAdd(Action()); controller.Tick(Now); Assert.Equal(0, game.Starts);
    }
    [Fact] public void SnapshotDropDoesNotRemoveDifferentInFlightItem()
    {
        var queue = new BoundedQueue<string>(1); queue.TryAdd("old"); queue.ReplaceOldest("new");
        Assert.False(queue.TryRemove("old")); Assert.True(queue.TryTake(out var value)); Assert.Equal("new", value);
    }
}

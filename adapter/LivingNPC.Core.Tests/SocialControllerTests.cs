using System;
using System.Collections.Generic;
using System.Linq;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class SocialGame : ISocialGameAdapter
{
    public readonly WorldState World = new() { ProtagonistId = "franklin" };
    public readonly ActorState[] Actors = Enumerable.Range(0, 3).Select(i => new ActorState { ActorId = "npc-" + i, RuntimeInstanceId = "ped-" + i, Generation = 1, Exists = true, Alive = true, Distance = 2 + i, X = i, Role = PedRole.Civilian, Policy = ControlPolicy.Ambient, Ownership = Ownership.Ambient, AreaId = "block", InteriorId = "0" }).ToArray();
    public int Starts, Ends, Lines, Voices, PlayerSubtitles;
    public bool Ready = true, Valid = true;
    public SocialLineObservation? Observation;
    public WorldState ReadWorld() => World;
    public ActorState? Resolve(string actorId) => Actors.FirstOrDefault(a => a.ActorId == actorId);
    public bool BeginSocial(string id, ActorState[] actors, double seconds) { Starts++; return true; }
    public bool SocialReady(string id) => Ready;
    public bool SocialValid(string id) => Valid;
    public void EndSocial(string id) { Ends++; }
    public void ShowSocialLine(string scene, string command, string actor, string text) { Lines++; }
    public SocialLineObservation? ObserveSocialLine(string commandId) => Observation;
    public void ClearSocialLine(string commandId) { }
    public void AmbientSpeech(string actorId, string context) { Voices++; }
    public bool Start(Command command, ActorState actor, string actionId, JObject arguments) => false;
    public EffectObservation Observe(Command command, ActorState actor, string actionId) => new() { Status = EffectStatus.Failed };
    public void Release(Command command, ActorState actor, string actionId) { }
    public void Forget(Command command) { }
    public bool SubtitleRendered => true;
    public void ShowSubtitle(string speaker, string text) { PlayerSubtitles++; }
    public void ClearSubtitle() { }
    public void ShowStatus(string state, string message) { }
    public bool Pin(Command command, ActorState actor, string name) => false;
}

public class SocialControllerTests
{
    static readonly DateTimeOffset Now = DateTimeOffset.UtcNow;
    static Command Scene(int count = 2) => new() { Id = "setup", IdempotencyKey = "setup", Kind = "social.scene", SessionId = "session", RuntimeGeneration = 1, ExpiresAt = Now.AddSeconds(15), Payload = JObject.FromObject(new { scene_id = "social", duration_seconds = 45, participants = Enumerable.Range(0, count).ToDictionary(i => "npc-" + i, i => "ped-" + i) }) };
    static Command Line() => new() { Id = "line", IdempotencyKey = "line", Kind = "dialogue.chunk", SessionId = "session", RuntimeGeneration = 1, ExpiresAt = Now.AddSeconds(120), ActorId = "npc-0", RuntimeInstanceId = "ped-0", Payload = JObject.FromObject(new { scene_id = "social", text = "Hello there.", presentation = "overhead" }) };

    [Theory]
    [InlineData("police")][InlineData("mission")][InlineData("vehicle")][InlineData("combat")][InlineData("far")][InlineData("binding")][InlineData("interior")][InlineData("following")]
    public void RefusesUnavailableParticipantsBeforeNativeDispatch(string change)
    {
        var game = new SocialGame(); var actor = game.Actors[1]; var controller = new SocialController(game, "session", 1);
        switch (change)
        {
            case "police": actor.Role = PedRole.Police; break;
            case "mission": actor.MissionEntity = true; break;
            case "vehicle": actor.InVehicle = true; break;
            case "combat": actor.InCombat = true; break;
            case "far": actor.X = 7; break;
            case "binding": actor.RuntimeInstanceId = "replacement"; break;
            case "interior": actor.InteriorId = "different"; break;
            case "following": actor.ModFollowing = true; break;
        }
        controller.Handle(Scene(), Now);
        Assert.Equal(0, game.Starts); Assert.True(controller.Acks.TryTake(out var ack)); Assert.Equal("rejected", ack.Status);
    }
    [Fact] public void OverheadNeedsObservedDisplayAndKeepsForegroundSlotFree()
    {
        var game = new SocialGame(); var controller = new SocialController(game, "session", 1);
        controller.Handle(Scene(), Now); controller.Acks.Clear(); controller.Handle(Line(), Now); controller.Tick(Now);
        Assert.Equal(1, game.Lines); Assert.Equal(0, controller.Deliveries.Count); Assert.Equal(0, controller.Acks.Count); Assert.Equal(0, game.PlayerSubtitles);
        game.Observation = new SocialLineObservation { DisplayedAt = Now.AddMilliseconds(20), World = game.World, Actors = game.Actors.Take(2).ToArray(), Witnesses = new object[] { new { actor_id = "npc-1" } } };
        controller.Tick(Now.AddMilliseconds(20));
        Assert.True(controller.Deliveries.TryTake(out var delivered)); Assert.Equal("line", delivered.Item1.Id); Assert.Equal(game.Observation.DisplayedAt, delivered.Item2.DisplayedAt);
        controller.Handle(Line(), Now.AddMilliseconds(30)); Assert.True(controller.Acks.TryTake(out var replay)); Assert.Equal("completed", replay.Status);
        Assert.Equal(0, controller.Deliveries.Count); Assert.Equal(1, game.Lines);
    }
    [Fact] public void GlobalCancellationPreventsLateSetupFromRestartingScene()
    {
        var game = new SocialGame(); var controller = new SocialController(game, "session", 1);
        controller.Handle(Scene(), Now); controller.Cancel(); controller.Acks.Clear();
        var late = Scene(); late.Id = "late"; late.IdempotencyKey = "late";
        controller.Handle(late, Now);
        Assert.Equal(1, game.Starts); Assert.Equal(0, controller.ActiveCount);
        Assert.True(controller.Acks.TryTake(out var ack)); Assert.Equal("rejected", ack.Status);
    }
    [Fact] public void LostNativeTaskCancelsOnlyItsScene()
    {
        var game = new SocialGame(); var controller = new SocialController(game, "session", 1);
        controller.Handle(Scene(), Now); controller.Handle(Line(), Now); controller.Acks.Clear(); game.Valid = false; controller.Tick(Now);
        Assert.Equal(0, controller.ActiveCount); Assert.Equal(1, game.Ends); Assert.True(controller.Cancellations.TryTake(out var scene)); Assert.Equal("social", scene);
        Assert.True(controller.Acks.TryTake(out var canceled)); Assert.Equal("canceled", canceled.Status); Assert.Equal(0, game.PlayerSubtitles);
    }
    [Fact] public void ThreePersonScenesStillRequireDistinctBindings()
    {
        var game = new SocialGame(); var controller = new SocialController(game, "session", 1);
        var setup = Scene(3); setup.Payload["participants"]!["npc-2"] = "ped-1";
        controller.Handle(setup, Now); Assert.Equal(0, game.Starts);
        setup.Id = "valid"; setup.Payload["participants"]!["npc-2"] = "ped-2";
        controller.Handle(setup, Now); Assert.Equal(1, game.Starts); Assert.Equal(3, controller.ActiveActors.Length);
    }
    [Fact] public void AmbientBandUsesNativeVoiceWithoutMemoryAndYieldsOnApproach()
    {
        var game = new SocialGame(); var controller = new SocialController(game, "session", 1);
        foreach (var actor in game.Actors) actor.Distance = 20;
        Assert.True(controller.StartAmbient("ambient", game.Actors.Take(2).ToArray(), Now)); controller.Tick(Now);
        Assert.Equal(1, game.Voices); Assert.Equal(0, controller.Deliveries.Count); Assert.Equal(0, controller.Acks.Count);
        game.Actors[0].Distance = 10; controller.Tick(Now.AddSeconds(1));
        Assert.Equal(0, controller.ActiveCount); Assert.Equal(1, game.Ends); Assert.Equal(0, controller.Cancellations.Count);
    }
    [Fact] public void ExpiryAndMissingHearingNeverProduceAReceipt()
    {
        var game = new SocialGame(); var controller = new SocialController(game, "session", 1);
        controller.Handle(Scene(), Now); controller.Handle(Line(), Now); controller.Tick(Now);
        game.Observation = new SocialLineObservation { DisplayedAt = Now, World = game.World };
        controller.Tick(Now.AddSeconds(1));
        Assert.Equal(0, controller.Deliveries.Count); Assert.Equal(0, controller.ActiveCount);
    }
}

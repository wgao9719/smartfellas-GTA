using System;
using System.Collections.Generic;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class SpeechControllerTests
{
    private static Command Audio(int index, int? duration = null)
    {
        var command = ControllerTests.Action("audio-" + index); command.Kind = "dialogue.chunk";
        command.Payload = JObject.FromObject(new { text = "Audio " + index, speaker = "Neighbor", scene_id = "scene-1", turn_id = "voice-turn", chunk_index = index, final = false, delivery_mode = "audio" });
        if (duration.HasValue) command.Payload["audio_duration_ms"] = duration.Value;
        return command;
    }
    private static Command Status(string state)
    {
        var command = ControllerTests.Action("status-" + state); command.Kind = "status";
        command.Payload = JObject.FromObject(new { state, message = state, scene_id = "scene-1" }); return command;
    }
    private static List<Ack> Drain(CommandController controller)
    { var values = new List<Ack>(); while (controller.Acks.TryTake(out var ack)) values.Add(ack); return values; }

    [Fact] public void RecordingStatusRequiresCurrentActorInstance()
    {
        var game = new FakeGame(); var controller = ControllerTests.Controller(game);
        game.Actor.RuntimeInstanceId = "replacement"; controller.Commands.TryAdd(Status("recording"));
        controller.Tick(ControllerTests.Now);
        Assert.NotEqual("recording", game.Status); Assert.Equal("stale_actor_binding", Assert.Single(Drain(controller)).Reason);
    }

    [Fact] public void CanceledSceneCannotRestoreRecordingIndicator()
    {
        var game = new FakeGame(); var controller = ControllerTests.Controller(game);
        controller.Commands.TryAdd(Status("recording")); controller.Tick(ControllerTests.Now);
        Assert.Equal("recording", game.Status); Drain(controller);
        controller.Cancel("scene-1", ControllerTests.Now);
        Assert.Equal("canceled", game.Status);
        var late = Status("recording"); late.Id = late.IdempotencyKey = "late-recording";
        controller.Commands.TryAdd(late); controller.Tick(ControllerTests.Now.AddMilliseconds(100));
        Assert.Equal("canceled", game.Status); Assert.Equal("scene_canceled", Assert.Single(Drain(controller)).Reason);
    }

    [Fact] public void RemoteGlobalCancelDoesNotRemoveNewerRecordingStatusInSameBatch()
    {
        var game = new FakeGame(); var controller = ControllerTests.Controller(game);
        var cancel = ControllerTests.Action("cancel-old"); cancel.Kind = "scene.cancel";
        cancel.Seq = 5; cancel.ActorId = null; cancel.RuntimeInstanceId = null;
        cancel.Payload = new JObject();
        var recording = Status("recording"); recording.Seq = 6;
        controller.Commands.TryAdd(cancel); controller.Commands.TryAdd(recording);
        controller.Tick(ControllerTests.Now);
        Assert.Equal("recording", game.Status);
        Assert.Equal(2, Drain(controller).FindAll(a => a.Status == "completed").Count);
    }

    [Fact] public void AudioStartReplacesPreviousAudioWithoutWaitingForEstimatedDuration()
    {
        var game = new FakeGame(); var controller = ControllerTests.Controller(game);
        controller.Commands.TryAdd(Audio(0)); controller.Tick(ControllerTests.Now);
        Assert.Equal("Audio 0", game.LastSubtitle);
        controller.Commands.TryAdd(Audio(1)); controller.Tick(ControllerTests.Now.AddMilliseconds(100));
        Assert.Equal("Audio 1", game.LastSubtitle); Assert.Equal(2, game.Subtitles);
        var delivered = Assert.Single(Drain(controller));
        Assert.Equal("completed", delivered.Status); Assert.Equal("subtitle", delivered.Observed!["delivery"]);
        Assert.Equal("Audio 0", delivered.Observed["text"]); Assert.Equal(DisplayText.Rule, delivered.Observed["display_rule"]);
    }

    [Fact] public void ReplacedUnrenderedAudioHasNoDeliveryEvidence()
    {
        var game = new FakeGame { SubtitleRendered = false }; var controller = ControllerTests.Controller(game);
        controller.Commands.TryAdd(Audio(0)); controller.Tick(ControllerTests.Now);
        controller.Commands.TryAdd(Audio(1)); controller.Tick(ControllerTests.Now.AddMilliseconds(100));
        var canceled = Assert.Single(Drain(controller));
        Assert.Equal("canceled", canceled.Status); Assert.Null(canceled.Observed);
        Assert.Equal("Audio 1", game.LastSubtitle);
    }

    [Fact] public void AudioCompletionStatusDoesNotRemoveUnrenderedSubtitle()
    {
        var game = new FakeGame { SubtitleRendered = false }; var controller = ControllerTests.Controller(game);
        controller.Commands.TryAdd(Audio(0, 300)); controller.Commands.TryAdd(Status("idle"));
        controller.Tick(ControllerTests.Now);
        Assert.Equal("Audio 0", game.LastSubtitle); Assert.Equal("idle", game.Status);
        Assert.Equal("status_rendered", Assert.Single(Drain(controller)).Reason);
        game.SubtitleRendered = true; controller.Tick(ControllerTests.Now.AddMilliseconds(20));
        Assert.Equal("subtitle_rendered", Assert.Single(Drain(controller)).Reason);
    }

    [Fact] public void KnownAudioDurationControlsSubtitleAndFinalMarker()
    {
        var game = new FakeGame(); var controller = ControllerTests.Controller(game);
        var final = Audio(1); final.Payload["text"] = ""; final.Payload["final"] = true;
        controller.Commands.TryAdd(Audio(0, 300)); controller.Commands.TryAdd(final);
        controller.Tick(ControllerTests.Now); controller.Tick(ControllerTests.Now.AddMilliseconds(100));
        Assert.Equal("audio-0", Assert.Single(Drain(controller)).CommandId);
        controller.Tick(ControllerTests.Now.AddMilliseconds(301));
        Assert.Equal("audio-1", Assert.Single(Drain(controller)).CommandId);
        Assert.Equal("", game.LastSubtitle);
    }

    [Theory][InlineData(0)][InlineData(30001)]
    public void InvalidAudioDurationFailsClosed(int duration)
    {
        var game = new FakeGame(); var controller = ControllerTests.Controller(game);
        controller.Commands.TryAdd(Audio(0, duration)); controller.Tick(ControllerTests.Now);
        Assert.Equal(0, game.Subtitles); Assert.Equal("invalid_audio_subtitle", Assert.Single(Drain(controller)).Reason);
    }
}

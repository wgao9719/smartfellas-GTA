using System;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public class LocalActionControllerTests
{
    private sealed class Navigation : ILocalNavigation
    {
        public bool Safe = true, Pavement = true;
        public double Offset;
        public bool TryPavement(TargetVector requested, out TargetVector point)
        { point = new TargetVector(requested.X + Offset, requested.Y, requested.Z); return Pavement; }
        public bool CorridorSafe(TargetVector from, TargetVector to) => Safe;
    }
    private static readonly TargetVector Origin = new(0, 0, 0), Player = new(-2, 0, 0);
    private static LocalActionController Create(string action, JObject? args = null, Navigation? navigation = null) =>
        LocalActionController.Create(action, args ?? new JObject(), 0, Origin, Player, Player, navigation ?? new Navigation())!;
    private static TargetVector Step(TargetVector from, TargetVector to)
    {
        var distance = LocalActionController.Distance(from, to);
        var ratio = distance > 0 ? Math.Min(1, 0.2 / distance) : 0;
        return new TargetVector(from.X + (to.X - from.X) * ratio, from.Y + (to.Y - from.Y) * ratio, 0);
    }
    [Theory]
    [InlineData("follow_player")][InlineData("go_to_safe_point")][InlineData("wander_nearby")][InlineData("flee_from_threat")]
    public void NoUnobservedTaskCanComplete(string action)
    {
        var controller = Create(action);
        EffectObservation result = null!;
        for (var t = 100; t <= 1000; t += 100) result = controller.Observe(t, Origin, Player, false);
        Assert.NotEqual(EffectStatus.Completed, result.Status);
        Assert.NotEqual(EffectStatus.Running, result.Status);
    }
    [Theory]
    [InlineData("follow_player", "follow_interval_observed")]
    [InlineData("go_to_safe_point", "safe_point_arrival_observed")]
    [InlineData("wander_nearby", "wander_interval_observed")]
    [InlineData("flee_from_threat", "threat_distance_observed")]
    public void CompletionRequiresContinuousObservedMotionAndActionSpecificFacts(string action, string reason)
    {
        var controller = Create(action, JObject.FromObject(new { duration_seconds = 5 }));
        var position = Origin; EffectObservation result = null!;
        for (var t = 100; t <= 6000; t += 100)
        {
            position = Step(position, controller.Destination);
            result = controller.Observe(t, position, Player, true);
            if (result.Status != EffectStatus.Running) break;
        }
        Assert.Equal(EffectStatus.Completed, result.Status); Assert.Equal(reason, result.Reason);
        Assert.True((bool)result.Facts["task_observed"]);
        Assert.True((double)result.Facts["elapsed_ms"] >= 100);
        Assert.True((double)result.Facts["distance_meters"] >= (action == "follow_player" ? 0 : 0.5));
        if (action == "go_to_safe_point") Assert.InRange((double)result.Facts["destination_error_meters"], 0, 0.75);
        if (action == "flee_from_threat") Assert.True((double)result.Facts["threat_distance_meters"] >= 7.25);
    }
    [Theory]
    [InlineData("follow_player")][InlineData("go_to_safe_point")][InlineData("wander_nearby")][InlineData("flee_from_threat")]
    public void UnsafeRouteAndMissingSamplesLatchFailure(string action)
    {
        var navigation = new Navigation(); var controller = Create(action, navigation: navigation);
        navigation.Safe = false;
        var failed = controller.Observe(100, Origin, Player, true);
        Assert.Equal("unsafe_route", failed.Reason);
        navigation.Safe = true;
        Assert.Same(failed, controller.Observe(200, Origin, Player, true));
        controller = Create(action);
        Assert.Equal("movement_tracking_gap", controller.Observe(251, Origin, Player, true).Reason);
    }
    [Theory]
    [InlineData("go_to_safe_point")][InlineData("wander_nearby")][InlineData("flee_from_threat")]
    public void UnsafeOrRemoteDestinationRefusesBeforeDispatch(string action)
    {
        Assert.Null(Create(action, navigation: new Navigation { Pavement = false }));
        Assert.Null(Create(action, navigation: new Navigation { Safe = false }));
        Assert.Null(Create(action, navigation: new Navigation { Offset = 100 }));
    }
    [Fact] public void PlayerDistanceRadiusAndTimeoutAreHardBounds()
    {
        Assert.Equal("follow_distance_exceeded", Create("follow_player").Observe(100, Origin, new TargetVector(16, 0, 0), true).Reason);
        Assert.Equal("wander_radius_exceeded", Create("wander_nearby").Observe(100, new TargetVector(6, 0, 0), Player, true).Reason);
        var controller = Create("go_to_safe_point", JObject.FromObject(new { duration_seconds = 1 }));
        EffectObservation result = null!;
        for (var t = 100; t <= 1000; t += 100) result = controller.Observe(t, Origin, Player, true);
        Assert.Equal("destination_timeout", result.Reason);
    }
    [Fact] public void FollowWalksInShortSegmentsAndKeepsTwoMeters()
    {
        var player = new TargetVector(10, 0, 0);
        var controller = LocalActionController.Create("follow_player", new JObject(), 0, Origin, player, player, new Navigation())!;
        Assert.InRange(LocalActionController.Distance(Origin, controller.Destination), 0, 3);
        var position = Origin;
        for (var t = 100; t <= 5000; t += 100)
        { position = Step(position, controller.Destination); Assert.Equal(EffectStatus.Running, controller.Observe(t, position, player, true).Status); }
        Assert.InRange(LocalActionController.Distance(position, player), 2, 2.75);
    }
    [Fact] public void StopMustObserveReplacementAndAbsenceOfFollow()
    {
        var controller = Create("stop_following");
        for (var t = 100; t <= 500; t += 100) Assert.Equal(EffectStatus.Running, controller.Observe(t, Origin, Player, true, priorFollowActive: true).Status);
        var result = controller.Observe(600, Origin, Player, true);
        Assert.Equal(EffectStatus.Completed, result.Status); Assert.True((bool)result.Facts["follow_task_absent"]);
        controller = Create("stop_following");
        for (var t = 100; t <= 1600; t += 100) result = controller.Observe(t, Origin, Player, false);
        Assert.Equal(EffectStatus.Failed, result.Status);
    }
    [Theory][InlineData("wave")][InlineData("shrug")][InlineData("point")]
    public void GestureNeedsObservedPlaybackForOneSecond(string gesture)
    {
        var controller = Create("play_verified_gesture", JObject.FromObject(new { gesture_id = gesture }));
        for (var t = 100; t <= 1000; t += 100) Assert.Equal(EffectStatus.Running, controller.Observe(t, Origin, Player, true).Status);
        for (var t = 1100; t < 2000; t += 100) Assert.Equal(EffectStatus.Running, controller.Observe(t, Origin, Player, true, true).Status);
        var result = controller.Observe(2000, Origin, Player, true, true);
        Assert.Equal(EffectStatus.Completed, result.Status); Assert.True((bool)result.Facts["animation_observed"]);
    }
    [Fact] public void MissingOrInterruptedAnimationNeverCompletes()
    {
        var args = JObject.FromObject(new { gesture_id = "wave" }); var controller = Create("play_verified_gesture", args);
        Assert.Equal(EffectStatus.Running, controller.Observe(100, Origin, Player, true, true).Status);
        Assert.Equal("animation_interrupted", controller.Observe(200, Origin, Player, true).Reason);
        controller = Create("play_verified_gesture", args); EffectObservation result = null!;
        for (var t = 100; t <= 5100; t += 100) result = controller.Observe(t, Origin, Player, false);
        Assert.Equal("animation_not_observed", result.Reason);
    }
    [Fact] public void EndedHoldMustNeverBeReissuedOrClaimCompletion()
    {
        var controller = Create("follow_player");
        controller.Observe(100, Origin, Player, true);
        var revision = controller.Revision;
        var result = controller.Observe(200, Origin, Player, false);
        Assert.Equal(EffectStatus.TakenOver, result.Status);
        Assert.Equal(revision, controller.Revision);
        Assert.Same(result, controller.Observe(300, Origin, Player, true));
    }
    [Theory]
    [InlineData(200, -930, 30, true)][InlineData(159, -930, 30, false)]
    [InlineData(200, -1001, 30, false)][InlineData(200, -930, 41, false)]
    [InlineData(double.NaN, -930, 30, false)][InlineData(0, 0, 0, false)]
    public void UnknownOrOutsidePublicTrialAreaIsDenied(double x, double y, double z, bool allowed) =>
        Assert.Equal(allowed, LocalActionController.ApprovedArea(new TargetVector(x, y, z)));
}

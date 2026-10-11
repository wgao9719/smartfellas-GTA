using System;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public class ActionMotionTrackerTests
{
    [Fact]
    public void ReturningToOriginRetainsCumulativeDistance()
    {
        var tracker = new ActionMotionTracker(1000, 0, 0, 0);
        Assert.True(tracker.TrySample(1100, .3, .4, 0, out var outward));
        Assert.Equal(.5, outward!.DistanceMeters, 8);
        Assert.True(tracker.TrySample(1200, 0, 0, 0, out var returned));
        Assert.Equal(1, returned!.DistanceMeters, 8);
        Assert.Equal(0, returned.DisplacementMeters);
        Assert.Equal(200, returned.ElapsedMilliseconds);
    }

    [Fact]
    public void FiniteStationarySamplesCanProveZeroMovement()
    {
        var tracker = new ActionMotionTracker(1000, 10, 20, 30);
        Assert.True(tracker.TrySample(1250, 10, 20, 30, out var sample));
        Assert.Equal(0, sample!.DistanceMeters);
        Assert.Equal(250, sample.ElapsedMilliseconds);
    }

    [Theory]
    [InlineData(double.NaN, 0, 0, 0)]
    [InlineData(double.PositiveInfinity, 0, 0, 0)]
    [InlineData(-1, 0, 0, 0)]
    [InlineData(1000, double.NaN, 0, 0)]
    [InlineData(1000, 0, double.NegativeInfinity, 0)]
    [InlineData(1000, 0, 0, double.PositiveInfinity)]
    public void InvalidInitialSamplesCannotProduceEvidence(double time, double x, double y, double z)
    {
        var tracker = new ActionMotionTracker(time, x, y, z);
        Assert.False(tracker.IsValid);
        Assert.False(tracker.TrySample(1100, 0, 0, 0, out var sample));
        Assert.Null(sample);
    }

    [Theory]
    [InlineData(1250.001, 0, 0, 0, "movement_tracking_gap")]
    [InlineData(999, 0, 0, 0, "movement_tracking_gap")]
    [InlineData(double.NaN, 0, 0, 0, "movement_sample_unavailable")]
    [InlineData(1100, double.NaN, 0, 0, "movement_sample_unavailable")]
    [InlineData(1100, 0, double.PositiveInfinity, 0, "movement_sample_unavailable")]
    [InlineData(1100, 0, 0, double.NegativeInfinity, "movement_sample_unavailable")]
    [InlineData(1100, double.MaxValue, 0, 0, "movement_sample_unavailable")]
    public void InvalidSamplesPermanentlyInvalidateTracking(double time, double x, double y, double z, string reason)
    {
        var tracker = new ActionMotionTracker(1000, 0, 0, 0);
        Assert.False(tracker.TrySample(time, x, y, z, out var sample));
        Assert.Null(sample);
        Assert.Equal(reason, tracker.FailureReason);
        Assert.False(tracker.TrySample(1200, 0, 0, 0, out sample));
        Assert.Null(sample);
    }

    [Fact]
    public void EveryIntervalMustMeetTheTrackingBound()
    {
        var tracker = new ActionMotionTracker(1000, 0, 0, 0);
        Assert.True(tracker.TrySample(1100, 1, 0, 0, out _));
        Assert.True(tracker.TrySample(1350, 2, 0, 0, out _));
        Assert.False(tracker.TrySample(1601, 2, 0, 0, out var sample));
        Assert.Null(sample);
    }

    [Fact]
    public void StartedAcknowledgmentIncludesDispatchFact()
    {
        var game = new FakeGame();
        var controller = ControllerTests.Controller(game);
        controller.Commands.TryAdd(ControllerTests.Action());
        controller.Tick(ControllerTests.Now);
        Assert.True(controller.Acks.TryTake(out var ack));
        Assert.Equal("started", ack.Status);
        Assert.Equal("task_dispatched", ack.Reason);
        Assert.Equal(true, ack.Observed!["task_dispatched"]);
    }
}

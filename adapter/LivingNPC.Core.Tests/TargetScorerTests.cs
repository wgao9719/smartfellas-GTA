using System;
using System.Linq;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class TargetScorerTests
{
    private static readonly TargetVector Origin = new TargetVector(0, 0, 0), Forward = new TargetVector(0, 1, 0);

    [Fact] public void FartherLookedAtPedBeatsNearerPedBehindPlayer()
    {
        var behind = new TargetCandidate(1, 2, new TargetVector(0, -2, 0));
        var ahead = new TargetCandidate(2, 6, new TargetVector(0, 6, 0));
        Assert.Equal(2, TargetScorer.Rank(new[] { behind, ahead }, Origin, Forward).First().Handle);
    }

    [Theory][InlineData(3, 30, 4, 0, 2)][InlineData(2, 5, 7, 0, 1)][InlineData(4, 10, 3, 10, 2)]
    public void TwoPedsInConeBalanceAngleAndPlayerDistance(double firstDistance, double firstAngle, double secondDistance, double secondAngle, int expected)
    {
        var candidates = new[] { AtAngle(1, firstDistance, firstAngle), AtAngle(2, secondDistance, secondAngle) };
        Assert.Equal(expected, TargetScorer.Rank(candidates, Origin, Forward).First().Handle);
    }

    [Fact] public void OutOfConeFallsBackToNoTarget()
    {
        var candidates = new[] { AtAngle(1, 2, 46), AtAngle(2, 1, 180) };
        Assert.Null(TargetScorer.Rank(candidates, Origin, Forward).FirstOrDefault());
    }

    [Fact] public void EqualScoresUseHandleRegardlessOfEnumerationOrder()
    {
        var first = AtAngle(20, 4, -20); var second = AtAngle(10, 4, 20);
        Assert.Equal(TargetScorer.Score(first, Origin, Forward), TargetScorer.Score(second, Origin, Forward));
        Assert.Equal(new[] { 10, 20 }, TargetScorer.Rank(new[] { first, second }, Origin, Forward).Select(c => c.Handle));
        Assert.Equal(new[] { 10, 20 }, TargetScorer.Rank(new[] { second, first }, Origin, Forward).Select(c => c.Handle));
    }

    [Fact] public void EqualScoresPreferPlayerDistanceBeforeHandle()
    {
        var ahead = AtAngle(1, 8, 0);
        var edge = new TargetCandidate(2, 0, new TargetVector(1, 1, 0));
        Assert.Equal(TargetScorer.Score(ahead, Origin, Forward), TargetScorer.Score(edge, Origin, Forward));
        Assert.Equal(new[] { 2, 1 }, TargetScorer.Rank(new[] { ahead, edge }, Origin, Forward).Select(c => c.Handle));
    }

    [Fact] public void CameraOriginAndDirectionDetermineAngleButPlayerDistanceDeterminesRange()
    {
        var camera = new TargetVector(100, 100, 10);
        var candidate = new TargetCandidate(1, 8, new TargetVector(100, 120, 10));
        Assert.Equal(1, TargetScorer.Score(candidate, camera, new TargetVector(0, 3, 0)));
        Assert.Null(TargetScorer.Score(new TargetCandidate(2, 8.01, candidate.Position), camera, Forward));
    }

    [Theory][InlineData(-1)][InlineData(8.01)][InlineData(double.NaN)][InlineData(double.PositiveInfinity)]
    public void InvalidOrDistantCandidateIsRejected(double distance)
    {
        Assert.Null(TargetScorer.Score(new TargetCandidate(1, distance, Forward), Origin, Forward));
    }

    [Theory][InlineData(double.NaN)][InlineData(double.PositiveInfinity)]
    public void MissingCameraOrPositionFactsFailClosed(double value)
    {
        var invalid = new TargetVector(value, 1, 0); var candidate = AtAngle(1, 3, 0);
        Assert.Null(TargetScorer.Score(candidate, invalid, Forward));
        Assert.Null(TargetScorer.Score(candidate, Origin, invalid));
        Assert.Null(TargetScorer.Score(new TargetCandidate(1, 3, invalid), Origin, Forward));
    }

    [Fact] public void ZeroDirectionAndCoincidentCameraCannotEstablishLookAngle()
    {
        Assert.Null(TargetScorer.Score(AtAngle(1, 3, 0), Origin, Origin));
        Assert.Null(TargetScorer.Score(new TargetCandidate(1, 3, Origin), Origin, Forward));
    }

    [Fact] public void ConeIncludesItsBoundaryAndUsesVerticalAngle()
    {
        Assert.NotNull(TargetScorer.Score(new TargetCandidate(1, 8, new TargetVector(0, 1, 1)), Origin, Forward));
        Assert.Null(TargetScorer.Score(new TargetCandidate(2, 2, new TargetVector(0, 1, 2)), Origin, Forward));
    }

    private static TargetCandidate AtAngle(int handle, double distance, double degrees)
    {
        var radians = degrees * Math.PI / 180;
        return new TargetCandidate(handle, distance, new TargetVector(Math.Sin(radians), Math.Cos(radians), 0));
    }
}

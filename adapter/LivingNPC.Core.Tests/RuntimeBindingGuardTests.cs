using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class RuntimeBindingGuardTests
{
    [Fact] public void RepeatedMatchingSamplesRemainValid()
    {
        var guard = new RuntimeBindingGuard(-10, -20, 100);
        Assert.True(guard.Validate(true, true, -10, -20, 100));
        Assert.True(guard.Validate(true, true, -10, -20, 100));
        Assert.True(guard.IsValid);
    }

    [Theory]
    [InlineData(false, true, 10, 20, 100)]
    [InlineData(true, false, 10, 20, 100)]
    [InlineData(true, true, 11, 20, 100)]
    [InlineData(true, true, 10, 21, 100)]
    [InlineData(true, true, 10, 20, null)]
    [InlineData(true, true, 10, 20, 0)]
    [InlineData(true, true, 10, 20, 101)]
    public void MissingOrChangedSamplePermanentlyInvalidatesBinding(bool exists, bool alive, int handle, int modelHash, int? marker)
    {
        var guard = new RuntimeBindingGuard(10, 20, 100);
        Assert.False(guard.Validate(exists, alive, handle, modelHash, marker));
        Assert.False(guard.IsValid);
        Assert.False(guard.Validate(true, true, 10, 20, 100));
    }

    [Theory][InlineData(0, 20, 100)][InlineData(10, 0, 100)][InlineData(10, 20, 0)][InlineData(10, 20, -1)]
    public void InvalidInitialFactsFailClosed(int handle, int modelHash, int marker)
    {
        var guard = new RuntimeBindingGuard(handle, modelHash, marker);
        Assert.False(guard.IsValid);
        Assert.False(guard.Validate(true, true, handle, modelHash, marker));
    }

    [Fact] public void RecycledHandleWithSameModelNeedsAFreshGuardAndMarker()
    {
        var previous = new RuntimeBindingGuard(10, 20, 100);
        Assert.True(previous.Validate(true, true, 10, 20, 100));
        Assert.False(previous.Validate(true, true, 10, 20, 101));
        var replacement = new RuntimeBindingGuard(10, 20, 101);
        Assert.True(replacement.Validate(true, true, 10, 20, 101));
        Assert.False(previous.Validate(true, true, 10, 20, 100));
        Assert.False(replacement.Validate(true, true, 10, 20, 100));
    }

    [Fact] public void ExplicitInvalidationCannotRecover()
    {
        var guard = new RuntimeBindingGuard(10, 20, 100);
        guard.Invalidate();
        Assert.False(guard.IsValid);
        Assert.False(guard.Validate(true, true, 10, 20, 100));
    }
}

using System;
using System.Threading;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class ThreadAffinityTests
{
    [Fact] public void ConstructionDoesNotBindAndEarlyKeysOrAbortCannotUseGameState()
    {
        var affinity = new ThreadAffinity();
        Assert.False(affinity.IsCurrent);
        Assert.Throws<InvalidOperationException>(affinity.Verify);
    }

    [Fact] public void FirstTickBindsItsThreadInsteadOfTheConstructingThread()
    {
        var affinity = new ThreadAffinity(); Exception? failure = null;
        var script = new Thread(() =>
        {
            failure = Record.Exception(() =>
            {
                Assert.False(affinity.IsCurrent);
                affinity.Bind(); affinity.Verify(); affinity.Bind();
                Assert.True(affinity.IsCurrent);
            });
        });
        script.Start(); Assert.True(script.Join(TimeSpan.FromSeconds(5)));
        Assert.Null(failure);
        Assert.False(affinity.IsCurrent);
        Assert.Throws<InvalidOperationException>(affinity.Verify);
    }

    [Fact] public void WorkerOrDomainThreadCannotRebindTheScriptThread()
    {
        var affinity = new ThreadAffinity(); affinity.Bind(); Exception? failure = null;
        var worker = new Thread(() =>
        {
            failure = Record.Exception(() =>
            {
                Assert.False(affinity.IsCurrent);
                Assert.Throws<InvalidOperationException>(affinity.Verify);
                Assert.Throws<InvalidOperationException>(affinity.Bind);
                Assert.False(affinity.IsCurrent);
            });
        });
        worker.Start(); Assert.True(worker.Join(TimeSpan.FromSeconds(5)));
        Assert.Null(failure);
        affinity.Verify(); Assert.True(affinity.IsCurrent);
    }
}

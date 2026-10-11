using System.Collections.Generic;
using System.Linq;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class BindingRetentionTests
{
    private static KeyValuePair<string, float> At(string id, float meters) => new KeyValuePair<string, float>(id, meters);

    [Fact] public void DistantBindingsStayWhileThereIsRoom()
    {
        var far = Enumerable.Range(0, 32).Select(i => At("npc-" + i, 500f + i));
        Assert.Empty(BindingRetention.Evict(32, far));
        Assert.Empty(BindingRetention.Evict(0, new KeyValuePair<string, float>[0]));
    }

    [Fact] public void NearTheCapTheFarthestGoFirst()
    {
        var candidates = new[] { At("near", 3f), At("far", 300f), At("mid", 70f), At("lost", float.NaN), At("edge", 70f) };
        Assert.Equal(new[] { "lost", "far" }, BindingRetention.Evict(BindingRetention.MaxBindings - BindingRetention.Headroom + 2, candidates));
        Assert.Equal(new[] { "lost", "far", "edge", "mid" }, BindingRetention.Evict(BindingRetention.MaxBindings, candidates));
    }

    [Fact] public void OnlyTheReleasableCandidatesCanBeEvicted()
    {
        // The selected actor and pins are never passed in, so a cap made of them evicts nothing.
        Assert.Empty(BindingRetention.Evict(BindingRetention.MaxBindings, new KeyValuePair<string, float>[0]));
        Assert.Equal(new[] { "only" }, BindingRetention.Evict(BindingRetention.MaxBindings, new[] { At("only", 1f) }));
    }
}

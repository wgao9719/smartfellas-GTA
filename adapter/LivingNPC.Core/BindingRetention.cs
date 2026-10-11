using System.Collections.Generic;
using System.Linq;

namespace LivingNPC.Core
{
    // An ambient identity lasts as long as its ped. Despawn, a model or marker change, a script
    // takeover, or a new runtime generation releases it. Distance alone never does.
    public static class BindingRetention
    {
        // MaxBindings matches the service's default max_actors. Pruning keeps Headroom slots free,
        // so a new target binds before the service has processed any removal.
        public const int MaxBindings = 36, Headroom = 4;
        // The caller passes only bindings it may release, never the selected actor or a pin.
        // Returns the actor ids to release, farthest first. An unknown distance counts as farthest.
        public static string[] Evict(int bound, IEnumerable<KeyValuePair<string, float>> releasable, int headroom = Headroom)
        {
            if (headroom < 0 || headroom >= MaxBindings) throw new System.ArgumentOutOfRangeException(nameof(headroom));
            var excess = bound - (MaxBindings - headroom);
            if (excess <= 0) return new string[0];
            return releasable.OrderByDescending(c => float.IsNaN(c.Value) ? float.PositiveInfinity : c.Value)
                .ThenBy(c => c.Key, System.StringComparer.Ordinal).Take(excess).Select(c => c.Key).ToArray();
        }
    }
}

using System.IO;
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    public static class BridgeLimits
    {
        public static int MaxBodyBytes { get; }
        public static int MaxCommands { get; }
        static BridgeLimits()
        {
            using (var stream = typeof(BridgeLimits).GetTypeInfo().Assembly.GetManifestResourceStream("LivingNPC.bridge_limits.json") ?? throw new InvalidDataException("Bridge limits resource missing"))
            using (var reader = new StreamReader(stream))
            {
                var limits = JObject.Parse(reader.ReadToEnd());
                MaxBodyBytes = (int?)limits["max_body_bytes"] ?? 0;
                MaxCommands = (int?)limits["max_commands"] ?? 0;
                if (MaxBodyBytes <= 0 || MaxCommands <= 0) throw new InvalidDataException("Invalid bridge limits");
            }
        }
    }
}

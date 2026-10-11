using System.Collections.Generic;
using System.IO;
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    // Narrative context for the speaking actor. Never an input to eligibility or policy.
    // Shared with Python through data/ped_activity.json.
    public sealed class ActorActivity
    {
        public const string OtherScenario = "other";
        public string Movement = "unknown";
        public string? Scenario;
        public bool OnPhone, Swimming, Injured;
        // GTA scenario names in table order. The first match wins; any other scenario reports OtherScenario.
        public static IReadOnlyList<(string Name, string Label)> Scenarios { get; } = Load();
        public static string MovementName(bool sprinting, bool running, bool walking, bool stopped) =>
            sprinting ? "sprinting" : running ? "running" : walking ? "walking" : stopped ? "still" : "unknown";
        private static IReadOnlyList<(string Name, string Label)> Load()
        {
            using (var stream = typeof(ActorActivity).GetTypeInfo().Assembly.GetManifestResourceStream("LivingNPC.ped_activity.json") ?? throw new InvalidDataException("Ped activity table resource missing"))
            using (var reader = new StreamReader(stream))
            {
                var data = JObject.Parse(reader.ReadToEnd());
                if ((int?)data["schema_version"] != 1 || (bool?)data["grants_capabilities"] != false || (string?)data["verification"] != "unverified") throw new InvalidDataException("Unsupported ped activity table");
                if ((string?)data["other"]?["label"] != OtherScenario) throw new InvalidDataException("Missing generic activity label");
                var scenarios = new List<(string Name, string Label)>();
                foreach (var entry in data["scenarios"] as JArray ?? throw new InvalidDataException("Missing activity scenarios"))
                {
                    var name = (string?)entry["scenario"]; var label = (string?)entry["label"];
                    if (string.IsNullOrEmpty(name) || string.IsNullOrEmpty(label) || label == OtherScenario) throw new InvalidDataException("Invalid activity scenario");
                    scenarios.Add((name!, label!));
                }
                return scenarios;
            }
        }
    }
}

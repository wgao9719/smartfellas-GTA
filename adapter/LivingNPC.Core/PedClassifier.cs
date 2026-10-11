using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    public enum PedRole { Unknown, Civilian, ServiceWorker, Police, Military, Security, Emergency, Gang, Prisoner, Animal, Protagonist, StoryCharacter }
    public static class Joaat
    {
        // GET_HASH_KEY: one-at-a-time over the bytes with ASCII lowercasing.
        public static int Hash(string text)
        {
            unchecked
            {
                uint value = 0;
                foreach (var raw in Encoding.UTF8.GetBytes(text)) { value += raw >= 'A' && raw <= 'Z' ? raw + 32u : raw; value += value << 10; value ^= value >> 6; }
                value += value << 3; value ^= value >> 11; value += value << 15;
                return (int)value;
            }
        }
    }
    // Sampled on the script thread. The classifier never calls the game.
    public sealed class PedFacts
    {
        public int ModelHash, PedType = -1, RelationshipGroupHash;
        public bool Human, AnimalModel, Cuffed, InPoliceVehicle, PlayerGroup, MissionEntity, InModRegistry, TookPersistence, BelongsToModScript;
        public string? OwningScript;
    }
    public sealed class RoleClassification
    {
        public PedRole Role;
        public Ownership Ownership;
        public ControlPolicy Policy;
        public string Reason = "";
    }
    // Shared with Python through data/ped_roles.json. Labels restrict; they never grant capabilities.
    public sealed class PedRoleTable
    {
        private static readonly string[] Wire = { "unknown", "civilian", "service_worker", "police", "military", "security", "emergency", "gang", "prisoner", "animal", "protagonist", "story_character" };
        private static readonly string[] Labels = { "unrecognized pedestrian", "resident", "service worker", "police officer", "military personnel", "security guard", "emergency worker", "gang member", "detained person", "animal", "protagonist", "story character" };
        private readonly Dictionary<PedRole, ControlPolicy> policies = new Dictionary<PedRole, ControlPolicy>();
        private readonly Dictionary<int, PedRole> pedTypes = new Dictionary<int, PedRole>(), groups = new Dictionary<int, PedRole>(), models = new Dictionary<int, PedRole>();
        private readonly Dictionary<string, PedRole> modelNames = new Dictionary<string, PedRole>(StringComparer.Ordinal);
        // Declared before Default: static initializers run in textual order.
        public static IReadOnlyList<PedRole> Roles { get; } = (PedRole[])Enum.GetValues(typeof(PedRole));
        public static PedRoleTable Default { get; } = Load();
        public string Version { get; }
        public IReadOnlyDictionary<string, PedRole> ModelNames => modelNames;
        public static string WireName(PedRole role) => Wire[(int)role];
        public static string Label(PedRole role) => Labels[(int)role];
        public static PedRole Parse(string? name) { var index = Array.IndexOf(Wire, name); return index < 0 ? PedRole.Unknown : (PedRole)index; }
        public ControlPolicy PolicyFor(PedRole role) => policies[role];
        private PedRoleTable(JObject data)
        {
            if ((int?)data["schema_version"] != 1 || (bool?)data["grants_capabilities"] != false || (string?)data["verification"] != "unverified") throw new InvalidDataException("Unsupported ped role table");
            Version = (string?)data["table_version"] ?? throw new InvalidDataException("Missing role table version");
            var declared = data["role_policies"] as JObject ?? throw new InvalidDataException("Missing role policies");
            if (declared.Count != Roles.Count) throw new InvalidDataException("Role policies must cover each role exactly once");
            foreach (var role in Roles)
            {
                var policy = (string?)declared[WireName(role)];
                if (policy == "ambient" && role == PedRole.Civilian) policies[role] = ControlPolicy.Ambient;
                else if (policy == "protected") policies[role] = ControlPolicy.Protected;
                else throw new InvalidDataException("Only civilians may receive the ambient policy");
            }
            Read(data["ped_types"], (role, item) => { var type = (int)item; if (type < 0 || type > 255) throw new InvalidDataException("Invalid ped type"); Add(pedTypes, type, role); });
            Read(data["relationship_groups"], (role, item) => Add(groups, Joaat.Hash((string)item!), role));
            Read(data["models"], (role, item) => { var name = (string)item!; Add(models, Joaat.Hash(name), role); modelNames.Add(name, role); });
        }
        private static void Read(JToken? section, Action<PedRole, JToken> add)
        {
            foreach (var entry in section as JObject ?? throw new InvalidDataException("Missing role table section"))
            {
                var role = Parse(entry.Key);
                if (role == PedRole.Unknown || entry.Value is not JArray items) throw new InvalidDataException("Invalid role table entry");
                foreach (var item in items) add(role, item);
            }
        }
        private static void Add(Dictionary<int, PedRole> map, int key, PedRole role) { if (map.ContainsKey(key)) throw new InvalidDataException("Duplicate role table key"); map.Add(key, role); }
        private static PedRoleTable Load()
        {
            using (var stream = typeof(PedRoleTable).GetTypeInfo().Assembly.GetManifestResourceStream("LivingNPC.ped_roles.json") ?? throw new InvalidDataException("Ped role table resource missing"))
            using (var reader = new StreamReader(stream)) return new PedRoleTable(JObject.Parse(reader.ReadToEnd()));
        }
        public RoleClassification Classify(PedFacts? facts)
        {
            if (facts == null) return new RoleClassification { Role = PedRole.Unknown, Ownership = Ownership.Unknown, Policy = ControlPolicy.Protected, Reason = "facts_unavailable" };
            string? owner = null; var ownership = Ownership.Ambient;
            // The registry keeps a ped only while it is still ours. No other script's ped is cleared.
            if (facts.InModRegistry) { if (facts.TookPersistence ? facts.BelongsToModScript : !facts.MissionEntity) ownership = Ownership.ModOwned; else { ownership = Ownership.GameScriptOwned; owner = "registered_ped_taken_by_script"; } }
            else if (facts.MissionEntity) { ownership = Ownership.GameScriptOwned; owner = "mission_entity"; }
            else if (!string.IsNullOrEmpty(facts.OwningScript)) { ownership = Ownership.GameScriptOwned; owner = "owning_script"; }
            var (role, reason) = Role(facts);
            return new RoleClassification { Role = role, Ownership = ownership, Policy = ownership == Ownership.GameScriptOwned ? ControlPolicy.Protected : policies[role], Reason = owner ?? reason };
        }
        private (PedRole, string) Role(PedFacts facts)
        {
            if (facts.AnimalModel || !facts.Human) return (PedRole.Animal, "animal");
            if (facts.Cuffed) return (PedRole.Prisoner, "cuffed");
            if (facts.PlayerGroup) return (PedRole.StoryCharacter, "player_group_member");
            var signals = new[] { ("model", Find(models, facts.ModelHash)), ("ped_type", Find(pedTypes, facts.PedType)), ("relationship_group", Find(groups, facts.RelationshipGroupHash)) };
            foreach (var (name, role) in signals) if (role != null && role != PedRole.Civilian) return (role.Value, name + "_" + WireName(role.Value));
            // After specific evidence, so an officer driving a cruiser keeps the police label.
            if (facts.InPoliceVehicle) return (PedRole.Prisoner, "in_police_vehicle");
            foreach (var (name, role) in signals) if (role == null) return (PedRole.Unknown, name + "_unlisted");
            return (PedRole.Civilian, "civilian_evidence");
        }
        private static PedRole? Find(Dictionary<int, PedRole> map, int key) => map.TryGetValue(key, out var role) ? role : (PedRole?)null;
    }
    public static class PedClassifier
    {
        public static RoleClassification Classify(PedFacts? facts) => PedRoleTable.Default.Classify(facts);
        // SPECS §4.2 target states.
        public static string Availability(ActorState? actor, WorldState world, long generation)
        {
            if (actor == null || !world.Safe || !actor.Exists || !actor.Alive || actor.Generation != generation || actor.Policy == ControlPolicy.Protected ||
                actor.Ownership == Ownership.Unknown || actor.Ownership == Ownership.GameScriptOwned || actor.MissionEntity && actor.Ownership != Ownership.ModOwned) return "Not available";
            if (actor.Busy || actor.InCombat) return "Busy";
            return float.IsNaN(actor.Distance) || float.IsInfinity(actor.Distance) || actor.Distance < 0 || actor.Distance > 8 ? "Out of range" : "Available";
        }
    }
}

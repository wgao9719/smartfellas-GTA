using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class ClassifierTests
{
    private static readonly JObject Vectors = JObject.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "ped_role_vectors.json")));
    public static IEnumerable<object[]> VectorNames() => ((JArray)Vectors["cases"]!).Select(item => new object[] { (string)item["name"]! });
    internal static PedFacts Civilian() => new() { ModelHash = Joaat.Hash("a_m_y_business_01"), PedType = 4, RelationshipGroupHash = Joaat.Hash("CIVMALE"), Human = true };
    private static PedFacts? Facts(JToken token) => token.Type == JTokenType.Null ? null : new PedFacts {
        ModelHash = (int)token["model_hash"]!, PedType = (int)token["ped_type"]!, RelationshipGroupHash = (int)token["relationship_group_hash"]!,
        Human = (bool)token["human"]!, AnimalModel = (bool)token["animal_model"]!, Cuffed = (bool)token["cuffed"]!, InPoliceVehicle = (bool)token["in_police_vehicle"]!,
        PlayerGroup = (bool)token["player_group"]!, MissionEntity = (bool)token["mission_entity"]!, OwningScript = (string?)token["owning_script"],
        InModRegistry = (bool)token["in_mod_registry"]!, TookPersistence = (bool)token["took_persistence"]!, BelongsToModScript = (bool)token["belongs_to_mod_script"]! };
    private static string Wire(Ownership value) => value switch { Ownership.Ambient => "ambient", Ownership.ModOwned => "mod_owned", Ownership.GameScriptOwned => "game_script_owned", _ => "unknown" };
    private static string Wire(ControlPolicy value) => value switch { ControlPolicy.Ambient => "ambient", ControlPolicy.ServiceLimited => "service_limited", _ => "protected" };

    [Fact] public void JoaatMatchesGameHashes()
    {
        Assert.Equal(1581098148, Joaat.Hash("s_m_y_cop_01"));
        Assert.Equal(-1692214353, Joaat.Hash("player_one"));
        Assert.Equal(unchecked((int)0xA49E591C), Joaat.Hash("COP"));
        Assert.Equal(Joaat.Hash("cop"), Joaat.Hash("COP"));
    }

    [Theory][MemberData(nameof(VectorNames))]
    public void SharedVectorsMatch(string name)
    {
        Assert.Equal((string)Vectors["table_version"]!, PedRoleTable.Default.Version);
        var vector = ((JArray)Vectors["cases"]!).Single(item => (string)item["name"]! == name);
        var result = PedClassifier.Classify(Facts(vector["facts"]!));
        var expected = vector["expected"]!;
        Assert.Equal((string)expected["role"]!, PedRoleTable.WireName(result.Role));
        Assert.Equal((string)expected["ownership"]!, Wire(result.Ownership));
        Assert.Equal((string)expected["policy"]!, Wire(result.Policy));
        Assert.Equal((string)expected["reason"]!, result.Reason);
    }

    [Theory]
    [InlineData("unlisted_model", "model_unlisted")][InlineData("police_model", "model_police")][InlineData("unlisted_ped_type", "ped_type_unlisted")]
    [InlineData("police_ped_type", "ped_type_police")][InlineData("unlisted_group", "relationship_group_unlisted")][InlineData("cop_group", "relationship_group_police")]
    [InlineData("not_human", "animal")][InlineData("animal_model", "animal")][InlineData("cuffed", "cuffed")][InlineData("police_vehicle", "in_police_vehicle")]
    [InlineData("player_group", "player_group_member")][InlineData("mission_entity", "mission_entity")][InlineData("owning_script", "owning_script")][InlineData("missing", "facts_unavailable")]
    public void OnlyFullCivilianEvidenceIsAmbient(string deviation, string reason)
    {
        var baseline = PedClassifier.Classify(Civilian());
        Assert.Equal((PedRole.Civilian, Ownership.Ambient, ControlPolicy.Ambient, "civilian_evidence"), (baseline.Role, baseline.Ownership, baseline.Policy, baseline.Reason));
        PedFacts? facts = Civilian();
        switch (deviation)
        {
            case "unlisted_model": facts.ModelHash = Joaat.Hash("a_m_y_acult_01"); break;
            case "police_model": facts.ModelHash = Joaat.Hash("s_m_y_cop_01"); break;
            case "unlisted_ped_type": facts.PedType = 26; break;
            case "police_ped_type": facts.PedType = 6; break;
            case "unlisted_group": facts.RelationshipGroupHash = Joaat.Hash("MISSION2"); break;
            case "cop_group": facts.RelationshipGroupHash = Joaat.Hash("COP"); break;
            case "not_human": facts.Human = false; break;
            case "animal_model": facts.AnimalModel = true; break;
            case "cuffed": facts.Cuffed = true; break;
            case "police_vehicle": facts.InPoliceVehicle = true; break;
            case "player_group": facts.PlayerGroup = true; break;
            case "mission_entity": facts.MissionEntity = true; break;
            case "owning_script": facts.OwningScript = "am_hunting_ground"; break;
            case "missing": facts = null; break;
        }
        var result = PedClassifier.Classify(facts);
        Assert.Equal(ControlPolicy.Protected, result.Policy); Assert.Equal(reason, result.Reason);
    }

    [Fact] public void RegisteredPedTakenByScriptIsGameScriptOwned()
    {
        var ours = Civilian(); ours.InModRegistry = true;
        Assert.Equal(Ownership.ModOwned, PedClassifier.Classify(ours).Ownership);
        ours.MissionEntity = true;
        var taken = PedClassifier.Classify(ours);
        Assert.Equal((Ownership.GameScriptOwned, ControlPolicy.Protected, "registered_ped_taken_by_script"), (taken.Ownership, taken.Policy, taken.Reason));
        ours.TookPersistence = true; ours.BelongsToModScript = true;
        Assert.Equal(Ownership.ModOwned, PedClassifier.Classify(ours).Ownership);
        ours.BelongsToModScript = false;
        Assert.Equal("registered_ped_taken_by_script", PedClassifier.Classify(ours).Reason);
        var unregistered = Civilian(); unregistered.TookPersistence = true; unregistered.BelongsToModScript = true; unregistered.MissionEntity = true;
        Assert.Equal(Ownership.GameScriptOwned, PedClassifier.Classify(unregistered).Ownership);
    }

    [Fact] public void TableIsWellFormed()
    {
        var table = PedRoleTable.Default;
        Assert.Equal("1", table.Version);
        foreach (var role in PedRoleTable.Roles)
        {
            Assert.Equal(role == PedRole.Civilian ? ControlPolicy.Ambient : ControlPolicy.Protected, table.PolicyFor(role));
            Assert.Equal(role, PedRoleTable.Parse(PedRoleTable.WireName(role)));
            Assert.False(string.IsNullOrWhiteSpace(PedRoleTable.Label(role)));
        }
        foreach (var legacy in new[] { "a_m_y_business_01", "a_m_m_business_01", "a_f_y_business_01", "a_m_y_beach_01", "a_f_y_beach_01", "a_m_y_hipster_01" })
            Assert.Equal(PedRole.Civilian, table.ModelNames[legacy]);
        foreach (var excluded in new[] { "a_m_y_acult_01", "a_m_o_acult_01", "a_m_y_mexthug_01", "a_m_m_hillbilly_01", "a_m_m_prolhost_01" })
            Assert.False(table.ModelNames.ContainsKey(excluded));
        foreach (var (name, role) in new[] { ("s_m_y_cop_01", PedRole.Police), ("s_m_y_sheriff_01", PedRole.Police), ("s_m_y_swat_01", PedRole.Police), ("s_m_m_security_01", PedRole.Security),
            ("s_m_y_armymech_01", PedRole.Military), ("s_m_m_paramedic_01", PedRole.Emergency), ("s_m_y_fireman_01", PedRole.Emergency), ("mp_m_shopkeep_01", PedRole.ServiceWorker),
            ("g_m_y_ballaeast_01", PedRole.Gang), ("a_c_chop", PedRole.Animal), ("ig_lamardavis", PedRole.StoryCharacter), ("cs_lestercrest", PedRole.StoryCharacter),
            ("csb_ortega", PedRole.StoryCharacter), ("player_zero", PedRole.Protagonist), ("player_one", PedRole.Protagonist), ("player_two", PedRole.Protagonist) })
            Assert.Equal(role, table.ModelNames[name]);
        Assert.Equal(table.ModelNames.Count, table.ModelNames.Keys.Select(Joaat.Hash).Distinct().Count());
    }

    [Fact] public void AvailabilityUsesSpecifiedTargetStates()
    {
        var game = new FakeGame();
        Assert.Equal("Available", PedClassifier.Availability(game.Actor, game.World, 7));
        game.Actor.Distance = 9; Assert.Equal("Out of range", PedClassifier.Availability(game.Actor, game.World, 7));
        game.Actor.Busy = true; Assert.Equal("Busy", PedClassifier.Availability(game.Actor, game.World, 7));
        game.Actor.Policy = ControlPolicy.Protected; Assert.Equal("Not available", PedClassifier.Availability(game.Actor, game.World, 7));
        Assert.Equal("Not available", PedClassifier.Availability(null, game.World, 7));
    }

    [Fact] public void TableModelsExistInShvdnPedHash()
    {
        var path = typeof(ClassifierTests).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().Single(a => a.Key == "ShvdnReference").Value!;
        if (!File.Exists(path))
            Assert.Fail("SHVDN compile reference missing at " + path + ". Run `uv run python tools/fetch_shvdn_reference.py` from the repository root before `dotnet test`.");
        var members = new Dictionary<uint, string>();
        using (var stream = File.OpenRead(path))
        using (var pe = new PEReader(stream))
        {
            var metadata = pe.GetMetadataReader();
            var type = metadata.TypeDefinitions.Select(metadata.GetTypeDefinition).Single(t => metadata.GetString(t.Namespace) == "GTA" && metadata.GetString(t.Name) == "PedHash");
            foreach (var field in type.GetFields().Select(metadata.GetFieldDefinition))
            {
                var constant = field.GetDefaultValue();
                if (constant.IsNil) continue;
                var value = metadata.GetConstant(constant);
                Assert.Equal(ConstantTypeCode.UInt32, value.TypeCode);
                members[metadata.GetBlobReader(value.Value).ReadUInt32()] = metadata.GetString(field.Name);
            }
        }
        Assert.True(members.Count > 500, "GTA.PedHash was not read from " + path);
        var missing = PedRoleTable.Default.ModelNames.Keys.Where(name => !members.ContainsKey(unchecked((uint)Joaat.Hash(name)))).ToArray();
        Assert.True(missing.Length == 0, "Role table models absent from GTA.PedHash: " + string.Join(", ", missing));
        foreach (var (name, member) in new[] { ("a_m_y_business_01", "Business01AMY"), ("s_m_y_cop_01", "Cop01SMY"), ("player_one", "Franklin") })
            Assert.Equal(member, members[unchecked((uint)Joaat.Hash(name))]);
    }
}

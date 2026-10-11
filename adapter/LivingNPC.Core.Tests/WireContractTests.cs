using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using LivingNPC.Core;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class WireContractTests
{
    // Every case is also checked by tests/test_wire_contract.py against Pydantic.
    private static readonly JObject Fixtures = Load();
    private static JObject Load()
    {
        using var stream = typeof(WireContractTests).Assembly.GetManifestResourceStream("LivingNPC.Tests.wire_contract.json")!;
        using var reader = new JsonTextReader(new StreamReader(stream)) { DateParseHandling = DateParseHandling.None };
        return JObject.Load(reader);
    }
    public static IEnumerable<object[]> Cases() => ((JArray)Fixtures["cases"]!).Cast<JObject>()
        .Select(value => new object[] { (string)value["id"]!, (string)value["message_type"]!, (bool)value["valid"]!, value["payload"]!.ToString(Formatting.None) });

    [Theory][MemberData(nameof(Cases))]
    public void SharedWireContract(string id, string type, bool expected, string json)
    {
        bool valid;
        try { valid = Validate(type, json); }
        catch (JsonException) { valid = false; }
        catch (TargetInvocationException error) when (error.InnerException is JsonException) { valid = false; }
        Assert.True(valid == expected, id);
    }

    private static bool Validate(string type, string json)
    {
        switch (type)
        {
            case "session_open": return Wire.Decode<SessionRequest>(json) != null;
            case "session_response": return Wire.Decode<SessionResponse>(json) != null;
            case "event": return Wire.Decode<EventEnvelope>(json) != null;
            case "proposal": return Wire.Decode<ActionProposal>(json) != null;
            case "ack": return Wire.Decode<Ack>(json) != null;
            case "command":
                var command = Wire.Decode<Command>(json);
                // Schema validation precedes all native authorization in the real governor.
                var game = new FakeGame();
                return new SafetyGovernor(new[] { "face_player", "wait_here" }).Check(command,
                    "ses", 7, "p1", game.World, game.Actor, ControllerTests.Now) == null;
            default: throw new ArgumentException("Unknown fixture message type");
        }
    }

    [Theory]
    [InlineData("session_open")][InlineData("session_response")][InlineData("event")]
    [InlineData("proposal")][InlineData("command")][InlineData("ack")]
    public void EachMessageHasValidAndSeveralInvalidVectors(string type)
    {
        var cases = ((JArray)Fixtures["cases"]!).Cast<JObject>().Where(value => (string?)value["message_type"] == type).ToArray();
        Assert.Contains(cases, value => (bool)value["valid"]!);
        Assert.True(cases.Count(value => !(bool)value["valid"]!) >= 3);
        Assert.All(cases, value => Assert.False(string.IsNullOrWhiteSpace((string?)value["reason"])));
    }
}

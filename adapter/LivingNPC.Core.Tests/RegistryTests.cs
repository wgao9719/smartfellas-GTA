using System.Linq;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public class RegistryTests
{
    [Fact] public void EmbeddedRegistryContainsOnlyTheFiniteUnverifiedCatalog()
    {
        var registry = ActionRegistry.Default;
        Assert.Equal(18, registry.ActionIds.Count);
        Assert.Equal("2", registry.Version);
        foreach (var id in registry.ActionIds)
        {
            var metadata = registry.Metadata(id);
            Assert.Equal("unverified", (string?)metadata["executor"]!["validation"]);
            Assert.Empty((JArray)metadata["executor"]!["verified_game_builds"]!);
        }
        var game = new FakeGame(); var controller = new CommandController(game, "ses", 7, "p1", new string[0]);
        controller.Commands.TryAdd(ControllerTests.Action()); controller.Tick(ControllerTests.Now);
        Assert.Equal(0, game.Starts);
    }
    [Theory]
    [InlineData("face_player", "{\"duration_seconds\":0.1}")]
    [InlineData("wait_here", "{\"duration_seconds\":30}")]
    [InlineData("follow_player", "{\"max_follow_meters\":15}")]
    [InlineData("stop_following", "{}")]
    [InlineData("go_to_safe_point", "{\"point\":{\"x\":0,\"y\":1,\"z\":-2}}")]
    [InlineData("wander_nearby", "{\"radius\":20}")]
    [InlineData("flee_from_threat", "{\"threat_actor_id\":\"actor-1\"}")]
    [InlineData("play_verified_gesture", "{\"gesture_id\":\"wave\"}")]
    public void FiniteArgumentContract(string action, string json) => Assert.Null(ActionRegistry.Default.ValidateArguments(action, JObject.Parse(json)));
    [Theory]
    [InlineData("native_hash", "{}")]
    [InlineData("face_player", "{\"duration_seconds\":true}")]
    [InlineData("face_player", "{\"duration_seconds\":\"1\"}")]
    [InlineData("face_player", "{\"duration_seconds\":0}")]
    [InlineData("face_player", "{\"native_hash\":123}")]
    [InlineData("stop_following", "{\"duration_seconds\":1}")]
    [InlineData("play_verified_gesture", "{}")]
    [InlineData("go_to_safe_point", "{\"point\":{\"x\":0,\"y\":1}}")]
    [InlineData("go_to_safe_point", "{\"point\":{\"x\":999999,\"y\":1,\"z\":0}}")]
    [InlineData("play_verified_gesture", "{\"gesture_id\":\"\"}")]
    public void InvalidArgumentContract(string action, string json) => Assert.NotNull(ActionRegistry.Default.ValidateArguments(action, JObject.Parse(json)));
    [Fact] public void RegistryMetadataCannotMutateFutureValidation()
    {
        var metadata = ActionRegistry.Default.Metadata("face_player"); metadata["arguments"]!["properties"]!["duration_seconds"]!["maximum"] = 1000000;
        Assert.NotNull(ActionRegistry.Default.ValidateArguments("face_player", JObject.FromObject(new { duration_seconds = 1000 })));
        Assert.Null(ActionRegistry.Default.ValidateArguments("flee_from_threat", JObject.FromObject(new { threat_actor_id = string.Concat(Enumerable.Repeat("😀", 128)) })));
    }
}

using System.Linq;
using System.Text.RegularExpressions;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class ActivityTests
{
    [Theory]
    [InlineData(true, true, true, true, "sprinting")]
    [InlineData(false, true, true, false, "running")]
    [InlineData(false, false, true, true, "walking")]
    [InlineData(false, false, false, true, "still")]
    [InlineData(false, false, false, false, "unknown")]
    public void FasterMovementWins(bool sprinting, bool running, bool walking, bool stopped, string expected) =>
        Assert.Equal(expected, ActorActivity.MovementName(sprinting, running, walking, stopped));

    [Fact] public void WireUsesServiceFieldNamesAndOmitsMissingScenario()
    {
        var encoded = JObject.Parse(Wire.Encode(new ActorActivity { Movement = "running", OnPhone = true }));
        Assert.Equal(new[] { "movement", "on_phone", "swimming", "injured" }, encoded.Properties().Select(p => p.Name));
        Assert.Equal("running", (string)encoded["movement"]!);
        var seated = JObject.Parse(Wire.Encode(new ActorActivity { Movement = "still", Scenario = "sitting_bench" }));
        Assert.Equal("sitting_bench", (string)seated["scenario"]!);
    }

    // The service accepts labels of at most 32 characters and degrades unknown ones to "other".
    [Fact] public void SharedTableHasUniqueScenariosAndServiceSafeLabels()
    {
        var scenarios = ActorActivity.Scenarios;
        Assert.True(scenarios.Count >= 20);
        Assert.Equal(scenarios.Count, scenarios.Select(s => s.Name).Distinct().Count());
        Assert.All(scenarios, s => Assert.Matches(new Regex("^(WORLD|PROP)_HUMAN_[A-Z_]+$"), s.Name));
        Assert.All(scenarios, s => Assert.Matches(new Regex("^[a-z][a-z_]{0,31}$"), s.Label));
        Assert.DoesNotContain(ActorActivity.OtherScenario, scenarios.Select(s => s.Label));
    }
}

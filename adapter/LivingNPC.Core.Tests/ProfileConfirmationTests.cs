using System;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class ProfileConfirmationTests
{
    private static WorldState World() => new() { ProtagonistId = "franklin", GameDatetime = new DateTime(2013, 9, 18, 12, 0, 0) };

    [Fact] public void FirstLoadNeedsAWarningAndExplicitSelection()
    {
        var gate = new ProfileConfirmation(); var world = World();
        gate.Observe(world);
        Assert.False(gate.Confirmed);
        Assert.False(gate.Confirm(world, "profile-a", "slot-1"));
        Assert.Contains("new profile", gate.Warning("", "", "F10"));
        Assert.False(gate.Confirm(world, "", "slot-1"));
        Assert.False(gate.Confirm(world, "profile-a", " "));
        var warning = gate.Warning("profile-a", "slot-1", "F10");
        Assert.Contains("profile-a / slot slot-1", warning);
        Assert.Contains("F10", warning);
        Assert.True(gate.Confirm(world, "profile-a", "slot-1"));
        world.GameDatetime = world.GameDatetime.AddMinutes(1); gate.Observe(world);
        Assert.True(gate.Confirmed);
    }

    [Theory][InlineData("earlier-save")][InlineData("same-time-slot")][InlineData("protagonist")][InlineData("reconnect")]
    public void SessionChangesRequireAnotherWarningAndConfirmation(string change)
    {
        var gate = new ProfileConfirmation(); var world = World();
        gate.Observe(world); gate.Warning("profile-a", "slot-1", "F10");
        Assert.True(gate.Confirm(world, "profile-a", "slot-1"));
        if (change == "earlier-save") world.GameDatetime = world.GameDatetime.AddDays(-1);
        if (change == "same-time-slot") { world.Loading = true; gate.Observe(world); world.Loading = false; }
        if (change == "protagonist") world.ProtagonistId = "michael";
        gate.Observe(world, change == "reconnect");
        Assert.False(gate.Confirmed);
        Assert.False(gate.Confirm(world, "profile-a", "slot-1"));
        Assert.Contains("Different or unknown save", gate.Warning("profile-a", "slot-1", "F10"));
        Assert.True(gate.Confirm(world, "profile-a", "slot-1"));
    }

    [Fact] public void WorldChangingAfterTheWarningCannotBeConfirmed()
    {
        var gate = new ProfileConfirmation(); var world = World();
        gate.Observe(world); gate.Warning("profile-a", "slot-1", "F10");
        world.Loading = true;
        Assert.False(gate.Confirm(world, "profile-a", "slot-1"));
        world.Loading = false;
        Assert.False(gate.Confirm(world, "profile-a", "slot-1"));
    }

    [Fact] public void SessionWireDoesNotDefaultToAnExistingProfile()
    {
        var defaults = new SessionRequest();
        Assert.Empty(defaults.ProfileId); Assert.Empty(defaults.SaveSlot); Assert.False(defaults.ProfileConfirmed);
        var selected = new SessionRequest { ProfileId = "profile-a", SaveSlot = "slot-1", ProfileConfirmed = true };
        var wire = JObject.Parse(Wire.Encode(selected));
        Assert.Equal("profile-a", (string?)wire["profile_id"]);
        Assert.Equal("slot-1", (string?)wire["save_slot"]);
        Assert.True((bool?)wire["profile_confirmed"]);
    }
}

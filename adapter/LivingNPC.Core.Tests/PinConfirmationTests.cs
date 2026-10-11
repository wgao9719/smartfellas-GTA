using System;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class PinConfirmationTests
{
    private static readonly DateTimeOffset Now = ControllerTests.Now;

    [Fact] public void SecondPressMustFollowWithinTheWindow()
    {
        var pins = new PinConfirmation();
        Assert.Equal(PinPress.Armed, pins.Press("npc", "instance-1", Now));
        Assert.Equal(PinPress.Armed, pins.Press("npc", "instance-1", Now.AddSeconds(5.5)));
        Assert.Null(pins.PendingActorId);
        Assert.Equal(PinPress.Confirmed, pins.Press("npc", "instance-1", Now.AddSeconds(10)));
        Assert.Equal("npc", pins.PendingActorId);
    }

    [Fact] public void ADifferentActorOrInstanceRearms()
    {
        var pins = new PinConfirmation();
        Assert.Equal(PinPress.Armed, pins.Press("npc", "instance-1", Now));
        Assert.Equal(PinPress.Armed, pins.Press("npc", "instance-2", Now.AddSeconds(1)));
        Assert.Equal(PinPress.Armed, pins.Press("other", "instance-2", Now.AddSeconds(2)));
        Assert.Equal(PinPress.Confirmed, pins.Press("other", "instance-2", Now.AddSeconds(3)));
    }

    [Fact] public void OnlyOneRequestWaitsUntilCompletionOrTimeout()
    {
        var pins = new PinConfirmation();
        pins.Press("npc", "instance-1", Now); pins.Press("npc", "instance-1", Now.AddSeconds(1));
        Assert.Equal(PinPress.Pending, pins.Press("other", "instance-2", Now.AddSeconds(2)));
        Assert.Equal(PinPress.Pending, pins.Press("npc", "instance-1", Now.AddSeconds(10.5)));
        Assert.Equal(PinPress.Armed, pins.Press("other", "instance-2", Now.AddSeconds(11)));
        Assert.Null(pins.PendingActorId);
        Assert.Equal(PinPress.Confirmed, pins.Press("other", "instance-2", Now.AddSeconds(12)));
        pins.Complete("npc"); Assert.Equal("other", pins.PendingActorId);
        pins.Complete("other"); Assert.Null(pins.PendingActorId);
        pins.Press("npc", "instance-1", Now.AddSeconds(13)); pins.Reset();
        Assert.Equal(PinPress.Armed, pins.Press("npc", "instance-1", Now.AddSeconds(14)));
    }

    [Theory][InlineData("Resident 3f9a", true)][InlineData("Ana María", true)][InlineData("", false)][InlineData(" padded", false)][InlineData("a<b>", false)]
    [InlineData("tab\there", false)][InlineData("rtl‮override", false)][InlineData("emoji\U0001F600", false)]
    public void DisplayNamesAreDisplaySafe(string name, bool valid) => Assert.Equal(valid, PinPolicy.ValidateName(name) == null);
}

using System;
using System.Collections.Generic;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class DecoratorRegistrationTests
{
    [Fact] public void StartupLockMustBeTemporarilyReleasedToRegister()
    {
        var locked = true; var registered = false; var transitions = new List<bool>();
        var result = DecoratorRegistration.TryRegister(() => registered, () => locked,
            value => { locked = value; transitions.Add(value); }, () => { if (!locked) registered = true; });
        Assert.True(result);
        Assert.True(locked);
        Assert.Equal(new[] { false, true }, transitions);
    }

    [Fact] public void AlreadyRegisteredKeyDoesNotNeedTheNewerLockApi()
    {
        Assert.True(DecoratorRegistration.TryRegister(() => true, () => throw new NotSupportedException(),
            _ => throw new NotSupportedException(), () => throw new InvalidOperationException()));
    }

    [Theory][InlineData(true)][InlineData(false)]
    public void RegistrationExceptionRestoresTheOriginalLock(bool initiallyLocked)
    {
        var locked = initiallyLocked; var registerCalled = false;
        Assert.False(DecoratorRegistration.TryRegister(() => false, () => locked, value => locked = value,
            () => { registerCalled = true; throw new InvalidOperationException(); }));
        Assert.True(registerCalled);
        Assert.Equal(initiallyLocked, locked);
    }

    [Fact] public void UnlockedRuntimeRemainsUnlocked()
    {
        var locked = false; var registered = false;
        Assert.True(DecoratorRegistration.TryRegister(() => registered, () => locked, value => locked = value,
            () => registered = true));
        Assert.False(locked);
    }

    [Fact] public void UnsupportedLockApiFailsClosedWithoutRegistering()
    {
        var registerCalled = false;
        Assert.False(DecoratorRegistration.TryRegister(() => false, () => throw new NotSupportedException(),
            _ => throw new NotSupportedException(), () => registerCalled = true));
        Assert.False(registerCalled);
    }

    [Fact] public void SilentUnlockFailureDoesNotAttemptRegistration()
    {
        var registerCalled = false;
        Assert.False(DecoratorRegistration.TryRegister(() => false, () => true, _ => { }, () => registerCalled = true));
        Assert.False(registerCalled);
    }

    [Fact] public void SilentRegistrationFailureRestoresLockAndFailsClosed()
    {
        var locked = true;
        Assert.False(DecoratorRegistration.TryRegister(() => false, () => locked, value => locked = value, () => { }));
        Assert.True(locked);
    }

    [Fact] public void UnlockExceptionStillAttemptsToRestoreLock()
    {
        var locked = true;
        Assert.False(DecoratorRegistration.TryRegister(() => false, () => locked,
            value => { locked = value; if (!value) throw new InvalidOperationException(); }, () => { }));
        Assert.True(locked);
    }

    [Fact] public void FailureToRestoreLockNeverReportsSuccess()
    {
        var locked = true; var registered = false;
        Assert.False(DecoratorRegistration.TryRegister(() => registered, () => locked,
            value => { if (!value) locked = false; }, () => registered = true));
    }

    [Fact] public void RegistrationVerificationExceptionFailsClosedAfterRestoringLock()
    {
        var locked = true; var registered = false;
        Assert.False(DecoratorRegistration.TryRegister(() => registered ? throw new InvalidOperationException() : false,
            () => locked, value => locked = value, () => registered = true));
        Assert.True(locked);
    }
}

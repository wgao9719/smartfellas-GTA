using System;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class RuntimeBindingGuardTests
{
    [Fact] public void SameModelRecycleElsewhereMustInvalidateWithoutMarkerEvidence()
    {
        var original = Sample();
        var guard = new RuntimeBindingGuard(original);
        var replacement = Sample(x: 200, time: 16);
        Assert.True(guard.Validate(original));
        Assert.False(guard.Validate(replacement));
        Assert.False(guard.Validate(original));
        Assert.True(new RuntimeBindingGuard(replacement).IsValid);
    }

    [Fact] public void SlowWalkingRemainsValidAcrossManyTicks()
    {
        var guard = new RuntimeBindingGuard(Sample());
        for (var frame = 1; frame <= 600; frame++) Assert.True(guard.Validate(Sample(x: frame * .024, time: frame * 16)));
        Assert.True(guard.IsValid);
    }

    [Fact] public void PositionSlackCannotAccumulateIntoImplausibleFootSpeed()
    {
        var guard = new RuntimeBindingGuard(Sample());
        Assert.True(guard.Validate(Sample(x: 1.6, time: 16)));
        Assert.False(guard.Validate(Sample(x: 3.2, time: 32)));
    }

    [Theory]
    [InlineData(100, 0, 0, 16)]
    [InlineData(0, 100, 0, 16)]
    [InlineData(0, 0, 100, 16)]
    [InlineData(30, 0, 0, 1000)]
    public void TeleportLikeJumpPermanentlyInvalidates(double x, double y, double z, long time)
    {
        var guard = new RuntimeBindingGuard(Sample());
        Assert.False(guard.Validate(Sample(x: x, y: y, z: z, time: time)));
        Assert.False(guard.Validate(Sample(time: time + 16)));
    }

    [Fact] public void VehicleEntryAndContinuousDrivingRemainValid()
    {
        var guard = new RuntimeBindingGuard(Sample());
        Assert.True(guard.Validate(Sample(x: .5, time: 100, inVehicle: true)));
        Assert.True(guard.Validate(Sample(x: 50.5, time: 1100, inVehicle: true)));
        Assert.True(guard.Validate(Sample(x: 100.5, time: 2100, inVehicle: true)));
        Assert.True(guard.Validate(Sample(x: 101, time: 2200, inVehicle: false)));
    }

    [Fact] public void VehicleEntryCannotExcuseAJump()
    {
        var guard = new RuntimeBindingGuard(Sample());
        Assert.False(guard.Validate(Sample(x: 12, time: 100, inVehicle: true)));
    }

    [Fact] public void TeleportInsideVehicleIsRejected()
    {
        var guard = new RuntimeBindingGuard(Sample(inVehicle: true));
        Assert.False(guard.Validate(Sample(x: 200, time: 1000, inVehicle: true)));
    }

    [Fact] public void SameTimeStationarySamplesAreSafeButMovementIsNot()
    {
        var guard = new RuntimeBindingGuard(Sample(time: 100));
        Assert.True(guard.Validate(Sample(time: 100)));
        Assert.False(guard.Validate(Sample(x: .001, time: 100)));
        Assert.False(guard.Validate(Sample(time: 116)));
    }

    [Theory][InlineData(false, true)][InlineData(true, false)]
    public void DespawnOrDeathCannotRecoverOrRevive(bool exists, bool alive)
    {
        var guard = new RuntimeBindingGuard(Sample());
        Assert.False(guard.Validate(Sample(exists: exists, alive: alive, time: 16)));
        Assert.False(guard.Validate(Sample(time: 32)));
    }

    [Fact] public void DifferentHandleOrModelInvalidates()
    {
        Assert.False(new RuntimeBindingGuard(Sample()).Validate(Sample(handle: -11, time: 16)));
        Assert.False(new RuntimeBindingGuard(Sample()).Validate(Sample(modelHash: Joaat.Hash("a_m_y_business_02"), time: 16)));
    }

    [Theory][InlineData(99)][InlineData(2101)]
    public void BackwardsTimeOrObservationGapFailsClosed(long time)
    {
        var guard = new RuntimeBindingGuard(Sample(time: 100));
        Assert.False(guard.Validate(Sample(time: time)));
        Assert.False(guard.Validate(Sample(time: 2117)));
    }

    [Fact] public void InjuryIsAllowedButHealthRecoveryIsDiscontinuity()
    {
        var guard = new RuntimeBindingGuard(Sample());
        Assert.True(guard.Validate(Sample(health: 120, time: 16)));
        Assert.False(guard.Validate(Sample(health: 121, time: 32)));
        Assert.False(guard.Validate(Sample(health: 120, time: 48)));
    }

    [Fact] public void MaximumHealthChangeInvalidates()
    {
        var guard = new RuntimeBindingGuard(Sample());
        Assert.False(guard.Validate(Sample(maxHealth: 201, time: 16)));
    }

    [Theory]
    [InlineData("mission")][InlineData("registry")][InlineData("persistence")][InlineData("script")]
    [InlineData("belongs")][InlineData("cuffed")][InlineData("police_vehicle")][InlineData("player_group")]
    [InlineData("relationship")][InlineData("ped_type")][InlineData("animal")][InlineData("not_human")]
    public void OwnershipOrPolicyFactsChangeInvalidates(string change)
    {
        var guard = new RuntimeBindingGuard(Sample());
        Assert.False(guard.Validate(Sample(time: 16, facts: ChangedFacts(change))));
        Assert.False(guard.Validate(Sample(time: 32)));
    }

    [Theory]
    [InlineData("missing")][InlineData("dead")][InlineData("handle")][InlineData("model")][InlineData("facts")]
    [InlineData("unknown_model")][InlineData("unknown_type")][InlineData("unknown_group")][InlineData("mismatched_model")]
    [InlineData("zero_health")][InlineData("zero_max_health")][InlineData("excess_health")][InlineData("negative_time")]
    [InlineData("nan_x")][InlineData("infinite_y")][InlineData("infinite_z")][InlineData("protected")]
    public void InvalidInitialSampleOrLaterEvidenceFailsClosed(string reason)
    {
        var sample = InvalidSample(reason);
        var guard = new RuntimeBindingGuard(sample);
        Assert.False(guard.IsValid);
        Assert.False(guard.Validate(Sample()));
        Assert.False(new RuntimeBindingGuard(Sample()).Validate(sample));
    }

    [Fact] public void MissingSampleFailsClosed()
    {
        Assert.False(new RuntimeBindingGuard(null).IsValid);
        var guard = new RuntimeBindingGuard(Sample());
        Assert.False(guard.Validate(null));
        Assert.False(guard.Validate(Sample()));
    }

    [Fact] public void ObservationCopiesFactsAndCannotBeMutatedByCaller()
    {
        var facts = Civilian();
        var sample = Sample(facts: facts);
        facts.MissionEntity = true;
        sample.Facts!.InModRegistry = true;
        var guard = new RuntimeBindingGuard(sample);
        Assert.True(guard.IsValid);
        Assert.True(guard.Validate(Sample(time: 16)));
    }

    [Theory][InlineData(false)][InlineData(true)]
    public void AuthorizedPinAdoptsOnlyTheExpectedOwnershipChange(bool tookPersistence)
    {
        var guard = new RuntimeBindingGuard(Sample());
        var pinned = PinnedFacts(tookPersistence);
        Assert.True(guard.AcceptPinTransition(Sample(time: 16), Sample(time: 16, facts: pinned), tookPersistence));
        Assert.True(guard.Validate(Sample(x: .03, time: 32, facts: pinned)));
        Assert.False(guard.Validate(Sample(time: 48)));
    }

    [Fact] public void PinCannotResurrectInvalidatedBinding()
    {
        var guard = new RuntimeBindingGuard(Sample());
        guard.Invalidate();
        Assert.False(guard.AcceptPinTransition(Sample(time: 16), Sample(time: 16, facts: PinnedFacts(false)), false));
    }

    [Fact] public void RecycleBeforePinFailsWithoutAcceptingNewOwnership()
    {
        var guard = new RuntimeBindingGuard(Sample());
        Assert.False(guard.AcceptPinTransition(Sample(x: 100, time: 16), Sample(x: 100, time: 16, facts: PinnedFacts(false)), false));
        Assert.False(guard.Validate(Sample(time: 32)));
    }

    [Theory]
    [InlineData("movement")][InlineData("model")][InlineData("handle")][InlineData("death")][InlineData("despawn")]
    [InlineData("health")][InlineData("vehicle")][InlineData("cuffed")][InlineData("script")]
    public void DiscontinuityDuringPinPermanentlyInvalidates(string change)
    {
        var guard = new RuntimeBindingGuard(Sample());
        var after = ChangedPinSample(change);
        Assert.False(guard.AcceptPinTransition(Sample(time: 16), after, false));
        Assert.False(guard.Validate(Sample(time: 48)));
    }

    [Theory]
    [InlineData(false, true, true, true)]
    [InlineData(true, false, false, false)]
    [InlineData(true, true, false, true)]
    [InlineData(true, true, true, false)]
    public void PinPersistenceNeedsExplicitAuthorizationAndOwnershipProof(bool authorized, bool tookPersistence, bool mission, bool belongs)
    {
        var facts = PinnedFacts(tookPersistence); facts.MissionEntity = mission; facts.BelongsToModScript = belongs;
        var guard = new RuntimeBindingGuard(Sample());
        Assert.False(guard.AcceptPinTransition(Sample(time: 16), Sample(time: 16, facts: facts), authorized));
        Assert.False(guard.IsValid);
    }

    [Fact] public void RepeatingPinOrOmittingRegistryTransitionFailsClosed()
    {
        var guard = new RuntimeBindingGuard(Sample());
        var pinned = Sample(time: 16, facts: PinnedFacts(false));
        Assert.True(guard.AcceptPinTransition(Sample(time: 16), pinned, false));
        Assert.False(guard.AcceptPinTransition(pinned, pinned, false));
        Assert.False(new RuntimeBindingGuard(Sample()).AcceptPinTransition(Sample(), Sample(), false));
    }

    [Fact] public void ExplicitInvalidationCannotRecover()
    {
        var guard = new RuntimeBindingGuard(Sample());
        guard.Invalidate();
        Assert.False(guard.IsValid);
        Assert.False(guard.Validate(Sample(time: 16)));
    }

    private static PedFacts Civilian() => new PedFacts
    { ModelHash = Joaat.Hash("a_m_y_business_01"), Human = true, PedType = 4, RelationshipGroupHash = Joaat.Hash("CIVMALE") };

    private static RuntimeBindingSample Sample(double x = 0, double y = 0, double z = 0, long time = 0, int handle = -10,
        int? modelHash = null, bool exists = true, bool alive = true, int health = 150, int maxHealth = 200,
        bool inVehicle = false, PedFacts? facts = null, bool factsUnavailable = false)
    {
        var observed = facts ?? Civilian();
        if (modelHash.HasValue) observed.ModelHash = modelHash.Value;
        return new RuntimeBindingSample(exists, alive, handle, modelHash ?? observed.ModelHash,
            new TargetVector(x, y, z), time, health, maxHealth, inVehicle, factsUnavailable ? null : observed);
    }

    private static PedFacts ChangedFacts(string change)
    {
        var facts = Civilian();
        switch (change)
        {
            case "mission": facts.MissionEntity = true; break;
            case "registry": facts.InModRegistry = true; break;
            case "persistence": facts.TookPersistence = true; break;
            case "script": facts.OwningScript = "other_script"; break;
            case "belongs": facts.BelongsToModScript = true; break;
            case "cuffed": facts.Cuffed = true; break;
            case "police_vehicle": facts.InPoliceVehicle = true; break;
            case "player_group": facts.PlayerGroup = true; break;
            case "relationship": facts.RelationshipGroupHash = Joaat.Hash("CIVFEMALE"); break;
            case "ped_type": facts.PedType = 5; break;
            case "animal": facts.AnimalModel = true; break;
            case "not_human": facts.Human = false; break;
            default: throw new ArgumentException(change);
        }
        return facts;
    }

    private static RuntimeBindingSample InvalidSample(string reason)
    {
        var facts = Civilian();
        switch (reason)
        {
            case "missing": return Sample(exists: false);
            case "dead": return Sample(alive: false);
            case "handle": return Sample(handle: 0);
            case "model": return Sample(modelHash: 0);
            case "facts": return Sample(factsUnavailable: true);
            case "unknown_model": return Sample(modelHash: 123);
            case "unknown_type": facts.PedType = -1; return Sample(facts: facts);
            case "unknown_group": facts.RelationshipGroupHash = 123; return Sample(facts: facts);
            case "mismatched_model": return new RuntimeBindingSample(true, true, -10, 123, new TargetVector(), 0, 150, 200, false, facts);
            case "zero_health": return Sample(health: 0);
            case "zero_max_health": return Sample(maxHealth: 0);
            case "excess_health": return Sample(health: 201);
            case "negative_time": return Sample(time: -1);
            case "nan_x": return Sample(x: double.NaN);
            case "infinite_y": return Sample(y: double.PositiveInfinity);
            case "infinite_z": return Sample(z: double.NegativeInfinity);
            case "protected": return Sample(facts: ChangedFacts("mission"));
            default: throw new ArgumentException(reason);
        }
    }

    private static PedFacts PinnedFacts(bool tookPersistence)
    {
        var facts = Civilian(); facts.InModRegistry = true; facts.TookPersistence = tookPersistence;
        facts.MissionEntity = tookPersistence; facts.BelongsToModScript = tookPersistence;
        facts.OwningScript = tookPersistence ? "LivingNPC" : null;
        return facts;
    }

    private static RuntimeBindingSample ChangedPinSample(string change)
    {
        var facts = PinnedFacts(false);
        switch (change)
        {
            case "movement": return Sample(x: 100, time: 32, facts: facts);
            case "model": return Sample(modelHash: Joaat.Hash("a_m_y_business_02"), time: 32, facts: facts);
            case "handle": return Sample(handle: -11, time: 32, facts: facts);
            case "death": return Sample(alive: false, time: 32, facts: facts);
            case "despawn": return Sample(exists: false, time: 32, facts: facts);
            case "health": return Sample(health: 149, time: 32, facts: facts);
            case "vehicle": return Sample(inVehicle: true, time: 32, facts: facts);
            case "cuffed": facts.Cuffed = true; return Sample(time: 32, facts: facts);
            case "script": facts.OwningScript = "other_script"; return Sample(time: 32, facts: facts);
            default: throw new ArgumentException(change);
        }
    }
}

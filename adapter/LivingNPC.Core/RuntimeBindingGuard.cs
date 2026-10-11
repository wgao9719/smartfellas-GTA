using System;

namespace LivingNPC.Core
{
    // A copied observation, never a live entity reference or durable identity.
    public sealed class RuntimeBindingSample
    {
        private readonly PedFacts? facts;
        public bool Exists { get; }
        public bool Alive { get; }
        public int Handle { get; }
        public int ModelHash { get; }
        public TargetVector Position { get; }
        public long SampleTimeMs { get; }
        public int Health { get; }
        public int MaxHealth { get; }
        public bool InVehicle { get; }
        public PedFacts? Facts => Copy(facts);
        internal PedFacts? ObservedFacts => facts;

        public RuntimeBindingSample(bool exists, bool alive, int handle, int modelHash, TargetVector position,
            long sampleTimeMs, int health, int maxHealth, bool inVehicle, PedFacts? facts)
        {
            Exists = exists; Alive = alive; Handle = handle; ModelHash = modelHash; Position = position;
            SampleTimeMs = sampleTimeMs; Health = health; MaxHealth = maxHealth; InVehicle = inVehicle; this.facts = Copy(facts);
        }

        private static PedFacts? Copy(PedFacts? value) => value == null ? null : new PedFacts
        {
            ModelHash = value.ModelHash, PedType = value.PedType, RelationshipGroupHash = value.RelationshipGroupHash,
            Human = value.Human, AnimalModel = value.AnimalModel, Cuffed = value.Cuffed, InPoliceVehicle = value.InPoliceVehicle,
            PlayerGroup = value.PlayerGroup, MissionEntity = value.MissionEntity, InModRegistry = value.InModRegistry,
            TookPersistence = value.TookPersistence, BelongsToModScript = value.BelongsToModScript, OwningScript = value.OwningScript
        };
    }

    // FR-ID-01..03: evidence of uninterrupted observation only. Any doubt retires
    // this binding permanently; a replacement must receive a new actor identity.
    public sealed class RuntimeBindingGuard
    {
        public const long MaximumSampleGapMs = 2000;
        public const double MaximumFootSpeed = 15, MaximumVehicleSpeed = 120, PositionSlack = 1.5;
        private RuntimeBindingSample? last;
        private double motionSlack = PositionSlack;
        public bool IsValid { get; private set; }

        public RuntimeBindingGuard(RuntimeBindingSample? sample)
        {
            IsValid = Usable(sample);
            if (IsValid) last = sample;
        }

        public bool Validate(RuntimeBindingSample? sample)
        {
            if (!IsValid) return false;
            if (!Continuous(last!, sample, out var remainingSlack) || !SameFacts(last!.ObservedFacts!, sample!.ObservedFacts!)) Invalidate();
            else { last = sample; motionSlack = remainingSlack; }
            return IsValid;
        }

        // Called only around an explicitly authorized pin. Revalidation cannot
        // conceal movement, damage, or another script taking the entity mid-pin.
        public bool AcceptPinTransition(RuntimeBindingSample? before, RuntimeBindingSample? after, bool tookPersistence)
        {
            if (!Validate(before)) return false;
            if (!Continuous(before!, after, out var remainingSlack) || before!.Health != after!.Health || before.InVehicle != after.InVehicle ||
                !PinFacts(before.ObservedFacts!, after.ObservedFacts!, tookPersistence)) Invalidate();
            else { last = after; motionSlack = remainingSlack; }
            return IsValid;
        }

        public void Invalidate() => IsValid = false;

        private static bool Usable(RuntimeBindingSample? sample)
        {
            if (sample == null || !sample.Exists || !sample.Alive || sample.Handle == 0 || sample.ModelHash == 0 ||
                sample.SampleTimeMs < 0 || !Finite(sample.Position.X) || !Finite(sample.Position.Y) || !Finite(sample.Position.Z) ||
                sample.Health <= 0 || sample.MaxHealth <= 0 || sample.Health > sample.MaxHealth || sample.ObservedFacts == null ||
                sample.ObservedFacts.ModelHash != sample.ModelHash) return false;
            var facts = sample.ObservedFacts;
            if (facts.TookPersistence && (!facts.InModRegistry || !facts.MissionEntity || !facts.BelongsToModScript)) return false;
            var classification = PedClassifier.Classify(facts);
            return classification.Role == PedRole.Civilian && classification.Policy == ControlPolicy.Ambient &&
                (classification.Ownership == Ownership.Ambient || classification.Ownership == Ownership.ModOwned);
        }

        private bool Continuous(RuntimeBindingSample previous, RuntimeBindingSample? sample, out double remainingSlack)
        {
            remainingSlack = motionSlack;
            if (!Usable(sample) || sample!.Handle != previous.Handle || sample.ModelHash != previous.ModelHash ||
                sample.Health > previous.Health || sample.MaxHealth != previous.MaxHealth || sample.SampleTimeMs < previous.SampleTimeMs) return false;
            var elapsed = sample.SampleTimeMs - previous.SampleTimeMs;
            if (elapsed > MaximumSampleGapMs) return false;
            var dx = sample.Position.X - previous.Position.X;
            var dy = sample.Position.Y - previous.Position.Y;
            var dz = sample.Position.Z - previous.Position.Z;
            var distance = Math.Sqrt(dx * dx + dy * dy + dz * dz);
            // Entry/exit keeps the foot budget; reporting vehicle entry must not
            // excuse a recycled handle or teleport on that very observation.
            var speed = previous.InVehicle && sample.InVehicle ? MaximumVehicleSpeed : MaximumFootSpeed;
            if (!Finite(distance)) return false;
            if (elapsed == 0) return distance == 0;
            // Slack is a bounded reserve, not a new allowance every tick.
            remainingSlack = Math.Min(PositionSlack, motionSlack + speed * elapsed / 1000.0 - distance);
            return remainingSlack >= 0;
        }

        private static bool SameRoleFacts(PedFacts left, PedFacts right) =>
            left.ModelHash == right.ModelHash && left.PedType == right.PedType && left.RelationshipGroupHash == right.RelationshipGroupHash &&
            left.Human == right.Human && left.AnimalModel == right.AnimalModel && left.Cuffed == right.Cuffed &&
            left.InPoliceVehicle == right.InPoliceVehicle && left.PlayerGroup == right.PlayerGroup;

        private static bool SameFacts(PedFacts left, PedFacts right) => SameRoleFacts(left, right) &&
            left.MissionEntity == right.MissionEntity && left.InModRegistry == right.InModRegistry && left.TookPersistence == right.TookPersistence &&
            left.BelongsToModScript == right.BelongsToModScript && string.Equals(left.OwningScript, right.OwningScript, StringComparison.Ordinal);

        private static bool PinFacts(PedFacts before, PedFacts after, bool tookPersistence)
        {
            if (!SameRoleFacts(before, after) || before.InModRegistry || before.TookPersistence || before.MissionEntity ||
                !string.IsNullOrEmpty(before.OwningScript) || !after.InModRegistry || after.TookPersistence != tookPersistence) return false;
            return tookPersistence ? after.MissionEntity && after.BelongsToModScript :
                after.MissionEntity == before.MissionEntity && after.BelongsToModScript == before.BelongsToModScript &&
                string.Equals(after.OwningScript, before.OwningScript, StringComparison.Ordinal);
        }

        private static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);
    }
}

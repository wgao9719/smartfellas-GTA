using System;
using System.Globalization;

namespace LivingNPC.Core
{
    // FR-ID-03: the player may pin only a live, ambient, fully civilian pedestrian.
    public static class PinPolicy
    {
        public const int MaxPins = 8;
        public static string? Check(ActorState? actor, WorldState world, long generation)
        {
            if (!world.Safe) return "unsafe_world";
            if (actor == null || !actor.Exists || !actor.Alive || actor.Generation != generation || string.IsNullOrWhiteSpace(actor.ActorId) || string.IsNullOrWhiteSpace(actor.RuntimeInstanceId)) return "invalid_actor";
            if (actor.Ownership == Ownership.ModOwned) return "already_pinned";
            if (actor.Ownership != Ownership.Ambient || actor.MissionEntity) return "script_owned_actor";
            if (actor.Role != PedRole.Civilian) return "not_civilian";
            if (actor.Policy != ControlPolicy.Ambient) return "protected_actor";
            if (actor.Busy || actor.InCombat) return "actor_busy";
            if (float.IsNaN(actor.Distance) || float.IsInfinity(actor.Distance) || actor.Distance < 0 || actor.Distance > 8) return "actor_out_of_range";
            return null;
        }
        // Display-safe: 1-48 UTF-16 units, no control, format, surrogate, private, or unassigned characters, and no ~<>.
        public static string? ValidateName(string? name)
        {
            if (name == null || name.Length < 1 || name.Length > 48 || name.Trim() != name) return "invalid_display_name";
            foreach (var c in name)
            {
                var category = CharUnicodeInfo.GetUnicodeCategory(c);
                if (c == '~' || c == '<' || c == '>' || category == UnicodeCategory.Control || category == UnicodeCategory.Format || category == UnicodeCategory.Surrogate ||
                    category == UnicodeCategory.PrivateUse || category == UnicodeCategory.OtherNotAssigned) return "invalid_display_name";
            }
            return null;
        }
        public static string Describe(string reason) => reason switch
        {
            "unsafe_world" => "Pinning is unavailable in the current game state.",
            "already_pinned" => "This pedestrian is already pinned.",
            "script_owned_actor" => "The game controls this pedestrian.",
            "not_civilian" => "Only ordinary residents can be pinned.",
            "protected_actor" => "This pedestrian is protected.",
            "actor_busy" => "This pedestrian is busy.",
            "actor_out_of_range" => "Move within 8 meters to pin.",
            _ => "Select a nearby resident first."
        };
    }
    public enum PinPress { Armed, Confirmed, Pending }
    // A second press on the same actor and instance within 5 s confirms. One request waits at a time.
    public sealed class PinConfirmation
    {
        public static readonly TimeSpan Window = TimeSpan.FromSeconds(5), Timeout = TimeSpan.FromSeconds(10);
        private string? armedActor, armedInstance;
        private DateTimeOffset armedAt, pendingAt;
        public string? PendingActorId { get; private set; }
        public PinPress Press(string actorId, string instance, DateTimeOffset now)
        {
            Expire(now);
            if (PendingActorId != null) return PinPress.Pending;
            if (armedActor == actorId && armedInstance == instance && now >= armedAt && now - armedAt <= Window)
            {
                Disarm(); PendingActorId = actorId; pendingAt = now; return PinPress.Confirmed;
            }
            armedActor = actorId; armedInstance = instance; armedAt = now; return PinPress.Armed;
        }
        public void Expire(DateTimeOffset now) { if (PendingActorId != null && (now < pendingAt || now - pendingAt >= Timeout)) PendingActorId = null; }
        public void Complete(string actorId) { if (PendingActorId == actorId) PendingActorId = null; }
        public void Disarm() { armedActor = armedInstance = null; }
        public void Reset() { Disarm(); PendingActorId = null; }
    }
}

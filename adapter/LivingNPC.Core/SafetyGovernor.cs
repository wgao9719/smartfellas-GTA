using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    public sealed class SafetyGovernor
    {
        private readonly HashSet<string> capabilities;
        private readonly Dictionary<string, DateTimeOffset> cooldowns = new Dictionary<string, DateTimeOffset>();
        public SafetyGovernor(IEnumerable<string> capabilities) { this.capabilities = new HashSet<string>(capabilities); }
        public string? Check(Command command, string sessionId, long generation, string policyVersion,
            WorldState world, ActorState? actor, DateTimeOffset now)
        {
            if (command.SchemaVersion != 1) return "schema_mismatch";
            if (command.SessionId != sessionId || command.RuntimeGeneration != generation) return "stale_session";
            if (command.ExpiresAt <= now) return "authorization_expired";
            if (command.ExpiresAt > now.AddSeconds(command.Kind == "dialogue.chunk" ? 120 : 60)) return "authorization_too_long";
            if (string.IsNullOrWhiteSpace(command.Id) || string.IsNullOrWhiteSpace(command.IdempotencyKey)) return "missing_identity";
            if (!world.Safe) return "unsafe_world";
            if (actor == null || !actor.Exists || !actor.Alive || actor.Generation != generation) return "invalid_actor";
            if (actor.ActorId != command.ActorId) return "actor_mismatch";
            if (string.IsNullOrWhiteSpace(command.RuntimeInstanceId) || actor.RuntimeInstanceId != command.RuntimeInstanceId) return "stale_actor_binding";
            if (actor.Policy == ControlPolicy.Protected || actor.Ownership == Ownership.Unknown || actor.Ownership == Ownership.GameScriptOwned) return "protected_actor";
            if (actor.MissionEntity && actor.Ownership != Ownership.ModOwned) return "script_owned_actor";
            if (float.IsNaN(actor.Distance) || float.IsInfinity(actor.Distance) || actor.Distance > 15 || actor.Distance < 0) return "actor_out_of_range";
            if (actor.Busy || actor.InCombat) return "actor_busy";
            if (command.Kind != "action.execute") return null;
            if ((string?)command.Payload["policy_version"] != policyVersion) return "policy_mismatch";
            var action = (string?)command.Payload["action_id"] ?? "";
            if (!capabilities.Contains(action)) return "capability_unverified";
            if (actor.Policy == ControlPolicy.ServiceLimited && action != "face_player") return "service_limited";
            if (command.Payload["arguments"] is not JObject args) return "invalid_arguments";
            var argumentError = NativeActions.Check(action, args, actor, command.Payload["consent"]?.Type == JTokenType.Boolean && (bool)command.Payload["consent"]!);
            if (argumentError != null) return argumentError;
            if (cooldowns.TryGetValue(actor.ActorId + ":" + action, out var until) && now < until) return "cooldown";
            return null;
        }
        public void Started(string actorId, string action, DateTimeOffset now) { cooldowns[actorId + ":" + action] = now.AddSeconds(ActionRegistry.Default.Cooldown(action)); }
    }
}

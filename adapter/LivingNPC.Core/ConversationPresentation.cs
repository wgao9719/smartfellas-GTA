using System;

namespace LivingNPC.Core
{
    public sealed class ConversationStatus
    {
        private string message = "";
        private DateTimeOffset until;

        public void Show(string text, DateTimeOffset now)
        {
            message = DisplayText.Clean(text); until = now.AddSeconds(5);
        }

        public string Read(DateTimeOffset now) => now < until ? message : "";
        public void Clear() { message = ""; until = default; }
    }

    public static class TargetPresentation
    {
        public static string? Label(ActorState? actor, WorldState world, long generation)
        {
            if (actor == null || !world.Safe || !actor.Exists || !actor.Alive || actor.Generation != generation ||
                string.IsNullOrWhiteSpace(actor.ActorId) || string.IsNullOrWhiteSpace(actor.RuntimeInstanceId)) return null;
            return "Target (blue marker): " + DisplayText.Clean(actor.Name) + " (" + PedClassifier.Availability(actor, world, generation) + ")";
        }
    }
}

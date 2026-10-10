using System;

namespace LivingNPC.Core
{
    // State only. The adapter owns key events; no audio or game call lives here.
    public sealed class ConversationInput
    {
        public string? SceneId { get; private set; }
        public string? ActorId { get; private set; }
        public string? RuntimeInstanceId { get; private set; }
        public long Generation { get; private set; }
        public bool PushToTalkHeld { get; private set; }
        public bool PushToTalkStartQueued { get; private set; }
        public bool Bound => SceneId != null;

        private static bool Eligible(ActorState? actor, WorldState world, long generation, float distance)
        {
            return world.Safe && actor != null && actor.Exists && actor.Alive &&
                actor.Generation == generation && !string.IsNullOrWhiteSpace(actor.ActorId) &&
                !string.IsNullOrWhiteSpace(actor.RuntimeInstanceId) &&
                actor.Policy != ControlPolicy.Protected && actor.Ownership != Ownership.Unknown &&
                actor.Ownership != Ownership.GameScriptOwned &&
                (!actor.MissionEntity || actor.Ownership == Ownership.ModOwned) &&
                !actor.Busy && !actor.InCombat && !float.IsNaN(actor.Distance) &&
                !float.IsInfinity(actor.Distance) && actor.Distance >= 0 && actor.Distance <= distance;
        }

        public bool TryBind(ActorState? actor, WorldState world, long generation)
        {
            if (Bound || !Eligible(actor, world, generation, 8)) return false;
            SceneId = Guid.NewGuid().ToString("N"); ActorId = actor!.ActorId;
            RuntimeInstanceId = actor.RuntimeInstanceId; Generation = generation;
            return true;
        }

        public bool IsCurrent(ActorState? actor, WorldState world, long generation)
        {
            return Bound && Generation == generation && Eligible(actor, world, generation, 15) &&
                actor!.ActorId == ActorId && actor.RuntimeInstanceId == RuntimeInstanceId;
        }

        public bool StartPushToTalk()
        {
            if (!Bound || PushToTalkHeld) return false;
            PushToTalkHeld = true; return true;
        }

        public bool StopPushToTalk()
        {
            if (!PushToTalkHeld) return false;
            PushToTalkHeld = false; return true;
        }

        public bool TryQueuePushToTalkStart(bool targetSnapshotAccepted)
        {
            if (!PushToTalkHeld || PushToTalkStartQueued || !targetSnapshotAccepted) return false;
            PushToTalkStartQueued = true; return true;
        }

        public void Reset()
        {
            SceneId = ActorId = RuntimeInstanceId = null; Generation = 0;
            PushToTalkHeld = PushToTalkStartQueued = false;
        }
    }
}

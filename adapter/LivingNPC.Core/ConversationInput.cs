using System;
using System.Collections.Generic;

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
        // Locked with the capture at key-down. Only the key that started a capture can stop it.
        public bool Whisper { get; private set; }
        public int PushToTalkKey { get; private set; }
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

        public bool StartPushToTalk(int key, bool whisper)
        {
            if (!Bound || PushToTalkHeld) return false;
            PushToTalkHeld = true; PushToTalkKey = key; Whisper = whisper; return true;
        }

        public bool StopPushToTalk(int key)
        {
            if (!PushToTalkHeld || key != PushToTalkKey) return false;
            PushToTalkHeld = false; return true;
        }

        // Whisper is sent only when true, so normal input still works with an older service.
        public Dictionary<string, object>? ControlPayload()
        {
            if (!Bound) return null;
            var payload = new Dictionary<string, object> { ["scene_id"] = SceneId!, ["runtime_instance_id"] = RuntimeInstanceId! };
            if (Whisper) payload["whisper"] = true;
            return payload;
        }

        public Dictionary<string, object>? TypedPayload(string raw)
        {
            var typed = TypedInput.Parse(raw);
            if (!Bound || string.IsNullOrWhiteSpace(typed.Text)) return null;
            var payload = new Dictionary<string, object> { ["text"] = typed.Text, ["input_mode"] = "typed", ["scene_id"] = SceneId! };
            if (typed.Whisper) payload["whisper"] = true;
            return payload;
        }

        public bool TryQueuePushToTalkStart(bool targetSnapshotAccepted)
        {
            if (!PushToTalkHeld || PushToTalkStartQueued || !targetSnapshotAccepted) return false;
            PushToTalkStartQueued = true; return true;
        }

        public void Reset()
        {
            SceneId = ActorId = RuntimeInstanceId = null; Generation = 0;
            PushToTalkHeld = PushToTalkStartQueued = Whisper = false; PushToTalkKey = 0;
        }
    }

    // A leading "/w " whispers and "//" keeps a literal leading slash. Only the adapter parses; the service takes text literally.
    public sealed class TypedInput
    {
        public string Text { get; }
        public bool Whisper { get; }
        private TypedInput(string text, bool whisper) { Text = text; Whisper = whisper; }

        public static TypedInput Parse(string raw)
        {
            if (raw.StartsWith("//", StringComparison.Ordinal)) return new TypedInput(raw.Substring(1), false);
            var whisper = raw.Length >= 2 && raw[0] == '/' && (raw[1] == 'w' || raw[1] == 'W') && (raw.Length == 2 || raw[2] == ' ');
            return whisper ? new TypedInput(raw.Substring(Math.Min(3, raw.Length)), true) : new TypedInput(raw, false);
        }

        public static string Echo(string raw)
        {
            var typed = Parse(raw);
            return (typed.Whisper ? "You (whispering): " : "You: ") + DisplayText.Clean(typed.Text);
        }
    }
}

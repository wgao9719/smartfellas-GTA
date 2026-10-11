using System;
using System.Collections.Generic;

namespace LivingNPC.Core
{
    // Input lifecycle. The adapter owns key events and selection; no GTA dependency lives here.
    public sealed class ConversationInput
    {
        public bool AllowActions { get; set; }
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
        public bool CanContinueFollow(ActorState? actor, WorldState world, long generation, CommandController? controller) =>
            IsCurrent(actor, world, generation) && !PushToTalkHeld && controller?.IsFollowing(actor!.ActorId, actor.RuntimeInstanceId) == true;

        public bool StartPushToTalk(int key, bool whisper)
        {
            if (!Bound || PushToTalkHeld) return false;
            PushToTalkHeld = true; PushToTalkStartQueued = false; PushToTalkKey = key; Whisper = whisper; return true;
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
            if (AllowActions) payload["consent"] = true;
            return payload;
        }

        public Dictionary<string, object>? TypedPayload(string raw, bool phoneActive = false)
        {
            var typed = TypedInput.Parse(raw);
            if (phoneActive || !Bound || string.IsNullOrWhiteSpace(typed.Text)) return null;
            var payload = new Dictionary<string, object> { ["text"] = typed.Text, ["input_mode"] = "typed", ["scene_id"] = SceneId! };
            if (typed.Whisper) payload["whisper"] = true;
            if (AllowActions) payload["consent"] = true;
            return payload;
        }

        public bool TryQueuePushToTalkStart(bool targetSnapshotAccepted)
        {
            if (!PushToTalkHeld || PushToTalkStartQueued || !targetSnapshotAccepted) return false;
            PushToTalkStartQueued = true; return true;
        }

        // Turn/pin restarts preserve the selection; explicit cancellation clears it by default.
        public void Cancel(CommandController? controller, Action<string?> cancelScene, Action clearSelection, bool clearTarget = true)
        {
            controller?.Cancel(SceneId);
            if (clearTarget && Bound) controller?.Cancel(null);
            if (Bound || clearTarget) cancelScene(clearTarget ? null : SceneId);
            Reset();
            if (clearTarget) clearSelection();
        }

        // Reserve Escape for GTA's pause menu outside typing. The native behavior needs rig verification.
        public static bool ShouldCancel(bool typing, bool configuredCancel, bool escape) =>
            typing ? escape : configuredCancel && !escape;

        public void Reset()
        {
            SceneId = ActorId = RuntimeInstanceId = null; Generation = 0;
            PushToTalkHeld = PushToTalkStartQueued = Whisper = false; PushToTalkKey = 0;
        }
    }

    // GTA control ids, independent of SHVDN. Disable all three input groups while typing.
    public enum ConversationControl
    {
        Phone = 27, PhoneUp = 172, PhoneDown = 173, PhoneLeft = 174, PhoneRight = 175,
        PhoneSelect = 176, PhoneCancel = 177, PhoneOption = 178, PhoneExtraOption = 179,
        PhoneScrollForward = 180, PhoneScrollBackward = 181,
        FrontendDown = 187, FrontendUp = 188, FrontendLeft = 189, FrontendRight = 190,
        FrontendRdown = 191, FrontendRup = 192, FrontendRleft = 193, FrontendRright = 194,
        FrontendPause = 199, FrontendPauseAlternate = 200, FrontendAccept = 201, FrontendCancel = 202,
        FrontendEndscreenAccept = 215, FrontendSelect = 217
    }

    public sealed class ConversationControls
    {
        private readonly HashSet<int> held = new HashSet<int>();
        private static readonly ConversationControl[] controls = (ConversationControl[])Enum.GetValues(typeof(ConversationControl));
        private bool pendingFrame;

        public void KeyDown(int key, bool typing)
        {
            if (typing && (key == 13 || key == 27 || key == 8 || key >= 37 && key <= 40))
            { held.Add(key); pendingFrame = true; }
        }

        public void KeyUp(int key)
        {
            if (held.Remove(key)) pendingFrame = true;
        }

        // Key events precede Tick. Keep Enter/cancel/arrow presses suppressed through release,
        // even when submission or cancellation already closed the text box.
        public void DisableForFrame(bool typing, Action<int, int> disable)
        {
            if (!typing && held.Count == 0 && !pendingFrame) return;
            for (var group = 0; group < 3; group++)
                foreach (var control in controls) disable(group, (int)control);
            pendingFrame = false;
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

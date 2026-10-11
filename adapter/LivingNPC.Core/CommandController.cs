using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    public sealed class CommandController
    {
        private sealed class Active
        {
            public Command Command = null!;
            public string Action = "", Instance = "";
        }
        private sealed class Subtitle { public Command Command = null!; public string Text = "", Speaker = ""; public DateTimeOffset Until; public bool Delivered; }
        private readonly Queue<Subtitle> subtitles = new Queue<Subtitle>();
        private Subtitle? showing;
        private string? statusScene;
        private readonly IGameAdapter game;
        private readonly SafetyGovernor safety;
        private readonly Dictionary<string, Active> active = new Dictionary<string, Active>();
        private readonly Dictionary<string, Ack> results = new Dictionary<string, Ack>();
        private readonly Dictionary<string, DateTimeOffset> canceledScenes = new Dictionary<string, DateTimeOffset>();
        private sealed class ChunkState { public int Index; public DateTimeOffset ExpiresAt; }
        private readonly Dictionary<string, Ack> deferredAcks = new Dictionary<string, Ack>();
        private readonly Dictionary<string, ChunkState> chunks = new Dictionary<string, ChunkState>();
        public int CanceledSceneCount => canceledScenes.Count;
        public int TrackedTurnCount => chunks.Count;
        public readonly BoundedQueue<Command> Commands = new BoundedQueue<Command>(64);
        public readonly BoundedQueue<Ack> Acks = new BoundedQueue<Ack>(512);
        public string SessionId { get; }
        public long Generation { get; }
        public string PolicyVersion { get; }
        public bool FailedClosed { get; private set; }
        public CommandController(IGameAdapter game, string sessionId, long generation, string policyVersion, IEnumerable<string> capabilities)
        {
            this.game = game; safety = new SafetyGovernor(capabilities); SessionId = sessionId; Generation = generation; PolicyVersion = policyVersion;
        }
        private void Acknowledge(Command command, string status, string? reason = null, Dictionary<string, object>? observed = null)
        {
            var ack = new Ack { CommandId = command.Id, SessionId = SessionId, RuntimeGeneration = Generation, Status = status, Reason = reason, Observed = observed };
            results[command.IdempotencyKey] = ack;
            if (!Acks.TryAdd(ack)) { deferredAcks[command.Id] = ack; FailedClosed = true; game.ShowStatus("error", "Acknowledgment queue is full. AI stopped."); }
        }
        public void Tick(DateTimeOffset now)
        {
            foreach (var scene in canceledScenes.Where(pair => pair.Value <= now).Select(pair => pair.Key).ToArray()) canceledScenes.Remove(scene);
            foreach (var turn in chunks.Where(pair => pair.Value.ExpiresAt <= now).Select(pair => pair.Key).ToArray()) chunks.Remove(turn);
            foreach (var item in deferredAcks.ToArray()) { if (!Acks.TryAdd(item.Value)) break; deferredAcks.Remove(item.Key); }
            var world = game.ReadWorld();
            foreach (var item in active.Values.ToArray())
            {
                // Reserve enough space for terminal results. The worker retains failed sends.
                if (Acks.Remaining < 4) break;
                var actor = game.Resolve(item.Command.ActorId!);
                if (!MayRelease(item, actor, world))
                {
                    // Never clear tasks after an engine/mission takeover.
                    Acknowledge(item.Command, "canceled", "actor_or_world_changed"); ForgetAction(item); continue;
                }
                if (now >= item.Command.ExpiresAt)
                {
                    game.Release(item.Command, actor!, item.Action); Acknowledge(item.Command, "expired", "execution_timeout"); ForgetAction(item); continue;
                }
                var observation = game.Observe(item.Command, actor!, item.Action);
                if (observation.Status == EffectStatus.Running) continue;
                var status = observation.Status == EffectStatus.Completed ? "completed" : observation.Status == EffectStatus.TakenOver ? "canceled" : "failed";
                Acknowledge(item.Command, status, observation.Reason, observation.Facts); ForgetAction(item);
            }
            if (FailedClosed) return;
            for (var drained = 0; drained < 4 && Acks.Remaining >= 4 && Commands.TryTake(out var command); drained++) Dispatch(command, world, now);
            AdvanceSubtitles(world, now);
        }
        private void Dispatch(Command command, WorldState world, DateTimeOffset now)
        {
            if (results.TryGetValue(command.IdempotencyKey, out var previous))
            {
                if (!Acks.TryAdd(new Ack { CommandId = command.Id, SessionId = SessionId, RuntimeGeneration = Generation, Status = previous.Status, Reason = previous.Reason, Observed = previous.Observed })) FailedClosed = true;
                return;
            }
            if (results.Count >= 4096) { FailedClosed = true; game.ShowStatus("error", "Session command limit reached. Reload the adapter."); return; }
            if (command.SessionId != SessionId || command.RuntimeGeneration != Generation || command.SchemaVersion != 1)
            { Acknowledge(command, "rejected", "stale_session"); return; }
            if (command.ExpiresAt <= now) { Acknowledge(command, "expired", "authorization_expired"); return; }
            var scene = (string?)command.Payload["scene_id"] ?? "";
            if (command.Kind == "scene.cancel") { Cancel(scene, now, command.Seq); Acknowledge(command, "completed", "local_scene_canceled"); return; }
            if (scene.Length > 0 && canceledScenes.ContainsKey(scene)) { Acknowledge(command, "canceled", "scene_canceled"); return; }
            if (command.Kind == "status")
            {
                if (command.ActorId != null || scene.Length > 0)
                {
                    var invalidStatus = safety.Check(command, SessionId, Generation, PolicyVersion, world, command.ActorId == null ? null : game.Resolve(command.ActorId), now);
                    if (invalidStatus != null) { Acknowledge(command, "rejected", invalidStatus); return; }
                }
                statusScene = scene;
                game.ShowStatus((string?)command.Payload["state"] ?? "", (string?)command.Payload["message"] ?? ""); Acknowledge(command, "completed", "status_rendered"); return;
            }
            var actor = command.ActorId == null ? null : game.Resolve(command.ActorId);
            var reason = safety.Check(command, SessionId, Generation, PolicyVersion, world, actor, now);
            if (reason != null) { Acknowledge(command, reason == "authorization_expired" ? "expired" : "rejected", reason); return; }
            if (command.Kind == "dialogue.chunk")
            {
                var text = (string?)command.Payload["text"] ?? "";
                var turn = (string?)command.Payload["turn_id"] ?? "";
                var index = (int?)command.Payload["chunk_index"] ?? -1;
                var delivery = (string?)command.Payload["delivery_mode"] ?? "subtitle";
                var duration = command.Payload["audio_duration_ms"];
                if (delivery != "subtitle" && delivery != "audio" || duration != null && duration.Type != JTokenType.Null && (duration.Type != JTokenType.Integer || (double)duration < 1 || (double)duration > 30000)) { Acknowledge(command, "rejected", "invalid_audio_subtitle"); return; }
                if (text.Length > 4000 || index < 0 || turn.Length == 0) { Acknowledge(command, "rejected", "invalid_subtitle"); return; }
                // The service owns display normalization. Rewriting here would break delivery receipts.
                var speaker = (string?)command.Payload["speaker"] ?? actor!.Name;
                if (!DisplayText.IsSafe(text) || !DisplayText.IsSafe(speaker)) { Acknowledge(command, "rejected", "unsafe_display_text"); return; }
                if (chunks.TryGetValue(turn, out var last) && index <= last.Index) { Acknowledge(command, "rejected", "duplicate_chunk"); return; }
                if (chunks.Count >= 4096 && !chunks.ContainsKey(turn)) { Acknowledge(command, "rejected", "turn_history_full"); FailedClosed = true; return; }
                chunks[turn] = new ChunkState { Index = index, ExpiresAt = last != null && last.ExpiresAt > command.ExpiresAt ? last.ExpiresAt : command.ExpiresAt };
                if (subtitles.Count >= 64) { Acknowledge(command, "rejected", "subtitle_queue_full"); return; }
                if (delivery == "audio" && text.Length > 0) ReplacePriorAudio(scene);
                subtitles.Enqueue(new Subtitle { Command = command, Speaker = speaker, Text = text });
                return;
            }
            if (command.Kind == "actor.pin")
            {
                var name = command.Payload["display_name"] is JValue { Type: JTokenType.String } named ? (string?)named : null;
                var persistent = command.Payload["actor_kind"] is JValue { Type: JTokenType.String } kind && (string?)kind == "spawned_persistent";
                var refusal = PinPolicy.Check(actor, world, Generation) ?? PinPolicy.ValidateName(name) ??
                    (!persistent ? "invalid_actor_kind" : active.ContainsKey(actor!.ActorId) ? "actor_task_conflict" : null);
                if (refusal != null) { Acknowledge(command, "rejected", refusal); return; }
                if (!game.Pin(command, actor!, name!)) { Acknowledge(command, "rejected", "pin_refused"); return; }
                Acknowledge(command, "completed", "pinned", new Dictionary<string, object> { ["actor_id"] = actor!.ActorId }); return;
            }
            if (command.Kind != "action.execute") { Acknowledge(command, "rejected", "unknown_command"); return; }
            if (active.ContainsKey(actor!.ActorId)) { Acknowledge(command, "rejected", "actor_task_conflict"); return; }
            var action = (string)command.Payload["action_id"]!;
            if (!game.Start(command, actor, action, (JObject)command.Payload["arguments"]!)) { Acknowledge(command, "rejected", "native_task_refused"); return; }
            active.Add(actor.ActorId, new Active { Command = command, Action = action, Instance = actor.RuntimeInstanceId });
            safety.Started(actor.ActorId, action, now);
            Acknowledge(command, "started", "task_dispatched", new Dictionary<string, object> { ["task_dispatched"] = true });
        }
        private void ForgetAction(Active item) { game.Forget(item.Command); active.Remove(item.Command.ActorId!); }
        private bool MayRelease(Active item, ActorState? actor, WorldState world) =>
            world.Safe && actor != null && actor.Exists && actor.Alive && actor.Generation == Generation && actor.RuntimeInstanceId == item.Instance &&
            !actor.Busy && !actor.InCombat && actor.Policy != ControlPolicy.Protected && actor.Ownership != Ownership.GameScriptOwned && actor.Distance <= 15;
        private static Dictionary<string, object> Rendered(Subtitle subtitle) => new Dictionary<string, object> { ["text"] = subtitle.Text, ["delivery"] = "subtitle", ["display_rule"] = DisplayText.Rule };
        private static bool AudioForScene(Subtitle subtitle, string scene) => (string?)subtitle.Command.Payload["delivery_mode"] == "audio" && (string?)subtitle.Command.Payload["scene_id"] == scene;
        private void ReplacePriorAudio(string scene)
        {
            if (showing != null && AudioForScene(showing, scene))
            {
                if (!showing.Delivered)
                {
                    if (game.SubtitleRendered) Acknowledge(showing.Command, "completed", "subtitle_rendered", Rendered(showing));
                    else Acknowledge(showing.Command, "canceled", "audio_subtitle_replaced_before_render");
                }
                showing = null; game.ClearSubtitle();
            }
            var pending = subtitles.ToArray(); subtitles.Clear();
            foreach (var subtitle in pending)
            {
                if (AudioForScene(subtitle, scene)) Acknowledge(subtitle.Command, "canceled", "audio_subtitle_replaced_before_render");
                else subtitles.Enqueue(subtitle);
            }
        }
        private void AdvanceSubtitles(WorldState world, DateTimeOffset now)
        {
            if (showing != null)
            {
                var actor = game.Resolve(showing.Command.ActorId!);
                var invalid = safety.Check(showing.Command, SessionId, Generation, PolicyVersion, world, actor, now);
                if (invalid != null) { if (!showing.Delivered) Acknowledge(showing.Command, "canceled", invalid, game.SubtitleRendered ? Rendered(showing) : null); showing = null; game.ClearSubtitle(); }
                else if (!showing.Delivered && game.SubtitleRendered)
                {
                    Acknowledge(showing.Command, "completed", "subtitle_rendered", Rendered(showing));
                    showing.Delivered = true;
                }
                if (showing != null && showing.Delivered && now >= showing.Until) { showing = null; game.ClearSubtitle(); }
            }
            if (showing != null || subtitles.Count == 0 || Acks.Remaining < 4) return;
            var next = subtitles.Dequeue();
            var reason = safety.Check(next.Command, SessionId, Generation, PolicyVersion, world, game.Resolve(next.Command.ActorId!), now);
            if (reason != null) { Acknowledge(next.Command, "rejected", reason); return; }
            if (next.Text.Length == 0) { Acknowledge(next.Command, "completed", "subtitle_stream_finished", new Dictionary<string, object> { ["text"] = "", ["delivery"] = "subtitle" }); return; }
            var durationMs = (string?)next.Command.Payload["delivery_mode"] == "audio" ? (int?)next.Command.Payload["audio_duration_ms"] : null;
            showing = next; showing.Until = durationMs.HasValue ? now.AddMilliseconds(durationMs.Value) : now.AddSeconds(Math.Max(3, Math.Min(15, next.Text.Length / 12.0)));
            game.ShowSubtitle(next.Speaker, next.Text);
        }
        public void Cancel(string? sceneId = null, DateTimeOffset? canceledAt = null, long? beforeSequence = null)
        {
            if (!string.IsNullOrWhiteSpace(sceneId))
            {
                if (canceledScenes.Count >= 4096 && !canceledScenes.ContainsKey(sceneId!)) { FailedClosed = true; game.ShowStatus("error", "Cancellation history is full. Reload the adapter."); }
                else canceledScenes[sceneId!] = (canceledAt ?? DateTimeOffset.UtcNow).AddSeconds(125);
            }
            var world = game.ReadWorld();
            foreach (var item in active.Values.ToArray())
            {
                if (!string.IsNullOrWhiteSpace(sceneId) && (string?)item.Command.Payload["scene_id"] != sceneId) continue;
                var actor = game.Resolve(item.Command.ActorId!);
                if (MayRelease(item, actor, world)) game.Release(item.Command, actor!, item.Action);
                Acknowledge(item.Command, "canceled", "user_cancel"); ForgetAction(item);
            }
            // Local cancellation invalidates every already queued command, including late speech.
            var retained = new List<Command>();
            while (Commands.TryTake(out var pending))
            {
                if ((!beforeSequence.HasValue || pending.Seq <= beforeSequence.Value) && (string.IsNullOrWhiteSpace(sceneId) || (string?)pending.Payload["scene_id"] == sceneId)) Acknowledge(pending, "canceled", "user_cancel");
                else retained.Add(pending);
            }
            foreach (var item in retained) Commands.TryAdd(item);
            if (showing != null && (string.IsNullOrWhiteSpace(sceneId) || (string?)showing.Command.Payload["scene_id"] == sceneId))
            { if (!showing.Delivered) Acknowledge(showing.Command, "canceled", "user_cancel", game.SubtitleRendered ? Rendered(showing) : null); showing = null; }
            var pendingSubtitles = subtitles.ToArray(); subtitles.Clear();
            foreach (var subtitle in pendingSubtitles)
            {
                if (string.IsNullOrWhiteSpace(sceneId) || (string?)subtitle.Command.Payload["scene_id"] == sceneId) Acknowledge(subtitle.Command, "canceled", "user_cancel");
                else subtitles.Enqueue(subtitle);
            }
            game.ClearSubtitle();
            if (statusScene != null && (string.IsNullOrWhiteSpace(sceneId) || statusScene == sceneId))
            { statusScene = null; game.ShowStatus("canceled", "Living Los Santos: conversation canceled."); }
        }
    }
}

using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    public sealed class SocialLineObservation
    {
        public DateTimeOffset DisplayedAt;
        public WorldState World = null!;
        public ActorState[] Actors = Array.Empty<ActorState>();
        public object[] Witnesses = Array.Empty<object>();
    }
    public sealed class SocialDeliveryDispatch
    {
        public Ack Ack = null!;
        public EventEnvelope[] Snapshots = Array.Empty<EventEnvelope>();
        public object Body = null!;
    }
    // Game APIs run exclusively on the script thread. The worker receives immutable wire data.
    public interface ISocialGameAdapter : IGameAdapter
    {
        bool BeginSocial(string sceneId, ActorState[] actors, double seconds);
        bool SocialReady(string sceneId);
        bool SocialValid(string sceneId);
        void EndSocial(string sceneId);
        void ShowSocialLine(string sceneId, string commandId, string actorId, string text);
        SocialLineObservation? ObserveSocialLine(string commandId);
        void ClearSocialLine(string commandId);
        void AmbientSpeech(string actorId, string context);
    }

    public sealed class SocialController
    {
        private sealed class Scene
        {
            public string Id = "";
            public Dictionary<string, string> Participants = new Dictionary<string, string>();
            public DateTimeOffset Until, NextVoice;
            public bool Ambient;
            public readonly Queue<Command> Lines = new Queue<Command>();
            public Command? Showing;
            public DateTimeOffset LineUntil;
            public bool Delivered;
            public int VoiceIndex;
        }
        private readonly ISocialGameAdapter game;
        private readonly string sessionId;
        private readonly long generation;
        private readonly Dictionary<string, Scene> scenes = new Dictionary<string, Scene>();
        private readonly Dictionary<string, Ack> results = new Dictionary<string, Ack>();
        private readonly HashSet<string> canceled = new HashSet<string>();
        public readonly BoundedQueue<Ack> Acks = new BoundedQueue<Ack>(128);
        public readonly BoundedQueue<Tuple<Command, SocialLineObservation>> Deliveries = new BoundedQueue<Tuple<Command, SocialLineObservation>>(32);
        public readonly BoundedQueue<string> Cancellations = new BoundedQueue<string>(32);
        public bool FailedClosed { get; private set; }
        public string[] ActiveActors => scenes.Values.SelectMany(s => s.Participants.Keys).Distinct().ToArray();
        public int ActiveCount => scenes.Count;
        public SocialController(ISocialGameAdapter game, string sessionId, long generation)
        { this.game = game; this.sessionId = sessionId; this.generation = generation; }
        public static bool Owns(Command command) => command.Kind == "social.scene" || (command.Kind == "dialogue.chunk" && (string?)command.Payload["presentation"] == "overhead");
        private Ack Result(Command command, string status, string reason, Dictionary<string, object>? observed = null)
        {
            var ack = new Ack { CommandId = command.Id, SessionId = sessionId, RuntimeGeneration = generation, Status = status, Reason = reason, Observed = observed };
            if (results.Count >= 4096 && !results.ContainsKey(command.Id)) { FailedClosed = true; return ack; }
            results[command.Id] = ack;
            if (!Acks.TryAdd(ack)) FailedClosed = true;
            return ack;
        }
        public static bool Eligible(ActorState? actor, long generation, float radius = 35) =>
            actor != null && actor.Generation == generation && actor.Exists && actor.Alive && !actor.Busy && !actor.InCombat && !actor.InVehicle && !actor.ModFollowing &&
            actor.Role == PedRole.Civilian && actor.Policy == ControlPolicy.Ambient &&
            (actor.Ownership == Ownership.Ambient || actor.Ownership == Ownership.ModOwned) &&
            (!actor.MissionEntity || actor.Ownership == Ownership.ModOwned) &&
            ActionMotionTracker.IsFinite(actor.Distance) && actor.Distance >= 0 && actor.Distance <= radius &&
            ActionMotionTracker.IsFinite(actor.X) && ActionMotionTracker.IsFinite(actor.Y) && ActionMotionTracker.IsFinite(actor.Z);
        public static double Distance(ActorState a, ActorState b) => Math.Sqrt(Math.Pow(a.X - b.X, 2) + Math.Pow(a.Y - b.Y, 2) + Math.Pow(a.Z - b.Z, 2));
        private ActorState[]? Resolve(Scene scene)
        {
            var actors = scene.Participants.Select(p => game.Resolve(p.Key)).ToArray();
            if (actors.Any(a => !Eligible(a, generation) || a!.RuntimeInstanceId != scene.Participants[a.ActorId])) return null;
            var current = actors.Select(a => a!).ToArray();
            if (current.Any(a => string.IsNullOrEmpty(a.AreaId) || string.IsNullOrEmpty(a.InteriorId) || a.AreaId != current[0].AreaId || a.InteriorId != current[0].InteriorId)) return null;
            return current.Any(a => current.Any(b => Distance(a, b) > 6)) ? null : current;
        }
        public void Handle(Command command, DateTimeOffset now)
        {
            if (results.TryGetValue(command.Id, out var previous)) { if (!Acks.TryAdd(previous)) FailedClosed = true; return; }
            // A poll retry cannot redispatch an outstanding line or native setup.
            if (scenes.Values.Any(s => s.Showing?.Id == command.Id || s.Lines.Any(c => c.Id == command.Id))) return;
            if (FailedClosed || command.SchemaVersion != 1 || command.SessionId != sessionId || command.RuntimeGeneration != generation || command.ExpiresAt <= now || !game.ReadWorld().Safe)
            { Result(command, "rejected", "social_authority_unavailable"); return; }
            var id = (string?)command.Payload["scene_id"] ?? "";
            if (id.Length == 0 || canceled.Contains(id)) { Result(command, "rejected", "social_scene_unavailable"); return; }
            if (command.Kind == "social.scene")
            {
                if (command.Payload["participants"] is not JObject participants || participants.Count < 2 || participants.Count > 3 || scenes.ContainsKey(id) || scenes.Count >= 2)
                { Result(command, "rejected", "invalid_social_participants"); return; }
                var seconds = (double?)command.Payload["duration_seconds"] ?? 45;
                if (double.IsNaN(seconds) || seconds <= 0 || seconds > 45) { Result(command, "rejected", "invalid_social_duration"); return; }
                var scene = new Scene { Id = id, Until = now.AddSeconds(seconds) };
                foreach (var p in participants.Properties())
                {
                    if (p.Value.Type != JTokenType.String || string.IsNullOrWhiteSpace((string?)p.Value)) { Result(command, "rejected", "invalid_social_binding"); return; }
                    scene.Participants[p.Name] = (string)p.Value!;
                }
                var actors = Resolve(scene);
                if (actors == null || scene.Participants.Values.Distinct().Count() != participants.Count || actors.Any(a => a.Distance > 12 || ActiveActors.Contains(a.ActorId)))
                { Result(command, "rejected", "social_actor_unavailable"); return; }
                if (!game.BeginSocial(id, actors, seconds)) { Result(command, "rejected", "social_native_refused"); return; }
                scenes[id] = scene;
                Result(command, "completed", "social_setup_dispatched", new Dictionary<string, object> { ["native_completion_confirmed"] = false });
            }
            else if (command.Kind == "dialogue.chunk")
            {
                var text = (string?)command.Payload["text"];
                if (!scenes.TryGetValue(id, out var scene) || scene.Ambient || command.ActorId == null || !scene.Participants.TryGetValue(command.ActorId, out var binding) || binding != command.RuntimeInstanceId || text == null || text.Length == 0 || text.Length > 1000 || !DisplayText.IsSafe(text) || scene.Lines.Count >= 4)
                { Result(command, "rejected", "invalid_social_line"); return; }
                scene.Lines.Enqueue(command);
            }
        }
        public bool StartAmbient(string id, ActorState[] actors, DateTimeOffset now)
        {
            if (FailedClosed || scenes.Count >= 2 || !game.ReadWorld().Safe || actors.Length != 2 || actors.Any(a => !Eligible(a, generation) || a.Distance <= 12 || ActiveActors.Contains(a.ActorId)) || Distance(actors[0], actors[1]) > 6) return false;
            var scene = new Scene { Id = id, Ambient = true, Until = now.AddSeconds(30), NextVoice = now };
            foreach (var actor in actors) scene.Participants[actor.ActorId] = actor.RuntimeInstanceId;
            if (Resolve(scene) == null || !game.BeginSocial(id, actors, 30)) return false;
            scenes[id] = scene; return true;
        }
        public void Tick(DateTimeOffset now)
        {
            foreach (var scene in scenes.Values.ToArray())
            {
                var actors = Resolve(scene);
                if (FailedClosed || !game.ReadWorld().Safe || actors == null || now >= scene.Until || !game.SocialValid(scene.Id) ||
                    (scene.Ambient ? actors.Any(a => a.Distance <= 12) : actors.Any(a => a.Distance > 12)))
                { Cancel(scene.Id, !scene.Ambient); continue; }
                if (!game.SocialReady(scene.Id)) continue;
                if (scene.Ambient)
                {
                    if (now >= scene.NextVoice) { game.AmbientSpeech(actors[scene.VoiceIndex++ % actors.Length].ActorId, scene.VoiceIndex == 1 ? "GENERIC_HI" : "CHAT_STATE"); scene.NextVoice = now.AddSeconds(6); }
                    continue;
                }
                if (scene.Showing != null)
                {
                    var line = scene.Showing;
                    if (!scene.Delivered)
                    {
                        var observation = game.ObserveSocialLine(line.Id);
                        if (observation != null)
                        {
                            if (observation.Witnesses.Length == 0 || !Deliveries.TryAdd(Tuple.Create(line, observation))) { Cancel(scene.Id, true); continue; }
                            // Transport owns the exact ack and hearing batch together, in order.
                            var ack = new Ack { CommandId = line.Id, SessionId = sessionId, RuntimeGeneration = generation, Status = "completed", Reason = "overhead_rendered", Observed = new Dictionary<string, object> { ["delivery"] = "subtitle", ["text"] = (string)line.Payload["text"]!, ["display_rule"] = DisplayText.Rule, ["displayed_at"] = observation.DisplayedAt.ToString("O") } };
                            if (results.Count >= 4096) { FailedClosed = true; Cancel(scene.Id, true); continue; }
                            results[line.Id] = ack; scene.Delivered = true;
                        }
                    }
                    if (!scene.Delivered && (now >= scene.LineUntil || line.ExpiresAt <= now)) { Cancel(scene.Id, true); continue; }
                    if (scene.Delivered && now >= scene.LineUntil) { game.ClearSocialLine(line.Id); scene.Showing = null; }
                }
                if (scene.Showing == null && scene.Lines.Count > 0)
                {
                    var line = scene.Lines.Dequeue();
                    if (line.ExpiresAt <= now) { Result(line, "expired", "social_line_expired"); Cancel(scene.Id, true); continue; }
                    scene.Showing = line; scene.Delivered = false;
                    scene.LineUntil = now.AddSeconds(Math.Max(3, Math.Min(7, ((string)line.Payload["text"]!).Length / 15.0)));
                    game.ShowSocialLine(scene.Id, line.Id, line.ActorId!, (string)line.Payload["text"]!);
                }
            }
        }
        public void CancelActor(string actorId) { foreach (var s in scenes.Values.Where(s => s.Participants.ContainsKey(actorId)).ToArray()) Cancel(s.Id, !s.Ambient); }
        public void Cancel(string? sceneId = null, bool notify = false)
        {
            if (!string.IsNullOrEmpty(sceneId)) { if (canceled.Count >= 4096) FailedClosed = true; else canceled.Add(sceneId!); }
            foreach (var scene in scenes.Values.Where(s => string.IsNullOrEmpty(sceneId) || s.Id == sceneId).ToArray())
            {
                if (canceled.Count >= 4096) FailedClosed = true; else canceled.Add(scene.Id);
                if (scene.Showing != null) { if (!scene.Delivered) Result(scene.Showing, "canceled", "social_scene_stopped"); game.ClearSocialLine(scene.Showing.Id); }
                foreach (var line in scene.Lines) Result(line, "canceled", "social_scene_stopped");
                game.EndSocial(scene.Id); scenes.Remove(scene.Id);
                if (notify && !Cancellations.TryAdd(scene.Id)) FailedClosed = true;
            }
        }
    }
}

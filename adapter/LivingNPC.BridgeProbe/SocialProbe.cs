using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading.Tasks;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;

// Real HTTP and the production command controller; every game observation is simulated.
static class SocialProbe
{
    public static async Task Run(Uri endpoint, bool embodied = false)
    {
        using var http = new HttpClient { BaseAddress = endpoint, Timeout = TimeSpan.FromSeconds(10) };
        async Task<JObject> Post(string path, object body)
        {
            using var response = await http.PostAsync(path, new StringContent(Wire.Encode(body), Encoding.UTF8, "application/json"));
            var text = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode) throw new InvalidOperationException(path + ": " + text);
            return JObject.Parse(text);
        }
        var generation = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var request = new SessionRequest { ProfileId = "csharp-social-" + Guid.NewGuid().ToString("N"), ProtagonistId = "franklin", RuntimeGeneration = generation, GameDatetime = "2013-09-17T12:00:00", Capabilities = Array.Empty<string>(), Manifest = new Dictionary<string, object> {
            ["game_edition"] = "legacy", ["story_mode"] = true, ["gta_build"] = "simulated", ["scripthookv_build"] = "simulated", ["shvdn_build"] = "simulated", ["adapter_version"] = "bridge-probe", ["game_os_version"] = "simulated", ["service_os"] = "macos", ["service_host_mode"] = "local", ["compat_layer"] = "none", ["arch"] = "arm64", ["capability_flags"] = new[] { "fake_game", "development:social:v1" } } };
        if (embodied) request.Manifest["capability_flags"] = new[] { "fake_game", "development:social:v1", "development:social-embodiment:v1" };
        var opened = await Post("/v1/bridge/sessions", request);
        var sid = (string)opened["session_id"]!;
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", (string)opened["token"]!);
        var game = new Street(generation);
        var controller = new CommandController(game, sid, generation, (string)opened["policy_version"]!, request.Capabilities);
        var social = embodied ? new SocialController(game, sid, generation) : null;
        long seq = 0, cursor = 0;
        EventEnvelope Event(string kind, string? id, object payload) => new() { SessionId = sid, Seq = ++seq, Kind = kind, ActorId = id, Payload = payload, RuntimeGeneration = generation, ProtagonistId = "franklin", GameDatetime = request.GameDatetime };
        async Task Refresh()
        {
            var events = new List<EventEnvelope> { Event("world.snapshot", null, new { online = false, mission_active = false, cutscene_active = false, random_event_active = false, paused = false, protagonist_id = "franklin", weather = "clear" }) };
            for (int i = 0; i < game.Actors.Length; i++)
            {
                var actor = game.Actors[i];
                events.Add(Event("actor.snapshot", actor.ActorId, new { runtime_handle = 40 + i, runtime_instance_id = actor.RuntimeInstanceId, ownership = "ambient", control_policy = "ambient", exists = true, alive = true, distance = actor.Distance, mission_entity = false, busy = false, position = new { x = (double)i, y = 0.0, z = 0.0 }, name = "Passerby", actor_kind = "ambient_ephemeral", area_id = "block", interior_id = "street", role_class = "civilian", role_reason = "civilian_evidence", ped_facts = new PedFacts { ModelHash = Joaat.Hash("a_m_y_business_01"), PedType = 4, RelationshipGroupHash = Joaat.Hash("CIVMALE"), Human = true }, activity = new ActorActivity { Movement = "still" } }));
            }
            await Post("/v1/bridge/events:batch", new { session_id = sid, events });
        }
        var commands = new Dictionary<string, Command>();
        var delivered = new HashSet<string>();
        var speakers = new HashSet<string>();
        var deadline = DateTimeOffset.UtcNow.AddSeconds(45);
        var nextRefresh = DateTimeOffset.MinValue;
        string? scene = null;
        while (DateTimeOffset.UtcNow < deadline && delivered.Count < 4)
        {
            if (DateTimeOffset.UtcNow >= nextRefresh) { await Refresh(); nextRefresh = DateTimeOffset.UtcNow.AddMilliseconds(400); }
            var batch = Wire.Decode<CommandBatch>(await http.GetStringAsync("/v1/bridge/commands?session_id=" + sid + "&wait_seconds=0&after=" + cursor));
            foreach (var command in batch.Commands)
            {
                cursor = Math.Max(cursor, command.Seq);
                commands[command.Id] = command;
                if (social != null && SocialController.Owns(command)) social.Handle(command, DateTimeOffset.UtcNow);
                else { if (command.Kind == "scene.cancel") social?.Cancel((string?)command.Payload["scene_id"]); controller.Commands.TryAdd(command); }
            }
            controller.Tick(DateTimeOffset.UtcNow);
            if (social != null)
            {
                social.Tick(DateTimeOffset.UtcNow);
                while (social.Acks.TryTake(out var result)) controller.Acks.TryAdd(result);
                while (social.Deliveries.TryTake(out var rendered))
                {
                    controller.Acks.TryAdd(new Ack { CommandId = rendered.Item1.Id, SessionId = sid, RuntimeGeneration = generation, Status = "completed", Observed = new Dictionary<string, object> { ["text"] = (string)rendered.Item1.Payload["text"]!, ["delivery"] = "simulated", ["displayed_at"] = rendered.Item2.DisplayedAt.ToString("O") } });
                }
            }
            while (controller.Acks.TryTake(out var ack))
            {
                await Post("/v1/bridge/commands/" + ack.CommandId + "/ack", ack);
                var command = commands[ack.CommandId];
                if (command.Kind != "dialogue.chunk" || ack.Status != "completed" || !delivered.Add(command.Id)) continue;
                scene ??= (string)command.Payload["scene_id"]!;
                if ((string?)command.Payload["scene_id"] != scene) throw new InvalidOperationException("Expected one scene.");
                speakers.Add(command.ActorId!);
                // First pair is street-0 and street-1. Only its partner attests hearing.
                var listener = command.ActorId == "street-0" ? game.Actors[1] : game.Actors[0];
                await Refresh();
                await Post("/v1/bridge/social/delivery", new { session_id = sid, runtime_generation = generation, protagonist_id = "franklin", scene_id = scene, game_datetime = request.GameDatetime, command_id = command.Id, witnesses = new[] { new { actor_id = listener.ActorId, runtime_instance_id = listener.RuntimeInstanceId, sampled_at = DateTimeOffset.UtcNow.ToString("O"), position = new { x = listener.ActorId == "street-0" ? 0.0 : 1.0, y = 0.0, z = 0.0 }, area_id = "block", interior_id = "street", exists = true, alive = true, can_hear = true, confidence = 0.9 } } });
            }
            await Task.Delay(100);
        }
        if (delivered.Count != 4 || speakers.Count != 2) throw new InvalidOperationException("Ambient conversation incomplete: " + delivered.Count + " " + await http.GetStringAsync("/v1/diagnostics?session_id=" + sid));
        JObject diagnostics;
        var completionDeadline = DateTimeOffset.UtcNow.AddSeconds(5);
        do
        {
            await Refresh();
            diagnostics = JObject.Parse(await http.GetStringAsync("/v1/diagnostics?session_id=" + sid));
            if ((string?)diagnostics["social_scheduler"]?["status"] == "completed") break;
            await Task.Delay(200);
        } while (DateTimeOffset.UtcNow < completionDeadline);
        var scheduler = diagnostics["social_scheduler"]!;
        if ((string?)scheduler["reason"] != "turn_limit" || (int?)scheduler["reserved_model_calls"] != 4)
            throw new InvalidOperationException("Expected four calls and the turn limit: " + scheduler);
        await Post("/v1/bridge/sessions/" + sid + "/close", new { runtime_generation = generation });
        Console.WriteLine(Wire.Encode(new { status = "passed", mode = embodied ? "embodied_social_simulation" : "ambient_social", profile_id = request.ProfileId, session_id = sid, actors = 8, delivered_lines = delivered.Count, speakers = speakers.Count, scheduler = diagnostics["social_scheduler"], game_verified = false, native_verified = false }));
    }

    sealed class Street : ISocialGameAdapter
    {
        public readonly ActorState[] Actors;
        public Street(long generation) => Actors = Enumerable.Range(0, 8).Select(i => new ActorState { ActorId = "street-" + i, RuntimeInstanceId = "street-instance-" + i, Generation = generation, Name = "Passerby", Kind = "ambient_ephemeral", Exists = true, Alive = true, Distance = 2 + i, X = i, AreaId = "block", InteriorId = "street", Ownership = Ownership.Ambient, Policy = ControlPolicy.Ambient, Role = PedRole.Civilian, RoleReason = "civilian_evidence" }).ToArray();
        private readonly Dictionary<string, ActorState[]> scenes = new();
        private readonly Dictionary<string, SocialLineObservation> lines = new();
        public bool BeginSocial(string id, ActorState[] actors, double seconds) { scenes[id] = actors; return true; }
        public bool SocialReady(string id) => scenes.ContainsKey(id);
        public bool SocialValid(string id) => scenes.ContainsKey(id);
        public void EndSocial(string id) { scenes.Remove(id); }
        public void AmbientSpeech(string actorId, string context) { }
        public void ClearSocialLine(string id) { lines.Remove(id); }
        public SocialLineObservation? ObserveSocialLine(string id) => lines.TryGetValue(id, out var line) ? line : null;
        public void ShowSocialLine(string sceneId, string id, string actorId, string text)
        {
            var stamp = DateTimeOffset.UtcNow;
            lines[id] = new SocialLineObservation { DisplayedAt = stamp, World = ReadWorld(), Actors = scenes[sceneId], Witnesses = scenes[sceneId].Where(a => a.ActorId != actorId).Select(a => (object)new { actor_id = a.ActorId }).ToArray() };
        }
        public WorldState ReadWorld() => new() { ProtagonistId = "franklin", GameDatetime = DateTime.Parse("2013-09-17T12:00:00") };
        public ActorState? Resolve(string id) => Actors.FirstOrDefault(a => a.ActorId == id);
        public bool SubtitleRendered => true;
        public void ShowSubtitle(string speaker, string text) { }
        public void ClearSubtitle() { }
        public void ShowStatus(string state, string message) { }
        public bool Start(Command command, ActorState actor, string action, JObject arguments) => false;
        public EffectObservation Observe(Command command, ActorState actor, string action) => new() { Status = EffectStatus.Running };
        public void Release(Command command, ActorState actor, string action) { }
        public void Forget(Command command) { }
        public bool Pin(Command command, ActorState actor, string name) => false;
    }
}

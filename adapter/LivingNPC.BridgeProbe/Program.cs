using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Threading.Tasks;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;

var endpoint = new Uri(args.Length > 0 ? args[0] : "http://127.0.0.1:8765");
var speechMode = args.Contains("--speech");
var crashMode = args.Contains("--crash");
if (speechMode && crashMode) throw new InvalidOperationException("Crash and speech probes are separate.");
if (!endpoint.IsLoopback) throw new InvalidOperationException("The simulated bridge probe requires loopback.");
if (args.Contains("--pin")) { await PinProbe.Run(endpoint); return; }
var generation = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
var request = new SessionRequest { ProfileId = "csharp-probe-" + Guid.NewGuid().ToString("N"), ProtagonistId = "franklin", RuntimeGeneration = generation, GameDatetime = "2013-09-17T12:00:00", Capabilities = new[] { "face_player", "wait_here" }, Manifest = new Dictionary<string, object> {
    ["game_edition"] = "legacy", ["story_mode"] = true, ["gta_build"] = "simulated", ["scripthookv_build"] = "simulated", ["shvdn_build"] = "simulated", ["adapter_version"] = "bridge-probe", ["game_os_version"] = "simulated", ["service_os"] = "macos", ["service_host_mode"] = "local", ["compat_layer"] = "none", ["arch"] = "arm64", ["capability_flags"] = new[] { "fake_game" } } };
using var bridge = new BridgeWorker(endpoint, request, Environment.GetEnvironmentVariable("LLS_BRIDGE_TOKEN") ?? "");
var deadline = DateTimeOffset.UtcNow.AddSeconds(30);
while (bridge.Session == null && DateTimeOffset.UtcNow < deadline) await Task.Delay(25);
if (bridge.Session == null) throw new InvalidOperationException("Session open failed: " + bridge.LastFailure);
var game = new FakeGame(generation) { HoldAction = crashMode };
var controller = new CommandController(game, bridge.Session.SessionId, generation, bridge.Session.PolicyVersion, request.Capabilities);
long seq = 0;
EventEnvelope Envelope(string kind, string? actorId, object payload) => new() { SessionId = bridge.Session.SessionId, Seq = ++seq, Kind = kind, ActorId = actorId, ProtagonistId = "franklin", GameDatetime = request.GameDatetime, RuntimeGeneration = generation, Payload = payload };
var worldPayload = new { online = false, mission_active = false, cutscene_active = false, random_event_active = false, paused = false, protagonist_id = "franklin" };
var actorPayload = new { runtime_handle = 42, runtime_instance_id = "probe-instance", ownership = "ambient", control_policy = "ambient", exists = true, alive = true, distance = 2, mission_entity = false, busy = false, position = new { x = 0, y = 0, z = 0 }, name = "Probe Neighbor",
    role_class = "civilian", role_reason = "civilian_evidence", ped_facts = new PedFacts { ModelHash = Joaat.Hash("a_m_y_business_01"), PedType = 4, RelationshipGroupHash = Joaat.Hash("CIVMALE"), Human = true } };
bridge.Events.TryAdd(Envelope("world.snapshot", null, worldPayload));
var actorSnapshot = Envelope("actor.snapshot", "probe-npc", actorPayload);
bridge.Events.TryAdd(actorSnapshot);
var sceneId = Guid.NewGuid().ToString("N");
if (!speechMode) bridge.Events.TryAdd(Envelope("player.utterance_text", "probe-npc", new { text = "Please look at me.", input_mode = "typed", scene_id = sceneId }));
var acks = new List<Ack>();
var received = new Dictionary<string, Command>();
var states = new HashSet<string>();
var pttStarted = false; var pttStopped = false;
var nextSnapshot = DateTimeOffset.UtcNow.AddMilliseconds(500);
string? finalMarker = null;
var crashAnnounced = false; var withdrawalAnnounced = false;
while (DateTimeOffset.UtcNow < deadline)
{
    if (crashMode && crashAnnounced && !bridge.Online && !withdrawalAnnounced)
    {
        // Match the script host's offline response through the real controller.
        controller.Cancel(sceneId);
        if (game.Releases != 1) throw new InvalidOperationException("Offline controller did not withdraw its simulated task.");
        while (controller.Acks.TryTake(out var canceled)) { acks.Add(canceled); bridge.Acks.TryAdd(canceled); }
        Console.WriteLine(Wire.Encode(new { phase = "withdrawn", releases = game.Releases }));
        Console.Out.Flush(); withdrawalAnnounced = true;
    }
    if (crashMode && withdrawalAnnounced && bridge.RequiresReconnect)
    {
        var reconnectRequest = Wire.Decode<SessionRequest>(Wire.Encode(request));
        reconnectRequest.RuntimeGeneration = generation + 1;
        using var reconnected = new BridgeWorker(endpoint, reconnectRequest, "");
        var reconnectDeadline = DateTimeOffset.UtcNow.AddSeconds(10);
        while (reconnected.Session == null && DateTimeOffset.UtcNow < reconnectDeadline) await Task.Delay(25);
        if (reconnected.Session == null || reconnected.Session.SessionId == bridge.Session.SessionId)
            throw new InvalidOperationException("Restart did not create a fresh bridge session.");
        var nextController = new CommandController(game, reconnected.Session.SessionId, generation + 1, reconnected.Session.PolicyVersion, request.Capabilities);
        var oldAction = received.Values.Single(c => c.Kind == "action.execute");
        nextController.Commands.TryAdd(oldAction);
        nextController.Tick(DateTimeOffset.UtcNow);
        if (!nextController.Acks.TryTake(out var stale) || stale.Status != "rejected" || stale.Reason != "stale_session")
            throw new InvalidOperationException("New controller did not reject the old action.");
        using var restartedHttp = new HttpClient(new HttpClientHandler { UseProxy = false }) { BaseAddress = endpoint };
        restartedHttp.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", reconnected.Session.Token);
        var restartedDiagnostics = JObject.Parse(await restartedHttp.GetStringAsync("/v1/diagnostics"));
        if (((JArray)restartedDiagnostics["commands"]!).Count != 0 || game.Starts != 1 || game.Releases != 1 || acks.Any(a => a.Status == "completed" && a.CommandId == oldAction.Id))
            throw new InvalidOperationException("Service restart replayed or completed the old simulated action.");
        Console.WriteLine(Wire.Encode(new { status = "passed", mode = "simulated_game_process_death", game_verified = false, native_verified = false, native_simulations = game.Starts, task_withdrawals = game.Releases, reconnected = true, stale_action_rejected = true, old_completion_invented = false }));
        return;
    }
    if (crashMode && withdrawalAnnounced) { await Task.Delay(20); continue; }
    if (speechMode && !pttStarted && actorSnapshot.Accepted)
        pttStarted = bridge.Controls.TryAdd(Envelope("voice.ptt_started", "probe-npc", new { scene_id = sceneId, runtime_instance_id = "probe-instance" }));
    if (DateTimeOffset.UtcNow >= nextSnapshot)
    {
        bridge.Snapshots.ReplaceOldest(Envelope("world.snapshot", null, worldPayload));
        bridge.Snapshots.ReplaceOldest(Envelope("actor.snapshot", "probe-npc", actorPayload));
        nextSnapshot = DateTimeOffset.UtcNow.AddMilliseconds(500);
    }
    while (bridge.Commands.TryTake(out var command))
    {
        received[command.Id] = command;
        if (command.Kind == "status") states.Add((string?)command.Payload["state"] ?? "");
        if (command.Kind == "dialogue.chunk" && (bool?)command.Payload["final"] == true) finalMarker = command.Id;
        controller.Commands.TryAdd(command);
    }
    controller.Tick(DateTimeOffset.UtcNow);
    while (controller.Acks.TryTake(out var ack)) { acks.Add(ack); bridge.Acks.TryAdd(ack); }
    if (crashMode && !crashAnnounced && game.Starts == 1 && bridge.Acks.Count == 0 && acks.Any(a => a.Status == "started"))
    {
        var pending = received.Values.Single(c => c.Kind == "action.execute");
        Console.WriteLine(Wire.Encode(new { phase = "started", profile_id = request.ProfileId, session_id = bridge.Session.SessionId, command_id = pending.Id }));
        Console.Out.Flush(); crashAnnounced = true;
    }
    if (speechMode && !pttStopped && game.Status == "recording")
        pttStopped = bridge.Controls.TryAdd(Envelope("voice.ptt_stopped", "probe-npc", new { scene_id = sceneId, runtime_instance_id = "probe-instance" }));
    if (game.Starts == 1 && acks.Exists(a => a.Status == "completed" && a.Reason == "simulated_heading_observed") && finalMarker != null && acks.Exists(a => a.CommandId == finalMarker && a.Status == "completed") && bridge.Acks.Count == 0) break;
    await Task.Delay(20);
}
if (crashMode) throw new InvalidOperationException("Process-death probe timed out: " + bridge.LastFailure);
using var http = new HttpClient { BaseAddress = endpoint };
http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", bridge.Session.Token);
var diagnostics = JObject.Parse(await http.GetStringAsync("/v1/diagnostics"));
var commands = (JArray)diagnostics["commands"]!;
var completedAction = false;
foreach (var command in commands) if ((string?)command["kind"] == "action.execute" && (string?)command["state"] == "completed") completedAction = true;
var expectedText = received.Values.Count(c => c.Kind == "dialogue.chunk" && ((string?)c.Payload["text"] ?? "").Length > 0);
var allDialogueCompleted = commands.Where(c => (string?)c["kind"] == "dialogue.chunk").All(c => (string?)c["state"] == "completed");
var audioChunks = received.Values.Count(c => c.Kind == "dialogue.chunk" && (string?)c.Payload["delivery_mode"] == "audio" && ((string?)c.Payload["text"] ?? "").Length > 0);
var subtitleAcks = acks.Count(a => a.Status == "completed" && a.Reason == "subtitle_rendered" && a.Observed != null && (string?)a.Observed["delivery"] == "subtitle");
if (!completedAction || !allDialogueCompleted || finalMarker == null || expectedText < 2 || game.Subtitles != expectedText || game.Starts != 1) throw new InvalidOperationException("Cross-language round trip failed: " + Wire.Encode(new { game.Starts, game.Subtitles, bridge.LastFailure, acks }));
if (speechMode && (!pttStarted || !pttStopped || !states.Contains("recording") || !states.Contains("transcribing") || audioChunks != expectedText || subtitleAcks != expectedText))
    throw new InvalidOperationException("Speech bridge round trip failed: " + Wire.Encode(new { pttStarted, pttStopped, states, audioChunks, expectedText, subtitleAcks, bridge.LastFailure }));
var close = new StringContent(Wire.Encode(new { runtime_generation = generation }), System.Text.Encoding.UTF8, "application/json");
(await http.PostAsync("/v1/bridge/sessions/" + bridge.Session.SessionId + "/close", close)).EnsureSuccessStatusCode();
Console.WriteLine(Wire.Encode(new { status = "passed", mode = speechMode ? "simulated_game_speech" : "simulated_game", game_verified = false, audio_hardware_verified = false, subtitles = game.Subtitles, expected_text_chunks = expectedText, final_marker_acknowledged = true, native_simulations = game.Starts, acks = acks.Count, ptt_recording_confirmed = states.Contains("recording"), ptt_stop_sent = pttStopped, audio_text_chunks = audioChunks, subtitle_delivery_acks = subtitleAcks }));

sealed class FakeGame : IGameAdapter
{
    private readonly ActorState actor;
    public int Starts, Subtitles, Releases;
    public bool HoldAction;
    public string Status = "";
    public bool SubtitleRendered => true;
    public void Forget(Command command) { }
    public FakeGame(long generation) { actor = new ActorState { ActorId = "probe-npc", RuntimeInstanceId = "probe-instance", Generation = generation, Exists = true, Alive = true, CanMove = true, Distance = 2, Ownership = Ownership.Ambient, Policy = ControlPolicy.Ambient, Role = PedRole.Civilian, RoleReason = "civilian_evidence", Name = "Probe Neighbor" }; }
    public ActorState Actor => actor;
    public int Pins;
    public string Message = "";
    public bool Pin(Command command, ActorState state, string displayName) { Pins++; state.Ownership = Ownership.ModOwned; state.Kind = "spawned_persistent"; state.Name = displayName; return true; }
    public WorldState ReadWorld() => new() { ProtagonistId = "franklin" };
    public ActorState? Resolve(string id) => id == actor.ActorId ? actor : null;
    public bool Start(Command command, ActorState state, string action, JObject arguments) { Starts++; return true; }
    public EffectObservation Observe(Command command, ActorState state, string action) => HoldAction ? new() { Status = EffectStatus.Running } : new() { Status = EffectStatus.Completed, Reason = "simulated_heading_observed", Facts = new() { ["simulated"] = true } };
    public void Release(Command command, ActorState state, string action) { Releases++; }
    public void ShowSubtitle(string speaker, string text) => Subtitles++;
    public void ClearSubtitle() { }
    public void ShowStatus(string state, string message) { Status = state; Message = message; }
}

// FR-ID-03 round trip: a lying police label is refused, then a civilian is pinned and remembered.
static class PinProbe
{
    private static PedFacts Facts(string model, int pedType, string group, bool registered = false) =>
        new() { ModelHash = Joaat.Hash(model), PedType = pedType, RelationshipGroupHash = Joaat.Hash(group), Human = true, InModRegistry = registered };
    public static async Task Run(Uri endpoint)
    {
        var generation = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var request = new SessionRequest { ProfileId = "csharp-pin-probe-" + Guid.NewGuid().ToString("N"), ProtagonistId = "franklin", RuntimeGeneration = generation, GameDatetime = "2013-09-17T12:00:00", Capabilities = new[] { "face_player", "wait_here" }, Manifest = new Dictionary<string, object> {
            ["game_edition"] = "legacy", ["story_mode"] = true, ["gta_build"] = "simulated", ["scripthookv_build"] = "simulated", ["shvdn_build"] = "simulated", ["adapter_version"] = "bridge-probe", ["game_os_version"] = "simulated", ["service_os"] = "macos", ["service_host_mode"] = "local", ["compat_layer"] = "none", ["arch"] = "arm64",
            ["capability_flags"] = new[] { "fake_game", "role_classifier_v1", "role_table_" + PedRoleTable.Default.Version, "actor_pin" } } };
        using var bridge = new BridgeWorker(endpoint, request, Environment.GetEnvironmentVariable("LLS_BRIDGE_TOKEN") ?? "");
        var deadline = DateTimeOffset.UtcNow.AddSeconds(60);
        while (bridge.Session == null && DateTimeOffset.UtcNow < deadline) await Task.Delay(25);
        if (bridge.Session == null) throw new InvalidOperationException("Session open failed: " + bridge.LastFailure);
        var session = bridge.Session;
        var game = new FakeGame(generation); var npc = game.Actor;
        var controller = new CommandController(game, session.SessionId, generation, session.PolicyVersion, request.Capabilities);
        long seq = 0;
        EventEnvelope Envelope(string kind, string? actorId, object payload) => new() { SessionId = session.SessionId, Seq = ++seq, Kind = kind, ActorId = actorId, ProtagonistId = "franklin", GameDatetime = request.GameDatetime, RuntimeGeneration = generation, Payload = payload };
        object Snapshot() => new { runtime_handle = 42, runtime_instance_id = npc.RuntimeInstanceId, ownership = npc.Ownership == Ownership.ModOwned ? "mod_owned" : "ambient", control_policy = "ambient", exists = true, alive = true, distance = 2, mission_entity = false, busy = false,
            position = new { x = 0, y = 0, z = 0 }, name = npc.Name, actor_kind = npc.Kind, role_class = "civilian", role_reason = "civilian_evidence", ped_facts = Facts("a_m_y_business_01", 4, "CIVMALE", npc.Ownership == Ownership.ModOwned) };
        var world = new { online = false, mission_active = false, cutscene_active = false, random_event_active = false, paused = false, protagonist_id = "franklin" };
        var finals = new HashSet<string>(); var completed = new HashSet<string>(); var statuses = new List<string>();
        var nextSnapshot = DateTimeOffset.UtcNow;
        async Task Until(Func<bool> done, string what)
        {
            while (!done())
            {
                if (DateTimeOffset.UtcNow > deadline) throw new InvalidOperationException("Pin probe timed out waiting for " + what + ": " + bridge.LastFailure);
                // The adapter's 500 ms sampling cadence also keeps the probe under the service rate limit.
                if (DateTimeOffset.UtcNow >= nextSnapshot)
                {
                    bridge.Snapshots.ReplaceOldest(Envelope("world.snapshot", null, world));
                    bridge.Snapshots.ReplaceOldest(Envelope("actor.snapshot", npc.ActorId, Snapshot()));
                    nextSnapshot = DateTimeOffset.UtcNow.AddMilliseconds(500);
                }
                while (bridge.Commands.TryTake(out var command))
                {
                    if (command.Kind == "dialogue.chunk" && (bool?)command.Payload["final"] == true) finals.Add(command.Id);
                    if (command.Kind == "status") statuses.Add((string?)command.Payload["message"] ?? "");
                    controller.Commands.TryAdd(command);
                }
                controller.Tick(DateTimeOffset.UtcNow);
                while (controller.Acks.TryTake(out var ack)) { if (ack.Status == "completed") completed.Add(ack.CommandId); bridge.Acks.TryAdd(ack); }
                await Task.Delay(20);
            }
        }
        // A police officer whose adapter label claims "civilian" stays protected on the service.
        var cop = new { runtime_handle = 43, runtime_instance_id = "cop-instance", ownership = "ambient", control_policy = "ambient", exists = true, alive = true, distance = 3, mission_entity = false, busy = false,
            position = new { x = 1, y = 0, z = 0 }, name = "Probe Officer", role_class = "civilian", role_reason = "civilian_evidence", ped_facts = Facts("s_m_y_cop_01", 6, "COP") };
        bridge.Events.TryAdd(Envelope("world.snapshot", null, world));
        bridge.Events.TryAdd(Envelope("actor.snapshot", "probe-cop", cop));
        var refused = Envelope("player.utterance_text", "probe-cop", new { text = "Can you help me?", input_mode = "typed", scene_id = "pin-probe-cop" });
        bridge.Events.TryAdd(refused);
        await Until(() => bridge.Events.Count == 0, "the police utterance decision");
        if (refused.Accepted) throw new InvalidOperationException("A lying police snapshot accepted an utterance.");
        var notices = new List<string>(); while (bridge.Notices.TryTake(out var notice)) notices.Add(notice);
        if (!notices.Contains("event_rejected_409")) throw new InvalidOperationException("The police utterance was not refused: " + string.Join(",", notices));
        bridge.Events.TryAdd(Envelope("actor.snapshot", npc.ActorId, Snapshot()));
        bridge.Events.TryAdd(Envelope("player.utterance_text", npc.ActorId, new { text = "Please look at me.", input_mode = "typed", scene_id = "pin-probe-before" }));
        await Until(() => finals.Count == 1 && finals.All(completed.Contains) && bridge.Acks.Count == 0, "the pre-pin turn");
        bridge.Events.TryAdd(Envelope("actor.pin_requested", npc.ActorId, new { runtime_instance_id = npc.RuntimeInstanceId }));
        await Until(() => game.Pins == 1 && statuses.Any(m => m.StartsWith("Pinned as ")) && bridge.Acks.Count == 0, "the pin acknowledgment");
        bridge.Events.TryAdd(Envelope("actor.snapshot", npc.ActorId, Snapshot()));
        bridge.Events.TryAdd(Envelope("player.utterance_text", npc.ActorId, new { text = "Do you remember me?", input_mode = "typed", scene_id = "pin-probe-after" }));
        await Until(() => finals.Count == 2 && finals.All(completed.Contains) && bridge.Acks.Count == 0, "the post-pin turn");
        using var http = new HttpClient(new HttpClientHandler { UseProxy = false }) { BaseAddress = endpoint };
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", session.Token);
        var actor = JObject.Parse(await http.GetStringAsync("/v1/diagnostics"))["actors"]![npc.ActorId]!;
        if ((string?)actor["actor_kind"] != "spawned_persistent" || (string?)actor["ownership"] != "mod_owned" || (string?)actor["control_policy"] != "ambient" || (string?)actor["role_class"] != "civilian")
            throw new InvalidOperationException("The pinned snapshot was not accepted as mod-owned: " + actor);
        var close = new StringContent(Wire.Encode(new { runtime_generation = generation }), System.Text.Encoding.UTF8, "application/json");
        (await http.PostAsync("/v1/bridge/sessions/" + session.SessionId + "/close", close)).EnsureSuccessStatusCode();
        Console.WriteLine(Wire.Encode(new { status = "passed", mode = "simulated_game_pin", game_verified = false, profile_id = request.ProfileId, pinned_actor_id = npc.ActorId, display_name = npc.Name,
            lying_police_utterance_refused = true, pin_calls = game.Pins, pin_status = statuses.First(m => m.StartsWith("Pinned as ")), post_pin_turn_delivered = true, actor_kind_after_pin = (string?)actor["actor_kind"] }));
    }
}

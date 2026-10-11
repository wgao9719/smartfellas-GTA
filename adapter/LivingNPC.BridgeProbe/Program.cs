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
    public FakeGame(long generation) { actor = new ActorState { ActorId = "probe-npc", RuntimeInstanceId = "probe-instance", Generation = generation, Exists = true, Alive = true, CanMove = true, Distance = 2, Ownership = Ownership.Ambient, Policy = ControlPolicy.Ambient }; }
    public WorldState ReadWorld() => new() { ProtagonistId = "franklin" };
    public ActorState? Resolve(string id) => id == actor.ActorId ? actor : null;
    public bool Start(Command command, ActorState state, string action, JObject arguments) { Starts++; return true; }
    public EffectObservation Observe(Command command, ActorState state, string action) => HoldAction ? new() { Status = EffectStatus.Running } : new() { Status = EffectStatus.Completed, Reason = "simulated_heading_observed", Facts = new() { ["simulated"] = true } };
    public void Release(Command command, ActorState state, string action) { Releases++; }
    public void ShowSubtitle(string speaker, string text) => Subtitles++;
    public void ClearSubtitle() { }
    public void ShowStatus(string state, string message) { Status = state; }
}

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Net;
using System.Windows.Forms;
using GTA;
using LivingNPC.Core;

namespace LivingNPC.GameAdapter
{
    public sealed class LivingNpcScript : Script
    {
        private readonly GtaGameAdapter game;
        private readonly AdapterLog log;
        private readonly string adapterVersion;
        private readonly Uri endpoint;
        private readonly string profileId, bootstrapToken, hookVersion, serviceOs;
        private readonly string? parentBranchId;
        private readonly string[] capabilities;
        private readonly Keys targetKey, textKey, pttKey, cancelKey;
        private readonly Keys pinKey;
        private readonly PinConfirmation pinConfirmation = new PinConfirmation();
        private BridgeWorker? bridge;
        private CommandController? controller;
        private long generation = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), seq;
        private string? typing;
        private readonly ConversationInput input = new ConversationInput();
        private EventEnvelope? targetSnapshot;
        private DateTimeOffset pendingPttDeadline;
        private string protagonist = "";
        private bool wasOnline, offlineShown;
        // Held talk keys ignore key repeat. Each key keeps its own physical state.
        private readonly HashSet<Keys> heldTalkKeys = new HashSet<Keys>();
        private readonly Keys whisperKey;
        private bool worldSent;
        private readonly BoundedQueue<string> removedActors = new BoundedQueue<string>(128);
        private DateTime lastGameDate;
        private DateTimeOffset nextSample;
        private bool enabled;
        public LivingNpcScript()
        {
            Interval = 0;
            var assembly = typeof(LivingNpcScript).Assembly;
            // SHVDN supplies the script directory even when it loads an assembly from bytes.
            var scriptsDirectory = BaseDirectory;
            log = new AdapterLog(AdapterPaths.DataDirectory(scriptsDirectory));
            game = new GtaGameAdapter(log);
            adapterVersion = ((System.Reflection.AssemblyInformationalVersionAttribute)Attribute.GetCustomAttribute(assembly, typeof(System.Reflection.AssemblyInformationalVersionAttribute))).InformationalVersion;
            log.Write("adapter_loaded", "version=" + adapterVersion + " module_id=" + assembly.ManifestModule.ModuleVersionId);
            try
            {
                using (var sha = System.Security.Cryptography.SHA256.Create())
                using (var dll = System.IO.File.OpenRead(System.IO.Path.Combine(scriptsDirectory, "LivingNPC.GameAdapter.dll")))
                    log.Write("installed_dll", "sha256=" + BitConverter.ToString(sha.ComputeHash(dll)).Replace("-", "").ToLowerInvariant());
            }
            catch (System.IO.IOException) { log.Write("installed_dll_hash_unavailable"); }
            catch (UnauthorizedAccessException) { log.Write("installed_dll_hash_unavailable"); }
            var settings = ScriptSettings.Load(AdapterPaths.Configuration(scriptsDirectory));
            profileId = settings.GetValue("Service", "ProfileId", "default");
            var configuredBranch = settings.GetValue("Service", "ParentBranchId", ""); parentBranchId = configuredBranch.Length == 0 ? null : configuredBranch;
            bootstrapToken = Environment.GetEnvironmentVariable("LLS_BRIDGE_TOKEN") ?? settings.GetValue("Service", "BootstrapToken", "");
            hookVersion = settings.GetValue("Compatibility", "ScriptHookVBuild", "UNVERIFIED");
            serviceOs = settings.GetValue("Compatibility", "ServiceOS", "windows");
            endpoint = new Uri(settings.GetValue("Service", "URL", "http://127.0.0.1:8765"));
            var devLan = settings.GetValue("Service", "DevLAN", false);
            var nativeOptIn = settings.GetValue("Development", "EnableUnverifiedActions", false);
            var addressValid = IPAddress.TryParse(endpoint.Host, out var address);
            var loopback = addressValid && IPAddress.IsLoopback(address!);
#if DEVELOPMENT
            enabled = endpoint.Scheme == "http" && endpoint.UserInfo.Length == 0 && endpoint.AbsolutePath == "/" &&
                (loopback || (devLan && addressValid && PrivateDevAddress(address!) && bootstrapToken.Length >= 24));
            capabilities = nativeOptIn ? new[] { "face_player", "wait_here" } : Array.Empty<string>();
#else
            enabled = endpoint.Scheme == "http" && endpoint.UserInfo.Length == 0 && endpoint.AbsolutePath == "/" && loopback && !devLan;
            capabilities = Array.Empty<string>();
#endif
            targetKey = settings.GetValue("Input", "TargetKey", Keys.F6);
            textKey = settings.GetValue("Input", "TextKey", Keys.F7);
            pttKey = settings.GetValue("Input", "PttKey", Keys.N);
            whisperKey = settings.GetValue("Input", "WhisperKey", Keys.B);
            cancelKey = settings.GetValue("Input", "CancelKey", Keys.Back);
            pinKey = settings.GetValue("Input", "PinKey", Keys.F8);
#if DEVELOPMENT
            game.PinPersistence = settings.GetValue("Development", "EnableUnverifiedPinPersistence", false);
#endif
            Tick += OnTick; KeyDown += OnKeyDown; KeyUp += OnKeyUp; Aborted += OnAborted;
            log.Write("configuration_loaded", "enabled=" + enabled + " capabilities=" + capabilities.Length);
            if (!enabled) game.ShowStatus("error", "Living Los Santos: invalid endpoint or development settings.");
        }
        private static bool PrivateDevAddress(IPAddress address)
        {
            var b = address.GetAddressBytes();
            return b.Length == 4 && (b[0] == 10 || b[0] == 192 && b[1] == 168 || b[0] == 172 && b[1] >= 16 && b[1] <= 31 || b[0] == 100 && b[1] >= 64 && b[1] <= 127);
        }
        private void Connect(WorldState world)
        {
            controller?.Cancel(input.SceneId); bridge?.Dispose(); game.Reset(); controller = null;
            generation++; seq = 0; worldSent = false; removedActors.Clear(); input.Reset(); targetSnapshot = null; typing = null;
            protagonist = world.ProtagonistId; lastGameDate = world.GameDatetime;
            var request = new SessionRequest { ProfileId = profileId, ParentBranchId = parentBranchId, ProtagonistId = protagonist, GameDatetime = world.GameDatetime.ToString("s"), RuntimeGeneration = generation, Capabilities = capabilities,
                Manifest = new Dictionary<string, object> { ["game_edition"] = "legacy", ["story_mode"] = true, ["gta_build"] = Game.Version.ToString(), ["scripthookv_build"] = hookVersion,
                    ["shvdn_build"] = typeof(Script).Assembly.GetName().Version!.ToString(), ["adapter_version"] = adapterVersion,
                    ["game_os_version"] = Environment.OSVersion.VersionString, ["service_os"] = serviceOs, ["service_host_mode"] = IPAddress.IsLoopback(IPAddress.Parse(endpoint.Host)) ? "local" : "dev_lan",
                    ["compat_layer"] = "none", ["arch"] = "x64", ["capability_flags"] = CapabilityFlags() } };
            pinConfirmation.Reset();
            bridge = new BridgeWorker(endpoint, request, bootstrapToken); wasOnline = false; offlineShown = false;
            log.Write("bridge_connect", "generation=" + generation);
        }
        private string[] CapabilityFlags()
        {
            var flags = new List<string> { "typed_input", "ptt_control", "subtitles", "runtime_decorator_binding", "role_classifier_v1", "role_table_" + PedRoleTable.Default.Version, "actor_pin" };
            if (game.PinPersistence) flags.Add("pin_persistence_unverified");
            return flags.ToArray();
        }
        private EventEnvelope Event(string kind, string? actorId, object payload)
        {
            var world = game.ReadWorld();
            return new EventEnvelope { SessionId = bridge?.Session?.SessionId ?? "", Seq = ++seq, Kind = kind, GameDatetime = world.GameDatetime.ToString("s"), ProtagonistId = world.ProtagonistId,
                ActorId = actorId, RuntimeGeneration = generation, Payload = payload };
        }
        private void OnTick(object sender, EventArgs args)
        {
            if (!enabled) { game.Render(null); return; }
            var timer = Stopwatch.StartNew(); var world = game.ReadWorld();
            if (!world.Safe)
            {
                if (bridge != null) { CancelInput(); bridge.Dispose(); bridge = null; controller = null; game.Reset(); }
                game.ShowStatus("disabled", "Living Los Santos: unavailable in the current game state."); game.Render(null); return;
            }
            // A full game-date rewind and a protagonist switch invalidate every old binding/command.
            if (bridge == null || bridge.RequiresReconnect || world.ProtagonistId != protagonist || world.GameDatetime < lastGameDate.AddSeconds(-2)) Connect(world);
            lastGameDate = world.GameDatetime;
            if (input.Bound && !input.IsCurrent(game.Resolve(input.ActorId!), world, generation)) CancelInput();
            if (bridge!.Session != null && controller == null) controller = new CommandController(game, bridge.Session.SessionId, generation, bridge.Session.PolicyVersion, capabilities);
            if (bridge.Online)
            {
                if (!worldSent) { bridge.Events.TryAdd(Event("world.snapshot", null, new { online = world.Online, mission_active = world.MissionActive, cutscene_active = world.CutsceneActive, random_event_active = world.RandomEventActive, paused = world.Paused, protagonist_id = world.ProtagonistId })); worldSent = true; }
                if (!wasOnline) { log.Write("bridge_ready", "generation=" + generation); game.ShowStatus("ready", "Living Los Santos: " + targetKey + " target, " + textKey + " text (/w whispers), " + pttKey + " push to talk, " + whisperKey + " whisper, " + pinKey + " twice to pin, " + cancelKey + " cancel."); offlineShown = false; }
                if (input.PushToTalkHeld && !input.PushToTalkStartQueued)
                {
                    if (DateTimeOffset.UtcNow >= pendingPttDeadline) { CancelInput(); game.ShowStatus("unavailable", "Living Los Santos: target confirmation timed out. Try again."); }
                    else if (input.TryQueuePushToTalkStart(targetSnapshot?.Accepted == true))
                    {
                        if (Control("voice.ptt_started")) game.ShowStatus("opening_mic", "Living Los Santos: requesting microphone on the service computer.");
                        else CancelInput();
                    }
                }
                if (controller != null)
                {
                    while (controller.Commands.Remaining > 0 && bridge.Commands.TryTake(out var command)) controller.Commands.TryAdd(command);
                    controller.Tick(DateTimeOffset.UtcNow);
                    while (bridge.Acks.Remaining > 0 && controller.Acks.TryTake(out var ack)) bridge.Acks.TryAdd(ack);
                }
            }
            else if (!offlineShown)
            {
                log.Write("bridge_offline", bridge.LastFailure);
                CancelInput(); game.ShowStatus("offline", "Living Los Santos: service unavailable. AI stopped."); offlineShown = true;
            }
            wasOnline = bridge.Online;
            if (bridge.Notices.TryTake(out var notice)) { log.Write("bridge_notice", notice); game.ShowStatus("error", "Living Los Santos: " + notice); }
            bridge.Fps = Game.FPS; bridge.ActorCount = game.SelectedActorId == null ? 0 : 1;
            if (DateTimeOffset.UtcNow >= nextSample && bridge.Online)
            {
                PruneAndPublish();
                if (bridge == null) { game.Render(typing); return; }
                var pending = pinConfirmation.PendingActorId; pinConfirmation.Expire(DateTimeOffset.UtcNow);
                if (pending != null && game.Resolve(pending) is var pinned && (pinned == null || pinned.Ownership == Ownership.ModOwned)) pinConfirmation.Complete(pending);
                bridge.Snapshots.ReplaceOldest(Event("world.snapshot", null, new { online = world.Online, mission_active = world.MissionActive, cutscene_active = world.CutsceneActive, random_event_active = world.RandomEventActive, paused = world.Paused, protagonist_id = world.ProtagonistId }));
                if (game.SelectedActorId != null) SendSnapshot(game.Resolve(game.SelectedActorId));
                nextSample = DateTimeOffset.UtcNow.AddMilliseconds(500);
            }
            if (typing != null) Game.DisableAllControlsThisFrame();
            game.Render(typing, input.Whisper);
            timer.Stop();
            if (timer.ElapsedMilliseconds > 20) { log.Write("slow_tick", "milliseconds=" + timer.ElapsedMilliseconds); CancelInput(); game.ShowStatus("slow", "Living Los Santos: slow adapter tick. Pending AI stopped."); }
        }
        private void PruneAndPublish()
        {
            foreach (var actorId in game.PruneBindings(generation))
            {
                if (!removedActors.TryAdd(actorId)) { enabled = false; CancelInput(); bridge?.Dispose(); bridge = null; game.ShowStatus("error", "Actor removal queue is full. Reload the adapter."); return; }
            }
            while (bridge != null && bridge.Events.Remaining > 0 && removedActors.TryTake(out var actorId))
                bridge.Events.TryAdd(Event("actor.removed", actorId, new { }));
        }
        private EventEnvelope? SendSnapshot(ActorState? actor, bool reliable = false)
        {
            if (actor == null || bridge == null) return null;
            var snapshot = Event("actor.snapshot", actor.ActorId, new {
                runtime_handle = actor.Handle, runtime_instance_id = actor.RuntimeInstanceId,
                ownership = actor.Ownership == Ownership.ModOwned ? "mod_owned" : actor.Ownership == Ownership.GameScriptOwned ? "game_script_owned" : actor.Ownership == Ownership.Unknown ? "unknown" : "ambient",
                control_policy = actor.Policy == ControlPolicy.Protected ? "protected" : actor.Policy == ControlPolicy.ServiceLimited ? "service_limited" : "ambient", exists = actor.Exists, alive = actor.Alive,
                distance = actor.Distance, mission_entity = actor.MissionEntity, busy = actor.Busy, in_combat = actor.InCombat, can_move = actor.CanMove,
                position = new { x = actor.X, y = actor.Y, z = actor.Z }, name = actor.Name, actor_kind = actor.Kind,
                role_class = PedRoleTable.WireName(actor.Role), role_reason = actor.RoleReason, ped_facts = actor.Facts });
            if (reliable) { if (!bridge.Events.TryAdd(snapshot)) { game.ShowStatus("error", "Living Los Santos: observation queue is full."); return null; } }
            else bridge.Snapshots.ReplaceOldest(snapshot);
            return snapshot;
        }
        private bool LockTarget()
        {
            if (bridge?.Online != true || game.SelectedActorId == null) return false;
            var actor = game.Resolve(game.SelectedActorId);
            if (!input.TryBind(actor, game.ReadWorld(), generation)) return false;
            targetSnapshot = SendSnapshot(actor, true);
            if (targetSnapshot == null) { input.Reset(); return false; }
            return true;
        }
        private bool Control(string kind)
        {
            if (bridge == null || !input.Bound) return false;
            object payload = kind == "scene.cancel" ? (object)new { scene_id = input.SceneId } : input.ControlPayload()!;
            if (bridge.Controls.TryAdd(Event(kind, input.ActorId, payload))) return true;
            bridge.RequiresReconnect = true;
            game.ShowStatus("error", "Living Los Santos: control queue is full. Reconnecting to stop input.");
            return false;
        }
        private void CancelInput()
        {
            controller?.Cancel(input.SceneId);
            if (input.Bound) Control("scene.cancel");
            input.Reset(); targetSnapshot = null; typing = null;
            game.ShowStatus("canceled", "Living Los Santos: conversation canceled.");
        }
        private void OnKeyDown(object sender, KeyEventArgs args)
        {
            if (!enabled) return;
            if ((args.KeyCode == pttKey || args.KeyCode == whisperKey) && typing == null && !heldTalkKeys.Add(args.KeyCode)) return;
            if (args.KeyCode == cancelKey && typing == null || args.KeyCode == Keys.Escape && typing != null)
            {
                CancelInput(); return;
            }
            if (typing != null)
            {
                if (args.KeyCode == Keys.Enter)
                {
                    var typed = input.TypedPayload(typing);
                    if (typed != null && bridge != null && input.IsCurrent(game.Resolve(input.ActorId!), game.ReadWorld(), generation) && !bridge.Events.TryAdd(Event("player.utterance_text", input.ActorId, typed)))
                        game.ShowStatus("error", "Living Los Santos: input queue is full.");
                    typing = null; return;
                }
                if (args.KeyCode == Keys.Back && typing.Length > 0) typing = typing.Substring(0, typing.Length - 1);
                else if (typing.Length < 500)
                {
                    if (args.KeyCode >= Keys.A && args.KeyCode <= Keys.Z) typing += args.Shift ? args.KeyCode.ToString() : args.KeyCode.ToString().ToLowerInvariant();
                    else if (args.KeyCode >= Keys.D0 && args.KeyCode <= Keys.D9) typing += ((int)args.KeyCode - (int)Keys.D0).ToString();
                    else if (args.KeyCode == Keys.Space) typing += " ";
                    else if (args.KeyCode == Keys.OemPeriod) typing += ".";
                    else if (args.KeyCode == Keys.Oemcomma) typing += ",";
                    else if (args.KeyCode == Keys.OemQuestion) typing += args.Shift ? "?" : "/";
                    else if (args.KeyCode == Keys.OemQuotes) typing += "'";
                }
                return;
            }
            if (args.KeyCode == targetKey)
            {
                CancelInput();
                PruneAndPublish();
                if (bridge == null) return;
                var actor = game.SelectNearest(generation, out var refusal); SendSnapshot(actor); LogClassification(actor ?? refusal);
                game.ShowStatus("target", actor != null ? "Target: " + actor.Name + " (" + PedClassifier.Availability(actor, game.ReadWorld(), generation) + ")"
                    : refusal != null ? "Nearest: " + PedRoleTable.Label(refusal.Role) + " — not available" : "No nearby resident is available."); return;
            }
            if (args.KeyCode == pinKey) { RequestPin(); return; }
            if (args.KeyCode == textKey) { CancelInput(); if (LockTarget()) typing = ""; return; }
            if ((args.KeyCode == pttKey || args.KeyCode == whisperKey) && !input.PushToTalkHeld)
            {
                CancelInput();
                if (LockTarget() && input.StartPushToTalk((int)args.KeyCode, args.KeyCode == whisperKey))
                {
                    pendingPttDeadline = DateTimeOffset.UtcNow.AddSeconds(5);
                    game.ShowStatus("preparing_mic", "Living Los Santos: confirming the target before microphone capture.");
                }
            }
        }
        // FR-ID-03: two presses on the same actor and instance within 5 s send one request.
        private void RequestPin()
        {
            if (bridge?.Online != true) return;
            var actor = game.SelectedActorId == null ? null : game.Resolve(game.SelectedActorId);
            var refusal = PinPolicy.Check(actor, game.ReadWorld(), generation);
            if (refusal != null) { pinConfirmation.Disarm(); game.ShowStatus("pin", "Living Los Santos: " + PinPolicy.Describe(refusal)); return; }
            var press = pinConfirmation.Press(actor!.ActorId, actor.RuntimeInstanceId, DateTimeOffset.UtcNow);
            if (press == PinPress.Pending) { game.ShowStatus("pin", "Living Los Santos: a pin request is still waiting for the service."); return; }
            if (press == PinPress.Armed) { game.ShowStatus("pin", "Living Los Santos: press " + pinKey + " again within 5 seconds to pin " + actor.Name + "."); return; }
            CancelInput();
            if (!bridge.Events.TryAdd(Event("actor.pin_requested", actor.ActorId, new { runtime_instance_id = actor.RuntimeInstanceId })))
            { pinConfirmation.Reset(); game.ShowStatus("error", "Living Los Santos: input queue is full."); return; }
            game.ShowStatus("pin", "Living Los Santos: pinning " + actor.Name + ".");
        }
        private void LogClassification(ActorState? actor)
        {
#if DEVELOPMENT
            var facts = actor?.Facts;
            if (actor != null) log.Write("target_classified", "role=" + PedRoleTable.WireName(actor.Role) + " reason=" + actor.RoleReason + " ped_type=" + (facts?.PedType ?? -1) +
                " relgroup=0x" + (facts?.RelationshipGroupHash ?? 0).ToString("X8") + " model=0x" + (facts?.ModelHash ?? 0).ToString("X8"));
#endif
        }
        private void OnKeyUp(object sender, KeyEventArgs args)
        {
            if (args.KeyCode != pttKey && args.KeyCode != whisperKey) return;
            heldTalkKeys.Remove(args.KeyCode);
            if (!input.StopPushToTalk((int)args.KeyCode)) return;
            if (input.PushToTalkStartQueued)
            {
                if (!Control("voice.ptt_stopped")) CancelInput();
            }
            else CancelInput();
        }
        private void OnAborted(object sender, EventArgs args) { CancelInput(); bridge?.Dispose(); game.Reset(); log.Write("adapter_stopped"); log.Dispose(); }
    }
}

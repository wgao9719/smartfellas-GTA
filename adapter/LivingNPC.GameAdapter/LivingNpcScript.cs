using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Net;
using System.Linq;
using Newtonsoft.Json.Linq;
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
        private readonly string profileId, saveSlot, bootstrapToken, hookVersion, serviceOs;
        private readonly string? parentBranchId;
        private readonly string[] capabilities;
        private readonly Keys targetKey, textKey, pttKey, cancelKey;
        private readonly Keys pinKey, confirmProfileKey;
        private readonly ProfileConfirmation profileConfirmation = new ProfileConfirmation();
        private bool profileKeyHeld;
        private readonly PinConfirmation pinConfirmation = new PinConfirmation();
        private BridgeWorker? bridge;
        private CommandController? controller;
        private SocialController? social;
        private readonly bool socialEnabled = false;
        private DateTimeOffset nextNearbySample, nextAmbientScene;
        private readonly Dictionary<string, DateTimeOffset> ambientCooldowns = new Dictionary<string, DateTimeOffset>();
        private long generation = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), seq;
        private string? typing;
        private readonly ConversationInput input = new ConversationInput();
        private readonly ConversationControls inputControls = new ConversationControls();
        private EventEnvelope? targetSnapshot;
        private DateTimeOffset pendingPttDeadline;
        private bool wasOnline, offlineShown;
        // Held talk keys ignore key repeat. Each key keeps its own physical state.
        private readonly HashSet<Keys> heldTalkKeys = new HashSet<Keys>();
        private readonly Keys whisperKey;
        private bool worldSent;
        private readonly BoundedQueue<string> removedActors = new BoundedQueue<string>(128);
        private DateTimeOffset nextSample;
        private bool enabled;
        private readonly string? configurationStatus;
        private DateTimeOffset? configurationStatusUntil;
        private bool readyForInput;
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
            var settings = RawIni.Load(AdapterPaths.Configuration(scriptsDirectory));
            profileId = settings.GetString("Service", "ProfileId", "");
            saveSlot = settings.GetString("Service", "SaveSlot", "");
            var configuredBranch = settings.GetString("Service", "ParentBranchId", ""); parentBranchId = configuredBranch.Length == 0 ? null : configuredBranch;
            bootstrapToken = Environment.GetEnvironmentVariable("LLS_BRIDGE_TOKEN") ?? settings.GetString("Service", "BootstrapToken", "");
            hookVersion = settings.GetString("Compatibility", "ScriptHookVBuild", "UNVERIFIED");
            serviceOs = settings.GetString("Compatibility", "ServiceOS", "windows");
            var devLan = settings.GetBoolean("Service", "DevLAN", false);
#if DEVELOPMENT
            const bool developmentBuild = true;
            capabilities = settings.GetBoolean("Development", "EnableUnverifiedActions", false) ? NativeActions.DevelopmentCapabilities : Array.Empty<string>();
#else
            const bool developmentBuild = false;
            capabilities = Array.Empty<string>();
#endif
            input.AllowActions = capabilities.Length > 0;
            var serviceEndpoint = ServiceEndpoint.Resolve(settings.GetString("Service", "URL", ServiceEndpoint.DefaultUrl), devLan, developmentBuild, bootstrapToken);
            endpoint = serviceEndpoint.Uri;
            enabled = true;
            targetKey = settings.GetEnum("Input", "TargetKey", Keys.F6);
            textKey = settings.GetEnum("Input", "TextKey", Keys.F7);
            pttKey = settings.GetEnum("Input", "PttKey", Keys.N);
            whisperKey = settings.GetEnum("Input", "WhisperKey", Keys.B);
            cancelKey = settings.GetEnum("Input", "CancelKey", Keys.Back);
            pinKey = settings.GetEnum("Input", "PinKey", Keys.F8);
            confirmProfileKey = settings.GetEnum("Input", "ConfirmProfileKey", Keys.F10);
#if DEVELOPMENT
            socialEnabled = settings.GetBoolean("Development", "EnableUnverifiedSocial", false);
            game.PinPersistence = settings.GetBoolean("Development", "EnableUnverifiedPinPersistence", false);
#endif
            if (settings.HasInvalidValues)
            {
                log.Write("configuration_defaults_used");
                configurationStatus = "Living Los Santos: unreadable or invalid INI settings; defaults used. Check LivingNPC.ini and reload.";
            }
            if (serviceEndpoint.FellBack)
            {
                log.Write("configuration_endpoint_fallback");
                configurationStatus = "Living Los Santos: invalid service URL or DevLAN settings; using http://127.0.0.1:8765. Check LivingNPC.ini and reload.";
            }
            Tick += OnTick; KeyDown += OnKeyDown; KeyUp += OnKeyUp; Aborted += OnAborted;
            log.Write("configuration_loaded", "enabled=" + enabled + " capabilities=" + capabilities.Length);
        }
        private void Connect(WorldState world)
        {
            social?.Cancel(); social = null; controller?.Cancel(input.SceneId); bridge?.Dispose(); game.Reset(); controller = null; ambientCooldowns.Clear();
            generation++; seq = 0; worldSent = false; removedActors.Clear(); input.Reset(); targetSnapshot = null; typing = null;
            var request = new SessionRequest { ProfileId = profileId, SaveSlot = saveSlot, ProfileConfirmed = profileConfirmation.Confirmed, ParentBranchId = parentBranchId, ProtagonistId = world.ProtagonistId, GameDatetime = world.GameDatetime.ToString("s"), RuntimeGeneration = generation, Capabilities = capabilities,
                Manifest = new Dictionary<string, object> { ["game_edition"] = "legacy", ["story_mode"] = true, ["gta_build"] = Game.Version.ToString(), ["scripthookv_build"] = hookVersion,
                    ["shvdn_build"] = typeof(Script).Assembly.GetName().Version!.ToString(), ["adapter_version"] = adapterVersion,
                    ["game_os_version"] = Environment.OSVersion.VersionString, ["service_os"] = serviceOs, ["service_host_mode"] = IPAddress.IsLoopback(IPAddress.Parse(endpoint.Host)) ? "local" : "dev_lan",
                    ["compat_layer"] = "none", ["arch"] = "x64", ["capability_flags"] = CapabilityFlags() } };
            pinConfirmation.Reset();
            bridge = new BridgeWorker(endpoint, request, bootstrapToken, confirmProfileKey: confirmProfileKey.ToString()); wasOnline = false; offlineShown = false;
            log.Write("bridge_connect", "generation=" + generation);
        }
        private string[] CapabilityFlags()
        {
            var flags = new List<string> { "typed_input", "ptt_control", "subtitles", "runtime_continuity_binding", "role_classifier_v1", "role_table_" + PedRoleTable.Default.Version, "actor_pin" };
            if (socialEnabled) { flags.Add("development:social:v1"); flags.Add("development:social-embodiment:v1"); }
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
            game.BindScriptThread();
            inputControls.DisableForFrame(typing != null, game.DisableControl);
            if (typing != null) Game.DisableAllControlsThisFrame();
            if (configurationStatus != null && !readyForInput)
            {
                if (Game.IsPaused) { configurationStatusUntil = null; return; }
                configurationStatusUntil ??= DateTimeOffset.UtcNow.AddSeconds(8);
                if (DateTimeOffset.UtcNow < configurationStatusUntil.Value)
                {
                    game.ShowStatus("configuration", configurationStatus); game.Render(null); return;
                }
            }
            readyForInput = true;
            if (!enabled) { game.Render(null); return; }
            var timer = Stopwatch.StartNew(); var world = game.ReadWorld();
            profileConfirmation.Observe(world, bridge?.RequiresReconnect == true);
            if (!world.Safe)
            {
                CancelInput();
                if (bridge != null) { social?.Cancel(); social = null; bridge.Dispose(); bridge = null; controller = null; game.Reset(); }
                game.ShowStatus("disabled", "Living Los Santos: unavailable in the current game state."); game.Render(null); return;
            }
            if (!profileConfirmation.Confirmed)
            {
                if (bridge != null) { CancelInput(); social?.Cancel(); social = null; bridge.Dispose(); bridge = null; controller = null; game.Reset(); }
                game.ShowStatus("profile_selection", profileConfirmation.Warning(profileId, saveSlot, confirmProfileKey.ToString()));
                game.Render(null); return;
            }
            if (bridge == null) Connect(world);
            PruneAndPublish();
            if (bridge == null) { game.Render(typing); return; }
            if (input.Bound && !input.IsCurrent(game.Resolve(input.ActorId!), world, generation)) CancelInput();
            if (bridge!.Session != null && controller == null) { controller = new CommandController(game, bridge.Session.SessionId, generation, bridge.Session.PolicyVersion, bridge.Session.Capabilities); if (socialEnabled) social = new SocialController(game, bridge.Session.SessionId, generation); }
            if (bridge.ProtocolFault) { controller?.StopForProtocolFault(); social?.Cancel(); }
            if (bridge.Online)
            {
                if (!worldSent) { bridge.Events.TryAdd(Event("world.snapshot", null, new { online = world.Online, mission_active = world.MissionActive, cutscene_active = world.CutsceneActive, random_event_active = world.RandomEventActive, paused = world.Paused, protagonist_id = world.ProtagonistId, weather = world.Weather })); worldSent = true; }
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
                    while (controller.Commands.Remaining > 0 && bridge.Commands.TryTake(out var command))
                    {
                        if (social != null && !controller.FailedClosed && SocialController.Owns(command)) social.Handle(command, DateTimeOffset.UtcNow);
                        else { if (command.Kind == "scene.cancel") social?.Cancel((string?)command.Payload["scene_id"]); controller.Commands.TryAdd(command); }
                    }
                }
            }
            else if (!offlineShown)
            {
                log.Write("bridge_offline", bridge.LastFailure);
                CancelInput(); social?.Cancel(); game.ShowStatus("offline", "Living Los Santos: service unavailable. AI stopped."); offlineShown = true;
            }
            if (controller != null)
            {
                if (bridge.Online || bridge.ProtocolFault) controller.Tick(DateTimeOffset.UtcNow);
                if (bridge.Online) TickSocial(world);
                while (bridge.Acks.Remaining > 0 && controller.Acks.TryTake(out var ack)) bridge.Acks.TryAdd(ack);
            }
            wasOnline = bridge.Online;
            if (bridge.Notices.TryTake(out var notice)) { log.Write("bridge_notice", notice); game.ShowStatus("error", "Living Los Santos: " + notice); }
            bridge.Fps = Game.FPS; bridge.ActorCount = game.BoundActorCount;
            if (DateTimeOffset.UtcNow >= nextSample && bridge.Online)
            {
                var pending = pinConfirmation.PendingActorId; pinConfirmation.Expire(DateTimeOffset.UtcNow);
                if (pending != null && game.Resolve(pending) is var pinned && (pinned == null || pinned.Ownership == Ownership.ModOwned)) pinConfirmation.Complete(pending);
                bridge.Snapshots.ReplaceOldest(Event("world.snapshot", null, new { online = world.Online, mission_active = world.MissionActive, cutscene_active = world.CutsceneActive, random_event_active = world.RandomEventActive, paused = world.Paused, protagonist_id = world.ProtagonistId, weather = world.Weather }));
                if (game.SelectedActorId != null) SendSnapshot(game.Resolve(game.SelectedActorId));
                nextSample = DateTimeOffset.UtcNow.AddMilliseconds(500);
            }
            game.Render(typing, input.Whisper);
            timer.Stop();
            if (timer.ElapsedMilliseconds > 20) { log.Write("slow_tick", "milliseconds=" + timer.ElapsedMilliseconds); social?.Cancel(null, true); CancelInput(); game.ShowStatus("slow", "Living Los Santos: slow adapter tick. Pending AI stopped."); }
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
            var snapshot = ActorSnapshot(actor);
            if (reliable) { if (!bridge.Events.TryAdd(snapshot)) { game.ShowStatus("error", "Living Los Santos: observation queue is full."); return null; } }
            else bridge.Snapshots.ReplaceOldest(snapshot);
            return snapshot;
        }
        private EventEnvelope ActorSnapshot(ActorState actor)
        {
            return Event("actor.snapshot", actor.ActorId, new {
                runtime_handle = actor.Handle, runtime_instance_id = actor.RuntimeInstanceId,
                ownership = actor.Ownership == Ownership.ModOwned ? "mod_owned" : actor.Ownership == Ownership.GameScriptOwned ? "game_script_owned" : actor.Ownership == Ownership.Unknown ? "unknown" : "ambient",
                control_policy = actor.Policy == ControlPolicy.Protected ? "protected" : actor.Policy == ControlPolicy.ServiceLimited ? "service_limited" : "ambient", exists = actor.Exists, alive = actor.Alive,
                distance = actor.Distance, mission_entity = actor.MissionEntity, busy = actor.Busy, in_combat = actor.InCombat, can_move = actor.CanMove,
                observed_threat = actor.ObservedThreat,
                in_vehicle = actor.InVehicle, is_driver = actor.IsDriver, vehicle_stopped = actor.VehicleStopped,
                has_firearm = actor.HasFirearm, mod_following = actor.ModFollowing, action_context = actor.ActorId == game.SelectedActorId ? game.ActionContext(actor.ActorId) : new JObject(),
                position = new { x = actor.X, y = actor.Y, z = actor.Z }, heading = actor.Heading, area_id = actor.AreaId, interior_id = actor.InteriorId, name = actor.Name, actor_kind = actor.Kind,
                role_class = PedRoleTable.WireName(actor.Role), role_reason = actor.RoleReason, ped_facts = actor.Facts, activity = actor.Activity });
        }
        private void TickSocial(WorldState world)
        {
            if (social == null || bridge?.Session == null) return;
            var now = DateTimeOffset.UtcNow;
            if (controller?.FailedClosed == true) social.Cancel(null, true);
            else if (now >= nextNearbySample) { SendSnapshot(game.ScanNearby(generation, now)); nextNearbySample = now.AddMilliseconds(75); }
            social.Tick(now);
            while (bridge.Acks.Remaining > 0 && social.Acks.TryTake(out var ack)) bridge.Acks.TryAdd(ack);
            while (bridge.SocialDeliveries.Remaining > 0 && social.Deliveries.TryTake(out var delivery))
            {
                var command = delivery.Item1; var observed = delivery.Item2; var sampleWorld = observed.World;
                var snapshots = new List<EventEnvelope> { Event("world.snapshot", null, new { online = sampleWorld.Online, mission_active = sampleWorld.MissionActive, cutscene_active = sampleWorld.CutsceneActive, random_event_active = sampleWorld.RandomEventActive, paused = sampleWorld.Paused, protagonist_id = sampleWorld.ProtagonistId, weather = sampleWorld.Weather }) };
                snapshots.AddRange(observed.Actors.Select(ActorSnapshot));
                // All evidence belongs to the render frame, even if the game clock advanced this tick.
                foreach (var snapshot in snapshots) { snapshot.GameDatetime = sampleWorld.GameDatetime.ToString("s"); snapshot.ProtagonistId = sampleWorld.ProtagonistId; }
                bridge.SocialDeliveries.TryAdd(new SocialDeliveryDispatch {
                    Ack = new Ack { CommandId = command.Id, SessionId = bridge.Session.SessionId, RuntimeGeneration = generation, Status = "completed", Reason = "overhead_rendered", Observed = new Dictionary<string, object> { ["delivery"] = "subtitle", ["text"] = (string)command.Payload["text"]!, ["display_rule"] = DisplayText.Rule, ["displayed_at"] = observed.DisplayedAt.ToString("O") } },
                    Snapshots = snapshots.ToArray(),
                    Body = new { session_id = bridge.Session.SessionId, runtime_generation = generation, protagonist_id = sampleWorld.ProtagonistId, scene_id = (string)command.Payload["scene_id"]!, game_datetime = sampleWorld.GameDatetime.ToString("s"), command_id = command.Id, witnesses = observed.Witnesses }
                });
            }
            while (bridge.Controls.Remaining > 0 && social.Cancellations.TryTake(out var id)) bridge.Controls.TryAdd(Event("scene.cancel", null, new { scene_id = id }));
            if (social.FailedClosed) { social.Cancel(); bridge.RequiresReconnect = true; return; }
            if (controller?.FailedClosed == true || now < nextAmbientScene || social.ActiveCount >= 2) return;
            foreach (var expired in ambientCooldowns.Where(p => p.Value <= now).Select(p => p.Key).ToArray()) ambientCooldowns.Remove(expired);
            var actors = game.NearbyActors.Select(game.Resolve).Where(a => SocialController.Eligible(a, generation) && a!.Distance > 12 && !ambientCooldowns.ContainsKey(a.ActorId) && a.ActorId != input.ActorId && a.Activity?.OnPhone != true).Select(a => a!).ToArray();
            for (var i = 0; i < actors.Length; i++) for (var j = i + 1; j < actors.Length; j++)
                if (social.StartAmbient("ambient-" + Guid.NewGuid().ToString("N"), new[] { actors[i], actors[j] }, now))
                { ambientCooldowns[actors[i].ActorId] = ambientCooldowns[actors[j].ActorId] = now.AddSeconds(120); nextAmbientScene = now.AddSeconds(30); return; }
        }
        private bool LockTarget()
        {
            if (bridge?.Online != true || game.SelectedActorId == null) return false;
            var actor = game.Resolve(game.SelectedActorId);
            if (!input.TryBind(actor, game.ReadWorld(), generation)) return false;
            social?.CancelActor(actor!.ActorId);
            targetSnapshot = SendSnapshot(actor, true);
            if (targetSnapshot == null) { input.Reset(); return false; }
            return true;
        }
        private bool PrepareTurn()
        {
            var actor = game.SelectedActorId == null ? null : game.Resolve(game.SelectedActorId);
            if (input.CanContinueFollow(actor, game.ReadWorld(), generation, controller))
            {
                targetSnapshot = SendSnapshot(actor, true);
                return targetSnapshot != null;
            }
            CancelInput(clearTarget: false);
            return LockTarget();
        }
        private bool Control(string kind, string? canceledScene = null)
        {
            if (bridge == null || kind != "scene.cancel" && !input.Bound) return false;
            object payload = kind == "scene.cancel" ? (object)new { scene_id = canceledScene } : input.ControlPayload()!;
            if (bridge.Controls.TryAdd(Event(kind, kind == "scene.cancel" ? null : input.ActorId, payload))) return true;
            bridge.RequiresReconnect = true;
            game.ShowStatus("error", "Living Los Santos: control queue is full. Reconnecting to stop input.");
            return false;
        }
        private void CancelInput(bool clearTarget = true)
        {
            input.Cancel(controller, scene => Control("scene.cancel", scene), game.ClearSelection, clearTarget);
            if (clearTarget) pinConfirmation.Disarm();
            targetSnapshot = null; typing = null;
            game.ShowStatus("canceled", "Living Los Santos: conversation canceled.");
        }
        private void OnKeyDown(object sender, KeyEventArgs args)
        {
            // SHVDN drains key events before Tick, including before the first thread binding.
            if (!game.IsScriptThread || !readyForInput || !enabled) return;
            inputControls.KeyDown((int)args.KeyCode, typing != null);
            if (args.KeyCode == confirmProfileKey)
            {
                if (!profileKeyHeld)
                {
                    if (bridge?.TimelineSelectionRequired == true)
                    {
                        CancelInput(); bridge.Dispose(); bridge = null; controller = null; game.Reset();
                    }
                    else if (!profileConfirmation.Confirmed) profileConfirmation.Confirm(game.ReadWorld(), profileId, saveSlot);
                }
                profileKeyHeld = true; return;
            }
            if (!profileConfirmation.Confirmed) return;
            if ((args.KeyCode == pttKey || args.KeyCode == whisperKey) && typing == null && !heldTalkKeys.Add(args.KeyCode)) return;
            if (ConversationInput.ShouldCancel(typing != null, args.KeyCode == cancelKey, args.KeyCode == Keys.Escape))
            {
                CancelInput(); return;
            }
            if (typing != null)
            {
                if (args.KeyCode == Keys.Enter)
                {
                    var phoneActive = game.PhoneActive;
                    var typed = input.TypedPayload(typing, phoneActive);
                    if (phoneActive)
                    {
                        game.ShowStatus("phone", "Living Los Santos: phone active. Draft kept; press Enter after the call, or Escape to cancel."); return;
                    }
                    if (typed == null) return;
                    if (bridge?.Online != true || !input.IsCurrent(game.Resolve(input.ActorId!), game.ReadWorld(), generation))
                    { game.ShowStatus("error", "Living Los Santos: target unavailable. Cancel and select a target again."); return; }
                    if (!bridge.Events.TryAdd(Event("player.utterance_text", input.ActorId, typed)))
                    { game.ShowStatus("error", "Living Los Santos: input queue is full. Draft kept; try Enter again."); return; }
                    game.ShowStatus("thinking", "Living Los Santos: thinking.");
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
                var actor = game.SelectTarget(generation, out var refusal); PruneAndPublish();
                if (bridge == null) return;
                SendSnapshot(actor); LogClassification(actor ?? refusal);
                game.ShowStatus(game.TargetError == null ? "target" : "target_error", game.TargetError ?? (actor != null ? "Target: " + actor.Name + " (" + PedClassifier.Availability(actor, game.ReadWorld(), generation) + ")"
                    : refusal != null ? "Nearest: " + PedRoleTable.Label(refusal.Role) + " — not available" : "No nearby resident is available.")); return;
            }
            if (args.KeyCode == pinKey) { RequestPin(); return; }
            if (args.KeyCode == textKey)
            {
                if (PrepareTurn()) { typing = ""; game.ShowStatus("typing", "Living Los Santos: typing. Enter to send; Escape to cancel."); }
                else game.ShowStatus("unavailable", "Living Los Santos: select an available target with " + targetKey + ".");
                return;
            }
            if ((args.KeyCode == pttKey || args.KeyCode == whisperKey) && !input.PushToTalkHeld)
            {
                if (PrepareTurn() && input.StartPushToTalk((int)args.KeyCode, args.KeyCode == whisperKey))
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
            CancelInput(clearTarget: false);
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
            if (!game.IsScriptThread) return;
            inputControls.KeyUp((int)args.KeyCode);
            if (!readyForInput || !enabled) return;
            if (args.KeyCode == confirmProfileKey) profileKeyHeld = false;
            if (args.KeyCode != pttKey && args.KeyCode != whisperKey) return;
            heldTalkKeys.Remove(args.KeyCode);
            if (!input.StopPushToTalk((int)args.KeyCode)) return;
            if (input.PushToTalkStartQueued)
            {
                if (!Control("voice.ptt_stopped")) CancelInput();
            }
            else CancelInput();
        }
        // SHVDN may abort from its domain thread; cleanup is best effort and must never throw.
        private void OnAborted(object sender, EventArgs args)
        {
            if (game.IsScriptThread)
            {
                try { social?.Cancel(); CancelInput(); } catch (Exception) { log.Write("abort_cancel_failed"); }
                try { game.Reset(); } catch (Exception) { log.Write("abort_reset_failed"); }
            }
            else log.Write("abort_game_cleanup_skipped");
            try { bridge?.Dispose(); } catch (Exception error) { log.Write("abort_bridge_failed type=" + error.GetType().Name); }
            log.Write("adapter_stopped");
            try { log.Dispose(); } catch (Exception) { }
        }
    }
}

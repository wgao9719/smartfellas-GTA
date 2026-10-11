using System;
using System.Collections.Generic;
using System.Linq;
using GTA;
using GTA.Math;
using GTA.Native;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;

namespace LivingNPC.GameAdapter
{
    internal sealed partial class GtaGameAdapter
    {
        private sealed class VehicleBinding
        {
            public Vehicle Vehicle = null!;
            public int Model, Marker;
        }
        private readonly Dictionary<string, VehicleBinding> vehicles = new Dictionary<string, VehicleBinding>();
        private const string WaveDictionary = "friends@frj@ig_1", WaveAnimation = "wave_a";
        private static bool ActiveTask(Ped ped, int hash)
        {
            var status = Function.Call<int>(Hash.GET_SCRIPT_TASK_STATUS, ped.Handle, hash);
            return status == 0 || status == 1;
        }
        private bool HasOwnedTask(string actorId, string action) => tasks.Values.Any(t => t.ActorId == actorId && t.Action == action &&
            bindings.TryGetValue(actorId, out var b) && Valid(b) && ActiveTask(b.Ped, t.Hash));
        private bool ValidVehicle(VehicleBinding b) => b.Vehicle.Exists() && b.Vehicle.Model.Hash == b.Model &&
            !b.Vehicle.IsPersistent && b.Vehicle.Model.IsCar && !b.Vehicle.IsDead &&
            Function.Call<bool>(Hash.DECOR_EXIST_ON, b.Vehicle.Handle, Decorator) && Function.Call<int>(Hash.DECOR_GET_INT, b.Vehicle.Handle, Decorator) == b.Marker;
        private string? RegisterVehicle(Vehicle vehicle)
        {
            foreach (var entry in vehicles.ToArray())
                if (!ValidVehicle(entry.Value) || (entry.Value.Vehicle.Position.DistanceTo(Game.Player.Character.Position) > 30f &&
                    !tasks.Values.Any(t => t.TargetId == entry.Key))) vehicles.Remove(entry.Key);
            var existing = vehicles.FirstOrDefault(v => v.Value.Vehicle.Handle == vehicle.Handle);
            if (existing.Key != null) return existing.Key;
            if (vehicles.Count >= 8 || !vehicle.Exists() || vehicle.IsPersistent || !vehicle.Model.IsCar || vehicle.IsDead) return null;
            var marker = new Random(Guid.NewGuid().GetHashCode()).Next(1, int.MaxValue);
            if (!Function.Call<bool>(Hash.DECOR_SET_INT, vehicle.Handle, Decorator, marker)) return null;
            var id = Guid.NewGuid().ToString("N");
            vehicles[id] = new VehicleBinding { Vehicle = vehicle, Model = vehicle.Model.Hash, Marker = marker };
            return id;
        }
        private Ped? TargetPed(string id, Ped actor)
        {
            Ped? target = id == "player" ? Game.Player.Character : bindings.Values.FirstOrDefault(b => b.Instance == id && Valid(b) &&
                PedClassifier.Classify(Sample(b.Ped, b)).Policy == ControlPolicy.Ambient)?.Ped;
            return target != null && target.Exists() && !target.IsDead && target.Handle != actor.Handle &&
                target.Position.DistanceTo(actor.Position) <= 15f && Function.Call<bool>(Hash.HAS_ENTITY_CLEAR_LOS_TO_ENTITY, actor.Handle, target.Handle, 17) ? target : null;
        }
        private Vehicle? TargetVehicle(string id, Ped actor) => vehicles.TryGetValue(id, out var b) && ValidVehicle(b) &&
            b.Vehicle.Position.DistanceTo(actor.Position) <= 15f ? b.Vehicle : null;
        private static bool Threat(Ped target, Ped actor) => Function.Call<bool>(Hash.IS_PED_IN_COMBAT, target.Handle, actor.Handle);
        private static JObject Target(string id, string kind, string name, Vector3 position) => JObject.FromObject(new {
            target_id = id, kind, name, position = new { x = position.X, y = position.Y, z = position.Z },
            is_threat = false, driver_seat_free = false, passenger_seat_free = false
        });
        // Called for the 500ms snapshot, not every controller tick. Only these opaque
        // target IDs can be used by commands; no model-supplied entity handles.
        public JObject ActionContext(string actorId)
        {
            OnScriptThread(); var targets = new JArray();
            if (!bindings.TryGetValue(actorId, out var binding) || !Valid(binding)) return new JObject { ["targets"] = targets };
            var ped = binding.Ped;
            foreach (var id in new[] { "player" }.Concat(bindings.Values.Where(b => b.ActorId != actorId).Select(b => b.Instance)).Take(36))
            {
                var target = TargetPed(id, ped); if (target == null) continue;
                var item = Target(id, "ped", id == "player" ? "Player" : bindings.Values.First(b => b.Instance == id).Name, target.Position);
                item["is_threat"] = Threat(target, ped); targets.Add(item);
                if (targets.Count == 5) break;
            }
            var nearby = World.GetNearbyVehicles(ped, 15f).OrderBy(v => v.Position.DistanceTo(ped.Position)).Take(8);
            foreach (var vehicle in nearby)
            {
                var id = RegisterVehicle(vehicle); if (id == null) continue;
                var item = Target(id, "vehicle", vehicle.DisplayName, vehicle.Position);
                item["driver_seat_free"] = vehicle.Speed < 1f && vehicle.IsSeatFree(VehicleSeat.Driver);
                item["passenger_seat_free"] = vehicle.Speed < 1f && PassengerSeat(vehicle).HasValue;
                targets.Add(item); if (targets.Count == 8) break;
            }
            return new JObject { ["targets"] = targets };
        }
        private static VehicleSeat? PassengerSeat(Vehicle vehicle)
        {
            for (var seat = 0; seat < vehicle.PassengerCapacity; seat++) if (vehicle.IsSeatFree((VehicleSeat)seat)) return (VehicleSeat)seat;
            return null;
        }
        private static bool SafePoint(JObject args, Ped ped, bool driving, out Vector3 destination)
        {
            var point = (JObject)args["point"]!;
            destination = new Vector3((float)point["x"]!, (float)point["y"]!, (float)point["z"]!);
            if (destination.DistanceTo(ped.Position) > 15f) return false;
            if (driving)
            {
                var output = new OutputArgument();
                if (!Function.Call<bool>(Hash.GET_CLOSEST_VEHICLE_NODE, destination.X, destination.Y, destination.Z, output, 1, 3f, 0f)) return false;
                var road = output.GetResult<Vector3>();
                if (road.DistanceTo(destination) > 3f || road.DistanceTo(ped.Position) > 15f) return false;
                destination = road; return true;
            }
            var safe = new OutputArgument();
            if (!Function.Call<bool>(Hash.GET_SAFE_COORD_FOR_PED, destination.X, destination.Y, destination.Z, true, safe, 0)) return false;
            var candidate = safe.GetResult<Vector3>();
            if (candidate.DistanceTo(destination) > 2f || candidate.DistanceTo(ped.Position) > 15f) return false;
            destination = candidate; return true;
        }
        public bool Start(Command command, ActorState actor, string actionId, JObject arguments)
        {
            OnScriptThread();
            if (IsSocialActor(actor.ActorId) || !bindings.TryGetValue(actor.ActorId, out var binding) || !Valid(binding) || binding.Instance != actor.RuntimeInstanceId ||
                NativeActions.Check(actionId, arguments, actor, (bool?)command.Payload["consent"] == true) != null) return false;
            var ped = binding.Ped;
            if (!TryPosition(ped, out var origin)) return false;
            var record = new TaskRecord { ActorId = actor.ActorId, Action = actionId, Duration = NativeActions.Duration(actionId, arguments),
                Motion = new ActionMotionTracker(MonotonicMilliseconds(), origin.X, origin.Y, origin.Z) };
            if (!record.Motion.IsValid) return false;
            var targetId = (string?)arguments["target_id"] ?? (string?)arguments["threat_actor_id"] ?? "";
            var target = TargetPed(targetId, ped);
            var vehicle = actionId == "enter_vehicle" ? TargetVehicle(targetId, ped) : ped.CurrentVehicle;
            if (NativeActions.IsPoint(actionId) && !SafePoint(arguments, ped, actionId == "drive_to_point", out record.Destination)) return false;
            if (NativeActions.IsVehicle(actionId))
            {
                if (vehicle == null || !vehicle.Exists() || vehicle.IsPersistent || !vehicle.Model.IsCar || vehicle.IsDead) return false;
                targetId = RegisterVehicle(vehicle) ?? ""; if (targetId.Length == 0) return false;
            }
            record.TargetId = targetId;
            string task;
            switch (actionId)
            {
                case "face_player": ped.Task.TurnTo(Game.Player.Character, record.Duration); task = "TURN_PED_TO_FACE_ENTITY"; break;
                case "wait_here": ped.Task.StandStill(record.Duration); task = "STAND_STILL"; break;
                case "follow_player":
                    record.TargetId = "player"; record.MaxTravel = (double?)arguments["max_follow_meters"] ?? 10;
                    ped.Task.FollowToOffsetFromEntity(Game.Player.Character, new Vector3(0, -2, 0), 1f, record.Duration, 2f, true);
                    task = "FOLLOW_TO_OFFSET_OF_ENTITY"; break;
                case "stop_following":
                    var previous = tasks.Values.FirstOrDefault(t => t.ActorId == actor.ActorId && t.Action == "follow_player");
                    if (previous == null || !ActiveTask(ped, previous.Hash)) return false;
                    ped.Task.ClearAll(); task = "FOLLOW_TO_OFFSET_OF_ENTITY"; break;
                case "go_to_safe_point": ped.Task.GoTo(record.Destination, record.Duration); task = "FOLLOW_NAV_MESH_TO_COORD"; break;
                case "run_to_point": ped.Task.RunTo(record.Destination, false, record.Duration); task = "FOLLOW_NAV_MESH_TO_COORD"; break;
                case "wander_nearby":
                    record.MaxTravel = Math.Min(15, (double?)arguments["radius"] ?? 5);
                    Function.Call(Hash.TASK_WANDER_IN_AREA, ped.Handle, origin.X, origin.Y, origin.Z, (float)record.MaxTravel, 0f, 0f);
                    task = "WANDER_IN_AREA"; break;
                case "flee_from_threat":
                    if (target == null || !Threat(target, ped)) return false;
                    record.InitialTargetDistance = target.Position.DistanceTo(origin);
                    ped.Task.FleeFrom(target, record.Duration); task = "SMART_FLEE_PED"; break;
                case "look_at":
                    if (target == null) return false;
                    ped.Task.LookAt(target, record.Duration); task = "LOOK_AT_ENTITY"; break;
                case "hands_up": ped.Task.HandsUp(record.Duration); task = "HANDS_UP"; break;
                case "cower": ped.Task.Cower(record.Duration); task = "COWER"; break;
                case "play_verified_gesture":
                    Function.Call(Hash.REQUEST_ANIM_DICT, WaveDictionary); record.Dispatched = false; task = "PLAY_ANIM"; break;
                case "enter_vehicle":
                    if (vehicle == null || vehicle.Speed >= 1f) return false;
                    var seat = (string?)arguments["seat"] == "driver" ? VehicleSeat.Driver : PassengerSeat(vehicle);
                    if (!seat.HasValue || !vehicle.IsSeatFree(seat.Value)) return false;
                    record.Seat = seat.Value;
                    ped.Task.EnterVehicle(vehicle, record.Seat, record.Duration, 1f, EnterVehicleFlags.DontJackAnyone | EnterVehicleFlags.BlockSeatShuffling); task = "ENTER_VEHICLE"; break;
                case "leave_vehicle": ped.Task.LeaveVehicle(vehicle!, LeaveVehicleFlags.DontDefaultWarpIfDoorBlocked); task = "LEAVE_VEHICLE"; break;
                case "drive_to_point": ped.Task.DriveTo(vehicle!, record.Destination, 2f, 4f, DrivingStyle.Normal); task = "VEHICLE_DRIVE_TO_COORD_LONGRANGE"; break;
                case "cruise_vehicle": ped.Task.CruiseWithVehicle(vehicle!, 4f, DrivingStyle.Normal); task = "VEHICLE_DRIVE_WANDER"; break;
                case "fight_target":
                    if (target == null) return false;
                    ped.Task.FightAgainst(target, record.Duration); task = "COMBAT_PED"; break;
                case "shoot_at_target":
                    if (target == null || !actor.HasFirearm) return false;
                    ped.Task.ShootAt(target, record.Duration); task = "SHOOT_AT_ENTITY"; break;
                default: return false;
            }
            record.Hash = Game.GenerateHash("SCRIPT_TASK_" + task); tasks[command.Id] = record; return true;
        }
        private static EffectObservation Result(EffectStatus status, string reason, ActionMotionSample? motion = null) => new EffectObservation {
            Status = status, Reason = reason, Facts = motion == null ? new Dictionary<string, object>() : new Dictionary<string, object> {
                ["elapsed_ms"] = motion.ElapsedMilliseconds, ["distance_meters"] = motion.DistanceMeters, ["displacement_meters"] = motion.DisplacementMeters
            }
        };
        public EffectObservation Observe(Command command, ActorState actor, string actionId)
        {
            OnScriptThread();
            if (actionId == "face_player" || actionId == "wait_here") return ObserveG0(command, actor, actionId);
            if (!tasks.TryGetValue(command.Id, out var task) || !bindings.TryGetValue(actor.ActorId, out var binding) || !Valid(binding)) return Result(EffectStatus.Failed, "binding_lost");
            var ped = binding.Ped;
            if (!TryPosition(ped, out var position) || !task.Motion.TrySample(MonotonicMilliseconds(), position.X, position.Y, position.Z, out var motion))
                return Result(EffectStatus.Failed, "movement_tracking_unavailable");
            var elapsed = motion!.ElapsedMilliseconds;
            var active = ActiveTask(ped, task.Hash); task.ObservedActive |= active;
            if (actionId == "stop_following") return !active ? Result(EffectStatus.Completed, "follow_task_withdrawn", motion) : Result(EffectStatus.Failed, "follow_task_still_active", motion);
            if (motion.DistanceMeters > task.MaxTravel) return Result(EffectStatus.Failed, "travel_limit_reached", motion);
            if (!task.Dispatched)
            {
                if (Function.Call<bool>(Hash.HAS_ANIM_DICT_LOADED, WaveDictionary))
                {
                    Function.Call(Hash.TASK_PLAY_ANIM, ped.Handle, WaveDictionary, WaveAnimation, 8f, -8f, task.Duration, 0, 0f, false, false, false);
                    task.Dispatched = true;
                }
                else if (elapsed > 1000) return Result(EffectStatus.Failed, "animation_load_timeout", motion);
                return Result(EffectStatus.Running, "loading_animation");
            }
            var target = task.TargetId.Length == 0 || NativeActions.IsVehicle(actionId) || actionId == "enter_vehicle" ? null : TargetPed(task.TargetId, ped);
            if ((actionId == "look_at" || actionId == "flee_from_threat" || actionId == "follow_player" || NativeActions.IsCombat(actionId)) && target == null)
                return Result(EffectStatus.Failed, "target_lost", motion);
            if (NativeActions.IsVehicle(actionId) || actionId == "enter_vehicle")
            {
                var vehicle = TargetVehicle(task.TargetId, ped);
                if (vehicle == null) return Result(EffectStatus.Failed, "vehicle_lost", motion);
                if (actionId == "enter_vehicle")
                {
                    if (ped.IsInVehicle(vehicle) && ped.SeatIndex == task.Seat) return Result(EffectStatus.Completed, "vehicle_seat_observed", motion);
                    if (!vehicle.IsSeatFree(task.Seat)) return Result(EffectStatus.Failed, "seat_taken", motion);
                }
                else if (actionId == "leave_vehicle")
                {
                    if (!ped.IsInVehicle()) return Result(EffectStatus.Completed, "vehicle_exit_observed", motion);
                }
                else if (!ped.IsInVehicle(vehicle) || ped.SeatIndex != VehicleSeat.Driver) return Result(EffectStatus.Failed, "driver_seat_lost", motion);
            }
            if (NativeActions.IsPoint(actionId) && position.DistanceTo(task.Destination) <= 2f && (actionId != "drive_to_point" || ped.CurrentVehicle.Speed < 1f))
                return Result(EffectStatus.Completed, "destination_observed", motion);
            if (actionId == "look_at") task.EffectSeen |= Function.Call<bool>(Hash.IS_PED_HEADTRACKING_PED, ped.Handle, target!.Handle);
            else if (actionId == "play_verified_gesture") task.EffectSeen |= Function.Call<bool>(Hash.IS_ENTITY_PLAYING_ANIM, ped.Handle, WaveDictionary, WaveAnimation, 3);
            else if (actionId == "fight_target") task.EffectSeen |= Function.Call<bool>(Hash.IS_PED_IN_COMBAT, ped.Handle, target!.Handle);
            else if (actionId == "shoot_at_target") task.EffectSeen |= ped.IsShooting;
            else if (actionId == "flee_from_threat") task.EffectSeen |= target!.Position.DistanceTo(position) >= task.InitialTargetDistance + 2;
            else if (actionId == "follow_player") task.EffectSeen = target!.Position.DistanceTo(position) <= 4f;
            else if (actionId == "wander_nearby" || actionId == "cruise_vehicle") task.EffectSeen |= motion.DistanceMeters >= .5;
            else task.EffectSeen |= active;
            if (elapsed >= task.Duration)
            {
                if (NativeActions.IsPoint(actionId) || actionId == "enter_vehicle" || actionId == "leave_vehicle") return Result(EffectStatus.Failed, "destination_timeout", motion);
                var observed = task.EffectSeen && (task.ObservedActive || actionId == "look_at" || actionId == "play_verified_gesture");
                return observed ? Result(EffectStatus.Completed, "bounded_effect_observed", motion) : Result(EffectStatus.Failed, "effect_not_observed", motion);
            }
            // Secondary look/animation tasks have their own observation APIs.
            if (!active && elapsed > 750 && actionId != "look_at" && actionId != "play_verified_gesture") return Result(EffectStatus.TakenOver, "task_ended_early", motion);
            return Result(EffectStatus.Running, "observing_effect");
        }
        public void Release(Command command, ActorState actor, string actionId)
        {
            OnScriptThread();
            if (!tasks.TryGetValue(command.Id, out var task) || !bindings.TryGetValue(actor.ActorId, out var binding) ||
                !Valid(binding) || binding.Instance != actor.RuntimeInstanceId || !ReadWorld().Safe) return;
            var current = PedClassifier.Classify(Sample(binding.Ped, binding));
            if (current.Policy != ControlPolicy.Ambient || (current.Ownership != Ownership.Ambient && current.Ownership != Ownership.ModOwned)) return;
            var ped = binding.Ped;
            if (actionId == "look_at")
            {
                var target = TargetPed(task.TargetId, ped);
                if (target != null && Function.Call<bool>(Hash.IS_PED_HEADTRACKING_PED, ped.Handle, target.Handle)) Function.Call(Hash.TASK_CLEAR_LOOK_AT, ped.Handle);
            }
            else if (actionId == "play_verified_gesture")
            {
                if (Function.Call<bool>(Hash.IS_ENTITY_PLAYING_ANIM, ped.Handle, WaveDictionary, WaveAnimation, 3)) Function.Call(Hash.STOP_ANIM_TASK, ped.Handle, WaveDictionary, WaveAnimation, -4f);
            }
            else if (actionId != "stop_following" && ActiveTask(ped, task.Hash)) ped.Task.ClearAll();
        }
    }
}

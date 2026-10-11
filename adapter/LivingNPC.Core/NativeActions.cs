using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    // Explicit executor allowlist, separate from descriptive registry metadata.
    public static class NativeActions
    {
        public static readonly string[] DevelopmentCapabilities = {
            "face_player", "wait_here", "follow_player", "stop_following", "go_to_safe_point",
            "run_to_point", "wander_nearby", "flee_from_threat", "play_verified_gesture",
            "look_at", "hands_up", "cower", "enter_vehicle", "leave_vehicle",
            "drive_to_point", "cruise_vehicle", "fight_target", "shoot_at_target"
        };
        private static readonly HashSet<string> supported = new HashSet<string>(DevelopmentCapabilities);
        private static readonly HashSet<string> consent = new HashSet<string> {
            "follow_player", "go_to_safe_point", "run_to_point", "wander_nearby", "enter_vehicle",
            "leave_vehicle", "drive_to_point", "cruise_vehicle", "fight_target", "shoot_at_target"
        };
        public static bool IsCombat(string action) => action == "fight_target" || action == "shoot_at_target";
        public static bool IsVehicle(string action) => action == "leave_vehicle" || action == "drive_to_point" || action == "cruise_vehicle";
        public static bool IsPoint(string action) => action == "go_to_safe_point" || action == "run_to_point" || action == "drive_to_point";
        public static int Duration(string action, JObject args) => (int)Math.Ceiling(1000 * ((double?)args["duration_seconds"] ?? (action == "face_player" ? 1.5 : 3)));
        public static string? Check(string action, JObject args, ActorState actor, bool hasConsent)
        {
            if (!supported.Contains(action)) return "no_executor";
            var error = ActionRegistry.Default.ValidateArguments(action, args);
            if (error != null) return error;
            if (consent.Contains(action) && !hasConsent) return "consent_required";
            if (!LocalActionController.Supported(action) && Duration(action, args) > (action == "face_player" || action == "wait_here" ? 5000 : 10000)) return "invalid_duration";
            if (IsVehicle(action))
            {
                if (!actor.InVehicle) return "not_in_vehicle";
                if (action == "leave_vehicle" && !actor.VehicleStopped) return "vehicle_moving";
                if (action != "leave_vehicle" && !actor.IsDriver) return "not_driver";
            }
            else if (actor.InVehicle) return "actor_in_vehicle";
            if ((action == "follow_player" || action == "wander_nearby" || action == "flee_from_threat" ||
                action == "go_to_safe_point" || action == "run_to_point" || action == "enter_vehicle" || IsCombat(action)) && !actor.CanMove) return "actor_cannot_move";
            if (action == "shoot_at_target" && !actor.HasFirearm) return "no_equipped_firearm";
            if (action == "flee_from_threat" && !hasConsent && !actor.ObservedThreat) return "consent_required";
            if (action == "enter_vehicle" && (string?)args["seat"] != "driver" && (string?)args["seat"] != "passenger") return "invalid_seat";
            if (IsPoint(action) && args["point"] is JObject)
            {
                var point = (JObject)args["point"]!;
                var dx = (double)point["x"]! - actor.X; var dy = (double)point["y"]! - actor.Y; var dz = (double)point["z"]! - actor.Z;
                var distance = Math.Sqrt(dx * dx + dy * dy + dz * dz);
                if (!ActionMotionTracker.IsFinite(distance) || distance > 15) return "point_outside_local_area";
            }
            return null;
        }
    }
}

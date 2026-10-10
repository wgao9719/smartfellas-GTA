using System;

namespace LivingNPC.Core
{
    public sealed class ActionMotionSample
    {
        public double DistanceMeters { get; }
        public double DisplacementMeters { get; }
        public double ElapsedMilliseconds { get; }
        internal ActionMotionSample(double distance, double displacement, double elapsed)
        { DistanceMeters = distance; DisplacementMeters = displacement; ElapsedMilliseconds = elapsed; }
    }

    // Measures the sampled path, not just displacement. A missing interval is not
    // evidence of zero movement. Invalid tracking can never become valid again.
    public sealed class ActionMotionTracker
    {
        public const double MaximumSampleGapMilliseconds = 250;
        private readonly double started, originX, originY, originZ;
        private double last, previousX, previousY, previousZ, distance;
        public string FailureReason { get; private set; } = "";
        public bool IsValid => FailureReason.Length == 0;
        public static bool IsFinite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);

        public ActionMotionTracker(double milliseconds, double x, double y, double z)
        {
            started = last = milliseconds;
            originX = previousX = x; originY = previousY = y; originZ = previousZ = z;
            if (!IsFinite(milliseconds) || milliseconds < 0 || !IsFinite(x) || !IsFinite(y) || !IsFinite(z))
                FailureReason = "movement_sample_unavailable";
        }

        public bool TrySample(double milliseconds, double x, double y, double z, out ActionMotionSample? sample)
        {
            sample = null;
            if (!IsValid) return false;
            if (!IsFinite(milliseconds) || !IsFinite(x) || !IsFinite(y) || !IsFinite(z))
            { FailureReason = "movement_sample_unavailable"; return false; }
            if (milliseconds < last || milliseconds - last > MaximumSampleGapMilliseconds)
            { FailureReason = "movement_tracking_gap"; return false; }
            var segment = Length(x - previousX, y - previousY, z - previousZ);
            var displacement = Length(x - originX, y - originY, z - originZ);
            var total = distance + segment;
            var elapsed = milliseconds - started;
            if (!IsFinite(total) || !IsFinite(displacement) || !IsFinite(elapsed))
            { FailureReason = "movement_sample_unavailable"; return false; }
            distance = total; last = milliseconds;
            previousX = x; previousY = y; previousZ = z;
            sample = new ActionMotionSample(distance, displacement, elapsed);
            return true;
        }

        private static double Length(double x, double y, double z) => Math.Sqrt(x * x + y * y + z * z);
    }
}

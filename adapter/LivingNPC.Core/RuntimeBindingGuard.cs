namespace LivingNPC.Core
{
    // A marker is evidence of this runtime binding only. Once lost, it cannot
    // restore the old identity even if the handle, model and marker match later.
    public sealed class RuntimeBindingGuard
    {
        private readonly int handle, modelHash, marker;
        public bool IsValid { get; private set; }

        public RuntimeBindingGuard(int handle, int modelHash, int marker)
        {
            this.handle = handle; this.modelHash = modelHash; this.marker = marker;
            IsValid = handle != 0 && modelHash != 0 && marker > 0;
        }

        public bool Validate(bool exists, bool alive, int handle, int modelHash, int? observedMarker)
        {
            if (!exists || !alive || handle != this.handle || modelHash != this.modelHash || observedMarker != marker)
                Invalidate();
            return IsValid;
        }

        public void Invalidate() => IsValid = false;
    }
}

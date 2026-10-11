using System;

namespace LivingNPC.Core
{
    // The caller supplies the supported SHVDN lock API and native registration calls.
    public static class DecoratorRegistration
    {
        public static bool TryRegister(Func<bool> isRegistered, Func<bool> isLocked, Action<bool> setLocked, Action register)
        {
            try
            {
                if (isRegistered()) return true;
                var wasLocked = isLocked();
                try
                {
                    if (wasLocked) setLocked(false);
                    if (isLocked()) return false;
                    register();
                }
                finally { setLocked(wasLocked); }
                return isLocked() == wasLocked && isRegistered();
            }
            catch (Exception) { return false; }
        }
    }
}

using System;
using System.Reflection;
using GTA;
using GTA.Native;
using LivingNPC.Core;

namespace LivingNPC.GameAdapter
{
    internal static class GtaDecoratorRegistration
    {
        // IsLocked is public in SHVDN 3.7 nightlies, but absent from our 3.6 compile reference.
        // Use only that supported API; an older/unsupported runtime fails closed.
        public static bool TryRegister(string name)
        {
            var property = typeof(Game).Assembly.GetType("GTA.DecoratorInterface")?.GetProperty("IsLocked", BindingFlags.Public | BindingFlags.Static);
            return DecoratorRegistration.TryRegister(
                () => Function.Call<bool>(Hash.DECOR_IS_REGISTERED_AS_TYPE, name, 3),
                () => property?.GetValue(null) is bool locked ? locked : throw new NotSupportedException(),
                locked => { if (property?.CanWrite != true) throw new NotSupportedException(); property.SetValue(null, locked); },
                () => Function.Call(Hash.DECOR_REGISTER, name, 3));
        }
    }
}

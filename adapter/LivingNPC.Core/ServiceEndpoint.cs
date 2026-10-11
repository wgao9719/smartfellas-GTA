using System;
using System.Net;

namespace LivingNPC.Core
{
    public sealed class ServiceEndpoint
    {
        public const string DefaultUrl = "http://127.0.0.1:8765";
        public Uri Uri { get; }
        public bool FellBack { get; }
        private ServiceEndpoint(Uri uri, bool fellBack) { Uri = uri; FellBack = fellBack; }

        public static ServiceEndpoint Resolve(string? value, bool devLan, bool developmentBuild, string bootstrapToken)
        {
            if (Uri.TryCreate((value ?? DefaultUrl).Trim(), UriKind.Absolute, out var endpoint) &&
                endpoint.Scheme == "http" && endpoint.UserInfo.Length == 0 && endpoint.AbsolutePath == "/" &&
                endpoint.Query.Length == 0 && endpoint.Fragment.Length == 0 && endpoint.Port > 0 &&
                IPAddress.TryParse(endpoint.Host, out var address) &&
                (developmentBuild || !devLan) &&
                (IPAddress.IsLoopback(address) || developmentBuild && devLan && PrivateDevAddress(address) && bootstrapToken.Length >= 24))
                return new ServiceEndpoint(endpoint, false);
            return new ServiceEndpoint(new Uri(DefaultUrl), true);
        }

        private static bool PrivateDevAddress(IPAddress address)
        {
            var b = address.GetAddressBytes();
            return b.Length == 4 && (b[0] == 10 || b[0] == 192 && b[1] == 168 || b[0] == 172 && b[1] >= 16 && b[1] <= 31 || b[0] == 100 && b[1] >= 64 && b[1] <= 127);
        }
    }
}

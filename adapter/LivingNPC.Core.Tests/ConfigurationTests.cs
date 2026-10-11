using System;
using System.IO;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class ConfigurationTests
{
    [Theory]
    [InlineData("http")]
    [InlineData("")]
    [InlineData("not a URI")]
    [InlineData("http://127.0.0.1:bad")]
    public void MalformedEndpointFallsBackWithoutThrowing(string value)
    {
        var result = ServiceEndpoint.Resolve(value, false, false, "");
        Assert.True(result.FellBack);
        Assert.Equal(ServiceEndpoint.DefaultUrl + "/", result.Uri.AbsoluteUri);
    }

    [Theory]
    [InlineData("\n")]
    [InlineData("\r\n")]
    public void RawValuesSurviveCommentsBomSectionsAndWhitespace(string newline)
    {
        var ini = RawIni.Parse("\uFEFF" + string.Join(newline, new[] {
            "  ; URL=ignored", " // [Service]", " [ Other ] ", "URL=wrong", "[ service ]",
            " URL = http://127.0.0.1:8765 ", " BootstrapToken = //synthetic:token=with==padding;literal ",
            " SaveSlot = slot:one//two=three ", "[Input]", "URL=also-wrong" }));
        Assert.Equal(ServiceEndpoint.DefaultUrl, ini.GetString("Service", "url", "missing"));
        Assert.Equal("//synthetic:token=with==padding;literal", ini.GetString("SERVICE", "BootstrapToken", ""));
        Assert.Equal("slot:one//two=three", ini.GetString("Service", "SaveSlot", ""));
        Assert.Equal("missing", ini.GetString("Absent", "URL", "missing"));
        Assert.False(ini.HasInvalidValues);
        Assert.False(ServiceEndpoint.Resolve(ini.GetString("Service", "URL", ""), false, false, "").FellBack);
    }

    [Fact] public void InlineCommentMarkersRemainLiteralAndLastDuplicateWins()
    {
        var ini = RawIni.Parse("[Service]\nURL=first\n[service]\nurl=http://127.0.0.1:8765 // literal\nBootstrapToken=\n");
        Assert.Equal("http://127.0.0.1:8765 // literal", ini.GetString("Service", "URL", ""));
        Assert.Equal("", ini.GetString("Service", "BootstrapToken", "default"));
        Assert.True(ServiceEndpoint.Resolve(ini.GetString("Service", "URL", ""), false, false, "").FellBack);
    }

    [Theory]
    [InlineData("URL=outside\n[Service]\nURL=http://127.0.0.1:8765")]
    [InlineData("[Service]\nbroken\n =empty-key\nURL=http://127.0.0.1:8765")]
    [InlineData("[Service]\nURL=http://127.0.0.1:8765\n[broken\nURL=wrong-section")]
    public void MalformedLinesCannotOverwriteValidSettings(string text)
    {
        var ini = RawIni.Parse(text);
        Assert.True(ini.HasInvalidValues);
        Assert.Equal(ServiceEndpoint.DefaultUrl, ini.GetString("Service", "URL", ""));
    }

    [Fact] public void TypedValuesUseDefaultsWithoutThrowing()
    {
        var ini = RawIni.Parse("[Input]\nKey=not-a-key\nUndefined=9999\nValid=friday\n[Service]\nDevLAN=not-bool\nValid=TrUe");
        Assert.False(ini.GetBoolean("Service", "DevLAN", false));
        Assert.True(ini.GetBoolean("Service", "Valid", false));
        Assert.Equal(DayOfWeek.Monday, ini.GetEnum("Input", "Key", DayOfWeek.Monday));
        Assert.Equal(DayOfWeek.Monday, ini.GetEnum("Input", "Undefined", DayOfWeek.Monday));
        Assert.Equal(DayOfWeek.Friday, ini.GetEnum("Input", "Valid", DayOfWeek.Monday));
        Assert.True(ini.HasInvalidValues);
    }

    [Fact] public void MissingValuesUseDefaultsWithoutWarning()
    {
        var ini = RawIni.Parse("");
        Assert.False(ini.GetBoolean("Service", "DevLAN", false));
        Assert.Equal(DayOfWeek.Monday, ini.GetEnum("Input", "Key", DayOfWeek.Monday));
        Assert.Equal(ServiceEndpoint.DefaultUrl, ini.GetString("Service", "URL", ServiceEndpoint.DefaultUrl));
        Assert.False(ini.HasInvalidValues);
        var endpoint = ServiceEndpoint.Resolve(null, false, false, "");
        Assert.False(endpoint.FellBack);
        Assert.Equal(ServiceEndpoint.DefaultUrl + "/", endpoint.Uri.AbsoluteUri);
    }

    [Fact] public void UnreadableIniUsesDefaultsAndMissingIniIsNormal()
    {
        var path = Path.GetTempFileName();
        try
        {
            using (new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            {
                var ini = RawIni.Load(path);
                Assert.True(ini.HasInvalidValues);
                Assert.Equal("default", ini.GetString("Service", "URL", "default"));
            }
            File.WriteAllText(path, "[Service]\nURL=" + ServiceEndpoint.DefaultUrl);
            Assert.Equal(ServiceEndpoint.DefaultUrl, RawIni.Load(path).GetString("Service", "URL", ""));
        }
        finally { File.Delete(path); }
        Assert.False(RawIni.Load(path).HasInvalidValues);
    }

    [Theory]
    [InlineData("https://127.0.0.1:8765")]
    [InlineData("file:///tmp/test")]
    [InlineData("http://user:password@127.0.0.1:8765")]
    [InlineData("http://127.0.0.1:8765/path")]
    [InlineData("http://127.0.0.1:8765/?key=synthetic")]
    [InlineData("http://127.0.0.1:8765/#fragment")]
    [InlineData("http://127.0.0.1:0")]
    [InlineData("http://example.test:8765")]
    [InlineData("http://8.8.8.8:8765")]
    [InlineData("http://192.168.1.2:8765")]
    public void DisallowedEndpointFallsBackToLoopback(string value)
    {
        var result = ServiceEndpoint.Resolve(value, false, false, "");
        Assert.True(result.FellBack);
        Assert.Equal(ServiceEndpoint.DefaultUrl + "/", result.Uri.AbsoluteUri);
    }

    [Theory]
    [InlineData("http://127.0.0.1:8888")]
    [InlineData("http://127.0.0.2:8765")]
    [InlineData("http://[::1]:8765")]
    public void LoopbackEndpointIsAccepted(string value)
    {
        var result = ServiceEndpoint.Resolve(value, false, false, "");
        Assert.False(result.FellBack);
        Assert.Equal(new Uri(value), result.Uri);
    }

    [Theory]
    [InlineData("10.1.2.3", true)]
    [InlineData("192.168.1.2", true)]
    [InlineData("172.16.0.1", true)]
    [InlineData("172.31.255.255", true)]
    [InlineData("100.64.0.1", true)]
    [InlineData("100.127.255.255", true)]
    [InlineData("172.32.0.1", false)]
    [InlineData("100.128.0.1", false)]
    [InlineData("8.8.8.8", false)]
    public void DevLanRequiresDebugOptInPrivateAddressAndToken(string host, bool allowed)
    {
        var value = "http://" + host + ":8765"; var token = new string('x', 24);
        Assert.Equal(!allowed, ServiceEndpoint.Resolve(value, true, true, token).FellBack);
        Assert.True(ServiceEndpoint.Resolve(value, false, true, token).FellBack);
        Assert.True(ServiceEndpoint.Resolve(value, true, false, token).FellBack);
        Assert.True(ServiceEndpoint.Resolve(value, true, true, token.Substring(1)).FellBack);
    }

    [Fact] public void ReleaseDevLanSettingFallsBackEvenWithLoopbackUrl()
    {
        Assert.True(ServiceEndpoint.Resolve("http://127.0.0.1:8888", true, false, "").FellBack);
    }
}

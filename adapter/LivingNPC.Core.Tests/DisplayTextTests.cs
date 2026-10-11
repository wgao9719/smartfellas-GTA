using System.Collections.Generic;
using System.IO;
using System.Linq;
using LivingNPC.Core;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class DisplayTextTests
{
    // tests/fixtures/display_text.json is shared with tests/test_display.py.
    private static readonly JObject Fixtures = Load();
    private static JObject Load()
    {
        using var stream = typeof(DisplayTextTests).Assembly.GetManifestResourceStream("LivingNPC.Tests.display_text.json")!;
        using var reader = new JsonTextReader(new StreamReader(stream)) { DateParseHandling = DateParseHandling.None };
        return JObject.Load(reader);
    }
    private static JObject Case(int index) => (JObject)Fixtures["cases"]![index]!;
    public static IEnumerable<object[]> Cases() => Enumerable.Range(0, ((JArray)Fixtures["cases"]!).Count).Select(index => new object[] { index });

    [Fact] public void FixturesUseThisRule() { Assert.Equal(DisplayText.Rule, (int)Fixtures["display_rule"]!); Assert.True(((JArray)Fixtures["cases"]!).Count >= 50); }

    [Theory][MemberData(nameof(Cases))]
    public void ServiceOutputIsSafeAndSharedCasesMatchClean(int index)
    {
        var value = Case(index); var input = (string)value["input"]!; var display = (string)value["display"]!;
        Assert.True(DisplayText.IsSafe(display));
        Assert.Equal(DisplayText.Clean(input), DisplayText.Clean(DisplayText.Clean(input)));
        if (!(bool)value["shared"]!) return;
        Assert.Equal(display, DisplayText.Clean(input));
        Assert.Equal(input == display, DisplayText.IsSafe(input));
    }

    [Theory]
    [InlineData("Hi ~r~there~s~")][InlineData("<C>Now</C>")][InlineData("~n~")][InlineData("Line\nbreak")][InlineData("bidi \u202Eevil")]
    [InlineData("zero\u200Bwidth")][InlineData("two  spaces")][InlineData("tab\there")][InlineData("5 > 3")]
    public void MarkupInputsAreUnsafe(string text) { Assert.False(DisplayText.IsSafe(text)); Assert.True(DisplayText.IsSafe(DisplayText.Clean(text))); }

    [Fact] public void NullIsUnsafeAndPlainTextIsUnchanged() { Assert.False(DisplayText.IsSafe(null)); Assert.True(DisplayText.IsSafe(" Plain words, kept spacing. ")); }
}

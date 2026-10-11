using System.Text;
using System.Text.RegularExpressions;

namespace LivingNPC.Core
{
    // Display rule v1, shared with service/lls/display.py. The service also applies NFC and deletes
    // every Unicode format code point, so its output always passes IsSafe. Dialogue is never rewritten here.
    public static class DisplayText
    {
        public const int Rule = 1;
        private static readonly Regex Token = new Regex("~[A-Za-z0-9_]{1,40}~", RegexOptions.CultureInvariant);
        private static readonly Regex Tag = new Regex(@"</?[A-Za-z][A-Za-z0-9]*(?:[\t-\r ][^<>]*)?>", RegexOptions.CultureInvariant);
        public static bool IsSafe(string? text) => text != null && Clean(text) == text;
        public static string Clean(string text)
        {
            var value = Tag.Replace(Token.Replace(text.Replace("~n~", " "), ""), "");
            var output = new StringBuilder(value.Length);
            foreach (var c in value)
            {
                if (c == '~' || c == '<' || c == '>' || Invisible(c)) continue;
                if (!char.IsControl(c) && !char.IsWhiteSpace(c)) output.Append(c);
                else if (output.Length == 0 || output[output.Length - 1] != ' ') output.Append(' ');
            }
            return output.ToString();
        }
        private static bool Invisible(char c) => c == '\u00AD' || c >= '\u200B' && c <= '\u200F' || c >= '\u202A' && c <= '\u202E' || c == '\u2060' || c >= '\u2066' && c <= '\u2069' || c == '\uFEFF';
    }
}

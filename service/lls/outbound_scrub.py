"""Minimize cloud model bodies at the HTTP boundary without recording their contents."""

import base64
import binascii
import ipaddress
import json
import os
import re

import httpx

from .outbound import OutboundDenied

REDACTED = "[REDACTED]"
PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?"
    r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|$)",
    re.DOTALL,
)
API_KEY = re.compile(
    r"(?<![\w-])(?:sk-(?:proj-|svcacct-|ant-)?[A-Za-z0-9_-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}"
    r"|hf_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
    r"(?![\w-])"
)
AUTH = re.compile(
    r"\b(?P<scheme>bearer|basic)[ \t]+(?P<credential>[A-Za-z0-9._~+/-]+=*)",
    re.IGNORECASE,
)
SECRET_FIELD = re.compile(
    r"(?:authorization|(?:[a-z]+[_-])*api[_-]?key|(?:[a-z]+[_-])*token"
    r"|(?:client[_-])?secret|password|passwd)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT = re.compile(
    r"\b(?:authorization|(?:[a-z]+[_-])*api[_-]?key|(?:[a-z]+[_-])*token"
    r"|(?:client[_-])?secret|password|passwd)[ \t]*[:=][ \t]*"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;{}]+)",
    re.IGNORECASE,
)
EMAIL = re.compile(
    r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9-]+(?:\.[A-Z0-9-]+)+\b",
    re.IGNORECASE,
)
PROFILE = re.compile(
    r"[A-Z]:[\\/]+(?:Users|Documents and Settings)[\\/]+"
    r"(?P<user>[^\\/\r\n\"<>|:;,]+)(?:[\\/]+[^\s\"<>|,;]*)?",
    re.IGNORECASE,
)
USERNAME = re.compile(
    r"\b(?:user[_ -]?name|windows[_ -]?user)[ \t]*[:=][ \t]*"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;{}]+)",
    re.IGNORECASE,
)
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
IPV6 = re.compile(r"(?<![\w:])(?:[A-F0-9]{0,4}:){2,}[A-F0-9:.]*(?:%[\w.-]+)?", re.I)
NUMBER = re.compile(
    r"(?<![\w.+-])(?!\d{4}-\d{2}-\d{2}\b)\+?\(?\d[\d ()\t.-]*\d(?!\w|[.-]\d)"
)


def redact_ip(match):
    value = match[0].rstrip(".")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return match[0]
    return REDACTED + match[0][len(value):]


def redact_auth(match):
    credential = match["credential"].rstrip(".")
    if match["scheme"].lower() == "basic":
        try:
            decoded = base64.b64decode(credential, validate=True)
        except (ValueError, binascii.Error):
            return match[0]
        if b":" not in decoded:
            return match[0]
    elif credential.lower() == "of":
        return match[0]
    return REDACTED + match["credential"][len(credential):]


def redact_number(match):
    value = match[0]
    digits = sum(char.isdigit() for char in value)
    if re.fullmatch(r"\d+\.\d+", value) and not re.fullmatch(r"\d{3}\.\d{4}", value):
        return value
    # Telephone and card-like runs; short gameplay counts, dates and coordinates survive.
    if 10 <= digits <= 19 or re.fullmatch(r"\d{3}[-. ]\d{4}", value):
        return REDACTED
    return value


class OutboundScrubber:
    def __init__(self, settings):
        secrets = {
            value for value in (
                settings.bootstrap_token,
                os.environ.get("LLS_BRIDGE_TOKEN", ""),
                os.environ.get("LLM_API_KEY", ""),
            ) if value
        }
        # Clipping persona JSON can leave an undecodable string with escaped secrets.
        self.secrets = sorted(secrets | {
            json.dumps(value, ensure_ascii=ascii_only)[1:-1]
            for value in secrets for ascii_only in (False, True)
        }, key=len, reverse=True)
        self.usernames = {os.environ.get("USERNAME", "")}
        profile = os.environ.get("USERPROFILE", "")
        if profile:
            self.usernames.add(profile.rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1])

    def scrub(self, payload):
        # Inspect all strings before replacing so a profile's username is also removed
        # when it appears alone elsewhere in the same request.
        self.collect_usernames(payload)
        return self.walk(payload)

    def collect_usernames(self, value, depth=0):
        if depth > 32:
            raise ValueError("cloud payload nesting exceeds scrubber limit")
        if isinstance(value, str):
            self.usernames.update(match["user"] for match in PROFILE.finditer(value))
            if value.lstrip().startswith(("{", "[", '"')):
                try:
                    nested = json.loads(value)
                except ValueError:
                    pass
                else:
                    self.collect_usernames(nested, depth + 1)
        elif isinstance(value, dict):
            for key, item in value.items():
                self.collect_usernames(key, depth + 1)
                self.collect_usernames(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                self.collect_usernames(item, depth + 1)

    def text(self, value):
        for secret in self.secrets:
            value = value.replace(secret, REDACTED)
        value = PRIVATE_KEY.sub(REDACTED, value)
        value = PROFILE.sub(REDACTED, value)
        value = AUTH.sub(redact_auth, value)
        value = API_KEY.sub(REDACTED, value)
        value = SECRET_ASSIGNMENT.sub(REDACTED, value)
        value = USERNAME.sub(REDACTED, value)
        value = EMAIL.sub(REDACTED, value)
        value = IPV6.sub(redact_ip, value)
        value = IPV4.sub(redact_ip, value)
        value = NUMBER.sub(redact_number, value)
        for username in sorted(self.usernames - {""}, key=len, reverse=True):
            value = re.sub(r"(?<!\w)" + re.escape(username) + r"(?!\w)", REDACTED, value,
                           flags=re.IGNORECASE)
        return value

    def walk(self, value, depth=0):
        if depth > 32:
            raise ValueError("cloud payload nesting exceeds scrubber limit")
        if isinstance(value, str):
            # Context and persona are JSON strings inside the chat JSON envelope.
            # Decode before matching, including escaped paths and credential values.
            if value.lstrip().startswith(("{", "[", '"')):
                try:
                    nested = json.loads(value)
                except ValueError:
                    pass
                else:
                    self.collect_usernames(nested)
                    scrubbed = self.walk(nested, depth + 1)
                    if scrubbed != nested:
                        return json.dumps(scrubbed, ensure_ascii=False)
            return self.text(value)
        if isinstance(value, dict):
            return {
                self.text(key): REDACTED if SECRET_FIELD.fullmatch(key)
                else self.walk(item, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.walk(item, depth + 1) for item in value]
        if type(value) is int and 13 <= len(str(abs(value))) <= 19:
            return REDACTED
        return value


class ScrubbedModelClient(httpx.AsyncClient):
    """Every cloud send, including retries and new call sites, passes the same scrubber."""

    def __init__(self, settings, *, local, transport=None):
        super().__init__(
            timeout=httpx.Timeout(settings.model_timeout_seconds, connect=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self.settings = settings
        self.local = local

    async def send(self, request, **kwargs):
        if not self.local:
            failed = False
            try:
                payload = json.loads(await request.aread())
                scrubbed = OutboundScrubber(self.settings).scrub(payload)
            except (ValueError, RecursionError):
                failed = True
            # Decoder exceptions retain the original document. Raise outside the handler
            # so even the static error's exception context cannot expose that document.
            if failed:
                raise ValueError("cloud payload could not be scrubbed")
            headers = request.headers.copy()
            headers.pop("content-length", None)
            headers.pop("transfer-encoding", None)
            request = httpx.Request(
                request.method, request.url, headers=headers, json=scrubbed,
                extensions=request.extensions,
            )
            if len(request.content) + 512 > self.settings.ai_gateway.max_prompt_tokens:
                raise OutboundDenied("scrubbed prompt exceeds the conservative token ceiling")
        return await super().send(request, **kwargs)

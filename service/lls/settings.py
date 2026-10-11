"""Fail-closed configuration for local and explicitly enabled development hosts."""

import ipaddress
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .embeddings import EmbeddingConfig
from .gateway_config import GatewayConfig
from .memory_consolidation import MemoryConsolidationConfig
from .neighborhood_scheduler import NeighborhoodSchedulerConfig
from .protocol import StrictModel
from .skill_scheduler import SkillSchedulerConfig
from .social_scheduler import SocialSchedulerConfig
from .speech import device_selector
from .speech_coordinator import SpeechConfig


class Settings(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    release: bool = True
    dev_lan: bool = False
    dev_perception: bool = False
    dev_social: bool = False
    dev_social_autonomy: bool = False
    dev_skills: bool = False
    dev_skill_autonomy: bool = False
    skill_scheduler: SkillSchedulerConfig = Field(default_factory=SkillSchedulerConfig)
    dev_neighborhood: bool = False
    dev_neighborhood_autonomy: bool = False
    neighborhood_pack_path: str = ""
    neighborhood_scheduler: NeighborhoodSchedulerConfig = Field(
        default_factory=NeighborhoodSchedulerConfig
    )
    social_scheduler: SocialSchedulerConfig = Field(default_factory=SocialSchedulerConfig)
    allow_sources: list[str] = Field(default_factory=list)
    bootstrap_token: str = Field(default="", repr=False)
    database_path: str = "data/runtime/lls.sqlite3"
    provider: Literal["disabled", "fake", "openai", "local_openai"] = "disabled"
    cloud_opt_in: bool = False
    openai_base_url: str = "https://api.openai.com/v1"
    local_base_url: str = "http://127.0.0.1:1234/v1"
    model_timeout_seconds: float = Field(default=30, ge=1, le=120)
    model_response_bytes: int = Field(default=262144, ge=1024, le=1048576)
    context_byte_budget: int = Field(default=12000, ge=2048, le=48000)
    memory_token_budget: int = Field(default=512, ge=64, le=2048)
    memory_consolidation: MemoryConsolidationConfig = Field(
        default_factory=MemoryConsolidationConfig
    )
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    knowledge_token_budget: int = Field(default=512, ge=64, le=2048)
    dialogue_model: str = ""
    action_model: str = ""
    max_commands: int = Field(default=256, ge=8, le=2048)
    session_timeout_seconds: float = Field(default=30, ge=5, le=600)
    max_sessions: int = Field(default=8, ge=1, le=64)
    max_actors: int = Field(default=36, ge=1, le=128)
    max_turns_per_minute: int = Field(default=12, ge=1, le=60)
    max_reply_chars: int = Field(default=1200, ge=100, le=4000)
    speech: SpeechConfig = Field(default_factory=SpeechConfig)
    ai_gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    @field_validator("speech", mode="before")
    @classmethod
    def speech_configuration(cls, value):
        if isinstance(value, dict):
            try:
                return SpeechConfig(**value)
            except TypeError as exc:
                raise ValueError("invalid speech configuration fields") from exc
        return value

    @model_validator(mode="after")
    def validate_network(self):
        address = ipaddress.ip_address(self.host)
        if address.is_unspecified or address.is_multicast:
            raise ValueError("bind requires an explicit unicast interface address")
        if self.dev_lan and not self.bootstrap_token:
            raise ValueError("dev-LAN requires a bootstrap token")
        if self.dev_lan and self.release:
            raise ValueError("release builds refuse dev-LAN")
        if self.dev_perception and self.release:
            raise ValueError("release builds refuse development perception")
        if self.dev_social and self.release:
            raise ValueError("release builds refuse development social scenes")
        if self.dev_skills and self.release:
            raise ValueError("release builds refuse development skills")
        if self.dev_skill_autonomy and (self.release or not self.dev_skills):
            raise ValueError("skill autonomy requires development skills and a non-release build")
        if self.dev_neighborhood and self.release:
            raise ValueError("release builds refuse development neighborhood simulation")
        if self.dev_neighborhood_autonomy and (self.release or not self.dev_neighborhood):
            raise ValueError(
                "neighborhood autonomy requires development simulation and a non-release build"
            )
        if self.dev_social_autonomy and (self.release or not self.dev_social):
            raise ValueError(
                "social autonomy requires development social scenes and a non-release build"
            )
        if not address.is_loopback and not (
            self.dev_lan
            and (address.is_private or address in ipaddress.ip_network("100.64.0.0/10"))
            and self.allow_sources
        ):
            raise ValueError("non-loopback bind requires development LAN and source allowlist")
        for source in self.allow_sources:
            ipaddress.ip_address(source)
        if self.provider == "openai" and not self.dialogue_model.strip():
            raise ValueError("remote provider requires explicit model configuration")
        if self.provider in {"openai", "local_openai"}:
            endpoint = urlsplit(
                self.local_base_url if self.provider == "local_openai" else self.openai_base_url
            )
            if (
                endpoint.username
                or endpoint.password
                or endpoint.query
                or endpoint.fragment
                or not endpoint.hostname
            ):
                raise ValueError("model endpoint must not contain credentials, query, or fragment")
            if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
                raise ValueError("invalid model endpoint port")
            if self.provider == "local_openai":
                try:
                    local_address = ipaddress.ip_address(endpoint.hostname)
                except ValueError as exc:
                    raise ValueError("local model endpoint requires a literal loopback IP") from exc
                if not local_address.is_loopback or endpoint.scheme not in {"http", "https"}:
                    raise ValueError(
                        "local model endpoint requires a literal loopback HTTP address"
                    )
                if not self.dialogue_model.strip():
                    raise ValueError("local provider requires an explicit model")
            elif endpoint.scheme != "https":
                raise ValueError("remote model endpoint requires HTTPS")
        return self

    @classmethod
    def from_env(cls):
        database = cls.env_path("LLS_DATABASE")
        if not database:
            # Resolve the profile only when needed: on Windows, Path.home() raises if the
            # environment lacks USERPROFILE and HOMEPATH.
            if os.name == "nt":
                base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LivingLosSantos"
            else:
                base = Path.home() / "Library" / "Application Support" / "LivingLosSantos"
            database = str(base / "lls.sqlite3")
        return cls(
            bootstrap_token=os.environ.get("LLS_BRIDGE_TOKEN", ""),
            host=os.environ.get("LLS_HOST", "127.0.0.1"),
            port=int(os.environ.get("LLS_PORT", "8765")),
            release=os.environ.get("LLS_RELEASE", "1") == "1",
            dev_lan=os.environ.get("LLS_DEV_LAN", "0") == "1",
            dev_perception=os.environ.get("LLS_DEV_PERCEPTION", "0") == "1",
            dev_social=os.environ.get("LLS_DEV_SOCIAL", "0") == "1",
            dev_social_autonomy=os.environ.get("LLS_DEV_SOCIAL_AUTONOMY", "0") == "1",
            dev_skills=os.environ.get("LLS_DEV_SKILLS", "0") == "1",
            dev_skill_autonomy=os.environ.get("LLS_DEV_SKILL_AUTONOMY", "0") == "1",
            dev_neighborhood=os.environ.get("LLS_DEV_NEIGHBORHOOD", "0") == "1",
            dev_neighborhood_autonomy=(
                os.environ.get("LLS_DEV_NEIGHBORHOOD_AUTONOMY", "0") == "1"
            ),
            neighborhood_pack_path=cls.env_path("LLS_NEIGHBORHOOD_PACK") or "",
            allow_sources=[x for x in os.environ.get("LLS_ALLOW_SOURCES", "").split(",") if x],
            database_path=database,
            provider=os.environ.get("LLS_PROVIDER", "disabled"),
            cloud_opt_in=os.environ.get("LLS_CLOUD_OPT_IN", "0") == "1",
            dialogue_model=os.environ.get("LLM_DIALOGUE_MODEL", ""),
            action_model=os.environ.get("LLM_ACTION_MODEL", ""),
            local_base_url=os.environ.get("LLS_LOCAL_BASE_URL", "http://127.0.0.1:1234/v1"),
            openai_base_url=os.environ.get("LLS_OPENAI_BASE_URL", "https://api.openai.com/v1"),
            memory_consolidation=MemoryConsolidationConfig(
                enabled=os.environ.get("LLS_MEMORY_SUMMARIES", "0") == "1"
            ),
            embedding=EmbeddingConfig(
                provider=os.environ.get("LLS_EMBEDDING_PROVIDER", "disabled"),
                model=os.environ.get("LLM_EMBEDDING_MODEL", ""),
                base_url=os.environ.get("LLS_EMBEDDING_BASE_URL", ""),
                dimensions=(int(os.environ["LLS_EMBEDDING_DIMENSIONS"])
                            if os.environ.get("LLS_EMBEDDING_DIMENSIONS") else None),
            ),
            speech=SpeechConfig(
                enabled=os.environ.get("LLS_SPEECH_ENABLED", "0") == "1",
                whisper_executable=cls.env_path("LLS_WHISPER_EXECUTABLE") or "",
                whisper_model=cls.env_path("LLS_WHISPER_MODEL") or "",
                tts_provider=os.environ.get("LLS_TTS_PROVIDER", "disabled"),
                piper_executable=cls.env_path("LLS_PIPER_EXECUTABLE") or "",
                piper_model=cls.env_path("LLS_PIPER_MODEL") or "",
                input_device=cls.audio_device(os.environ.get("LLS_INPUT_DEVICE", "")),
                output_device=cls.audio_device(os.environ.get("LLS_OUTPUT_DEVICE", "")),
                minimum_action_confidence=float(
                    os.environ.get("LLS_STT_ACTION_CONFIDENCE", "0.85")
                ),
                minimum_dialogue_confidence=float(
                    os.environ.get("LLS_STT_DIALOGUE_CONFIDENCE", "0.3")
                ),
                silence_dbfs=float(os.environ.get("LLS_STT_SILENCE_DBFS", "-60")),
            ),
        )

    @staticmethod
    def env_path(name):
        """Expand ~ and %VAR%/$VAR. A relative result would follow the working directory."""
        value = os.environ.get(name)
        if not value or name == "LLS_DATABASE" and value == ":memory:":
            return value
        path = os.path.expandvars(os.path.expanduser(value))
        if not (PureWindowsPath if os.name == "nt" else PurePosixPath)(path).is_absolute():
            raise ValueError(f"{name} must be absolute after ~ and variable expansion: {path!r}")
        return path

    @staticmethod
    def audio_device(value):
        return device_selector(value)

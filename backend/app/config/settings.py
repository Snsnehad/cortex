"""Typed application settings.

Every value comes from environment variables prefixed ``CORTEX_``. Nested groups use a
double underscore, e.g. ``CORTEX_DATABASE__PASSWORD``. In local development a ``.env``
file at the repository root is read as well; real environment variables always win.

Safety rules for ``staging`` and ``production`` live in one place
(:meth:`Settings._enforce_deployed_environment_rules`) so a misconfigured deployment fails
at startup instead of running with development defaults.
"""

import os
from collections.abc import Iterator
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"
ENV_PREFIX = "CORTEX_"
ENV_NESTED_DELIMITER = "__"

# Development-only defaults. Staging/production refuse to start while these are in use.
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"  # noqa: S105
DEV_DATABASE_PASSWORD = "cortex"  # noqa: S105
MIN_JWT_SECRET_LENGTH = 32


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_deployed(self) -> bool:
        """True for environments reachable by real users (strict safety rules apply)."""
        return self in {Environment.STAGING, Environment.PRODUCTION}


class DatabaseSettings(BaseModel):
    # 127.0.0.1, not "localhost": on Windows async clients try IPv6 ::1 first and lose ~2 s
    # per connection before falling back to IPv4 (compose binds IPv4 only).
    host: str = "127.0.0.1"
    port: int = Field(default=15432, ge=1, le=65535)  # Cortex compose host port
    user: str = "cortex"
    password: SecretStr = SecretStr(DEV_DATABASE_PASSWORD)
    name: str = "cortex"
    pool_size: int = Field(default=10, ge=1, le=100)
    max_overflow: int = Field(default=10, ge=0, le=100)
    echo_sql: bool = False

    @property
    def url(self) -> str:
        """SQLAlchemy URL for psycopg 3 (works for both the async and sync engines)."""
        user = quote(self.user, safe="")
        password = quote(self.password.get_secret_value(), safe="")
        return f"postgresql+psycopg://{user}:{password}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseModel):
    url: SecretStr = SecretStr("redis://127.0.0.1:16379/0")  # Cortex compose host port


class QdrantSettings(BaseModel):
    url: str = "http://127.0.0.1:16333"  # Cortex compose host port
    api_key: SecretStr | None = None
    collection_prefix: str = Field(default="cortex_", pattern=r"^[a-z][a-z0-9_]*$")
    timeout_seconds: float = Field(default=10.0, gt=0)


class StorageSettings(BaseModel):
    backend: Literal["local", "s3"] = "local"
    local_root: Path = REPO_ROOT / ".data" / "storage"
    s3_bucket: str | None = None
    s3_region: str | None = None
    # Only for S3-compatible services in testing (e.g. MinIO); empty for real AWS.
    s3_endpoint_url: str | None = None
    # Server-side encryption for every object: S3-managed keys, or KMS (optionally a specific
    # key; without one, the account's AWS-managed key for S3 is used).
    s3_sse: Literal["AES256", "aws:kms"] = "AES256"
    s3_kms_key_id: str | None = None
    # Lifetime of direct download links (S3 presigned GET).
    presigned_url_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def _s3_requires_bucket(self) -> Self:
        if self.backend == "s3" and not self.s3_bucket:
            raise ValueError("storage.s3_bucket is required when storage.backend is 's3'")
        if self.s3_kms_key_id and self.s3_sse != "aws:kms":
            raise ValueError("storage.s3_kms_key_id requires storage.s3_sse = 'aws:kms'")
        return self


class AuthSettings(BaseModel):
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_ttl_seconds: int = Field(default=15 * 60, ge=60, le=60 * 60)
    refresh_token_ttl_days: int = Field(default=14, ge=1, le=90)
    # Browsers drop Secure cookies on plain http://localhost, so this is off in development.
    refresh_cookie_secure: bool = False


class EmbeddingSettings(BaseModel):
    provider: Literal["fastembed", "openai_compatible"] = "fastembed"
    model: str = "BAAI/bge-small-en-v1.5"
    dimensions: int = Field(default=384, gt=0)
    profile_version: int = Field(default=1, ge=1)
    batch_size: int = Field(default=32, ge=1, le=2048)
    # Only used by the openai_compatible provider.
    base_url: str | None = None
    api_key: SecretStr | None = None

    @property
    def profile(self) -> str:
        """Name of the current embedding profile, e.g. ``bge-small-en-v1.5__384__v1``.

        Stored on every knowledge base when it is created and fixed afterwards: vectors made
        with different models or dimensions must never be mixed (ADR-008).
        """
        model = self.model.rsplit("/", 1)[-1].lower()
        return f"{model}__{self.dimensions}__v{self.profile_version}"


class LLMSettings(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr | None = None
    model: str = "gpt-4o-mini"
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_output_tokens: int = Field(default=1024, ge=1)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class RAGSettings(BaseModel):
    chunk_size_tokens: int = Field(default=512, ge=64, le=2048)
    chunk_overlap_tokens: int = Field(default=64, ge=0)
    top_k_dense: int = Field(default=40, ge=1, le=200)
    top_k_sparse: int = Field(default=40, ge=1, le=200)
    fused_k: int = Field(default=30, ge=1, le=200)
    rerank_enabled: bool = True
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_top_n: int = Field(default=30, ge=1, le=200)
    final_k: int = Field(default=6, ge=1, le=50)
    dense_weight: float = Field(default=1.0, ge=0.0)
    sparse_weight: float = Field(default=1.0, ge=0.0)
    rrf_k: int = Field(default=60, ge=1)
    context_max_tokens: int = Field(default=6000, ge=500)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("rag.chunk_overlap_tokens must be smaller than chunk_size_tokens")
        if self.dense_weight == 0 and self.sparse_weight == 0:
            raise ValueError("rag.dense_weight and rag.sparse_weight cannot both be 0")
        if self.final_k > self.fused_k:
            raise ValueError("rag.final_k cannot exceed rag.fused_k")
        return self


class RateLimitSettings(BaseModel):
    # Staging/production refuse to start with rate limiting disabled.
    enabled: bool = True
    # Redis key namespace; tests use a unique prefix per test for isolation.
    key_prefix: str = Field(default="cortex:rl", pattern=r"^[a-z0-9:_-]+$")
    login_per_ip_per_minute: int = Field(default=10, ge=1)
    login_per_email_per_minute: int = Field(default=5, ge=1)
    register_per_ip_per_hour: int = Field(default=5, ge=1)
    upload_per_user_per_hour: int = Field(default=30, ge=1)
    # How long a rate-limit check may wait for Redis before giving up.
    redis_timeout_seconds: float = Field(default=0.5, gt=0, le=5)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter=ENV_NESTED_DELIMITER,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="forbid",  # unknown CORTEX_ keys in the .env file are errors
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # json: one JSON object per line (machines, deployed envs). console: coloured text (local).
    log_format: Literal["json", "console"] = "json"
    # JSON list in env vars, e.g. CORTEX_CORS_ORIGINS=["https://app.example.com"]
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_environment_variables(cls, data: Any) -> Any:
        # pydantic-settings only reads variables matching known fields, so a typo such as
        # CORTEX_DATABSE__PASSWORD would otherwise be ignored and the default used silently.
        known = set(iter_env_var_names())
        unknown = sorted(
            name
            for name in (key.upper() for key in os.environ)
            if name.startswith(ENV_PREFIX) and name not in known
        )
        if unknown:
            raise ValueError(f"unknown environment variables: {', '.join(unknown)}")
        return data

    @model_validator(mode="after")
    def _enforce_deployed_environment_rules(self) -> Self:
        if not self.environment.is_deployed:
            return self

        problems: list[str] = []
        jwt_secret = self.auth.jwt_secret.get_secret_value()
        if jwt_secret == DEV_JWT_SECRET or len(jwt_secret) < MIN_JWT_SECRET_LENGTH:
            problems.append(
                f"auth.jwt_secret must be a unique secret of at least "
                f"{MIN_JWT_SECRET_LENGTH} characters"
            )
        if self.database.password.get_secret_value() == DEV_DATABASE_PASSWORD:
            problems.append("database.password must not be the development default")
        if self.debug:
            problems.append("debug must be false")
        if not self.cors_origins or any(
            origin == "*" or not origin.startswith("https://") for origin in self.cors_origins
        ):
            problems.append("cors_origins must be explicit https:// origins (no '*')")
        if self.storage.backend != "s3":
            problems.append("storage.backend must be 's3'")
        if not self.auth.refresh_cookie_secure:
            problems.append("auth.refresh_cookie_secure must be true")
        if not self.rate_limit.enabled:
            problems.append("rate_limit.enabled must be true")

        if problems:
            raise ValueError(
                f"unsafe configuration for environment '{self.environment}': " + "; ".join(problems)
            )
        return self


def iter_env_var_names() -> Iterator[str]:
    """Every environment variable name Settings understands, e.g. CORTEX_DATABASE__HOST."""
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for sub_name in annotation.model_fields:
                yield f"{ENV_PREFIX}{name}{ENV_NESTED_DELIMITER}{sub_name}".upper()
        else:
            yield f"{ENV_PREFIX}{name}".upper()


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, loaded once. Use as a FastAPI dependency or call directly."""
    return Settings()

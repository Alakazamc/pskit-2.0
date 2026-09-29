from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PSKit 2.0"
    app_version: str = "0.3.0"
    app_env: str = "development"
    bind_host: str = "127.0.0.1"
    bind_port: int = 10716
    allow_legacy_port_10706: bool = Field(
        default=False,
        alias="PSKIT_ALLOW_LEGACY_PORT_10706",
    )

    database_url: str = "sqlite:///./data/pskit2.sqlite3"
    redis_url: str = "redis://127.0.0.1:6379/0"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    qdrant_path: Path | None = None
    qdrant_collection: str = "pskit_knowledge"
    qdrant_vector_size: int | None = None
    qdrant_distance: str = "cosine"
    qdrant_rebuild_keep_collections: int = Field(default=2, ge=1, le=10)
    readiness_require_qdrant: bool = False
    registration_mode: str = "open"
    trusted_proxy_ips: str = ""
    auth_login_limit: int = Field(default=30, ge=1, le=10000)
    auth_register_limit: int = Field(default=5, ge=1, le=10000)
    auth_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    max_active_tasks_per_user: int = Field(
        default=4,
        ge=1,
        le=100,
        alias="PSKIT_MAX_ACTIVE_TASKS_PER_USER",
    )
    max_active_af3_per_user: int = Field(
        default=10,
        ge=1,
        le=100,
        alias="PSKIT_MAX_ACTIVE_AF3_PER_USER",
    )
    max_global_active_tasks: int = Field(
        default=100,
        ge=1,
        le=10000,
        alias="PSKIT_MAX_GLOBAL_ACTIVE_TASKS",
    )
    max_global_active_af3: int = Field(
        default=20,
        ge=1,
        le=1000,
        alias="PSKIT_MAX_GLOBAL_ACTIVE_AF3",
    )

    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model_id: str = "deepseek-v4-flash"
    llm_api_key: str | None = None

    embedding_base_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        validation_alias=AliasChoices("EMBEDDING_BASE_URL", "EMBED_BASE_URL"),
    )
    embedding_model: str = Field(
        default="BAAI/bge-m3",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "EMBED_MODEL_NAME"),
    )
    embedding_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "EMBED_API_KEY"),
    )

    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_api_key: str | None = None

    serpapi_api_key: str | None = None
    remote_rna_expert_sse_url: str = ""
    rcsb_search_url: str = "https://search.rcsb.org/rcsbsearch/v2/query"
    rcsb_data_base: str = "https://data.rcsb.org/rest/v1/core/entry"
    rcsb_files_base: str = "https://files.rcsb.org/download"
    uniprot_search_url: str = "https://rest.uniprot.org/uniprotkb/search"
    uniprot_entry_base: str = "https://rest.uniprot.org/uniprotkb"
    rnacentral_search_url: str = "https://rnacentral.org/api/v1/rna"
    rnacentral_entry_base: str = "https://rnacentral.org/api/v1/rna"
    serpapi_search_url: str = "https://serpapi.com/search.json"

    data_dir: Path = Path("./data")
    artifact_dir: Path = Path("./data/artifacts")
    knowledge_dir: Path = Path("../knowledge")
    frontend_dist_dir: Path = Path("../frontend/dist")

    session_cookie_name: str = "pskit_session"
    session_ttl_days: int = 14
    cookie_secure: bool = False
    initial_admin_bootstrap_token: str | None = Field(
        default=None,
        alias="INITIAL_ADMIN_BOOTSTRAP_TOKEN",
    )

    pskit_model_parameters: Path | None = Field(default=None, alias="PSKIT_MODEL_PARAMETERS")
    pskit_foldseek: Path | None = Field(default=None, alias="PSKIT_FOLDSEEK")
    pskit_foldseek_db: Path | None = Field(default=None, alias="PSKIT_FOLDSEEK_DB")
    pskit_dssp: Path | None = Field(default=None, alias="PSKIT_DSSP")
    pskit_sequence_search_backend: str = Field(default="mmseqs", alias="PSKIT_SEQUENCE_SEARCH_BACKEND")
    pskit_sequence_search_binary: Path | None = Field(default=None, alias="PSKIT_SEQUENCE_SEARCH_BINARY")
    pskit_sequence_search_db: Path | None = Field(default=None, alias="PSKIT_SEQUENCE_SEARCH_DB")
    pepccd_mcp_url: str = Field(
        default="",
        alias="PEPCCD_MCP_URL",
    )
    pepccd_mcp_tool_name: str | None = Field(
        default="pepccd_generate_peptides",
        alias="PEPCCD_MCP_TOOL_NAME",
    )
    pskit_af3_db_dir: Path | None = Field(default=None, alias="PSKIT_AF3_DB_DIR")
    pskit_af3_model_dir: Path | None = Field(default=None, alias="PSKIT_AF3_MODEL_DIR")
    pskit_af3_image: str = Field(default="alphafold3:3.0.1", alias="PSKIT_AF3_IMAGE")
    pskit_af3_gpu_device: str = Field(default="0", alias="PSKIT_AF3_GPU_DEVICE")
    pskit_af3_allowed_gpu_devices: str = Field(
        default="0",
        alias="PSKIT_AF3_ALLOWED_GPU_DEVICES",
    )
    pskit_legacy_root: Path | None = Field(default=None, alias="PSKIT_LEGACY_ROOT")
    pskit_legacy_python: Path | None = Field(
        default=None,
        alias="PSKIT_LEGACY_PYTHON",
    )
    task_subprocess_timeout_seconds: int = Field(default=1800, ge=10, le=86400)
    pskit_af3_timeout_seconds: int = Field(
        # Full protein/RNA database searches can exceed one hour on a shared
        # A6000 host even when the task is healthy and making progress.
        default=7200,
        ge=60,
        le=86400,
        alias="PSKIT_AF3_TIMEOUT_SECONDS",
    )
    task_mcp_timeout_seconds: int = Field(default=900, ge=10, le=7200)
    task_stale_after_seconds: int = Field(default=14400, ge=60, le=172800)
    task_heartbeat_seconds: int = Field(default=10, ge=5, le=300)
    task_max_attempts: int = Field(default=2, ge=1, le=10)
    worker_readiness_required: bool = True
    worker_readiness_max_age_seconds: int = Field(default=20, ge=5, le=300)
    science_readiness_required: bool = False
    science_readiness_max_probe_age_seconds: int = Field(
        default=90,
        ge=60,
        le=600,
    )
    llm_request_timeout_seconds: int = Field(default=90, ge=10, le=600)
    llm_stream_read_timeout_seconds: int = Field(default=120, ge=10, le=600)
    agent_max_steps: int = Field(default=8, ge=1, le=30)
    agent_max_tool_calls: int = Field(default=24, ge=1, le=100)
    agent_max_tool_result_chars: int = Field(default=8000, ge=1000, le=100000)
    agent_max_concurrent_runs: int = Field(default=4, ge=1, le=32)
    agent_sse_heartbeat_seconds: float = Field(default=15, ge=1, le=60)
    agent_turn_stale_seconds: int = Field(default=900, ge=60, le=7200)
    agent_history_recent_messages: int = Field(default=12, ge=4, le=50)
    agent_history_scan_messages: int = Field(default=60, ge=12, le=500)
    agent_history_summary_chars: int = Field(default=6000, ge=500, le=30000)

    @model_validator(mode="after")
    def reject_legacy_port_for_pskit2(self) -> "Settings":
        # wzf：10706 属于旧版演示实例；2.0 只有显式灾备兼容时才允许绑定该端口。
        if self.bind_port == 10706 and not self.allow_legacy_port_10706:
            raise ValueError(
                "BIND_PORT=10706 is reserved for the legacy deployment; "
                "use 10716 (deployment) or an isolated test port"
            )
        if self.task_stale_after_seconds <= max(
            self.task_subprocess_timeout_seconds,
            self.task_mcp_timeout_seconds,
            self.pskit_af3_timeout_seconds,
        ):
            raise ValueError(
                "TASK_STALE_AFTER_SECONDS must exceed subprocess and MCP timeouts"
            )
        allowed_gpu_devices = {
            item.strip()
            for item in self.pskit_af3_allowed_gpu_devices.split(",")
            if item.strip()
        }
        if self.pskit_af3_gpu_device not in allowed_gpu_devices:
            raise ValueError(
                "PSKIT_AF3_GPU_DEVICE must be listed in "
                "PSKIT_AF3_ALLOWED_GPU_DEVICES"
            )
        if self.registration_mode not in {"open", "disabled"}:
            raise ValueError("REGISTRATION_MODE must be 'open' or 'disabled'")
        # wzf：readiness 至少容忍一次心跳抖动，避免 Worker 正常运行时被瞬时误判为离线。
        if (
            self.worker_readiness_required
            and self.worker_readiness_max_age_seconds
            < self.task_heartbeat_seconds * 2
        ):
            raise ValueError(
                "WORKER_READINESS_MAX_AGE_SECONDS must be at least twice "
                "TASK_HEARTBEAT_SECONDS"
            )
        bootstrap_token = self.initial_admin_bootstrap_token or ""
        if self.app_env.lower() in {"production", "release"} and (
            len(bootstrap_token) < 24
            or bootstrap_token.lower().startswith(
                ("changeme", "placeholder", "replace", "your-")
            )
        ):
            raise ValueError(
                "INITIAL_ADMIN_BOOTSTRAP_TOKEN must contain at least 24 "
                "characters in production/release"
            )
        return self

    @property
    def chat_completions_url(self) -> str:
        return f"{self.llm_base_url.rstrip('/')}/chat/completions"

    @property
    def embeddings_url(self) -> str:
        return f"{self.embedding_base_url.rstrip('/')}/embeddings"

    @property
    def allowed_af3_gpu_devices(self) -> set[str]:
        return {
            item.strip()
            for item in self.pskit_af3_allowed_gpu_devices.split(",")
            if item.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

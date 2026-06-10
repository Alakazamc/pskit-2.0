from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PSKit 2.0"
    app_env: str = "development"
    bind_host: str = "127.0.0.1"
    bind_port: int = 10706

    database_url: str = "sqlite:///./data/pskit2.sqlite3"
    redis_url: str = "redis://127.0.0.1:6379/0"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    qdrant_path: Path | None = None
    qdrant_collection: str = "pskit_knowledge"
    qdrant_vector_size: int | None = None
    qdrant_distance: str = "cosine"

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
    remote_rna_expert_sse_url: str = "http://172.31.226.126:8099/sse"
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

    pskit_model_parameters: Path | None = Field(default=None, alias="PSKIT_MODEL_PARAMETERS")
    pskit_foldseek: Path | None = Field(default=None, alias="PSKIT_FOLDSEEK")
    pskit_dssp: Path | None = Field(default=None, alias="PSKIT_DSSP")
    pskit_af3_db_dir: Path | None = Field(default=None, alias="PSKIT_AF3_DB_DIR")
    pskit_af3_model_dir: Path | None = Field(default=None, alias="PSKIT_AF3_MODEL_DIR")
    pskit_af3_image: str = Field(default="alphafold3:3.0.1", alias="PSKIT_AF3_IMAGE")
    pskit_af3_gpu_device: str = Field(default="0", alias="PSKIT_AF3_GPU_DEVICE")
    pskit_legacy_root: Path | None = Field(default=Path("/data1/kxchen/pskit"), alias="PSKIT_LEGACY_ROOT")
    task_subprocess_timeout_seconds: int = 1800

    @property
    def chat_completions_url(self) -> str:
        return f"{self.llm_base_url.rstrip('/')}/chat/completions"

    @property
    def embeddings_url(self) -> str:
        return f"{self.embedding_base_url.rstrip('/')}/embeddings"


@lru_cache
def get_settings() -> Settings:
    return Settings()

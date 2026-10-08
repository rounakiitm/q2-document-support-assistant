from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: str = ""
    gemini_api_keys: str = ""  # comma-separated pool; first available is used
    model_name: str = "gemini-3.8-flash"
    embedding_model: str = "gemini-embedding-001"

    @property
    def api_keys(self) -> list[str]:
        pool = [k.strip() for k in self.gemini_api_keys.split(",") if k.strip()]
        if pool:
            return pool
        return [self.gemini_api_key] if self.gemini_api_key else []

    @property
    def effective_api_key(self) -> str:
        keys = self.api_keys
        return keys[0] if keys else ""

    agent_max_iterations: int = 20
    citation_repair_attempts: int = 2
    retrieval_k: int = 6
    # cosine similarity below which the retrieval tool declares "not in KB"
    kb_score_threshold: float = 0.35
    # questions whose embedding similarity to a prior question exceeds this
    # are flagged as likely repeats by the memory layer
    repeat_similarity_threshold: float = 0.82
    # compact history into a rolling summary when the session exceeds this many turns
    summary_after_turns: int = 14

    db_path: Path = ROOT / "data" / "agent.db"
    chroma_dir: Path = ROOT / "data" / "chroma"
    uploads_dir: Path = ROOT / "data" / "uploads"
    seed_docs_dir: Path = ROOT / "data" / "seed_docs"

    log_level: str = "INFO"
    max_upload_mb: int = 15
    allowed_upload_exts: str = ".pdf,.docx,.md"

    @property
    def upload_exts(self) -> set[str]:
        return {
            e.strip().lower() if e.strip().startswith(".") else f".{e.strip().lower()}"
            for e in self.allowed_upload_exts.split(",")
            if e.strip()
        }

    def ensure_dirs(self) -> None:
        for d in (self.uploads_dir, self.chroma_dir, self.seed_docs_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
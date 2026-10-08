"""Configuration lue depuis les variables d'environnement (ou un fichier .env)."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Ordre de grandeur pour une question type (~15 000 tokens de sources, ~800 de réponse), aux tarifs API d'octobre 2026.
DEFAULT_CHAT_MODELS = [
    {"id": "claude-haiku-5-5", "label": "Haiku 5.5", "note": "le plus rapide, ≈ 0,3 ct la question"},
    {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5", "note": "équilibré, ≈ 4 ct la question"},
    {"id": "claude-opus-5-5", "label": "Opus 5.5", "note": "plus fin sur les questions complexes, ≈ 8 ct"},
    {"id": "claude-fable-5-1", "label": "Fable 5.1", "note": "raisonnement le plus poussé, ≈ 20 ct"},
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # --- Base de données (Supabase : « Session pooler », port 5432) ---
    database_url: str = "postgresql://postgres:postgres@localhost:5432/postgres"

    # --- Sécurité ---
    kb_api_token: str = ""          # Bearer utilisé par le Raccourci et l'app
    kb_mcp_secret: str = ""         # segment secret de l'URL du connecteur MCP
    public_base_url: str = ""       # ex. https://kb.example.com (liens vers les fiches)
    cors_origins: str = ""          # origines autorisées si le front est hébergé ailleurs

    # --- Claude ---
    anthropic_api_key: str = ""
    anthropic_admin_key: str = ""     # optional: Console Admin API key (sk-ant-admin01-…), to sync costs
    enrich_model: str = "claude-haiku-5-5"
    chat_model: str = "claude-sonnet-5-5"   # modèle par défaut du chat de l'app
    chat_models: str = ""           # choix proposés dans l'app : "id:Libellé,id:Libellé" (vide = liste par défaut)
    kb_language: str = "fr"         # langue principale des résumés et des réponses (les tags sont en anglais)
    kb_second_language: str = ""    # résumés écrits aussi dans cette langue ; vide = en (ou fr si la principale est en),
                                    # "none" pour désactiver

    # --- Embeddings ---
    embeddings_provider: str = "voyage"   # "voyage" ou "fake" (tests)
    voyage_api_key: str = ""
    embed_model: str = "voyage-4"
    embed_dim: int = 1024                  # doit correspondre à vector(1024) dans le schéma

    # --- X (Twitter) ---
    x_bearer_token: str = ""
    x_article_fallback_fxtwitter: bool = True  # secours pour les Articles X si l'API ne renvoie pas le texte

    # --- Transcription (API compatible OpenAI : Groq, OpenAI…) ---
    transcription_api_key: str = ""
    transcription_base_url: str = "https://api.groq.com/openai/v1"
    transcription_model: str = "whisper-large-v3-turbo"
    max_media_minutes: int = 180

    # --- Stockage de fichiers (Supabase Storage ; sinon disque local) ---
    supabase_url: str = ""
    supabase_service_key: str = ""
    storage_bucket: str = "kb-files"
    local_storage_dir: str = "./data/files"
    max_upload_mb: int = 50

    @field_validator("supabase_url")
    @classmethod
    def _project_url(cls, v: str) -> str:
        """Only the project URL: a pasted ".../rest/v1/" would send files to PostgREST (404 PGRST125)."""
        v = (v or "").strip().rstrip("/")
        for suffix in ("/rest/v1", "/storage/v1"):
            if v.endswith(suffix):
                v = v[: -len(suffix)]
        return v

    @property
    def second_language(self) -> str | None:
        v = self.kb_second_language.strip().lower()
        if v == "none":
            return None
        v = v or ("fr" if self.kb_language == "en" else "en")
        return None if v == self.kb_language else v

    # --- Réseau ---
    youtube_proxy_url: str = ""   # proxy résidentiel si YouTube bloque l'IP du serveur
    github_token: str = ""
    jina_api_key: str = ""

    # --- Copie Notion (facultative) ---
    notion_token: str = ""          # API token of a Notion connection (Developer tools > Connections, ntn_…)
    notion_parent_page_id: str = "" # page (or its URL) the connection can access; the database is created there
    notion_spaces: str = "main,perso"  # espaces copiés : "perso" pour ne copier que l'espace Perso

    # --- Daily / weekly tech digest agent (optional) ---
    digest_enabled: bool = False
    digest_hour: int = 7                          # local hour when the daily digest is written
    digest_timezone: str = "Europe/Paris"
    digest_model: str = "claude-sonnet-5-5"       # writes the daily digest
    digest_weekly_model: str = "claude-opus-5-5"  # writes the weekly digest and the project ideas
    digest_x_max_posts: int = 50                  # X posts read per day from followed people (0 = off)
    smtp_host: str = ""                           # optional: e-mail each digest
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    digest_email_to: str = ""
    digest_email_from: str = ""

    # --- Worker ---
    run_worker: bool = True       # traite la file dans le même process que l'API
    auto_migrate: bool = True     # apply supabase/migrations at startup (idempotent)
    worker_concurrency: int = 2
    max_attempts: int = 3
    link_min_similarity: float = 0.45
    max_links_per_item: int = 5

    @property
    def chat_model_options(self) -> list[dict]:
        """Modèles sélectionnables dans le chat de l'app (le modèle par défaut est toujours inclus)."""
        if self.chat_models.strip():
            options = []
            for part in self.chat_models.split(","):
                model_id, _, label = part.strip().partition(":")
                if model_id:
                    options.append({"id": model_id.strip(), "label": label.strip() or model_id.strip(), "note": ""})
        else:
            options = [dict(o) for o in DEFAULT_CHAT_MODELS]
        if not any(o["id"] == self.chat_model for o in options):
            options.insert(0, {"id": self.chat_model, "label": self.chat_model, "note": ""})
        for o in options:
            o["default"] = o["id"] == self.chat_model
        return options

    @property
    def notion_space_list(self) -> list[str]:
        spaces = [s.strip().lower() for s in self.notion_spaces.split(",") if s.strip()]
        return [{"veille": "main"}.get(s, s) for s in spaces] or ["main", "perso"]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def item_url(self, item_id: str) -> str:
        base = self.public_base_url.rstrip("/")
        return f"{base}/item/{item_id}" if base else f"/item/{item_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()

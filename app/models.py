from dataclasses import dataclass, field

PUBLIC_LISTS = {"trending", "popular", "watched"}

SOURCE_TYPES = PUBLIC_LISTS | {"watchlist", "user_list"}


class ConfigError(Exception):
    """Raised when configuration validation fails."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(", ".join(errors))


@dataclass
class MedusaAddOptions:
    quality: str | list[str] | None = None
    required_words: list[str] = field(default_factory=list)


@dataclass
class ShowFilters:
    blacklisted_genres: list[str] = field(default_factory=list)
    blacklisted_networks: list[str] = field(default_factory=list)
    blacklisted_min_year: int | None = None
    blacklisted_max_year: int | None = None
    blacklisted_title_keywords: list[str] = field(default_factory=list)
    blacklisted_tvdb_ids: list[int] = field(default_factory=list)
    allowed_countries: list[str] = field(default_factory=list)
    allowed_languages: list[str] = field(default_factory=list)


@dataclass
class PendingShow:
    """A show waiting for manual approval before being added to Medusa."""

    tvdb_id: int
    title: str
    year: int | None = None
    imdb_id: str | None = None
    source_type: str = ""
    source_label: str = ""
    discovered_at: str = ""
    status: str = "pending"  # "pending", "approved", "rejected"
    quality: str | list[str] | None = None
    required_words: list[str] = field(default_factory=list)
    poster_url: str | None = None
    network: str | None = None
    genres: list[str] = field(default_factory=list)


@dataclass
class TraktSource:
    type: str
    owner: str = ""
    list_slug: str = ""
    auth: bool | None = None
    auto_approve: bool = True
    medusa: MedusaAddOptions = field(default_factory=MedusaAddOptions)
    filters: ShowFilters = field(default_factory=ShowFilters)

    @property
    def requires_auth(self) -> bool:
        return bool(self.auth)

    @property
    def label(self) -> str:
        if self.type == "user_list":
            suffix = " (auth)" if self.requires_auth else ""
            return f"user_list:{self.owner}/{self.list_slug}{suffix}"
        return self.type


@dataclass
class TraktConfig:
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    sources: list[TraktSource] = field(default_factory=lambda: [TraktSource(type="watchlist")])
    limit: int = 50


@dataclass
class MedusaConfig:
    url: str = ""
    api_key: str = ""


@dataclass
class SyncConfig:
    dry_run: bool = False
    interval: int = 0
    max_retries: int = 3
    retry_backoff: float = 2.0
    log_format: str = "text"


@dataclass
class HealthConfig:
    enabled: bool = False
    port: int = 8095


@dataclass
class WebUIConfig:
    enabled: bool = False
    port: int = 8089


@dataclass
class NotifyConfig:
    enabled: bool = False
    urls: list[str] = field(default_factory=list)
    on_success: bool = True
    on_failure: bool = True
    only_if_added: bool = False


@dataclass
class AppConfig:
    trakt: TraktConfig = field(default_factory=TraktConfig)
    medusa: MedusaConfig = field(default_factory=MedusaConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    webui: WebUIConfig = field(default_factory=WebUIConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    config_dir: str = "."
    #: Populated when config is loaded with ``skip_validate``/``validate=False`` and numeric
    #: fields in YAML could not be parsed (so coerced defaults were used).
    load_warnings: list[str] = field(default_factory=list)

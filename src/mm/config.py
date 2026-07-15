"""Configuration: .env secrets + config/brands.yaml, and repo-anchored paths."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Repo root = two levels above this file's package dir (src/mm/config.py -> repo)
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = ROOT / "prompts"
TEMPLATE_DIR = ROOT / "template"
DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
OUTPUT_DIR = ROOT / "output"
FIXTURES_DIR = ROOT / "fixtures"
DB_PATH = DATA_DIR / "monitor.db"

BRANDS_YAML = CONFIG_DIR / "brands.yaml"

DEFAULT_MODEL = "claude-sonnet-5"

# Platform keys used across the pipeline / DB. "wechat_mp" and "wechat_channels"
# are ingestion surfaces; both roll up to display platform "WECHAT".
PLATFORMS = ["weibo", "douyin", "xhs", "wechat_mp", "wechat_channels"]
DISPLAY_PLATFORMS = ["WEIBO", "RED", "WECHAT", "DOUYIN"]
PLATFORM_TO_DISPLAY = {
    "weibo": "WEIBO",
    "xhs": "RED",
    "wechat_mp": "WECHAT",
    "wechat_channels": "WECHAT",
    "douyin": "DOUYIN",
}


def load_env() -> None:
    # precedence: real environment > local .env > committed team defaults
    load_dotenv(ROOT / ".env")
    load_dotenv(CONFIG_DIR / "default.env")


@dataclass
class Settings:
    tikhub_api_key: str
    anthropic_api_key: str
    model: str

    @classmethod
    def load(cls) -> "Settings":
        load_env()
        tik = os.environ.get("TIKHUB_API_KEY", "")
        ant = os.environ.get("ANTHROPIC_API_KEY", "")
        missing = [name for name, v in
                   (("TIKHUB_API_KEY", tik), ("ANTHROPIC_API_KEY", ant)) if not v]
        if missing:
            raise RuntimeError(
                f"Missing required secrets: {', '.join(missing)} — set them in "
                f"{ROOT / '.env'} or {CONFIG_DIR / 'default.env'}")
        return cls(
            tikhub_api_key=tik,
            anthropic_api_key=ant,
            model=os.environ.get("MM_MODEL", DEFAULT_MODEL),
        )


@dataclass
class Account:
    platform: str
    status: str  # verified | resolve
    screen_name: str | None = None
    uid: str | None = None
    vanity_url: str | None = None
    lookup_query: str | None = None
    note: str | None = None
    verified_date: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status == "verified" and bool(self.uid)


@dataclass
class Brand:
    key: str
    display_name: str
    order: int
    accounts: dict[str, Account] = field(default_factory=dict)

    def account(self, platform: str) -> Account | None:
        return self.accounts.get(platform)


@dataclass
class BrandsConfig:
    labels: dict
    filters: dict
    brands: list[Brand]
    _raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: Path = BRANDS_YAML) -> "BrandsConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        brands = []
        for b in raw.get("brands", []):
            accounts = {}
            for plat in PLATFORMS:
                node = b.get(plat)
                if node is None:
                    continue
                accounts[plat] = Account(
                    platform=plat,
                    status=node.get("status", "resolve"),
                    screen_name=node.get("screen_name"),
                    uid=str(node["uid"]) if node.get("uid") not in (None, "") else None,
                    vanity_url=node.get("vanity_url"),
                    lookup_query=node.get("lookup_query"),
                    note=node.get("note"),
                    verified_date=node.get("verified_date"),
                )
            brands.append(Brand(
                key=b["key"], display_name=b["display_name"],
                order=int(b.get("order", 99)), accounts=accounts))
        brands.sort(key=lambda b: b.order)
        return cls(labels=raw.get("labels", {}), filters=raw.get("filters", {}),
                   brands=brands, _raw=raw)

    def brand(self, key: str) -> Brand:
        for b in self.brands:
            if b.key == key:
                return b
        raise KeyError(key)

    def save_account_resolution(self, brand_key: str, platform: str,
                                uid: str, screen_name: str | None,
                                verified_date: str,
                                path: Path = BRANDS_YAML) -> None:
        """Write a confirmed internal ID back into brands.yaml and flip to verified."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for b in raw.get("brands", []):
            if b.get("key") != brand_key:
                continue
            node = b.get(platform)
            if node is None:
                node = {}
                b[platform] = node
            node["status"] = "verified"
            node["uid"] = uid
            if screen_name:
                node["screen_name"] = screen_name
            node["verified_date"] = verified_date
        path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8")
        # refresh in-memory copy
        fresh = BrandsConfig.load(path)
        self.labels, self.filters, self.brands, self._raw = (
            fresh.labels, fresh.filters, fresh.brands, fresh._raw)


def ensure_dirs() -> None:
    for d in (DATA_DIR, RUNS_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)

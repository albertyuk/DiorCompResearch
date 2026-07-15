"""Configuration: .env secrets + config/brands.yaml, and repo-anchored paths.

Environment awareness (MM_ENV):
- ``local``  (default) — everything anchored at the repo root, as always.
- ``hosted`` — data root comes from MM_DATA_DIR (the Fly volume, ``/data``);
  generated decks/xlsx live under it so they survive deploys, and Phase R
  account confirmations are persisted to a data-root copy of brands.yaml
  (the image filesystem is ephemeral).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Repo root = two levels above this file's package dir (src/mm/config.py -> repo)
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"

# Env files load at import time so MM_ENV / MM_DATA_DIR can shape the paths
# below. Precedence: real environment > local .env > committed team defaults.
load_dotenv(ROOT / ".env")
load_dotenv(CONFIG_DIR / "default.env")

MM_ENV = os.environ.get("MM_ENV", "local").strip().lower() or "local"
IS_HOSTED = MM_ENV == "hosted"

PROMPTS_DIR = ROOT / "prompts"
TEMPLATE_DIR = ROOT / "template"
DATA_DIR = Path(os.environ.get("MM_DATA_DIR") or (ROOT / "data"))
RUNS_DIR = DATA_DIR / "runs"
OUTPUT_DIR = Path(os.environ.get("MM_OUTPUT_DIR")
                  or ((DATA_DIR / "output") if IS_HOSTED else (ROOT / "output")))
FIXTURES_DIR = ROOT / "fixtures"
DB_PATH = DATA_DIR / "monitor.db"

# brands.yaml: the repo copy is ALWAYS the source of truth (deploy-time edits
# — new brands, filters, labels — must never be shadowed). In hosted mode,
# runtime account confirmations are persisted as a small per-account OVERLAY
# on the volume (the image filesystem is ephemeral) and merged over the repo
# copy at load time.
BRANDS_YAML = CONFIG_DIR / "brands.yaml"
ACCOUNT_OVERRIDES_YAML = DATA_DIR / "account_overrides.yaml"

_OVERRIDE_FIELDS = ("status", "uid", "screen_name", "verified_date")

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_VISUALS = "live" if not IS_HOSTED else "card"

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
                f"{ROOT / '.env'} or {CONFIG_DIR / 'default.env'}"
                + (" (hosted: `fly secrets set …`)" if IS_HOSTED else ""))
        return cls(
            tikhub_api_key=tik,
            anthropic_api_key=ant,
            model=os.environ.get("MM_MODEL", DEFAULT_MODEL),
        )


def console_auth_config() -> dict:
    """Auth is enforced whenever CONSOLE_PASSPHRASE is set — always the case
    in hosted mode (fail fast there if it's missing). A bare local checkout
    with no passphrase keeps the old no-login Console."""
    load_env()
    passphrase = os.environ.get("CONSOLE_PASSPHRASE", "")
    secret = os.environ.get("MM_SECRET_KEY", "")
    if IS_HOSTED and (not passphrase or not secret):
        raise RuntimeError(
            "hosted mode requires CONSOLE_PASSPHRASE and MM_SECRET_KEY "
            "(fly secrets set CONSOLE_PASSPHRASE=… MM_SECRET_KEY=…)")
    if passphrase and not secret:
        raise RuntimeError("CONSOLE_PASSPHRASE is set but MM_SECRET_KEY is not — "
                           "add MM_SECRET_KEY=<long random hex> to .env")
    return {"enabled": bool(passphrase), "passphrase": passphrase,
            "secret": secret}


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
    def load(cls, path: Path | None = None) -> "BrandsConfig":
        path = path or BRANDS_YAML
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if IS_HOSTED and ACCOUNT_OVERRIDES_YAML.exists():
            overrides = yaml.safe_load(
                ACCOUNT_OVERRIDES_YAML.read_text(encoding="utf-8")) or {}
            for b in raw.get("brands", []):
                for platform, fields in (overrides.get(b.get("key")) or {}).items():
                    node = b.get(platform)
                    if node is None:
                        node = {}
                        b[platform] = node
                    for f in _OVERRIDE_FIELDS:
                        if fields.get(f) is not None:
                            node[f] = fields[f]
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
                                path: Path | None = None) -> None:
        """Persist a confirmed internal ID and flip the account to verified.
        Local: written into config/brands.yaml as before. Hosted: written to
        the durable per-account overlay on the volume — the repo yaml stays
        the source of truth for everything else."""
        if IS_HOSTED and path is None:
            ACCOUNT_OVERRIDES_YAML.parent.mkdir(parents=True, exist_ok=True)
            overrides = {}
            if ACCOUNT_OVERRIDES_YAML.exists():
                overrides = yaml.safe_load(
                    ACCOUNT_OVERRIDES_YAML.read_text(encoding="utf-8")) or {}
            entry = overrides.setdefault(brand_key, {}).setdefault(platform, {})
            entry.update({"status": "verified", "uid": uid,
                          "verified_date": verified_date})
            if screen_name:
                entry["screen_name"] = screen_name
            ACCOUNT_OVERRIDES_YAML.write_text(
                yaml.safe_dump(overrides, allow_unicode=True, sort_keys=True),
                encoding="utf-8")
            fresh = BrandsConfig.load()
            self.labels, self.filters, self.brands, self._raw = (
                fresh.labels, fresh.filters, fresh.brands, fresh._raw)
            return
        path = path or BRANDS_YAML
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

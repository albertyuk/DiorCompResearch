"""Anthropic client wrapper: prompt loading from prompts/*.md, strict-JSON
calls with defensive parsing (strip fences, one retry), and cost logging."""
from __future__ import annotations

import json
import re

import anthropic

from .config import PROMPTS_DIR, Settings

# $/MTok (input, output) — used only for the run cost summary.
_PRICES = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
}


def _price(model: str, tokens_in: int, tokens_out: int) -> float:
    for prefix, (pin, pout) in _PRICES.items():
        if model.startswith(prefix):
            return tokens_in / 1e6 * pin + tokens_out / 1e6 * pout
    return tokens_in / 1e6 * 3.0 + tokens_out / 1e6 * 15.0


def load_prompt(name: str) -> str:
    """Load prompts/{name}.md (user-tunable without code changes)."""
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, variables: dict) -> str:
    """Substitute {{var}} placeholders."""
    text = load_prompt(name)
    for k, v in variables.items():
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False, indent=1)
        text = text.replace("{{" + k + "}}", v)
    return text


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def parse_json_loose(text: str):
    """Parse model output as JSON: try direct, then fenced block, then first
    {...} / [...] span. Raises ValueError if nothing parses."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Model output is not parseable JSON: {text[:200]!r}")


class LLM:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        # Pin the base URL: a stray ANTHROPIC_BASE_URL in the environment must
        # not silently reroute calls made with the app's own API key.
        # Override deliberately with MM_ANTHROPIC_BASE_URL if ever needed.
        import os
        base_url = os.environ.get("MM_ANTHROPIC_BASE_URL",
                                  "https://api.anthropic.com")
        self.client = anthropic.Anthropic(
            api_key=self.settings.anthropic_api_key, base_url=base_url)
        self.model = self.settings.model

    def call_json(self, prompt_name: str, variables: dict, *,
                  conn=None, brand: str | None = None, month: str | None = None,
                  max_tokens: int = 2000):
        """Render prompts/{prompt_name}.md, call the model, return parsed JSON.
        On a parse failure, retries once asking for corrected strict JSON."""
        prompt = render_prompt(prompt_name, variables)
        system = ("You are a data-extraction component inside a pipeline. "
                  "Respond with STRICT JSON only — no prose, no markdown fences, "
                  "no commentary. Your entire response must be a single JSON value.")
        messages = [{"role": "user", "content": prompt}]
        last_err = None
        for attempt in range(2):
            response = self.client.messages.create(
                model=self.model, max_tokens=max_tokens,
                system=system, messages=messages)
            text = "".join(b.text for b in response.content if b.type == "text")
            self._log(conn, prompt_name, brand, month, response)
            try:
                return parse_json_loose(text)
            except ValueError as e:
                last_err = e
                messages = messages + [
                    {"role": "assistant", "content": text or "(empty)"},
                    {"role": "user", "content":
                        "That was not parseable as strict JSON. Respond again with "
                        "ONLY the corrected JSON value and nothing else."},
                ]
        raise last_err

    def _log(self, conn, endpoint: str, brand, month, response) -> None:
        if conn is None:
            return
        from . import db
        u = response.usage
        tin = (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) \
            + (getattr(u, "cache_read_input_tokens", 0) or 0)
        tout = u.output_tokens or 0
        db.log_api_call(conn, "anthropic", f"prompt:{endpoint}", brand=brand,
                        month=month, ok=True, tokens_in=tin, tokens_out=tout,
                        cost_usd=_price(response.model, tin, tout))

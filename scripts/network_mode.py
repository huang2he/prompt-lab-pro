"""Dispatcher helpers: overseas/domestic detection + model request quirks.

Used by run_round.py / run_smoke.py / intake-time validation. Two responsibilities:

1. is_overseas(base_url) → bool. Decides whether a role block should carry
   `proxy: true` (overseas) or `network: {mode: "direct"}` (domestic).

2. model_request_quirks(model) → dict. Returns the merge-keys the dispatcher
   needs for that model family (max_completion_tokens for gpt-5/5.x non-chat,
   enable_thinking=False for qwen3*).

No external deps (stdlib only).
"""
from __future__ import annotations
from urllib.parse import urlparse


# Domain whitelist: hostname (or its suffix) matches → overseas → proxy:true.
# Add more as new providers come online. Keep lowercase.
#
# 注意：一些"国内域名 + 海外底层模型"的反代也要放进来，否则 direct 走默认
# routing pool 容易撞限速。例如 sub2api.agoraio.cn 是国内域名但后端转 OpenAI，
# 必须用 proxy=true 才能稳定（2026-05-21 实测：direct 时 95% fail，proxy 时 0 fail）。
OVERSEAS_DOMAINS: frozenset[str] = frozenset({
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",   # Gemini
    "api.cohere.com",
    "openrouter.ai",
    "api.together.xyz",
    "api.x.ai",                            # xAI / Grok
    "api.mistral.ai",
    "api.deepinfra.com",
    "api.fireworks.ai",
    "sub2api.agoraio.cn",                  # 国内反代到 OpenAI，必须 proxy 才稳
})


def is_overseas(base_url: str) -> bool:
    """Return True if the base_url's hostname is in the overseas whitelist.

    Matches exact host or any subdomain (e.g. 'gateway.openai.com' is treated
    as overseas because its suffix matches '.openai.com').

    Anything else (DashScope, 智谱, DeepSeek, Kimi, self-hosted IP, localhost,
    private nets) is treated as domestic.
    """
    host = (urlparse(base_url).hostname or "").lower()
    if not host:
        return False
    for d in OVERSEAS_DOMAINS:
        if host == d or host.endswith("." + d):
            return True
    return False


def role_network_block(base_url: str) -> dict:
    """Return the dict to merge into a role block (assistant/user/end_checker).

    Examples:
        role_network_block("https://api.openai.com/v1")
        # -> {"proxy": True}

        role_network_block("https://dashscope.aliyuncs.com/compatible-mode/v1")
        # -> {"network": {"mode": "direct"}}
    """
    return {"proxy": True} if is_overseas(base_url) else {"network": {"mode": "direct"}}


def network_mode_label(base_url: str) -> str:
    """Human-readable label for config.json (network_mode field): 'proxy' or 'direct'."""
    return "proxy" if is_overseas(base_url) else "direct"


# -------- model quirks --------

def model_request_quirks(model: str) -> dict:
    """Return extra request-block fields specific to a model family.

    These are merged into the `request: {...}` block when building the HTTP body.

    Quirks covered:
    - GPT-5 family (non chat-latest) requires `max_completion_tokens` instead of
      `max_tokens`. Caller should swap the key name when this returns a hint.
    - Qwen3 thinking models default to a reasoning chain; disable it so the
      output stays under max_tokens.
    - Anthropic / OpenAI standard models: no extra fields.

    Returns a dict like:
        {"use_max_completion_tokens": True}     ← caller swaps max_tokens key
        {"enable_thinking": False}              ← inject as-is

    Empty dict means no quirks.
    """
    m = (model or "").lower()
    # GPT-5 / 5.x non-chat variants: chat completions endpoint refuses max_tokens
    if m.startswith("gpt-5") and "chat-latest" not in m and not m.endswith("-pro"):
        # Note: gpt-5*-pro CAN'T go through chat endpoint at all — caller should
        # validate that separately at intake. We just flag for max_completion_tokens swap.
        return {"use_max_completion_tokens": True}
    # Qwen3 family thinking models (catch both "qwen3-..." and "qwen/qwen3...." vendor prefixes)
    if "qwen3" in m:
        return {"enable_thinking": False}
    return {}


def is_pro_model_blocked(model: str) -> bool:
    """gpt-5*-pro / gpt-5.x-pro can't go through chat completions endpoint.

    Return True if the caller should refuse this model and ask the user
    to switch to *-chat-latest or a non-pro variant.
    """
    m = (model or "").lower()
    return m.startswith("gpt-5") and m.endswith("-pro")


def apply_quirks_to_request(request: dict, model: str) -> dict:
    """Apply model quirks to a request dict in place. Returns the same dict for chaining.

    Behavior:
    - If quirks say use_max_completion_tokens: rename 'max_tokens' → 'max_completion_tokens'
    - Otherwise inject quirks fields as-is
    """
    q = model_request_quirks(model)
    if not q:
        return request
    if q.get("use_max_completion_tokens"):
        if "max_tokens" in request:
            request["max_completion_tokens"] = request.pop("max_tokens")
    else:
        request.update(q)
    return request


# -------- self-test --------
if __name__ == "__main__":
    cases = [
        ("https://api.openai.com/v1", True),
        ("https://api.openai.com/v1/chat/completions", True),    # path doesn't matter
        ("https://api.anthropic.com", True),
        ("https://generativelanguage.googleapis.com/v1beta", True),
        ("https://openrouter.ai/api/v1", True),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", False),
        ("http://47.100.137.178:8080", False),
        ("http://localhost:8000/v1", False),
        ("https://api.deepseek.com/v1", False),
    ]
    for url, expected in cases:
        got = is_overseas(url)
        status = "✓" if got == expected else "✗"
        print(f"{status} is_overseas({url!r}) = {got}, want {expected}")
        print(f"   block: {role_network_block(url)}")

    print()
    for m, expected in [
        ("qwen-plus", {}),
        ("gpt-5-chat-latest", {}),
        ("gpt-5.5", {"use_max_completion_tokens": True}),
        ("gpt-5.5-pro", False),   # blocked
        ("qwen3.6-plus", {"enable_thinking": False}),
        ("claude-opus-4-7", {}),
    ]:
        if expected is False:
            blocked = is_pro_model_blocked(m)
            print(f"{'✓' if blocked else '✗'} is_pro_model_blocked({m!r}) = {blocked}")
        else:
            got = model_request_quirks(m)
            ok = got == expected
            print(f"{'✓' if ok else '✗'} model_request_quirks({m!r}) = {got}, want {expected}")

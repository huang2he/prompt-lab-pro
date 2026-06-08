#!/usr/bin/env python3
"""Resolve dispatcher URL + token for prompt-lab-pro.

优先级 (高 → 低)：
  1. env var PROMPT_LAB_DISPATCHER_URL / PROMPT_LAB_DISPATCHER_TOKEN
  2. ~/.claude/skills/prompt-lab-pro/.env  (primary)
  3. ~/.claude/skills/prompt-lab/.env      (legacy fallback, 兼容老用户)
  4. baked-in DEFAULT_URL (token 无默认 — 缺则报缺)

用法：
    python3 load_dispatcher.py            # 打印 url<TAB>token<TAB>source
    python3 load_dispatcher.py --json     # 机器可读
    python3 load_dispatcher.py --save-token <TOKEN>  # 首次拿到 token 后写回 .env
    python3 load_dispatcher.py --check    # 只检查并退出码：0=全有, 2=缺 token, 3=缺 url

skill 启动 Phase A 时调用 --json 拿 (url, token, missing[])：
  - missing 包含 "token" → 进 Q0-B 问用户 → 拿到后跑 --save-token
  - missing 包含 "url"   → 实际不会发生 (有 baked-in default)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_URL = "http://47.100.137.178:8080"
ENV_FILE = Path.home() / ".claude" / "skills" / "prompt-lab-pro" / ".env"
LEGACY_ENV_FILE = Path.home() / ".claude" / "skills" / "prompt-lab" / ".env"


def _parse_env_file(p: Path) -> dict:
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def resolve() -> dict:
    """Return {url, token, source_url, source_token, missing[]}."""
    # primary 优先；缺 token 时 fallback legacy
    file_vars = _parse_env_file(ENV_FILE)
    if not file_vars.get("PROMPT_LAB_DISPATCHER_TOKEN"):
        legacy = _parse_env_file(LEGACY_ENV_FILE)
        for k, v in legacy.items():
            file_vars.setdefault(k, v)

    url = os.environ.get("PROMPT_LAB_DISPATCHER_URL") \
        or file_vars.get("PROMPT_LAB_DISPATCHER_URL") \
        or DEFAULT_URL
    src_url = "env" if os.environ.get("PROMPT_LAB_DISPATCHER_URL") \
        else (".env" if file_vars.get("PROMPT_LAB_DISPATCHER_URL") else "default")

    tok = os.environ.get("PROMPT_LAB_DISPATCHER_TOKEN") \
        or file_vars.get("PROMPT_LAB_DISPATCHER_TOKEN") \
        or ""
    src_tok = "env" if os.environ.get("PROMPT_LAB_DISPATCHER_TOKEN") \
        else (".env" if file_vars.get("PROMPT_LAB_DISPATCHER_TOKEN") else "missing")

    missing = []
    if not tok:
        missing.append("token")
    if not url:
        missing.append("url")

    return {
        "url": url,
        "token": tok,
        "source_url": src_url,
        "source_token": src_tok,
        "missing": missing,
        "env_file": str(ENV_FILE),
    }


def save_token(tok: str) -> None:
    """Persist token to .env (preserve other keys; chmod 600)."""
    if not tok or not tok.strip():
        print("refuse: empty token", file=sys.stderr)
        sys.exit(1)
    tok = tok.strip()

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _parse_env_file(ENV_FILE)
    existing["PROMPT_LAB_DISPATCHER_TOKEN"] = tok
    existing.setdefault("PROMPT_LAB_DISPATCHER_URL", DEFAULT_URL)

    body = [
        "# prompt-lab dispatcher config — auto-written by load_dispatcher.py",
        "# 不要提交到 git；改文件时保留两个 KEY",
        f"PROMPT_LAB_DISPATCHER_URL={existing['PROMPT_LAB_DISPATCHER_URL']}",
        f"PROMPT_LAB_DISPATCHER_TOKEN={existing['PROMPT_LAB_DISPATCHER_TOKEN']}",
        "",
    ]
    ENV_FILE.write_text("\n".join(body), encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass
    print(f"saved: {ENV_FILE}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--save-token", metavar="TOKEN")
    args = ap.parse_args()

    if args.save_token is not None:
        save_token(args.save_token)
        return 0

    info = resolve()
    if args.check:
        if info["missing"]:
            print(f"missing: {','.join(info['missing'])}", file=sys.stderr)
            return 2 if "token" in info["missing"] else 3
        print(f"ok url={info['url']} (from {info['source_url']}) "
              f"token=*** (from {info['source_token']})")
        return 0

    if args.json:
        # 隐藏 token 明文，只回 mask
        masked = info.copy()
        if masked["token"]:
            t = masked["token"]
            masked["token_masked"] = (t[:4] + "***" + t[-2:]) if len(t) >= 6 else "***"
        else:
            masked["token_masked"] = ""
        # 主跑脚本仍需明文 → 单独透出在 token 字段
        print(json.dumps(masked, ensure_ascii=False))
        return 0

    # 默认人可读
    print(f"url\t{info['url']}\t(from {info['source_url']})")
    print(f"token\t{'<set>' if info['token'] else '<MISSING>'}\t(from {info['source_token']})")
    if info["missing"]:
        print(f"missing\t{','.join(info['missing'])}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

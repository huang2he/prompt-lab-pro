#!/usr/bin/env python3
"""prompt-lab-pro · Phase E.2 dispatcher-native judge.

替代 prompt-lab 的 6-subagent dispatch 评分链路：
  本地 Claude 主会话先拼好 evaluator_prompt.md (E.2a) ──[本脚本]──> POST /evaluation_batch
                                                                       并发 N transcripts × M evaluators × count_self_consistency

设计 split:
  - 本地（Claude 主会话）: 拼装 evaluator_prompt.md（基于 rubric/criteria/failure-types/persona/auto_check）
  - dispatcher: 拿到拼好的 prompt，纯执行（按 output_schema 强制返回 JSON）
  - 本脚本: client-side fan-out (ThreadPool) + 收聚合 + median 取众数（self-consistency）

用法:
  python3 judge_via_dispatcher.py <workspace> --round round-03
      读 iterations/round-03/{evaluator_prompt.md, transcripts.jsonl, auto_check.json}
      读 prompts/<id>/personas/pool.jsonl
      写 iterations/round-03/judgments.json

依赖: stdlib only. 与 run_round.py / load_dispatcher.py 同目录。

退出码: 0 全 OK；2 evaluator_prompt.md 缺；3 dispatcher 调用整批失败；4 schema 校验失败>20%。
"""
from __future__ import annotations
import argparse
import concurrent.futures
import json
import statistics
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# 复用同目录 load_dispatcher（URL+token 来源同 prompt-lab）
sys.path.insert(0, str(Path(__file__).parent))
from load_dispatcher import resolve as resolve_dispatcher  # noqa: E402


# ============================================================
# output_schema：跟 prompt-lab v3 scoring-pipeline.md 的 Judge 输出对齐
# ============================================================
JUDGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "subjective_violations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "n": {"type": "integer"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "turn": {"type": "integer"},
                                "u": {"type": "string"},
                                "why": {"type": "string"}
                            },
                            "required": ["turn", "u", "why"]
                        }
                    }
                },
                "required": ["id", "n"]
            }
        },
        "goal_statuses": {
            "type": "object",
            "additionalProperties": {"enum": ["done", "partial", "none"]}
        },
        "conversation_flow": {"type": "integer", "minimum": 1, "maximum": 5},
        "conversation_flow_notes": {
            "type": "object",
            "properties": {
                "pacing": {"type": "string"},
                "transition": {"type": "string"},
                "info_density": {"type": "string"},
                "ai_tells": {"type": "string"},
                "recovery": {"type": "string"}
            }
        },
        "asr_robustness": {"type": "integer", "minimum": 1, "maximum": 5},
        "naturalness": {"type": "integer", "minimum": 1, "maximum": 5},
        "hard_fails": {
            "type": "array",
            "items": {
                "enum": [
                    "hallucination",
                    "out_of_scope_commitment",
                    "identity_breach",
                    "injection_breach",
                    "infinite_loop",
                    "early_hangup"
                ]
            }
        },
        "notable_moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "turn": {"type": "integer"},
                    "issue": {"type": "string"}
                }
            }
        },
        "bad_case_summary": {"type": "string"}
    },
    "required": ["goal_statuses", "conversation_flow", "naturalness", "hard_fails"]
}


# ============================================================
# HTTP helpers
# ============================================================
def http_post_json(url: str, body: dict, token: str, timeout: int = 60) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "x-access-token": token},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_get_json(url: str, token: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"x-access-token": token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ============================================================
# Evaluator config 来源：config.json["judge_evaluator"]
# ============================================================
def load_workspace(workspace: Path) -> dict:
    cfg = json.loads((workspace / "config.json").read_text())
    if "judge_evaluator" not in cfg:
        raise SystemExit(
            "workspace config.json 缺 judge_evaluator 字段。\n"
            "在 config.json 顶层加：\n"
            '  "judge_evaluator": {\n'
            '    "provider": "openai",\n'
            '    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",\n'
            '    "model": "qwen-plus",\n'
            '    "api_key": "sk-...",\n'
            '    "temperature": 0,\n'
            '    "max_tokens": 800\n'
            "  }\n"
            "如需 ensemble 多 judge，写成 judge_evaluators (数组)。"
        )
    return cfg


def find_round_dir(workspace: Path, round_name: str) -> Path:
    prompts_dir = workspace / "prompts"
    projects = [p for p in prompts_dir.iterdir() if p.is_dir()]
    if len(projects) != 1:
        raise SystemExit(f"prompts/ 下应该恰好 1 个 project；找到 {len(projects)} 个")
    rd = projects[0] / "iterations" / round_name
    if not rd.exists():
        raise SystemExit(f"missing {rd}")
    return rd, projects[0]


def load_personas(project_dir: Path) -> dict[str, dict]:
    pool = {}
    p = project_dir / "personas" / "pool.jsonl"
    if not p.exists():
        return pool
    for line in p.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            pool[d.get("id", "")] = d
    return pool


def load_transcripts(round_dir: Path) -> list[dict]:
    """读 transcripts.jsonl，统一字段名给下游用。

    上游 run_round.py 写的 schema:
      {conv_id, persona_id, asr_noise, transcript: [{speaker, text}], n_turns, status, ...}

    本脚本下游 evaluate_one() 期望:
      {id: ..., history: [{role, content}], persona_id: ..., ...}

    所以在这里做映射。
    """
    p = round_dir / "transcripts.jsonl"
    if not p.exists():
        raise SystemExit(f"missing {p}")
    out = []
    speaker_to_role = {"agent": "assistant", "user": "user"}
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        rec["id"] = rec.get("conv_id") or rec.get("id") or rec.get("transcript_id")
        # 把 [{speaker, text}] 转 [{role, content}]（dispatcher /evaluation_batch 需要的格式）
        history = []
        for m in (rec.get("transcript") or []):
            role = speaker_to_role.get(m.get("speaker"), m.get("speaker"))
            if role in ("assistant", "user"):
                history.append({"role": role, "content": m.get("text", "")})
        rec["history"] = history
        out.append(rec)
    return out


def load_auto_check(round_dir: Path) -> dict:
    p = round_dir / "auto_check.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def load_evaluator_prompt(round_dir: Path) -> str:
    """E.2a 的产物：本地 Claude 主会话已经把 rubric+criteria+failure-types+prompt 拼好了。"""
    p = round_dir / "evaluator_prompt.md"
    if not p.exists():
        raise SystemExit(
            f"missing {p}\n"
            "请先让 Claude 主会话执行 Phase E.2a：\n"
            "  1. 读 workspace/prompts/<id>/rubric.md\n"
            "  2. 读 iterations/<round>/criteria.json\n"
            "  3. 读 ~/.claude/skills/prompt-lab-pro/references/failure-types.md\n"
            "  4. 读 iterations/<round>/prompt.md\n"
            "  5. 拼成 evaluator_prompt.md 写到该 round 目录\n"
            "  详见 references/scoring-pipeline.md 的 Phase E.2a"
        )
    return p.read_text()


# ============================================================
# 单 transcript 单 evaluator 调用
# ============================================================
def evaluate_one(
    dispatcher: str,
    token: str,
    transcript: dict,
    evaluator_prompt_base: str,
    persona: dict,
    auto_check_for_this: dict,
    evaluator_cfg: dict,
    count_self_consistency: int,
    timeout: int = 180,
) -> dict:
    """对一条 transcript 跑一个 evaluator × count_self_consistency 次取中位数。

    Returns:
      {"transcript_id":..., "evaluator_model":..., "results_raw": [...], "results_aggregated": {...}, "error":?}
    """
    # 把 per-transcript 的 persona + auto_check 嵌进 evaluator.prompt
    persona_block = (
        f"\n═══ PERSONA METADATA (this transcript) ═══\n"
        f"{json.dumps(persona, ensure_ascii=False, indent=2)}\n"
    )
    auto_check_block = (
        f"\n═══ OBJECTIVE RULES PRE-CHECKED (dedupe, don't re-flag) ═══\n"
        f"{json.dumps(auto_check_for_this, ensure_ascii=False, indent=2)}\n"
    )
    full_evaluator_prompt = evaluator_prompt_base + persona_block + auto_check_block + (
        "\n═══ TASK ═══\n"
        "Read the conversation in `target.history` and output a single JSON object "
        "exactly matching the provided output_schema. Don't include code fences or commentary."
    )

    body = {
        "count": count_self_consistency,
        "target": {
            "history": transcript.get("history", []),
            "system_prompt": transcript.get("agent_prompt_used", ""),
            "llm_base_url": transcript.get("agent_base_url", ""),
            "model": transcript.get("agent_model", ""),
        },
        "evaluator": {
            "provider": evaluator_cfg.get("provider", "openai"),
            "base_url": evaluator_cfg["base_url"],
            "model": evaluator_cfg["model"],
            "api_key": evaluator_cfg["api_key"],
            "prompt": full_evaluator_prompt,
            "network": {"mode": "direct"},
            "request": {
                "temperature": evaluator_cfg.get("temperature", 0),
                "top_p": evaluator_cfg.get("top_p", 1),
                "max_tokens": evaluator_cfg.get("max_tokens", 800),
            },
        },
        "timeout_seconds": evaluator_cfg.get("timeout_seconds", 120),
        "output_schema": JUDGE_OUTPUT_SCHEMA,
        "verbose": False,
    }

    if evaluator_cfg.get("proxy"):
        body["evaluator"]["proxy"] = True
        body["evaluator"].pop("network", None)

    result = {
        "transcript_id": transcript.get("id") or transcript.get("transcript_id"),
        "persona_id": persona.get("id"),
        "evaluator_model": evaluator_cfg["model"],
        "results_raw": [],
        "results_aggregated": None,
        "error": None,
    }

    try:
        resp = http_post_json(f"{dispatcher}/evaluation_batch", body, token, timeout=30)
        batch_id = resp.get("evaluation_batch_id") or resp.get("batch_id") or resp.get("id")
        if not batch_id:
            result["error"] = f"no batch_id in response: {resp}"
            return result

        # 轮询
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            status = http_get_json(f"{dispatcher}/evaluation_batch/{batch_id}", token)
            if status.get("status") in ("succeeded", "failed", "partial_failed", "cancelled"):
                break
            if status.get("result_ready"):
                break

        # 拿结果
        batch_result = http_get_json(
            f"{dispatcher}/evaluation_batch/{batch_id}/result", token, timeout=30
        )
        # batch_result 结构因实现而异；尝试常见两种 shape
        raws = (
            batch_result.get("evaluations")
            or batch_result.get("results")
            or batch_result.get("items")
            or []
        )
        for r in raws:
            output = r.get("output") or r.get("result") or r
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except Exception:
                    continue
            result["results_raw"].append(output)
    except urllib.error.HTTPError as e:
        result["error"] = f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    if result["results_raw"]:
        result["results_aggregated"] = aggregate_self_consistency(result["results_raw"])
    return result


# ============================================================
# Self-consistency 聚合
# ============================================================
def _median_int(values: list, default: int = 3) -> int:
    if not values:
        return default
    try:
        return int(round(statistics.median(values)))
    except Exception:
        return default


def aggregate_self_consistency(raws: list[dict]) -> dict:
    """count 次评分 → 取中位数 / 多数票。"""
    if not raws:
        return {}
    out = {}
    # 数值维度取中位数
    for dim in ("conversation_flow", "asr_robustness", "naturalness"):
        vals = [r.get(dim) for r in raws if isinstance(r.get(dim), (int, float))]
        out[dim] = _median_int(vals, 3)

    # goal_statuses 每个 g 单独多数票
    goal_keys = set()
    for r in raws:
        gs = r.get("goal_statuses") or {}
        goal_keys.update(gs.keys())
    out["goal_statuses"] = {}
    for g in goal_keys:
        vals = [(r.get("goal_statuses") or {}).get(g) for r in raws if (r.get("goal_statuses") or {}).get(g)]
        if not vals:
            continue
        # 多数票（done > partial > none 优先级 tie-break，留谨慎档）
        counter = {}
        for v in vals:
            counter[v] = counter.get(v, 0) + 1
        max_n = max(counter.values())
        winners = [k for k, n in counter.items() if n == max_n]
        # tie-break: prefer none > partial > done (保守)
        for prefer in ("none", "partial", "done"):
            if prefer in winners:
                out["goal_statuses"][g] = prefer
                break

    # hard_fails 取所有出现的并集（保守：任何一次说有就当有）
    hf_union = set()
    for r in raws:
        for h in (r.get("hard_fails") or []):
            hf_union.add(h)
    out["hard_fails"] = sorted(hf_union)

    # subjective_violations 取 count >=半数 的 rule id（至少半数 judge 一致认定）
    rule_count = {}
    for r in raws:
        seen_rules = set()
        for v in (r.get("subjective_violations") or []):
            seen_rules.add(v.get("id"))
        for rid in seen_rules:
            rule_count[rid] = rule_count.get(rid, 0) + 1
    half = max(1, len(raws) // 2 + (len(raws) % 2))  # ceil
    persistent_rules = [rid for rid, c in rule_count.items() if c >= half]
    # 给每条 persistent rule 选 evidence 最丰富的那一份
    out["subjective_violations"] = []
    for rid in persistent_rules:
        best = None
        for r in raws:
            for v in (r.get("subjective_violations") or []):
                if v.get("id") == rid:
                    if best is None or len(v.get("evidence") or []) > len(best.get("evidence") or []):
                        best = v
        if best:
            out["subjective_violations"].append(best)

    # cf_notes / notable / bad_case_summary 从第一份取（也可拼接，简化先这样）
    first_with = next((r for r in raws if r.get("conversation_flow_notes")), {})
    if first_with.get("conversation_flow_notes"):
        out["conversation_flow_notes"] = first_with["conversation_flow_notes"]
    first_summary = next((r.get("bad_case_summary") for r in raws if r.get("bad_case_summary")), "")
    out["bad_case_summary"] = first_summary
    out["notable_moments"] = (raws[0].get("notable_moments") or []) if raws else []

    out["_consistency"] = {
        "n_runs": len(raws),
        "cf_values": [r.get("conversation_flow") for r in raws],
        "nat_values": [r.get("naturalness") for r in raws],
    }
    return out


# ============================================================
# Schema 简易校验（避免每个 raw 写正经 jsonschema 依赖）
# ============================================================
def quick_schema_check(j: dict) -> bool:
    if not isinstance(j, dict):
        return False
    for req in ("goal_statuses", "conversation_flow", "naturalness", "hard_fails"):
        if req not in j:
            return False
    if not isinstance(j["conversation_flow"], (int, float)):
        return False
    if not (1 <= j["conversation_flow"] <= 5):
        return False
    return True


# ============================================================
# main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", help="prompt-lab workspace dir")
    ap.add_argument("--round", required=True, help="round-NN")
    ap.add_argument("--count-self-consistency", type=int, default=3,
                    help="POST /evaluation_batch top-level count (per transcript per evaluator)")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="ThreadPool max workers (HTTP fan-out)")
    ap.add_argument("--timeout", type=int, default=180,
                    help="per-transcript polling timeout (s)")
    args = ap.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists():
        raise SystemExit(f"missing workspace {workspace}")

    # 1. dispatcher URL + token
    disp = resolve_dispatcher()
    if "token" in disp["missing"]:
        raise SystemExit("dispatcher token 未配置；先跑 load_dispatcher.py --save-token <TOKEN>")
    dispatcher = disp["url"].rstrip("/")
    token = disp["token"]

    # 2. workspace inputs
    cfg = load_workspace(workspace)
    round_dir, project_dir = find_round_dir(workspace, args.round)
    personas = load_personas(project_dir)
    transcripts = load_transcripts(round_dir)
    auto_check = load_auto_check(round_dir)
    evaluator_prompt_base = load_evaluator_prompt(round_dir)

    # evaluators: 单 evaluator 或 ensemble 数组
    if "judge_evaluators" in cfg:
        evaluators = cfg["judge_evaluators"]
    else:
        evaluators = [cfg["judge_evaluator"]]
    print(f"workspace={workspace}", flush=True)
    print(f"round={args.round}  transcripts={len(transcripts)}  evaluators={len(evaluators)}  "
          f"count_self_consistency={args.count_self_consistency}  concurrency={args.concurrency}", flush=True)

    # 3. per-transcript auto_check lookup（auto_check 可能用 transcript_id 或 conv_id）
    per_t_ac = {}
    for entry in (auto_check.get("per_transcript") or []):
        key = entry.get("transcript_id") or entry.get("conv_id") or entry.get("id")
        if key:
            per_t_ac[key] = entry

    # 4. fan-out
    jobs = []
    for t in transcripts:
        tid = t.get("id")  # load_transcripts 统一过 id 字段
        pid = t.get("persona_id")
        persona = personas.get(pid, {"id": pid})
        ac = per_t_ac.get(tid, {})
        for ev in evaluators:
            jobs.append((t, persona, ac, ev))

    results = []
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {
            pool.submit(
                evaluate_one,
                dispatcher, token, t, evaluator_prompt_base, persona, ac, ev,
                args.count_self_consistency, args.timeout
            ): (t.get("id"), ev["model"])
            for (t, persona, ac, ev) in jobs
        }
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            results.append(r)
            done += 1
            tag = futs[fut]
            err = (r.get("error") or "")[:80]
            print(f"[{done}/{len(jobs)}] tid={tag[0]} model={tag[1]} "
                  f"raw={len(r['results_raw'])} {('ERR ' + err) if err else ''}", flush=True)

    elapsed = time.time() - t0
    n_ok = sum(1 for r in results if r.get("results_aggregated"))
    n_err = sum(1 for r in results if r.get("error"))
    print(f"\ndone: {n_ok}/{len(jobs)} ok, {n_err} errored, elapsed={elapsed:.1f}s", flush=True)

    if n_err / max(1, len(jobs)) > 0.2:
        print(f"ERROR: >20% calls errored", file=sys.stderr)
        # 仍然写出部分结果让用户看，但用 exit 4
        out_exit = 4
    else:
        out_exit = 0

    # 5. 写盘
    judgments_path = round_dir / "judgments.json"
    payload = {
        "round": args.round,
        "evaluator_models": [ev["model"] for ev in evaluators],
        "count_self_consistency": args.count_self_consistency,
        "n_transcripts": len(transcripts),
        "n_jobs": len(jobs),
        "n_ok": n_ok,
        "n_err": n_err,
        "elapsed_s": round(elapsed, 1),
        "results": results,
    }
    judgments_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {judgments_path}", flush=True)

    return out_exit


if __name__ == "__main__":
    sys.exit(main())

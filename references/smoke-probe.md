# Phase D · 完整 Smoke Probe

> **三层探测一句话**：A.0 healthz（URL 拼写）→ A.3 chat 拿 id（key + schema）→ **D 完整 smoke**（history shape + end_checker 行为）。三层逐级递进，前两层在 Phase A intake 内完成（详见 `references/intake.md`），本文档专讲 Phase D。

抽完 criteria 用户签字后，**正式开跑前必须做一次完整探测**。Phase D 比 A.3 更严：
1. ✓ 用真实的 pool[0] persona（不是 stub）
2. ✓ 跑完整 chat 直到 succeeded
3. ✓ 验 history shape（assistant/user role, content 非空）
4. ✓ 验 result 字段（stop_reason, turns_used, ended_by_checker）

## 做什么

调一次最短的 `/chat`（不用 `/simulation`，因为只测 1 次）：

```bash
POST <config.remote_server>/chat   # URL 从 workspace/config.json 读（用户在 Phase A 提供）
Body:
{
  "runtime": {
    "max_turns": 4,
    "start_agent": "assistant",
    "min_messages_before_end_check": 2,
    "timeout_seconds": 60
  },
  "assistant": {
    "provider": <config.agent_a.provider>,
    "model": <config.agent_a.model>,
    "llm_base_url": <config.agent_a.llm_base_url>,
    "llm_api_key": <config.agent_a.llm_api_key>,
    "network": {"mode": "direct"},
    "request": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 100},
    "system_prompt": "<round-01/prompt.md 内容>",
    "greeting": "<config.greeting>"
  },
  "user": {
    "...同 assistant 字段...": "...",
    "model": <config.agent_b.model>,
    "system_prompt": "<pool[0].prompt 全文>",  // 选 pool 第一条 persona
    "greeting": "<config.greeting>"  // 占位
  },
  "end_checker": {
    "...同上...": "...",
    "model": <config.end_checker.model>,
    "request": {"temperature": 0, "top_p": 1, "max_tokens": 60},
    "system_prompt": "你负责判断这通电话是否应该结束。只能返回严格 JSON。",
    "end_description": "<Phase C 后生成的 end_description>"
  },
  "verbose": false
}
```

## 校验项

POST 后拿到 `chat_id`，轮询 GET `/chat/{chat_id}` 直到 status 是 `succeeded` 或 `failed`。

### 校验 1：status

- `succeeded` ✓ 继续
- `failed` → 显示 `result.error` 字段，让用户决定（重试 / 换 server / 中止）

### 校验 2：response shape

response 必须含：
- `history`（数组，非空）
- 每条 `history[i]` 是 `{role: string, content: string}`
- `role` ∈ {"assistant", "user"}（个别服务可能用 "agent_a"/"agent_b"，需要识别）
- `content` 字符串非空

### 校验 3：result 字段（可选）

- `stop_reason`：end_checker 给的停止理由
- `turns_used`：实际跑了多少轮
- `ended_by_checker`：bool，是否 end_checker 触发

不必填，但若存在记录到 transcripts.jsonl 帮助评分。

## ★ Gate 决策

- **全部通过** → 进 Phase E 主循环
- **status fail** → 显示 error + 让用户选：
  - 重试 1 次
  - 修改 API key（如认证失败）
  - 修改 server URL（如不通）
  - 中止 skill
- **shape 不对** → 显示 response 原文 + 让用户决定：
  - "也许是 server 新版本"——记录 actual shape，让 skill 适配
  - 中止重新做

## 错误诊断辅助

常见错误及推断原因：

| Error pattern | 可能原因 |
|---|---|
| `unauthorized` / `401` | API key 错 |
| `connection refused` / 网络错 | server URL 错或服务挂了 |
| `model not found` | provider/model 名字错 |
| `quota exceeded` | DashScope 用量到顶 |
| `no healthy workers` | server 队列堵了（重试或等） |
| `decode request body: unknown field` | body schema 不对（一般是 server 版本旧） |

## 例外

如果用户在 Phase A 选了"已有 transcripts，不跑远端"（详 SKILL.md 例外场景），**跳过 Phase D**。

如果服务器返回 200 但 chat status `failed` 因为 `timeout` 错误——可能是远端 LLM 不通——同样属于"远端不通"，按上面的诊断处理。

## smoke probe 完成后

写到 workspace：
```
<workspace>/prompts/<id>/iterations/round-01/smoke_test_response.json
```

记录原始 response，便于后续 debugging。

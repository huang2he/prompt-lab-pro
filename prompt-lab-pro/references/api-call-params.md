# 远端 API 调用参数完整参考（prompt-lab-pro）

skill 跟 dispatcher 之间的协议，2026-06 dispatcher 版本对齐（`GET /skill.md` 拉到的官方文档）。

## 端点与用途

| 端点 | 方法 | 用途 | pro 用 | body |
|---|---|---|---|---|
| `/healthz` | GET | 探活（A.0） | ✅ | 无 body |
| `/chat` | POST | 单通对话（A.3 探测 + Phase D smoke） | ✅ | 见下 schema |
| `/chat/{id}` | GET | 轮询单通 chat 状态 | ✅ | 无 body |
| `/simulation` | POST | 批量对话（Phase E 主跑，per persona 一次 count=K） | ✅ | `/chat` body + 顶层 `count` |
| `/simulation/{id}` | GET | 轮询批 simulation 状态 | ✅ | 无 body |
| `/simulation/{id}/result` | GET | 拿 simulation 结果（chats 数组） | ✅ | 无 body |
| `/simulation/{id}` | DELETE | **取消跑中 sim**（Ctrl-C 清理用） | ⚠️ TODO | 无 body |
| **`/evaluation_batch`** | **POST** | **评分批跑（Phase E.2b 核心端点）** | ✅ | 见下 schema |
| `/evaluation_batch/{id}` | GET | 轮询评分批 | ✅ | 无 body |
| `/evaluation_batch/{id}/result` | GET | 拿评分结果（含 N 次 self-consistency 数组） | ✅ | 无 body |
| `/evaluation_batch/{id}` | DELETE | 取消评分批 | ⚠️ TODO | 无 body |
| `/chat_completion` | POST | 同步 OpenAI 兼容包装（一发一收） | ❌ | 不用，直接走 LLM provider 更直接 |
| `/evaluation` | POST | 单条 evaluation（无 count） | ❌ | 我们都用 batch (count>=1) |

**所有请求**都要带 HTTP header：

```
Content-Type: application/json
x-access-token: <从 ~/.claude/skills/prompt-lab/.env 读>
```

> dispatcher 官方文档说 Basic Auth 是 "Preferred"、X-Access-Token 是 "Legacy"。pro 版当前仍用 Legacy header（兼容旧 dispatcher 实例 + 减少改动）。未来 dispatcher 弃 Legacy 时，改 `scripts/run_round.py` 和 `scripts/judge_via_dispatcher.py` 的 `http_post_json` / `http_get_json` 即可（一处改动）。

## /simulation 的 user 块 ASR 噪声扰动（pro 版独有用法）

dispatcher 文档说 `user.silence_rate` / `user.silence_message` / `user.asr_failure_rate` / `user.asr_failure_message` 是 **simulation-only** 的扰动控制。

pro 版在 `scripts/run_round.py:asr_noise_block()` 把 persona.asr_noise 映射进来：

```json
"user": {
  "provider": "openai",
  "model": "qwen-flash",
  "...其它 llm_base_url / llm_api_key / network / request / system_prompt": "...",

  "silence_rate": 0.10,
  "silence_message": "请确认对方是否还在听（如：喂？还在吗？）",
  "asr_failure_rate": 0.20,
  "asr_failure_message": "请对方重复刚才那句，理由是没听清/信号不好"
}
```

dispatcher 按概率插入扰动：每个 user turn 有 `silence_rate` 概率"沉默"（agent 看到 `silence_message`），`asr_failure_rate` 概率说出 `asr_failure_message`。**确定性可重放**（不依赖 user-side LLM 假装演噪声）。

`/chat` 端点忽略这 4 个字段（dispatcher 文档明说）—— 所以 Phase D smoke 不会触发噪声。

## 完整请求体 schema（/chat）

```json
{
  "runtime": {
    "max_turns": 20,
    "start_agent": "assistant",
    "min_messages_before_end_check": 6,
    "timeout_seconds": 180
  },
  "assistant": {
    "provider": "openai",
    "model": "qwen-plus",
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "llm_api_key": "sk-...",
    "network": {"mode": "direct"},        // 国内（DashScope / 智谱 / DeepSeek / Kimi / 自部署）
    "request": {
      "temperature": 0.7,
      "top_p": 0.9,
      "max_tokens": 280
    },
    "system_prompt": "<round-NN/prompt.md 内容>",
    "greeting": "<Q6 用户给的开场白>"
  },
  "user": {
    "provider": "openai",
    "model": "gpt-5-chat-latest",
    "llm_base_url": "https://api.openai.com/v1",
    "llm_api_key": "sk-proj-...",
    "proxy": true,                         // 海外（OpenAI / Anthropic / Gemini 等）
    "request": {
      "temperature": 0.85,
      "top_p": 0.9,
      "max_tokens": 220
    },
    "system_prompt": "<persona.prompt + asr_noise 噪声块>",
    "greeting": "<复用 assistant 同句作占位>"
  },
  "end_checker": {
    "provider": "openai",
    "model": "qwen-flash",
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "llm_api_key": "sk-...",
    "network": {"mode": "direct"},
    "request": {
      "temperature": 0,
      "top_p": 1,
      "max_tokens": 120
    },
    "system_prompt": "你负责判断这通电话是否应该结束。只能返回严格 JSON。",
    "end_description": "<Phase C 后 Suggester 生成 + 用户确认>"
  },
  "verbose": false
}
```

**/simulation 比 /chat 多一个顶层字段**：

```json
{
  "count": 5,           // 该 persona 跑 5 通
  "runtime": {...},
  "assistant": {...},
  "user": {...},
  "end_checker": {...}
}
```

## 海外/国内判定（决定 `network` vs `proxy`）

每个角色块（assistant / user / end_checker）根据 `llm_base_url` 自动判定，结果直接写进该块。

**海外白名单**（命中 → 加 `"proxy": true`，**不写 `network` 字段**）：

```python
OVERSEAS_DOMAINS = {
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.cohere.com",
    "openrouter.ai",
    "api.together.xyz",
    "api.x.ai",
    "api.mistral.ai",
    "api.deepinfra.com",
    "api.fireworks.ai",
}
```

**未命中**（DashScope / 智谱 / DeepSeek / Kimi / 硅基流动 / 自部署 IP / localhost）→ 国内 → 加 `"network": {"mode": "direct"}`（**显式写**，不省略）。

**Helper 在 `scripts/network_mode.py`**：

```python
from urllib.parse import urlparse

def is_overseas(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in OVERSEAS_DOMAINS)

def role_network_block(base_url: str) -> dict:
    """Return the block to merge into the role: {'proxy': True} OR {'network': {'mode': 'direct'}}."""
    return {"proxy": True} if is_overseas(base_url) else {"network": {"mode": "direct"}}
```

## 字段配置等级

### Tier 1 — 必填（intake 必问）

| 字段 | 来源 | 说明 |
|---|---|---|
| HTTP `x-access-token` header | Q0-B | dispatcher 鉴权 |
| `assistant.system_prompt` | round-NN/prompt.md | 被测 prompt |
| `assistant.greeting` | Q6 | agent 开场白 |
| `user.system_prompt` | persona.prompt + ASR 噪声 | persona 行为定义 |
| `end_checker.end_description` | Phase C 生成 + 用户确认 | 何时停止 |
| `*.llm_api_key` × 3 角色 | Q3 | 3 个角色必须有 key |
| `*.model` × 3 | Q3 | 模型名 |
| `*.provider` + `*.llm_base_url` × 3 | Q3 | provider + URL |
| `*.network` 或 `*.proxy` × 3 | 自动判 + 用户确认 | 国内 direct / 海外 proxy |

### Tier 2 — 高级可调

| 字段 | 默认 | 何时调高 | 何时调低 |
|---|---|---|---|
| `runtime.max_turns` | 20 | 复杂多步骤业务（30-40） | 短 FAQ（8-12） |
| `runtime.start_agent` | "assistant" | 外呼场景 | "user" 内接客服 |
| `runtime.min_messages_before_end_check` | 6 | 长流程（10-12） | 简短任务（4） |
| `runtime.timeout_seconds` | 180 | 服务端拥堵（300） | 快速验证（120） |
| `assistant.request.temperature` | 0.7 | 测多样性（0.9） | 测严格遵循（0.3） |
| `user.request.temperature` | 0.85 | 测 agent 抗变化（1.0） | 锁定 persona（0.5） |
| `end_checker.request.temperature` | 0 | **永远 0**——别调 | — |
| `*.request.max_tokens` | 280/220/120 | agent 输出更长（500） | 输出更精简（150） |

### Tier 3 — 默认即可

| 字段 | 默认 | 调它的极端场景 |
|---|---|---|
| `verbose` | false | 调试时 true 看更多 response 字段 |

## GPT-5 / reasoning 模型字段差异

不同模型对 `request` 里的字段有不同要求：

| 模型 | `max_tokens` | `max_completion_tokens` | `enable_thinking` |
|---|---|---|---|
| qwen-plus / qwen-flash / qwen-max | ✓ | — | — |
| qwen3-* / qwen3.6-* （thinking） | ✓ | — | 必须 `false`（否则 thinking 链吃光 token） |
| gpt-4o / gpt-4o-mini / gpt-4.1 | ✓ | — | — |
| gpt-5-chat-latest / gpt-5.1-chat-latest | ✓ | — | — |
| gpt-5 / gpt-5.x (非 chat-latest) | ✗ | ✓ | — |
| gpt-5*-pro / gpt-5.5-pro | **不能走 chat 端点** | — | — |
| claude-opus-4-7 / claude-haiku-4-5 (Anthropic API) | ✓（通过 OpenAI-compat 层）/`max_tokens` 原生 | — | — |

skill 在 Q3 收到 model 字段后，按这张表自动选字段名，并在请求体里写正确的字段。**用户给的 max_tokens 数值**会同时被映射到 `max_tokens` 或 `max_completion_tokens`（按 model 决定）。

## 响应 schema

### POST /chat 同步返回

```json
{
  "chat_id": "uuid",
  "worker_id": "...",
  "status": "queued",
  "created_at": "2026-05-14T16:07:10Z"
}
```

### GET /chat/<id> 轮询返回（终态）

```json
{
  "chat_id": "uuid",
  "worker_id": "...",
  "status": "succeeded",         // succeeded | failed | timeout（不是 completed！）
  "created_at": "...",
  "started_at": "...",
  "finished_at": "...",
  "result": {
    "history": [
      {
        "role": "assistant",      // 转 transcript 时映射成 "agent"
        "content": "您好。",
        "metrics": {"source": "greeting"}
      },
      {
        "role": "user",
        "content": "你们卖什么车？",
        "metrics": {
          "source": "openai",
          "ttfb_ms": 1433,
          "latency_ms": 1433,
          "input_tokens": 22,
          "output_tokens": 18,
          "total_tokens": 40
        }
      },
      ...
    ],
    "stop_reason": "Reached max_turns.",     // 或 "Ended by end_checker." / 错误描述
    "turns_used": 4,
    "started_role": "assistant",
    "ended_by_checker": false,
    "usage": {
      "conversation": {"input_tokens": 137, "output_tokens": 92, "total_tokens": 229},
      "end_checker": {"input_tokens": 162, "output_tokens": 27, "total_tokens": 189},
      "total": {"input_tokens": 299, "output_tokens": 119, "total_tokens": 418}
    }
  }
}
```

**关键字段映射**（旧 → 新）：

| skill v1/v2 假设 | 实际（v3 dispatcher） |
|---|---|
| `status: "completed"` | `status: "succeeded"` |
| `messages` 顶层数组 | `result.history` |
| `n_turns` | `result.turns_used` |
| `error: null/<str>` | `status == "failed"` + `result.stop_reason` 或顶层 `error` |

### GET /simulation/<id> 轮询返回

按 dispatcher 实现，可能是：
- 同 /chat shape，但 `result.history` 替换为 `result.chats[].history`（每通一个 entry）
- 或直接返回 N 个 chat_id，逐个 GET

skill 兼容两种 shape：先看 `result.chats`，再看 `result.history`。

### 失败终态

```json
{
  "chat_id": "...",
  "status": "failed",
  "error": "signal: killed",      // 或 "worker exited unexpectedly" / openai 拒绝原因
  "result": {
    "stop_reason": "...",
    ...
  }
}
```

常见 error：
- `signal: killed` / `worker exited` → 服务端 worker 进程被杀（撞 worker_timeout 概率最高）
- `openai error: ...` / `dashscope error: ...` → LLM 返回的错误（key 错 / model 错 / 字段错）
- `decode request body` → schema 不对

## ASR 噪声注入位置

`user.system_prompt` = persona 原 prompt + ASR 噪声指令块（如果 persona.asr_noise != "none"）。

注入由**客户端**拼，**不走 HTTP body 字段**。详 `persona-sources.md`。

## end_description 自动生成模板

Phase C 抽 criteria 后，Suggester 看 prompt + 场景描述输出 end_description。模板：

```
满足以下任一条件时，结束通话：
1. <从 prompt 抽的"完成"触发，如"信息确认+尾号确认"完成>
2. <从 prompt 抽的"拒绝"触发，如"明确拒绝/没意向"+ agent 礼貌结束>
3. 任意一方说"再见"或明显结束话术。
继续通话除此之外。

返回严格 JSON：{"should_end": true/false, "reason": "<一句话>"}
```

具体例子见旧版本本文档（外呼销售/售后客服/教育辅导 3 个场景模板未变）。

## 客户端轮询节奏（Phase D smoke + Phase E 主跑）

- POST 后立即拿 `chat_id`
- 等 `poll_initial_delay` 秒（默认 3s）再开始 GET
- GET 间隔 `poll_interval_sec` 秒（默认 3s）
- 总等待时长上限 `poll_max_total_sec` 秒（默认 1800s）—— 这是**客户端**的上限，不是服务端
- 终态 `status: succeeded` / `failed` / `timeout` → 停止轮询，记录结果
- 中间态 `status: queued` / `running` → 继续轮询

## 客户端 vs 服务端 timeout

**两件事**，别混：

| | 客户端轮询 timeout | 服务端 worker timeout |
|---|---|---|
| 谁定的 | skill 客户端 | dispatcher 维护者 |
| 配置位置 | `concurrency.poll_max_total_sec`（workspace） | `Q0-C worker_timeout`（dispatcher 端） |
| 触发动作 | 客户端放弃轮询，标记 timeout | 服务端杀 worker 进程，返回 `signal: killed` |
| 默认 | 1800s | 120s |
| 单 turn 估算超过此值的 70% 时 | — | 主动警告（skill 在 Phase D 末尾做） |

---

## /evaluation_batch 完整 schema（pro 版核心，prompt-lab 没有）

### POST /evaluation_batch — 请求体

```json
{
  "count": 3,
  "target": {
    "history": [
      {"role": "assistant", "content": "您好，这边是..."},
      {"role": "user", "content": "嗯，你说"},
      {"role": "assistant", "content": "..."},
      "..."
    ],
    "system_prompt": "<被测 agent 当时用的 prompt 全文>",
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen-plus"
  },
  "evaluator": {
    "provider": "openai",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen-plus",
    "api_key": "sk-...",
    "prompt": "<本地拼好的 evaluator_prompt.md + per-transcript persona + auto_check + TASK>",
    "network": {"mode": "direct"},
    "request": {
      "temperature": 0,
      "top_p": 1,
      "max_tokens": 800
    }
  },
  "timeout_seconds": 120,
  "output_schema": { /* 见 scripts/judge_via_dispatcher.py:JUDGE_OUTPUT_SCHEMA */ },
  "verbose": false
}
```

### 顶层字段语义

| 字段 | 必填 | 说明 |
|---|---|---|
| `count` | ✅ | self-consistency 次数。同一条 transcript × 同一份 evaluator 跑 N 次，client 端取众数 |
| `target.history` | ✅ | 被评的 transcript（assistant/user 交替的 `[{role, content}]`） |
| `target.system_prompt` | ⬜ | 让 evaluator 知道 agent 当时遵循的是什么指令（强烈建议带，不然评分会发散） |
| `target.llm_base_url` / `target.model` | ⬜ | 元数据，记录用 |
| `evaluator.{provider,base_url,model,api_key,prompt}` | ✅ | 5 个必填；prompt 是 evaluator 的 system prompt |
| `evaluator.network` 或 `evaluator.proxy` | ⬜ | 国内 direct / 海外 proxy=true（与 /chat 一致） |
| `evaluator.request.*` | ⬜ | temperature 建议 0；max_tokens 视 output_schema 复杂度，本 skill 默认 800 |
| `output_schema` | ✅ | **服务端强制**输出符合 schema 的 JSON。schema 不对 → evaluation 失败 |
| `timeout_seconds` | ⬜ | 单次 evaluation 超时；0 = 无超时 |
| `verbose` | ⬜ | 排查用 |

### 响应（同步返回）

```json
{
  "evaluation_batch_id": "uuid",
  "worker_id": "...",
  "status": "queued",
  "count": 3,
  "created_at": "..."
}
```

### GET /evaluation_batch/{id} — 状态

返回字段类似 simulation：`status` / `pending` / `running` / `succeeded` / `failed` / `cancelled` / `result_ready` / `result_path` / `result_url`。

终态：`succeeded` / `failed` / `partial_failed` / `cancelled`。

### GET /evaluation_batch/{id}/result — 结果

返回每次 evaluation 的输出数组（schema 因 dispatcher 实现而异；`judge_via_dispatcher.py` 尝试三种 key：`evaluations` / `results` / `items`）：

```json
{
  "evaluation_batch_id": "uuid",
  "evaluations": [
    {"output": { /* 符合 output_schema 的 JSON */ }, "...": "..."},
    {"output": { /* 第 2 次 */ }, "...": "..."},
    {"output": { /* 第 3 次 */ }, "...": "..."}
  ]
}
```

### Self-consistency 聚合（client 侧，脚本内置）

`scripts/judge_via_dispatcher.py:aggregate_self_consistency()`：

- 数值维度（cf / asr / nat）：**中位数**
- `goal_statuses` per goal：**多数票**，tie-break 偏保守（none > partial > done）
- `hard_fails`：**并集**（任一次说有就当有 — 保守原则）
- `subjective_violations`：**出现 ≥ 半数次**的 rule 才采纳（过滤 LLM 抖动）
- `conversation_flow_notes` / `bad_case_summary`：取第一份有内容的

### 失败排查

| 现象 | 可能原因 |
|---|---|
| HTTP 400 `output_schema invalid` | schema JSON 写错（脚本里写死，理论上不该出） |
| 所有 evaluation `output` 为字符串而非 JSON | `evaluator.prompt` 没强调 "no code fences / no commentary"；或 model 不遵守 schema（换更强 model） |
| `partial_failed` | 某次 evaluation 超时或 LLM 拒答；`judge_via_dispatcher.py` 用 raws 数量 < count 仍能聚合 |
| `n_err / n_jobs > 20%` | 脚本退出码 4。检查 dispatcher 健康度 + evaluator api_key + evaluator base_url |

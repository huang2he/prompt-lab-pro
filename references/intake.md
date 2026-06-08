# Phase A · Intake（多轮交互，只问必需）

> prompt-lab-pro 版本。继承 prompt-lab v3.4 流程，**两处升级**：
> 1. **Q2 ASR 噪声**：映射到 dispatcher 原生字段（`user.silence_rate` / `silence_message` / `asr_failure_rate` / `asr_failure_message`），不再嵌 persona 描述
> 2. **Q3-D Judge**：必须**远端 dispatcher 走 /evaluation_batch**（不再支持 inline Claude / subagent dispatch），Q3-D 多收一些字段做 ensemble

skill 进入 Phase A 时按这个模板**一个一个**问。用户答完显示一句简短确认再进下一问。**只问必需的；高级参数（max_turns / temperature / timeout 等）走默认值，问完所有必需后再统一问"要不要调高级参数？"**

---

## A.-1（仅 Claude Code 宿主）· Permission preflight

**触发条件**：宿主是 Claude Code。其它宿主（Codex CLI / Cursor / OpenClaw / 自研 agent）按各自 permission 模型处理，本 skill 跳过本节。

**为什么需要**：本 skill 通过 dispatcher 转发 LLM API key（dispatcher 设计要求 key 内联 HTTP body）。Claude Code 的 auto-mode safety classifier 会把"key 流向非 LLM 官方域名"识别为数据外泄并 **HARD BLOCK**。且 **skill 自己不能改 `~/.claude/settings.json`**（系统硬性禁止）。

**v3.4 起 URL 已写死**：host 直接从 `scripts/load_dispatcher.py` 的 `DEFAULT_URL` 取（默认 `47.100.137.178`，env var 可覆盖）。

**操作**：先跑解析拿 host，再生成 allowlist 命令模板：

```bash
# skill 内部跑（不让用户看见复杂解析）
HOST_PORT=$(python3 -c "from urllib.parse import urlparse; import subprocess, json; r=json.loads(subprocess.check_output(['python3','$HOME/.claude/skills/prompt-lab/scripts/load_dispatcher.py','--json']).decode()); print(urlparse(r['url']).netloc)")
HOST="${HOST_PORT%%:*}"
```

然后给用户复制粘贴：

```
! python3 -c "
import json, os
p = os.path.expanduser('~/.claude/settings.json')
s = json.load(open(p)) if os.path.exists(p) else {}
s.setdefault('permissions', {}).setdefault('allow', [])
rules = [
    'Bash(curl * <HOST_PORT>*)',
    'Bash(python3 * <HOST>*)',
]
added = []
for r in rules:
    if r not in s['permissions']['allow']:
        s['permissions']['allow'].append(r); added.append(r)
json.dump(s, open(p,'w'), indent=2)
print('added:', added if added else '(nothing new, already allowed)')
"
```

把 `<HOST_PORT>` 替换为 baked-in `47.100.137.178:8080`，`<HOST>` 替换为 `47.100.137.178`（若用户用 env var 覆盖了 URL，则替换为覆盖后的 host）。

用户跑完显示 `added: [...]` 或 `nothing new`，即可继续。**不要让 Claude 自己跑 Edit 工具改 settings.json**——系统会硬拦，跑也跑不通，徒增噪声。

---

## Q0-A — ~~dispatcher URL~~（v3.4 起撤销）

**v3.4 改动**：URL 已写死在 `scripts/load_dispatcher.py:DEFAULT_URL`（当前值 `http://47.100.137.178:8080`）。

**为什么写死**：
- 共享 skill 时 dispatcher 是公司内部资源，没必要每次让用户重输
- 减少拼写错误（端口/协议/末尾斜杠都是踩坑点）
- 多人协作时统一指向同一实例

**怎么改 URL**（罕见情况）：
- **临时切实例**：`export PROMPT_LAB_DISPATCHER_URL=http://new-host:port`
- **长期切实例**：改 `scripts/load_dispatcher.py` 的 `DEFAULT_URL` 常量
- **多实例并存**：在每个 workspace 的 `config.json` 顶层加 `remote_server` 字段，覆盖解析结果（向下兼容旧 workspace）

## Q0-B — dispatcher access_token（**首次必填**，之后自动复用）

**进入 Phase A 时第一件事**：跑解析脚本判断是否需要问：

```bash
python3 ~/.claude/skills/prompt-lab/scripts/load_dispatcher.py --json
```

返回 JSON 含 `missing` 数组：

| missing | 含义 | 处理 |
|---|---|---|
| `[]` | URL + token 都齐 | 跳过 Q0-B，直接进 A.-1 / A.0 |
| `["token"]` | URL 有（baked-in），token 缺 | 问 Q0-B，下方流程 |
| `["url", "token"]` | 不应发生（有 baked-in default） | 报 bug |

**Ask Q0-B（仅 missing 含 "token" 时）**:
> "dispatcher 需要 access_token 鉴权（HTTP header `x-access-token`），首次跑要配一次。从 dispatcher 维护者拿一个，贴给我。
>
> 我会把它写到 `~/.claude/skills/prompt-lab/.env`（chmod 600，只你能读），后续会话自动复用，不再问。"

**拿到 token 后**：
```bash
python3 ~/.claude/skills/prompt-lab/scripts/load_dispatcher.py --save-token <TOKEN>
```

预期返回 `saved: /Users/.../.env`。然后立刻跑 A.0 healthz 验证（用刚存的 token）。

**healthz 失败时的回退**：
- 401 `invalid access token` → token 错。**不要重写 .env**，先问用户再确认一次；如果用户说"确实是这个 token，是 dispatcher 端的问题" → 让用户去找维护者；如果用户给新 token → 重跑 `--save-token <新值>` 覆盖
- 网络错 / 404 → 检查 URL（baked-in 是否过期？联系维护者）

**Hard rule**：token 必填，**写到 `.env` 而非 config.json**（config.json 在每个 workspace 下，token 应该全局复用）。runtime 跑批时 `scripts/run_round.py` 读 workspace config.json 拿 token，**Phase B 写 config.json 时把解析到的 token 复制进去**（保证现有 run_round.py 不用改）。

**注意**：dispatcher access_token 跟 LLM provider 的 API key（A/B/end_checker 的 `llm_api_key`）不是同一回事。前者认证 dispatcher 客户端身份，后者由 dispatcher 转发给 OpenAI/DashScope 等。

## Q0-C — ~~询问 worker_timeout~~（v3.1 起取消）

**v3.1 起不再询问**。原因：

- 服务端 worker 超时这个数值，**用户大概率不知道**（dispatcher 维护者没主动公开）
- 同事 bug 报告里的"120s"其实是从失败时长（"恰好 2 分钟被杀"）**反推**出来的，没人正式告诉过我们
- 不同 dispatcher 实例 / 不同版本可能数值不同

**v3.1 改成 Phase D smoke 实测 + 绝对阈值告警**：

- skill 不预先假设 timeout 数值
- smoke 跑完，从 `metrics.latency_ms` 读单 turn 实测耗时
- `max(latency_ms across turns) > 30000`（30 秒绝对阈值）→ 警告用户："实测单 turn X ms 偏高，接近常见 dispatcher worker_timeout 范围（60-300s），主跑时可能撞 `signal: killed`"
- 同时**主会话 Claude 用 WebSearch 查**：`<model_name> disable thinking reasoning chain` → 看该模型是否支持关 reasoning，给用户具体建议
- 用户拿到警告后选择：换 non-reasoning model / 降 max_tokens / 关 reasoning 开关 / 问维护者真实 timeout

### Q0 之后立即跑 **A.0 healthz 探测**（带 token）

```bash
curl -s --max-time 5 -H "x-access-token: <token>" <url>/healthz
```

预期 → `{"status": "ok"}`

失败处理：
- 401 `invalid access token` → Q0-B 的 token 错，重填
- 404 → URL 拼错，重填
- 网络/拒连接 → 让用户检查 URL + 服务是否在跑 + 防火墙
- 5xx → 服务挂，让用户先修

通过 → 进 Q1。

## Q1 — 基准 prompt

**Ask**:
> "把要优化的基准 prompt 给我。可以粘贴文本或给文件路径。"

**接收**：多行文本 / 绝对路径 / `~/...` 路径
**处理**：路径 → Read 工具读出来；显示字数 + 估 token + 头 5 行预览；确认无误

**Hard rule**：prompt 非空。

**注**：此时显示 token 数 + 一句温和提示"暂不设上限，跑完一轮看具体数量后你可以再决定是否要限"。**不强制问 token_ceiling**。

## Q2 — 测试集（persona）来源

**Ask（用 AskUserQuestion）** 三选一：
- (a) 我已有 persona JSON
- (b) 从 Q1 prompt 自动抽 persona（默认推荐）
- (c) 从过去真实 transcripts 抽 persona

分支处理详见 `persona-sources.md`。

**然后追问 ASR 噪声**（同一 AskUserQuestion 第 2 题）：
- (i) 不加（默认）
- (ii) 全 light / (iii) 全 moderate / (iv) 全 heavy
- (v) 按 tier 分配（stretch heavy / core light / gate none）

### ASR 噪声映射（pro 版独有）

prompt-lab-pro 把 noise level 映射到 dispatcher 原生扰动字段，**dispatcher 服务端按概率确定性插入**（每轮可重放，不再依赖 user-side LLM 假装演噪声）：

| level | `silence_rate` | `asr_failure_rate` |
|---|---:|---:|
| `none`     | 0.00 | 0.00 |
| `light`    | 0.05 | 0.08 |
| `moderate` | 0.10 | 0.20 |
| `heavy`    | 0.20 | 0.40 |

两个 message 字段从 `config.json` 顶层读，没设走默认中文：
- `silence_message` 默认：`"请确认对方是否还在听（如：喂？还在吗？）"`
- `asr_failure_message` 默认：`"请对方重复刚才那句，理由是没听清/信号不好"`

**这 4 个字段只在 `/simulation` 生效，`/chat` 不应用**——所以 Phase D smoke（走 /chat）不会触发噪声，到 Phase E 主跑（走 /simulation）才生效。

代码位置：`scripts/run_round.py:asr_noise_block()`。

**配置 silence_message / asr_failure_message 的中英文切换**：场景是英文外呼时，在 Phase A 高级参数处或事后改 `config.json` 加：
```json
{
  "silence_message": "Could you still hear me?",
  "asr_failure_message": "Sorry, could you repeat that? The line wasn't clear."
}
```

**persona 字段保留 `asr_noise: light|moderate|heavy|none`**——映射在 runtime 做，schema 不变。

## Q3 — 模型配置（**A/B/end_checker 必填 key**，每角色自动判海外/国内）

**重要前置说明**：
> "对话过程涉及 3 个远端模型，**全部 inline 进 HTTP 请求体**（不是 env var），**都必须有 API key**。如果用同一家服务商（如 DashScope）一个 key 可以三个角色都用。
>
> 我会根据你给的 base_url 自动判断模型是**海外**（OpenAI/Anthropic/Gemini）还是**国内**（DashScope/智谱/DeepSeek/Kimi 等），然后请求体里相应加 `proxy: true` 或 `network.mode: direct`。判错了你可以纠正。"

### Q3-A: Agent A（被测主体）
> "被测的 agent 模型？"
- provider（默认 openai）+ model name（如 qwen-plus）+ base_url（默认 DashScope `https://dashscope.aliyuncs.com/compatible-mode/v1`）+ **API key**
- **自动判海外/国内**（见下方"海外判定"章节）→ 显示给用户确认

### Q3-B: Agent B（persona 一侧）
> "模拟客户的 persona 模型？通常用更便宜的，如 qwen-flash。"
- 同上 4 字段
- 若用户说"和 A 一样"：复用 A 的所有字段 + 同样的 network 设定
- 通常 model 不同（A 用 qwen-plus，B 用 qwen-flash 省钱）；**也可能不同 provider**（如 A 用 DashScope，B 用 OpenAI）—— 这时 network 字段会一个 direct 一个 proxy

### Q3-C: end_checker（判断对话是否该停）
> "end_checker 是个判断对话该不该结束的小模型。可用 cheap model（qwen-flash）。"
- 同上 4 字段
- 若用户说"和 A 一样"：复用

### Q3-D: Judge（评分模型）— pro 版强制远端 + /evaluation_batch

**pro 版收窄**：Judge **必须远端**，走 dispatcher `/evaluation_batch` 端点（不再支持 inline Claude / subagent dispatch）。

> "评分模型必须远端，走 dispatcher 的 /evaluation_batch（服务端并发跑分，比本地 subagent 快 ~2.6×、不撞 rate limit、可做 ensemble + self-consistency）。
>
> 选一个模型当 Judge（推荐 qwen-plus / gpt-4o-mini / claude-haiku 这类便宜的；评分场景不需要顶级模型）：
>   - provider, base_url, model, api_key
>
> 要做 **multi-evaluator ensemble**（同 transcript 让两个 model 都评一次，分歧大的样本人审）吗？默认不开。"

落到 `config.json` 顶层：
```json
{
  "judge_evaluator": {
    "provider": "openai",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen-plus",
    "api_key": "sk-...",
    "temperature": 0,
    "max_tokens": 800,
    "timeout_seconds": 120
  }
}
```

若用户要 ensemble：用 `judge_evaluators` (数组) 替代 `judge_evaluator` 单值。`scripts/judge_via_dispatcher.py` 自动识别。

**self-consistency**：调用脚本时 `--count-self-consistency` 默认 3（每条 transcript × 每个 evaluator 跑 3 次取众数）。可在主会话执行 Phase E.2 时按需调。

**为什么收窄**：prompt-lab v3.4 的 inline/subagent 模式撞 Claude rate limit 频繁，大样本（>30 通）整批 stall 600s+；pro 版直接走 dispatcher 服务端并发，并发上限是 dispatcher worker 池而非 Claude API。

### Q3-E: Suggester（优化 prompt 模型）
> "改进 prompt 的模型，同样可选远端或本地。建议用 Claude（写长文本最好），本地直接用主会话。"
- 同 Q3-D

### 海外判定（Q3-A/B/C/D/E 收到 base_url 后立即跑）

域名白名单（命中即海外，请求体加 `proxy: true`）：

```python
OVERSEAS_DOMAINS = {
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.cohere.com",
    "openrouter.ai",
    "api.together.xyz",
    "api.x.ai",                           # xAI / Grok
    "api.mistral.ai",
    "api.deepinfra.com",
    "api.fireworks.ai",
}
```

判定逻辑：
- 命中 → 海外 → `proxy: true`（顶层字段，不在 `network` 里）
- 未命中（DashScope / 智谱 / DeepSeek / Kimi / 自部署 IP / localhost / 内网）→ 国内 → `network: {"mode": "direct"}`
- 显示给用户确认："base_url=X 我判断为 [海外/国内]，请求体里我加 [`proxy: true` / `network.mode: direct`]，对吗？" → 用户可改

### GPT-5 / reasoning 模型注意

- `gpt-5-chat-latest` ✓ 支持 `max_tokens`，可直接用
- `gpt-5` / `gpt-5.1` / `gpt-5.2` / `gpt-5.5` 等：必须用 `max_completion_tokens`（dispatcher 已能透传）
- `gpt-5*-pro` / `gpt-5.5-pro`：**不能走 chat completions 端点**（OpenAI 返回 "not a chat model"），换 `*-chat-latest` 或不带 -pro 的版本
- **reasoning 模型（thinking 链）每轮耗时翻倍**：在 Q3 末尾、Phase D smoke 之前主动提醒用户警惕 timeout
- DashScope qwen3 系列要关 thinking：`request.enable_thinking: false`

### 收集完 5 个角色显示配置表给用户确认

```
Agent A:    qwen-plus      DashScope (direct)  sk-xxx
Agent B:    gpt-5-chat-latest  OpenAI (proxy)  sk-proj-xxx
end_checker: qwen-flash    DashScope (direct)  sk-xxx (same key as A)
Judge:      claude-opus-4-7  local
Suggester:  claude-opus-4-7  local
```

### Q3 之后立即跑 **A.3 chat 连通探测**

不依赖 persona/criteria。POST 一个最短 chat，**只要拿回 chat_id 就算通过**——不等真完成，省 LLM 费用：

```bash
curl -s --max-time 30 -X POST <url>/chat \
  -H 'content-type: application/json' \
  -H 'x-access-token: <Q0-B token>' \
  -d '{
    "runtime": {"max_turns": 2, "start_agent": "assistant", "min_messages_before_end_check": 1, "timeout_seconds": 30},
    "assistant": {
      "provider": "openai", "model": "<Q3-A model>",
      "llm_base_url": "<Q3-A base_url>", "llm_api_key": "<Q3-A key>",
      "network": {"mode": "direct"},           # 国内
      # 或 "proxy": true,                       # 海外（二选一）
      "request": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 50},
      "system_prompt": "Reply with a one-sentence acknowledgement.",
      "greeting": "Hello, this is a connectivity test."
    },
    "user": {"...同 assistant 但用 Q3-B 配置...", "system_prompt": "Reply briefly to greet back.", "greeting": ""},
    "end_checker": {"...同 Q3-C...", "system_prompt": "Return JSON only.", "end_description": "Stop after any 2 messages exchanged."},
    "verbose": false
  }'
```

预期返回：
```json
{"chat_id": "uuid", "worker_id": "...", "status": "queued", "created_at": "..."}
```

校验：
- 有 `chat_id` → 通过 ✓
- 无 `chat_id` 但有 error → 显示给用户 + 给修复提示
- 网络错 → 提示检查防火墙

**附加（可选）**：拿到 chat_id 后 GET 一次 `/chat/<chat_id>` 看 status 是 `queued` / `running` / `succeeded` 任一即可。**不要等 succeeded**——只为验证 URL+token+key+schema 通畅。

失败处理：
- HTTP 401/403 + `invalid access token` → Q0-B token 错
- HTTP 401/403 + 别的 → Q3 某个 key 错（LLM provider 拒）
- HTTP 400 + `decode request body: unknown field` → schema 不对（server 版本旧/新）
- HTTP 400 + `unsupported parameter: max_tokens` → 你用了 GPT-5 系列但没改 `max_completion_tokens`
- HTTP 503 / `no healthy workers` → 让用户去启 worker
- 长时间 queued 不动（>2 分钟）→ dispatcher worker 池可能不识别这个 model（如 `qwen3.6-plus` 经历过）—— 换 model 或联系维护者

通过 → 进 Q4。

## Q4 — 迭代轮数 N

**Ask**: "跑几轮？（默认 3）"
- 提示 token/key 消耗估算（基于 Q5 计算）
- 每轮跑完会停下来问"继续/停/微调"

## Q5 — 每 persona 每轮跑几次 K

**Ask**: "每个 persona 每轮跑几次？（默认 2，能看方差）"
- 算总 simulation 数：M（persona 数）× K × N = X 通
- POST 数：M × N（per-persona simulation，每次 count=K）
- 估算 token：粗略 ~30k/通

## Q6 — agent 开场白（greeting）

**Ask**: "外呼场景 agent 先说一句开场白。给一句具体的（如 '您好，这边是 XX 客服回访...'）。"

**处理**：
- 用户给一句 → 保存到 config.json，写到每次 /chat 的 `assistant.greeting`
- 后续每轮如果 prompt 改了开场设计，skill 会提示用户："prompt 里这次改了开场，要不要更新 greeting？"

## Q7 — 场景描述

**Ask**: "一句话描述这个 prompt 干啥（如：'外呼销售对接 4S 店报价' / '电商售后客服' / '法律咨询初筛' / '英语口语陪练'）。这句话会喂给 Suggester 生成 criteria 和 end_description。"

## Q8 — workspace 路径

**Ask**: "workspace 放哪？（默认 `~/prompt-lab-workspaces/<project_id>/`）"
- project_id 可让用户给，或自动生成（用 prompt 头几个字 + 时间戳）
- 已存在路径 → 询问"继续旧项目 / 备份后新建 / 换路径"

## 收集完后

显示一份完整配置摘要 + 让用户确认：

```
=== prompt-lab 配置摘要 ===
Project:        auto-call-20260514
Workspace:     ~/prompt-lab-workspaces/auto-call-20260514
场景:           外呼销售-汽车线索回访
Dispatcher:    http://47.100.137.178:8080 (baked-in · token: d9bP** from .env)
基准 prompt:   3716 tokens
Persona:       从 prompt 抽 20 条，全 moderate ASR 噪声
Agent A:       qwen-plus           DashScope (direct)  sk-xxx
Agent B:       gpt-5-chat-latest   OpenAI (proxy)      sk-proj-xxx
end_checker:   qwen-flash          DashScope (direct)  sk-xxx (same as A)
Judge:         claude-opus-4-7    本地
Suggester:     claude-opus-4-7    本地
Greeting:      "您好，这边是新车销售线索回访..."
迭代:          3 轮 × 每 persona 2 次 = 总 ~120 通
Token 上限:    暂未设（跑完第一轮后会提示是否需要限）

⚠️ 主观预警：B 是 OpenAI（proxy）+ 没有 reasoning，单 turn 估 ~1.5s；
   单通 ~25s（max_turns=20 算上限），远低于 worker_timeout 120s ✓

要调高级参数吗？（max_turns / temperature / timeout / token_ceiling 等）
  - 不调 → 用默认开跑
  - 调 → 逐项问

确认？(yes / 改 X)
```

## 高级参数（用户选"要调"时才问）

详见 `api-call-params.md`。常见 4 个：

- **max_turns**: 默认 20。短场景（FAQ 1 轮）改 8；长场景（多步骤）改 30
- **temperature**: A=0.7 / B=0.85 / end_checker=0. **end_checker 必须 0**（否则停得很随机）
- **timeout_seconds**: 默认 180（远端单 chat 超时）—— 客户端轮询用，不是服务端 worker 超时
- **token_ceiling**: 默认 null（不限制）

## 自动生成 end_description

**不在 intake 阶段问用户**。等 Phase C 抽 criteria 完，Suggester 会基于 prompt + 场景描述自动生成一份 end_description，然后显示给用户确认/修改。详见 `api-call-params.md` 的 end_description 模板。

---

## Hard rules during intake

- **Q0-B token 缺失**（load_dispatcher 报 `missing: ["token"]` 且用户拒绝提供）→ 不能进入 Q1（v3.4：Q0-A 已撤，URL 有 baked-in default）
- **Q3 三个 key (A/B/end_checker) 任一缺失** → 不能进入 Phase B（key 是 HTTP body 必填）
- **A.-1 Claude Code allowlist 未加** → A.0 healthz 探测会被 auto-mode 拦，直接报"Denied by auto mode classifier"。skill 检测到这个错误立刻回到 A.-1 步骤
- **persona < 5 条** → 警告"样本太少分数不稳"，但允许继续
- **token 估算超过用户设的上限** → 提示，让用户确认或减 K/N

---

## 撞 timeout 怎么办（故障树）

Phase D smoke 拿到 `status: failed` + `error: signal: killed` / `timeout` → 按以下顺序排查：

1. **看是 client 还是 server 超时**：
   - 如果客户端 fetch 自己 timeout（请求都没发出去）→ 检查网络
   - 如果 dispatcher 返回 `status: failed, error: signal: killed` / `worker exited` → 服务端 worker 杀进程

2. **服务端 worker 杀进程的常见原因**（按概率排）：
   - **reasoning 模型 + 大 max_tokens**：gpt-5 / gpt-5.x / qwen3-thinking 等单轮就 1-2 分钟。**降 max_tokens 到 200-500**，或换非 reasoning 模型（qwen-plus / gpt-5-chat-latest 等）
   - **prompt 太长 + max_tokens 巨大**：3K input + 4K output 在弱模型上要 90s+。**拆 prompt** 或 **降 max_tokens**
   - **dispatcher worker_timeout 设得太小**：联系维护者改大（默认 120s 可调到 300s）

3. **不要直接重试**：先估算单 turn 耗时 vs worker_timeout，预测会不会再撞。skill 在 smoke 完成后已经把 `metrics.latency_ms` 读出来展示给用户，根据这个判断。

4. **smoke 阶段就要发现这件事**：Phase D 拿到第一通 transcript，立刻把 `max(latency_ms)` 跟 `worker_timeout × 0.7` 比较：
   - 超过 → 警告并停下，问用户调什么
   - 未超 → 进 Phase E 主跑

详细日志解读见 `references/scoring-pipeline.md` 的失败处理章节。

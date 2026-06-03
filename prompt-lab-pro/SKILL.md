---
name: prompt-lab-pro
description: Dispatcher-native upgrade of prompt-lab. Same 6-phase eval-improve-eval SOP and v3 rubric (5 dimensions incl. conversation_flow), but two surgical upgrades — (1) Phase E.2 评分走 dispatcher /evaluation_batch（本地拼 evaluator.prompt + dispatcher 服务端并发执行 + self-consistency + multi-evaluator ensemble），不再用 inline Claude 或 6-subagent dispatch；(2) Phase E ASR 噪声直接映射到 dispatcher 原生字段 user.silence_rate/silence_message/asr_failure_rate/asr_failure_message，dispatcher 按概率确定性插入扰动，不再嵌 persona 描述让 user-LLM 假装演噪声。Use when iterating a chat / voice / outbound-call prompt and you want fast (~2.6× vs prompt-lab) + reliable (no Claude rate limit) + reproducible (dispatcher-driven ASR noise) scoring, ESPECIALLY for ≥30 transcripts per round. Trigger phrases include "用 prompt-lab-pro", "上 pro 版", "走 evaluation_batch", "dispatcher 评分", "ensemble judge", "self-consistency 评分". Do NOT use for one-shot prompt edits, real-call audio analysis, or when dispatcher is unreachable (fallback to prompt-lab v3.4 inline mode).
---

> **关系说明**：本 skill 继承 `prompt-lab` v3.4 的 6 阶段 SOP（A→F）+ rubric v3（5 维度含 conversation_flow）+ 三档分类 + capability map + Case A-D Round-to-Round 决策树。**所有 references 大部分 symlink 共享**，只 fork 这 3 个：`intake.md` / `scoring-pipeline.md` / `api-call-params.md`。
>
> **核心差异 vs prompt-lab v3.4**：
> 1. **Phase E.2 评分链路重写** — 不再有 inline / subagent 二选一，强制走 dispatcher `/evaluation_batch`。**本地 Claude 主会话只负责拼 evaluator.prompt（思考工作）**，dispatcher 负责 fan-out 执行（执行工作）。
> 2. **Phase E ASR 噪声机制** — `persona.asr_noise` 等级映射到 dispatcher 原生扰动字段（`user.silence_rate` / `silence_message` / `asr_failure_rate` / `asr_failure_message`），不再让 user-side LLM 演噪声。
>
> **何时用 prompt-lab-pro（vs 上游 prompt-lab v3.4）**：

| 场景 | 选哪个 |
|---|---|
| 首次跑 prompt 优化、想学方法论 | `prompt-lab` |
| **大样本评分**（≥30 通） | **prompt-lab-pro** |
| **多 evaluator ensemble**（cross-model 评分对照） | **prompt-lab-pro** |
| **要求 self-consistency**（同 transcript 评 3 次取众数） | **prompt-lab-pro** |
| **ASR 鲁棒性测试要可重放** | **prompt-lab-pro** |
| dispatcher 不可达 / 仅本地评分 | `prompt-lab`（inline 兜底） |

> **Disclaimer**：本 skill 仍是通用框架；具体 criteria / persona / rubric 权重等业务参数每个项目自抽。pro 版升级的是**评分链路 + 噪声机制**，不是评分维度本身（5 维 + 1-5 制 + hard_fails 6 类闭枚举与 prompt-lab v3.4 完全一致）。

---

# prompt-lab-pro

继承 prompt-lab v3.4 的端到端提示词迭代 SOP，把"评分"和"ASR 噪声"两件事彻底交给 dispatcher。

## SOP 一图概览

```
Phase A  介绍 + 输入收集（继承 prompt-lab v3.4）
         ├─ A.-1 ★ Claude Code permission preflight（host = baked-in URL）
         ├─ A.0  load_dispatcher 自动解析 → healthz 探测（token 首次问，落 .env）
         ├─ Q0-B dispatcher access_token（仅首次需要）
         ├─ Q1 基准 prompt
         ├─ Q2 测试集来源 + ASR 噪声  ← pro 改：映射到 dispatcher 原生字段（不再嵌 persona）
         ├─ Q3 5 角色模型配置
         │    └─ Q3-D Judge ★ pro 改：必须远端，落 judge_evaluator 字段；可选 ensemble (judge_evaluators[])
         ├─ Q3 → A.3 chat 连通探测
         ├─ Q4-Q8 N 轮 / K / greeting / 场景 / workspace 路径
   ↓
Phase B  建立 workspace + 落盘 config.json
         （pro 改：config.json 顶层多 judge_evaluator + silence_message + asr_failure_message）
   ↓
Phase C  抽 criteria → ★ 用户签字（同 prompt-lab）
   ↓
Phase D  远端 smoke probe（同 prompt-lab，走 /chat；噪声字段不生效是预期）
   ↓
Phase E  主循环 × N 轮
         ├─ E.1   run /simulation per persona（ASR 噪声 4 字段在此生效 ★）
         ├─ E.1.5 加 3-5 新 persona 扩边（同 prompt-lab）
         ├─ E.2 ★★ 评分 — 双步骤（pro 核心改造）
         │    ├─ E.2a 本地 Claude 拼 evaluator_prompt.md → ★ gate
         │    └─ E.2b python3 judge_via_dispatcher.py（dispatcher 并发跑）
         ├─ E.3   ★ 显示分档结果（稳定 ≥95% / 不稳定 40-95% / 做不到 <40%）
         ├─ E.4   生成 suggestions（同 prompt-lab）
         ├─ E.5   应用到 round-(K+1) prompt（同）
         ├─ E.6   ★ 显示 prompt diff
         └─ E.7   ★ 用户决定下步
   ↓
Phase F  收尾 · 能力地图（同 prompt-lab）
```

★ = 用户 gate，必须等用户回应才继续。

## 设计哲学（继承 prompt-lab v3.2+ 全部）

本 skill 的本质**不是"无限优化分数"**，是在**固定的 agent A 模型下**，找出该模型 + 一份 prompt 能稳定承载的**最大指令集**。3 个推论同 prompt-lab：

1. **prompt 体积有预算**：每轮新 prompt 不应该比上一轮大 >10%
2. **end_checker 是辅助挂断工具**，不算 agent A 的能力（单独标注"end_checker 误判通数"）
3. **最终交付物是能力地图（capability map），不是单一最佳分数**

## pro 版独有：评分链路工作分配（must read）

```
                            E.2 评分
            ┌───────────────────┴────────────────────┐
            ↓                                         ↓
    ┌──────────────────┐                  ┌──────────────────┐
    │ E.2a 本地 Claude   │                  │ E.2b dispatcher  │
    │ 拼 evaluator.prompt│ ─── 字符串 ───→ │ 执行 + JSON Schema │
    └──────────────────┘                  └──────────────────┘
       ↑                                         │
       │ 思考工作                                  │ 执行工作
       │ - 读 rubric.md                            │ - fan-out N×M×count
       │ - 读 criteria.json                        │ - 任意 LLM provider
       │ - 读 failure-types.md                     │ - 不撞 Claude rate limit
       │ - 读 prompt.md                            │ - 服务端校验 schema
       │ - 选 anchor 例子                          │
       │ - 复用上一轮（如 criteria 未变）           │
       │                                          ↓
       │                          ┌─────────────────────────┐
       └──────────────────────────│ judge_via_dispatcher.py │
                                  │ self-consistency 聚合    │
                                  │ writes judgments.json    │
                                  └─────────────────────────┘
```

**用户硬要求**："生成 judge 的 prompt 这件事交给本地写"——所以 evaluator.prompt **必须**由主会话 Claude 拼装，**不**写在脚本里固化模板。

详情见 `references/scoring-pipeline.md` 的 "Layer 2 工作分配（pro 版关键设计）"章节。

## 通用约束（Hard Rules）

继承 prompt-lab v3.4 全部 hard rules，**新增 pro 版独有**：

- **Dispatcher 寻址**（同 prompt-lab）：baked-in URL `http://47.100.137.178:8080`（共享 `~/.claude/skills/prompt-lab/.env` 的 token）。改 URL 设 env var `PROMPT_LAB_DISPATCHER_URL`。
- **Judge 强制远端**：pro 版 Q3-D 不接受 `local: true`。要本地评分用上游 prompt-lab。
- **ASR 噪声必须走 dispatcher 字段**：persona.asr_noise level → `scripts/run_round.py:asr_noise_block()` → `user.silence_rate / silence_message / asr_failure_rate / asr_failure_message`。**不再**在 persona.prompt 末尾 append 噪声块。
- **E.2a 拼 evaluator.prompt 是 gate**：必须主会话 Claude 读 rubric/criteria/failure-types/prompt 后拼好，写到 `<round>/evaluator_prompt.md`，**用户预览确认**才能跑 E.2b。
- **count_self_consistency ≥ 3**：默认 3，high stakes 决策可调 5。低于 3 失去聚合意义，会被脚本警告。
- **多 evaluator 分歧 ≥ 1 档 → 人审**：ensemble 时 cf / nat / asr 三个 dim 任一在不同 evaluator 之间差 ≥ 1 档的 transcript 列入人审清单。
- 其它（prompt size budget +10% / end_checker 误判单独诊断 / rubric v3 / 5 维度 + 1-5 制 / hard_fails 6 类闭枚举 / 每个 prompt 项目独立 workspace）：**全部同 prompt-lab v3.4**。

---

# Phase A · 介绍 + 输入收集

进入 skill 第一件事：

**A.-1（仅 Claude Code 宿主）. Permission preflight**

同 prompt-lab v3.4。Host 用 baked-in URL（`47.100.137.178`）。详细脚本模板见 `references/intake.md` A.-1 章节。

**A.0 dispatcher 自动解析**：跑 `python3 ~/.claude/skills/prompt-lab/scripts/load_dispatcher.py --json`（pro 版 symlink 共享）：
- `missing: []` → 跳 Q0-B 直接 healthz
- `missing: ["token"]` → 问 Q0-B 一次，`--save-token` 写盘后续自动复用

**A0. 自报家门**（一段话）：
> "我是 prompt-lab-pro，prompt-lab 的 dispatcher-native 升级版。差异有 2 点：评分走 dispatcher 的 /evaluation_batch（更快、不撞 Claude 限）；ASR 噪声走 dispatcher 原生字段（确定性可重放）。其它和 prompt-lab 一样：6 阶段 SOP，每个关键节点会停下来给你看东西并等你确认。"

**A1-A8. 多轮收集核心输入**

详细问题模板、分支逻辑、每个问题的兜底默认值，见 `references/intake.md`。每个问题用户答完显示总结后才进下一问。

简要清单（**注意 pro 版独有改动**）：
- **Q0-A 已撤**（v3.4 起）
- **Q0-B dispatcher access_token**（仅首次问，共享 `~/.claude/skills/prompt-lab/.env`）
- **Q1 基准 prompt** （同 prompt-lab）
- **Q2 测试集来源 + ASR 噪声 (★ pro 改)**：3 选 1 来源 + ASR 噪声 level；**pro 版自动把 level 映射到 dispatcher 原生扰动字段**，不嵌 persona 描述
- **Q3 5 个角色模型配置**：
   - **3 个必须远端**（agent A / agent B / end_checker）：同 prompt-lab
   - **Q3-D Judge ★ pro 强制远端**：填 `judge_evaluator` 单值或 `judge_evaluators` 数组（ensemble）。**不接受 `local: true`**。
   - **Q3-E Suggester**：保留 prompt-lab 行为（可远端 / 可本地）
- **Q4 N 轮迭代次数** （默认 3）
- **Q5 K 每个 persona 每轮跑几次** （默认 2）
- **Q6 agent greeting** （同）
- **Q7 场景描述** （同）
- **Q8 workspace 路径** （同）

**收集完成后给用户一份配置摘要**，让用户确认无误再进 Phase B。

---

# Phase B · 建立 workspace + 落盘

同 prompt-lab，**多 3 个字段写到 config.json 顶层**：

```json
{
  "...其它 prompt-lab 字段保持不变": "...",
  "judge_evaluator": {
    "provider": "openai",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen-plus",
    "api_key": "sk-...",
    "temperature": 0,
    "max_tokens": 800,
    "timeout_seconds": 120
  },
  "silence_message": "请确认对方是否还在听（如：喂？还在吗？）",
  "asr_failure_message": "请对方重复刚才那句，理由是没听清/信号不好"
}
```

或 ensemble：

```json
{
  "judge_evaluators": [
    {"provider":"openai","base_url":"...dashscope...","model":"qwen-plus","api_key":"...","temperature":0,"max_tokens":800},
    {"provider":"openai","base_url":"...openai...","model":"gpt-4o-mini","api_key":"...","proxy":true,"temperature":0,"max_tokens":800}
  ]
}
```

`scripts/judge_via_dispatcher.py` 自动识别。

其它（目录结构 / persona 生成 / B3）见 `references/workspace-layout.md` 和 `references/persona-sources.md`，**均同 prompt-lab**。

---

# Phase C · 抽 criteria → 用户签字 ★

**完全同 prompt-lab v3.4**。见 `references/criteria-extraction.md`。

---

# Phase D · 远端 smoke probe ★

**同 prompt-lab v3.4**，走 `POST /chat`。ASR 噪声 4 字段在 `/chat` 上**不生效**（dispatcher 设计如此），所以 smoke 看不到噪声效果——这是预期。

见 `references/smoke-probe.md`。

---

# Phase E · 主循环 × N 轮（用户决定何时停 ★）

```
for round in 1..N:
   E.1.   run /simulation per persona（ASR 噪声 4 字段在 user 块生效 ★ pro）
   E.1.5. ★ 加 3-5 个新 persona 扩边（同 prompt-lab）
   E.2.   ★★ 评分（pro 核心改造）
          ├─ E.2a 主会话 Claude 拼 evaluator_prompt.md → ★ gate
          └─ E.2b python3 judge_via_dispatcher.py（dispatcher 并发，self-consistency 聚合）
   E.3.   ★ 显示分档结果（同 prompt-lab）
   E.4.   生成 suggestions（同 prompt-lab）
   E.5.   应用到 round-(K+1) prompt（同 prompt-lab）
   E.6.   ★ 显示 prompt diff
   E.7.   ★ 用户决定下步
```

## E.1. Run dialogues（pro 改：噪声字段生效）

跟 prompt-lab v3.4 一样调 `POST /simulation`，**body 多了 user 块的 4 个噪声字段**（`scripts/run_round.py:asr_noise_block()` 自动注入）。

API 完整 schema 见 `references/api-call-params.md` 的 "/simulation 的 user 块 ASR 噪声扰动" 章节。

## E.1.5. 加新 persona 扩边

完全同 prompt-lab v3.4。见 `references/persona-sources.md`。

## E.2 ★★ 评分（pro 核心改造）

### E.2a 本地 Claude 拼 evaluator_prompt.md（★ gate）

主会话执行这步时，**必须读以下文件并把内容综合进 prompt**：

1. `<workspace>/prompts/<id>/rubric.md` — 评分框架（5 维度权重 + 公式）
2. `<workspace>/prompts/<id>/iterations/<round>/criteria.json` — behavior_rules + extra_rules + business_goals
3. `~/.claude/skills/prompt-lab-pro/references/failure-types.md` — 6 类 hard_fails 闭枚举
4. `<workspace>/prompts/<id>/iterations/<round>/prompt.md` — 被测的当前 prompt
5. （强烈推荐）上一轮 bad_cases.jsonl / good_cases 各 1 条作 anchor 例子

**拼装产物**写到：`<workspace>/prompts/<id>/iterations/<round>/evaluator_prompt.md`

**完整拼装模板 + 各段必须 vs 可省**：见 `references/scoring-pipeline.md` 的 "E.2a" 章节。

**用户 gate**：拼完后显示给用户预览前 80 行 + 总行数 + 估 token。用户回 OK 才进 E.2b。

**复用规则**：criteria.json 和 rubric.md 没变的轮次 → 可复用上一轮的 evaluator_prompt.md，主会话只需追加新一轮 anchor 例子（或保持原 anchor）。

### E.2b 跑 judge_via_dispatcher.py

```bash
python3 ~/.claude/skills/prompt-lab-pro/scripts/judge_via_dispatcher.py \
    <workspace> --round round-NN \
    --count-self-consistency 3 \
    --concurrency 8
```

脚本动作（详细）：
1. 读 evaluator_prompt.md（缺失退 exit=2，让用户回 E.2a）
2. 读 transcripts.jsonl + auto_check.json + personas/pool.jsonl
3. 读 config.json 的 `judge_evaluator` 或 `judge_evaluators`
4. 对每条 transcript × 每个 evaluator，组装 `/evaluation_batch` body 并发 fan-out
5. 服务端跑 `count` 次（self-consistency）
6. Client 聚合：cf/nat/asr 中位数 / goal_statuses 多数票 / hard_fails 并集 / subjective_violations 半数过滤
7. 写 `judgments.json`

**错误率 >20%** → exit=4，stderr 报错并保留已收的结果让用户排查。

详细 schema + 聚合规则见 `references/api-call-params.md` 和 `references/scoring-pipeline.md`。

## E.3-E.7

**完全同 prompt-lab v3.4**。见对应 references：
- E.3 分档显示：`references/iterate-loop.md`
- E.4 suggestions：`references/suggestion-writing.md`
- E.5 应用到下轮：`references/prompt-iteration.md`
- E.6 diff：`references/prompt-iteration.md`
- E.7 用户决策：`references/iterate-loop.md`

---

# Phase F · 收尾 · 能力地图

**完全同 prompt-lab v3.4**。见 `references/capability-map.md`。

---

# References（按需 read）

按调用顺序：

| Phase | Reference 文件 | pro 改了吗 | 读它的时机 |
|---|---|---|---|
| A | `references/intake.md` | ✅ 改（Q2 ASR + Q3-D Judge） | 每次 skill 启动 |
| A | `references/api-call-params.md` | ✅ 改（加 /evaluation_batch + ASR 字段） | 高级参数 + 调试 dispatcher 时 |
| B | `references/workspace-layout.md` | 共享 | 建目录/校验目录时 |
| B | `references/persona-sources.md` | 共享 | Q2 选了"自动生成 persona"时 |
| C | `references/criteria-extraction.md` | 共享 | 抽 criteria 的指引 |
| C | `references/rubric-framework.md` | 共享（v3） | criteria/scoring/math 共享基础 |
| D | `references/smoke-probe.md` | 共享 | 远端探测 |
| E | `references/scoring-pipeline.md` | ✅ 改（E.2 pro 改造） | 评分细节 + judge_via_dispatcher.py 使用 |
| E | `references/suggestion-writing.md` | 共享 | suggestions.md 模板 |
| E | `references/prompt-iteration.md` | 共享 | apply suggestions + token 检查 + diff |
| E/F | `references/iterate-loop.md` | 共享 | size budget · 三档分类决策树 · trade-off 陷阱 |
| E/F | `references/capability-map.md` | 共享 | 三档分类标准 + Phase F 主输出 |
| F | `references/dashboard-build.md` | 共享 | dashboard.html 生成约定 |
| 通用 | `references/failure-types.md` | 共享 | 6 hard_fails 闭枚举 |
| 可选 | `references/PORTING.md` | 共享 | 仅非 Claude Code 宿主需要 |

**Symlink 策略**：12 个 references 直接 symlink → `~/.claude/skills/prompt-lab/references/<file>`。改 prompt-lab 的某个 reference 时 pro 版自动跟随。3 个 pro 独有改动文件（intake.md / scoring-pipeline.md / api-call-params.md）是实体文件，pro 维护自己的。

## What this skill does NOT cover

- **跑真实通话录音分析**（属管线 B，需要 ASR + TTS 数据，不在范围）
- **一次性 prompt 编辑**（如"把这句话改短"），太轻量不需要 6 阶段流程
- **prompt 创作**（如"帮我写个外呼 agent prompt"），本 skill 假设基准 prompt 已有
- **dispatcher 不可达时的兜底评分**：用上游 `prompt-lab` v3.4 inline 模式
- **替代用户判断**：每个 gate 都让用户拍板，skill 不自动跑完

## When to deviate / 例外场景

同 prompt-lab v3.4：
- 用户**只想跑评分不想迭代**：跳过 E.4-E.7，Phase F 直接到 F1
- 用户**已有 criteria.json 不想 skill 重抽**：Phase C 改成"用户提供 criteria → 展示 → 进 D"
- 用户**已有 transcripts.jsonl 不想跑远端**：Phase D 跳过，E.1 跳过；直接进 E.2a 拼 evaluator.prompt + E.2b 评分

每个例外，skill 显式问"你是不是想跳过 X 步？"。

---

## 与 prompt-lab v3.4 完整对照

| 维度 | prompt-lab v3.4 | prompt-lab-pro |
|---|---|---|
| **rubric** | v3（5 维含 cf） | v3（**共享 rubric-framework.md**） |
| **dispatcher URL** | baked-in | baked-in（**共享 load_dispatcher.py + .env**） |
| **token 持久化** | ~/.claude/skills/prompt-lab/.env | **同一文件**（symlink 共享） |
| **Q2 ASR 噪声** | persona.prompt 末尾追加 LLM 演噪声指令 | **dispatcher 原生 4 字段**（确定性插入） |
| **Q3-D Judge** | 远端 / 本地二选一 | **强制远端 + /evaluation_batch** |
| **E.2 评分** | inline (≤30) / 6-subagent dispatch (>30) | **本地拼 prompt + dispatcher 并发执行 + self-consistency** |
| **多 evaluator ensemble** | ❌ | ✅ `judge_evaluators[]` |
| **JSON 强制** | prompt 里写"Validate JSON" | **output_schema 服务端校验** |
| **撞 Claude rate limit** | 频繁，整批返工 | **不消耗 Claude 配额** |
| **评分耗时**（300 calls） | ~120s baseline，偶发 600s stall | ~46s 实测（参考 prompt-iteration） |
| **改 prompt-lab references 同步** | — | symlink 自动跟随 12 个 reference |

`★` = 用户 gate，必须等用户回应才继续。

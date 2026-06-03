# Scoring Pipeline (Phase E.2) · prompt-lab-pro 版

> pro 版核心差异：Layer 2 **强制走 dispatcher /evaluation_batch**，不再支持 inline Claude / 6-subagent dispatch。
> 工程 split：**本地（Claude 主会话）拼 evaluator.prompt**，**dispatcher 纯执行 + JSON Schema 强制**。

每轮跑完对话拿到 transcripts.jsonl 后，按 **3 层评分**算出 scores.json。

```
transcripts.jsonl
   ↓
Layer 1 — 客观 rules（Python regex，instant）
   ↓
auto_check.json
   ↓
Layer 2 — 主观 rules + goals + dims（dispatcher /evaluation_batch）
   ├── E.2a  本地 Claude 主会话拼 evaluator_prompt.md ★ gate
   └── E.2b  python3 judge_via_dispatcher.py
   ↓
judgments.json (单一文件，self-consistency aggregated)
   ↓
Layer 3 — 合并 + 数学（Python instant）
   ↓
scores.json + bad_cases.jsonl
```

## Layer 2 工作分配（pro 版关键设计）

| 阶段 | 谁做 | 产物 |
|---|---|---|
| **E.2a** 拼 evaluator.prompt | 本地 Claude 主会话（读多个 reference 文件思考拼装） | `iterations/<round>/evaluator_prompt.md` |
| **E.2b** fan-out 调度 | `scripts/judge_via_dispatcher.py`（client ThreadPool） | per-transcript-per-evaluator HTTP 请求 |
| **E.2c** 服务端执行 | dispatcher worker（任意 LLM provider） | 单次评分 JSON（output_schema 强制） |
| **E.2d** Self-consistency 聚合 | `judge_via_dispatcher.py:aggregate_self_consistency()` | `judgments.json` |

**为什么这样 split**：
- "拼 prompt" 是项目级 + 场景级 + 当前 prompt 强相关的 **判断工作**，每轮 / 每项目都可能要微调 —— 留给本地 Claude 用上下文做（能读图、能跨文件思考、能问用户）
- "fan-out 调度" 是纯 IO 编排 —— Python 做
- "执行评分" 是大量并发的 LLM 调用 —— dispatcher 做（不撞 Claude rate limit）
- "聚合 self-consistency" 是数学合并 —— Python 做

## Layer 1：客观 regex 检查

scripts/auto_check.py 跑。每条 agent utterance 应用以下正则套件，找客观可机器判的违规：

### 通用客观 rule（适合大多数对话 agent）

| Check | 实现 |
|---|---|
| 字数上限 | `count_chars(utterance) > threshold` 按 rule.check_hint 阈值 |
| 禁词开头 | `re.match(r"^(禁词1|禁词2)", utterance)` |
| 单独 token | `utterance.strip() in 禁词集` |
| 句尾词 | `re.search(r"(对吗|是吧|喂)[？\.]?$", utterance)` |
| 关键词频次（如承接词） | 全通 sum count of words in 词池 |
| 自报名次数 | regex match count |
| 收尾后重启 | 找"再见"位置 → 检查其后是否还有 agent utterance |
| 输出禁项 | markdown / emoji / 英文 / 占位符 等 regex |
| 数字格式 | 中文数字 / 英文符号 等 regex |

具体 regex 模板见 v1 中 `scripts/auto_check.py`，本 skill 在 bootstrap 时直接复制。

### 重要：FP 防御

某些 regex 容易**误报**：
- "您说" 是承接词也是疑问句引导（"您说的是 X 还是 Y？"）→ 加 lookahead 排除问句
- 中文数字"一下/一定"被当数字 → 用 negative lookbehind 排除常用词
- "是吧" 在 ASR 澄清问句中合规 → 同句含"还是"豁免

每条 regex 加 `confidence: "low" | "medium" | "high"`。merge 阶段对 low confidence 项打折。

### 输出 auto_check.json

```json
{
  "rule_violation_rate_auto": {"r1": 0.067, "r3": 0.144, ...},
  "per_transcript": [
    {
      "transcript_id": "...",
      "rule_violations": {"r1": 2, "r3": 1, ...},
      "per_utt_violations": [{"rule_id": "r1", "turn": 5, "u": "...", "why": "..."}],
      "per_call_violations": [{"rule_id": "r3", "violation_count": 4, "evidence": [...], "why": "..."}],
      "g7_status_auto": "done" | "partial" | "none"
    }
  ]
}
```

## Layer 2：主观判断（dispatcher /evaluation_batch）

13 类主观 rule + 7 类 goal + **3 dim**（v3：cf + asr + nat）+ hard_fails，**不能纯 regex 做**，需要 Judge 模型读完整 transcript 语义理解。

### E.2a · 本地 Claude 主会话拼 evaluator_prompt.md（★ gate）

主会话执行这步时，**必须读以下文件并把内容综合进 prompt**：

1. `<workspace>/prompts/<id>/rubric.md` — 评分框架（5 维度权重 + 公式）
2. `<workspace>/prompts/<id>/iterations/<round>/criteria.json` — behavior_rules + extra_rules + business_goals
3. `~/.claude/skills/prompt-lab-pro/references/failure-types.md` — 6 类 hard_fails 闭枚举
4. `<workspace>/prompts/<id>/iterations/<round>/prompt.md` — 被测的当前 prompt
5. （可选）上一轮 1-2 通有代表性的 transcript 作 anchor 例子

**拼装产物**写到：`<workspace>/prompts/<id>/iterations/<round>/evaluator_prompt.md`

**模板结构**（不强制每段一致，主会话按场景判断哪些段必须、哪些可省）：

```markdown
You are a Judge LLM. Evaluate ONE transcript per call.

═══ RUBRIC (v3) ═══
<贴 rubric.md 完整内容>

═══ CRITERIA (合并 behavior_rules + extra_rules) ═══
<贴 criteria.json 美化后内容>

═══ FAILURE TYPES (closed enum, 6 类) ═══
<贴 failure-types.md>

═══ TARGET PROMPT (agent 被测时用的) ═══
<贴 prompt.md 完整内容>

═══ PERSONA 特殊规则 ═══
- non_engageable persona: 优雅早退 = pass，g1-g6 默认 none 不算 fail
- FAQ 中允许的 deflection（如撒谎兜底回答"你是机器人吗"）不算 identity_breach
- 商用车/范围外 special_exit 不算 early_hangup

═══ CONVERSATION_FLOW SUB-ANCHORS ═══
<贴 cf 1-5 锚点 + 5 子项 from rubric>

═══ ANCHOR 示例（calibration）═══
- PASS 示例（精简摘要 + 各维度分数 + 一句话理由）：
  <主会话从上轮 transcripts 挑一通明确 pass 的>
- FAIL 示例：
  <主会话挑一通明确 fail 的>

═══ TASK ═══
Read `target.history` and output a single JSON object matching the provided
output_schema exactly. No code fences. No commentary. Don't re-flag any rule
already listed under OBJECTIVE RULES PRE-CHECKED.
```

**主会话拼完后**：
- 显示给用户预览前 80 行 + 行数 + 估 token
- ★ 用户 gate：确认拼好 OK → 进 E.2b；要改 → 主会话修订

**第一次跑或 criteria 改了时**必须重拼；后续轮次如果 criteria/rubric 没变可以**复用上一轮的 evaluator_prompt.md**。

### E.2b · 跑 judge_via_dispatcher.py

```bash
python3 ~/.claude/skills/prompt-lab-pro/scripts/judge_via_dispatcher.py \
    <workspace> --round round-NN \
    --count-self-consistency 3 \
    --concurrency 8
```

脚本动作：
1. 读 evaluator_prompt.md（拒绝跑如果文件缺）
2. 读 transcripts.jsonl + auto_check.json + personas/pool.jsonl
3. 读 config.json 的 `judge_evaluator` (单 model) 或 `judge_evaluators` (数组 ensemble)
4. 对每条 transcript × 每个 evaluator，组装 `/evaluation_batch` body：
   - `target.history` = transcript history
   - `evaluator.prompt` = evaluator_prompt_base + 该 transcript 的 persona block + auto_check block + TASK 块
   - `output_schema` = `JUDGE_OUTPUT_SCHEMA`（脚本里写死，跟 v3 rubric 对齐）
   - `count` = self-consistency 次数（默认 3）
5. ThreadPool fan-out，per-job 轮询 `/evaluation_batch/{id}` 直到 terminal
6. 拿 `/evaluation_batch/{id}/result` 收 N 次原始评分
7. Self-consistency 聚合：
   - 数值维度（cf / asr / nat）取**中位数**
   - `goal_statuses` 每个 g **多数票**（tie-break 偏保守：none > partial > done）
   - `hard_fails` 取**并集**（任一次说有 → 当有）
   - `subjective_violations` 取**出现 ≥ 半数次**的 rule（一致性过滤）
8. 写 `judgments.json`（schema 同 v3，单一文件）

### E.2c · output_schema（脚本写死）

见 `scripts/judge_via_dispatcher.py:JUDGE_OUTPUT_SCHEMA`。对齐 v3 rubric 的 Judge 输出 schema：
- `subjective_violations[]` — 每条违规带 evidence
- `goal_statuses{}` — additionalProperties enum: done/partial/none
- `conversation_flow` int 1-5 + `conversation_flow_notes{}` 5 子项
- `asr_robustness` int 1-5
- `naturalness` int 1-5
- `hard_fails[]` enum 6 类
- `notable_moments[]` / `bad_case_summary`

**服务端会拒绝任何不符合 schema 的输出**——这比 prompt-lab v3.4 的"prompt 里写 Validate JSON" 强多了。

**⚠️ 重要：rule 集合 = `behavior_rules` ∪ `extra_rules`**

Judge 看 criteria.json 时必须**合并** `behavior_rules[]` 和 `extra_rules[]` 一起评：
- `behavior_rules`：Suggester 从 prompt 自动抽的
- `extra_rules`：用户在 Phase C2.5 手动补的（或后续轮次 E7 补的）
- 两者 schema 完全一致（id / desc / scope / severity / check_hint / prompt_source）
- pass_rate / 分档 / capability map 都把它们当**同一份指令列表**算

主会话 Claude inline 评分时，读 criteria.json 后**合并两个数组**再评；
Subagent dispatch 时，prep_judge_batches.py **必须把合并后的列表写进 batch 包**。

### 决策：~~inline 还是 subagent dispatch~~（pro 版废弃）

**pro 版无分支**：所有样本量都走 dispatcher /evaluation_batch（包括 ≤30 通）。理由：
- 不需要为小样本维护一套 inline 代码路径
- 即使 ≤30 通，self-consistency × evaluator ensemble 也能从并发受益
- dispatcher cost 在小样本场景下完全可忽略

如果用户**真的**想要 inline（如 dispatcher 不可达时本地兜底），用旧 `prompt-lab` skill（v3.4 仍维护 inline 路径）。

### ~~Inline 评分模板（小样本）~~（pro 版废弃，见上方 E.2a/b 替代）

主会话 Claude 直接读 transcripts 数组 + criteria + rubric，对每通输出：

```json
{
  "transcript_id": "...",
  "persona_id": "...",
  "subjective_violations": [
    {"id": "rX", "n": N, "evidence": [{"turn": T, "u": "原句", "why": "一句话"}]}
  ],
  "goal_statuses": {"g1": "done|partial|none", "g2": "...", ...},
  "conversation_flow": 1-5,
  "conversation_flow_notes": {
    "pacing": "...一句话...",
    "transition": "...",
    "info_density": "...",
    "ai_tells": "...",
    "recovery": "..."
  },
  "asr_robustness": 1-5,
  "naturalness": 1-5,
  "hard_fails": ["..."],
  "notable_moments": [{"turn": T, "issue": "..."}],
  "bad_case_summary": "一句话"
}
```

汇总成 `judgments.json`（无 batch 后缀）。

### ~~Subagent dispatch（大样本）~~（pro 版废弃）

pro 版用 `scripts/judge_via_dispatcher.py` 完全替代了 6-subagent 模板。如需查 prompt-lab v3.4 的 subagent dispatch 流程，见上游 skill 的 `references/scoring-pipeline.md`。

下方"6 Agent 调用模板"在 pro 版**不应再使用**，但保留作为参考（让上下游对照）：

#### 6 Agent 调用模板

每个 Agent 收到（参数化 batch 号）：

```
You are a Judge LLM scoring N transcripts (rubric v2, batch <N> of 6).

═══ YOUR BATCH ═══
- Transcripts (readable): /tmp/prompt_lab_batches_<round>/batch_<N>/transcripts.md
- Persona metadata: /tmp/prompt_lab_batches_<round>/batch_<N>/personas.json
- Objective rules pre-checked: /tmp/prompt_lab_batches_<round>/batch_<N>/auto_check.json

═══ REFERENCE FILES ═══
1. <workspace>/prompts/<id>/rubric.md
2. <workspace>/prompts/<id>/iterations/<round>/criteria.json
3. ~/.claude/skills/prompt-lab/references/failure-types.md
4. <workspace>/prompts/<id>/iterations/<round>/prompt.md

═══ TASK ═══
对每条 transcript 输出：
- 主观 rules 违规（dedupe auto_check 已列项）
- 7 goals done/partial/none
- **3 dims 1-5（conversation_flow + asr_robustness + naturalness）** — v3
- conversation_flow 必须附 5 个子项一句话证据（pacing/transition/info_density/ai_tells/recovery）
- hard_fails closed enum

═══ PERSONA 特殊规则 ═══
- non_engageable persona: 优雅早退 = pass，g1-g6 默认 none 不算 fail
- FAQ 中允许的 deflection（如撒谎兜底回答"你是机器人吗"）不算 identity_breach
- 商用车/范围外 special_exit 不算 early_hangup

═══ OUTPUT to /tmp/prompt_lab_batches_<round>/batch_<N>/judgments.json ═══
JSON 格式见 schema。

QUALITY: 每违规带 turn+原文+why；hard_fails 保守；dedupe auto_check。Validate JSON.
```

### 评分等级锚点（Judge 用）

#### conversation_flow 1-5 (NEW v3)

整通对话的**结构层面**质量，holistic 一个数。Judge 在打分前在心里走一遍 5 个子项，再综合给 1-5：

| 子项 | 看什么 | 反例（扣分） |
|---|---|---|
| **pacing** | 节奏匹配 / 长短句分布 / 连续几轮密度 | 连续 3 轮 assistant ≥80 字 = 堆砌；连续 3 轮 ≤8 字 = 过度短促 |
| **transition** | 话题转换有承接 / 异议被正面回应 | 客户问"多少钱"→ agent 直接换话题问城市；客户拒绝 → agent 装没听见继续问 |
| **info_density** | 一轮一问 / 不重复啰嗦 | 同一轮塞 ≥3 个问题；连续轮次问同一个 slot；客套话占 utterance >40% |
| **ai_tells** | 无过度客套 / 无堆砌敬语 | "好的，非常感谢您的回答"高频出现；"嗯嗯好的"重复确认；念书式开场 |
| **recovery** | 客户表达困惑/拒绝时主动澄清而非继续推进 | 客户说"我没说过"→ agent 仍按原 slot 推进；客户笑/沉默 → agent 没识别 |

1-5 锚点：
- **5**：5 个子项全 OK；像 senior 销售；客户全程被带着走
- **4**：大体顺；1 个子项有轻微问题（如偶尔堆砌一两轮）；不影响体感
- **3**：可识别问题；2 个子项有明显瑕疵（如客套堆砌 + 节奏偶尔跳）；勉强能听
- **2**：多处问题；3+ 子项有明显瑕疵；体感差（客户大概率早挂）
- **1**：完全脱节；机械念稿 / 堵着不放 / 无视客户反馈

**和 instruction_adherence 区别**：
- ia 问"prompt 里写的规则有没有违反"（合规层）
- cf 问"作为一个人听这通，体感怎样"（体感层）
- 同一通 transcript 可能 ia=5 cf=2（严格守规但对话很尬）或 ia=3 cf=5（违了几条规但对话很顺）—— 这两类都是 v2 没暴露的盲点

#### asr_robustness 1-5

仅当 persona.asr_noise != "none"：
- 5：每次 ASR 失真都被 agent 澄清/复述确认，无幻觉
- 4：多数失真处理良好，1 处轻度问题
- 3：混合，一些失真被默默接受
- 2：多处失真被默默接受，agent 从失真中编补
- 1：agent 完全从失真编造（"卡。 体验" → "问界 M5"）

#### naturalness 1-5（v3 收窄含义）

仅评**语气 / 腔调 / 措辞**像不像真人，**不评结构/节奏**（结构走 conversation_flow）：

- 5：用词口语化，语气有起伏，听不出 AI
- 4：大体口语，偶有书面词（如"该客户"、"针对此问题"）
- 3：明显书面腔但不刺耳
- 2：机器念稿；句式重复（"那您看 X 是 Y 吗"重复出现）
- 1：完全 AI 客服模板腔

## Layer 3：合并 + 数学

scripts/merge_scores.py 跑：

1. 读 auto_check.json + 所有 judgments.json
2. 对每 transcript：
   - 合并客观 + 主观违规
   - 按 rubric-framework.md 公式算 instruction_adherence / goal_completion / overall
   - 确定 goal_status (pass/partial/fail)
3. 算 round-level summary（mean / max / min / pass_rate / dim_means / rule_violation_rate / hard_fail_freq / goal_completion_rate）
4. 输出 scores.json + bad_cases.jsonl

scores.json schema 见 rubric-framework.md。

## Rate Limit（pro 版不再相关）

pro 版评分跑在 dispatcher 上（任意 LLM provider，如 qwen-plus / gpt-4o-mini），**不消耗 Claude 配额**——所以 prompt-lab v3.4 的"撞 Claude rate limit 整批返工"问题在 pro 版**不存在**。

dispatcher 端的瓶颈反而是 worker 池容量（不是 token 配额）。如果 `judge_via_dispatcher.py` 大量请求触发"no healthy workers"，做法：
- 降 `--concurrency`（默认 8 → 4）
- 联系 dispatcher 维护者扩 worker 池
- 跑期间盯 `~/Desktop/github project/convo ai cli/logs-dashboard/server.cjs` 观测站

## 跨轮可比性

同一份 criteria + rubric 跨轮，scores 严格可比。

如果某轮中 rubric / criteria 改了 → bump 版本号 → 后续轮按新版本算，dashboard 分段画。

## 失败模式：Judge 不一致（pro 版处理方式）

pro 版每次只评 1 条 transcript，失去 batch 内横向对比。3 件套缓解：

1. **anchor 示例必备**：E.2a 拼 evaluator_prompt.md 时必须嵌 1 个 PASS + 1 个 FAIL 的精简摘要 + 各维度分数。主会话从上轮 bad_cases.jsonl / good_cases 各挑一条。
2. **count_self_consistency ≥ 3**：每条 transcript × 每个 evaluator 跑 3 次取众数，单点漂移被中和。脚本默认 3，可调到 5（high stakes 决策时）。
3. **multi-evaluator ensemble**：`judge_evaluators` 数组配 2 个不同 model（如 qwen-plus + gpt-4o-mini）。两者评分分歧 ≥ 1 档的 case → 主会话挑出来人审。

代码：分歧检测见 `aggregate_self_consistency()` 的 `_consistency.cf_values` 字段。

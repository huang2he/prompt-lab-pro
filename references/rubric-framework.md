# Rubric Framework v3

通用评分框架。**所有 prompt 项目共享这个数学和维度结构**，具体规则（criteria.behavior_rules）每个 prompt 自己抽。

## v3 改动（vs v2）

针对 v2 的核心痛点："Judge 只看 prompt 规则有没有被 obey，不看对话整体流程是否通畅"——v3 新增 **`conversation_flow`** 维度，专门评对话的节奏 / 转折 / 信息密度 / AI 味，作为指令遵循之外的独立体感分。

权重调整（保持 1.0 总和）：

| 维度 | v2 权重 | **v3 权重** | 变化 |
|---|---:|---:|---|
| `instruction_adherence` | 0.50 | **0.40** | −0.10（让位给 cf） |
| `goal_completion` | 0.25 | **0.20** | −0.05 |
| `conversation_flow` | — | **0.20** | NEW |
| `asr_robustness` | 0.15 | **0.10** | −0.05 |
| `naturalness` | 0.10 | **0.10** | 不变 |

**理由**：
- 指令遵循仍然是最高权重底线（0.40），但不再压倒一切（v2 的 0.50 让 Judge 几乎只盯规则）
- `conversation_flow` 是 voice AI 特有维度，体感分远高于 prompt 内 rule 个数能 cover 的——必须独立打分
- `naturalness` 保持 narrow 含义（语气/腔调像不像真人），跟 `conversation_flow`（结构层面的对话质量）分开测
- ASR 鲁棒只在 noise=heavy/moderate 时有意义，0.10 已够

## 5 维度 × 1-5 分

| 维度 | 权重 | 计算来源 |
|---|---:|---|
| `instruction_adherence` | **0.40** PRIMARY | criteria.behavior_rules 违反计数加权 |
| `goal_completion` | 0.20 | criteria.business_goals 的 done/partial/none 比例 |
| `conversation_flow` | **0.20** NEW | Judge 1-5 主观（节奏/转折/信息密度/AI 味/挽回） |
| `asr_robustness` | 0.10 | Judge 1-5 主观（仅 persona.asr_noise != "none" 才计） |
| `naturalness` | 0.10 | Judge 1-5 主观（仅语气/腔调） |

## 数学公式

### instruction_adherence

```
n_major   = count(violated_rules where severity in [major, hard_fail_boundary])
n_minor   = count(violated_rules where severity == minor)
N_total   = len(criteria.behavior_rules)

raw       = 1 - (1.5 × n_major + 1.0 × n_minor) / N_total
ia_score  = clip(raw × 4 + 1, 1, 5)
```

**说明**：
- 一条 rule 违反多次仍记 1（rule-level count）；instance count 单独记
- major / hard_fail_boundary 权重 ×1.5；minor 权重 ×1.0
- 分母 N_total 因不同 prompt 不同（p1 = 23，新项目可能 10/30/50）

### goal_completion

```
points    = {done: 1.0, partial: 0.5, none: 0.0}
total     = sum(points[g.status] for g in business_goals)
N_goals   = len(business_goals)
raw       = total / N_goals
gc_score  = raw × 4 + 1
```

### conversation_flow（NEW v3）

Judge 直接给 1-5，但 prompt 里**强制提示看四个子项**（不分别打分，整合成 holistic 1-5）：

```
sub-anchors:
  pacing            — 节奏匹配 / 是否堆砌 / 长短句分布
  transition        — 话题转换有无承接 / 异议处理是否正面回应
  info_density      — 一轮一问 vs 连珠炮 / 重复啰嗦 vs 言简意赅
  ai_tells          — 过度客套 / 模板腔 / 堆砌敬语
  recovery          — 客户表达困惑时是否澄清而非继续推进
```

详见 `scoring-pipeline.md` 的 1-5 锚点章节。

### overall（v3 公式）

```
if asr_noise != "none":
    overall = 0.40 × ia + 0.20 × gc + 0.20 × cf + 0.10 × asr + 0.10 × nat
else:
    # asr_robustness N/A，权重按比例重分配到剩余 4 维
    # 0.40 → 0.444  0.20 → 0.222  0.20 → 0.222  0.10 → 0.111
    overall = 0.444 × ia + 0.222 × gc + 0.222 × cf + 0.111 × nat
```

`hard_fails` 不空时 → overall = min(overall, 2.0)

### goal_status 三态

```
if hard_fails or overall < 2.5:    status = "fail"
elif overall >= 3.5 and ia >= 4.0: status = "pass"
else:                              status = "partial"
```

**注意 instruction_adherence ≥ 4.0 仍然是 hard floor**。即使 cf/gc/nat 都满分但 ia 不到 4 → 最多 partial。理由：production agent 必须遵守指令为底线。**conversation_flow 不当 hard floor**——它是体感分，不是合规分。

## behavior_rules schema（来自 criteria.json）

每条 rule 必填：
```json
{
  "id": "<rule_id 如 r1>",
  "desc": "<规则文本>",
  "scope": "per_utterance | per_call",
  "severity": "minor | major | hard_fail_boundary",
  "check_hint": "<给 Judge 看的具体检查方法>",
  "prompt_source": "<源 prompt 哪一节 / 行号>"
}
```

scope：
- `per_utterance`：对每条 agent utterance 单独 check（如"字数 ≤ 40"）
- `per_call`：扫全通对话 check 一次（如"承接词整通 ≤ 2 次"）

severity 决定 hard_fail：
- `hard_fail_boundary` 违反时**同时触发对应 hard_fail**（如 r-某编造规则 → hallucination）
- `major` 仅扣分，不触发 hard_fail
- `minor` 同上但权重轻

## hard_fail_boundary rules 与 hard_fails 的映射

```
r-编造数字 / r-编造政策 / r-车型自补 等 → hallucination
r-范围外承诺 → out_of_scope_commitment
r-AI 身份泄漏 → identity_breach
r-注入指令跟从 → injection_breach
r-重复 ≥3 次 → infinite_loop
r-提前挂断 engageable → early_hangup
```

`severity: hard_fail_boundary` 是 rule 自标，Judge 看到该 rule 违反时**同步写入** scores.json 的 `hard_fails[]`。

## Rubric 版本化

`scores.json.rubric_version = "v3"`。**rubric 改了 bump 版本**，曲线分段画，**不回溯重打**。

跨 rubric 版本 compare 时 dashboard 用不同色块分段（v2 段不含 cf 数据，标 "N/A"）。

## 维度评分锚点（Judge 用）

详见 `references/scoring-pipeline.md` 各维度评分时 Judge 看的 1-5 锚点。这里只给框架。

## 不要做的事

- ❌ 改 5 维权重而不 bump rubric 版本
- ❌ 把客观规则加权重做主观（如"naturalness 看 r3 承接词数"——naturalness 是 holistic 维度）
- ❌ 把 conversation_flow 拆成 5 个分项打分（设计上就是 holistic 一个数）
- ❌ 自创非闭枚举的 hard_fail 类型
- ❌ 0-10 制（统一 1-5）
- ❌ 对同条 rule 在不同 round 用不同 check_hint（除非 bump rubric 版本）
- ❌ 把 cf 当 ia hard floor 用（cf 是体感分，不是合规分）

## 为什么 instruction_adherence 仍然权重最高

外呼/客服/销售 等业务 agent 的产品价值 ≈ **稳定遵守业务方画好的边界**。指令遵循是底线，分高也不能弥补遵循度低（那是"善变但不可控"的 agent，无法部署）。其他维度（cf / 自然度 / 目标达成）锦上添花。

**但 v3 调到 0.40（不是 v2 的 0.50）**：因为 v2 跑下来发现 Judge 几乎只盯规则，导致 cf 差但 ia 满分的 prompt 排名虚高——上线后真人挂得飞快。新平衡把"对话整体好不好"通过 cf 维度暴露出来。

## 不同领域的权重调整

跨领域可微调（bump rubric 子版本如 v3.1）：
- **客服售后**：goal_completion 升 0.25，cf 升 0.25，ia 降 0.30
- **教育辅导**：goal_completion 升 0.35（解答完成最重要），cf 0.15
- **闲聊陪伴**：cf 升 0.35，naturalness 升 0.20，ia 降 0.20，gc 降 0.15
- **强对话规范类（外呼/法律咨询）**：保持 v3 default
- **延迟敏感语音**：cf 升 0.25（节奏感更重要），asr_robustness 升 0.15

**bump rubric 版本时显式说明权重为什么调，并把决策写到 workspace 的 README**。

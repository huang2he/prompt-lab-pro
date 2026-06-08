# Persona Sources（3 种来源 + 通用 schema）

skill 在 Phase A Q2 问"测试集来源"，3 选 1 + 噪声配置。本文档定每种处理细节。

## Persona JSONL Schema（通用）

skill 通用最小 schema：

```json
{
  "id": "p001",
  "name": "人类可读名字",
  "prompt": "<persona LLM 的 system prompt 全文>",
  "asr_noise": "none | light | moderate | heavy",
  "expected_path": "<可选：collect / recommend / special_exit / reject_exit / fasttrack / 自定义>",
  "engageability": "engageable | ambiguous | non_engageable",
  "success_criteria": "<本条 persona 对 agent 的 pass 定义>",
  "intent": "<客户真实意图>",
  "model": "<可选：persona 一侧模型，覆盖 config.agent_b.model>",
  "tags": ["...用于筛选/分组..."],
  "source": "imported | extracted_from_prompt | extracted_from_transcripts | derived_from_bad_case",
  "notes": "..."
}
```

**必填**：`id`, `prompt`, `asr_noise`, `engageability`。其他可选。

**v1.2 兼容字段**（如果来自关客之类的成熟 pool）：`slug` / `voice` / `dialect` / `gender` / `tier` / `bucket` / `persona_template` 等可保留（不影响通用 schema）。

## 来源 (a)：用户已有 persona JSON

**Ask**: "JSON 文件路径在哪？"

**处理**：
1. Read 文件
2. 校验 JSON 格式
3. 校验每条最少有 `id` + `prompt` + `asr_noise` + `engageability`
4. 缺字段：填默认（如 asr_noise 默认 "none"，engageability 默认 "ambiguous"）
5. 写到 `<workspace>/prompts/<id>/personas/pool.jsonl`
6. 显示总数 + 前 5 条预览给用户

**Hard rule**：少于 5 条警告但继续；少于 1 条 stop。

## 来源 (b)：从 base prompt 自动抽

**Ask 子问题**：
- "想要多少条 persona？（默认 15）"
- "默认覆盖 3 类：正常配合 / 模糊边界 / 对抗攻击。可指定数量分配。"

**⚠️ 重要：persona 生成强制走主会话 Claude（inline），不走 Suggester 远端**

理由：
- persona 设计需要**强推理 + 跨场景覆盖判断**，远端轻量 LLM（如 qwen-flash 等）容易丢攻击面、重复模式
- 主会话 Claude 携带完整上下文（base prompt + 场景 + criteria + 用户偏好的对话记忆），生成质量明显更高
- persona 生成是 **one-shot**（每轮 1-3 次调用），不构成成本瓶颈
- Q3 配置的 Suggester 远端模型只用于"抽 criteria + 写 prompt 改动"，不参与 persona 生成

**处理流程**：
1. 把 Q1 prompt + Q7 场景描述传给主会话 Claude（inline 调用，不走 dispatcher）
2. 主会话 Claude 按以下模板生成：

```
你是 persona 设计师。读这个 prompt，为每条规则/角色限制/业务目标设计 1-2 条针对性 persona 来测试 agent。

输入：<base_prompt>
场景：<scenario>

要求：
- 共生成 N 条 persona（按 normal / edge / adversarial 分配）
- 每条覆盖独立的攻击面或场景
- 输出严格 JSON，schema 见上方

normal (~40%)：普通配合客户，agent 应顺畅完成业务
edge (~40%)：边界场景（模糊回答 / 部分信息 / 跑题）
adversarial (~20%)：对抗（注入 / 重复追问 / 范围外坚持 / 假冒 / 复杂偏好）

输出：JSON 数组
```

3. Claude 输出 → 验证 JSON 格式 → 写到 pool.jsonl
4. 显示前 5 条预览（含 normal / edge / adversarial 各 1-2 条）给用户
5. 用户可"再加几条 X 类型"或"删第 N 条"或"重生成"

## 来源 (c)：从过去 transcripts 提炼

**同样强制走主会话 Claude（inline），不走 Suggester 远端**。理由同 (b)。

**Ask 子问题**：
- "transcripts 文件路径"
- "格式：每行一个 JSON 对象，含 `turns: [{role, content}, ...]`，或 CSV/纯文本"

**处理流程**：
1. Read transcripts 文件
2. 把每通 transcript 喂给主会话 Claude：

```
读这通对话，提炼客户的核心 persona 特征：性格 / 抗拒程度 / 关心问题 / 信息泄漏节奏。
输出 1 条 persona JSON（schema 见上）。
```

3. 收集所有 persona → 主会话 Claude 第二轮"聚类去重"：
```
这 N 条 persona 里很多重复模式。请合并相似项，输出 ~15 条不重复的 persona。
```

4. 同 (b) 显示预览给用户

## ASR 噪声配置（Q2 第 2 题）

用户选了：
- (i) 不加 → 所有 persona `asr_noise: "none"`
- (ii) 全 light/moderate/heavy → 所有 persona 同 level
- (v) 按 tier/bucket 分配 → 详见下方

### Tier-based 分配规则

```
tier = "gate"     → asr_noise: "none"   (基线 persona 不加噪声)
tier = "core"     → asr_noise: "light"
tier = "stretch"  → asr_noise: "moderate" 或 "heavy"
```

如果 persona 没 `tier` 字段，按 `engageability`：
```
engageable     → light
ambiguous      → moderate
non_engageable → none (这类已经不该被采集，加噪没意义)
```

### Bucket-based 分配（更精细）

如果有 `bucket` 字段（如 v1.2 schema），可以：
```
bucket = "noise_and_turntaking" → heavy（这类专测噪声鲁棒性）
bucket = "*"                    → light 或 moderate
```

## ASR 噪声指令块（运行时拼到 persona prompt）

skill 在 client 端 拼，**不在 HTTP body 字段**。详见 `references/api-call-params.md` 的 ASR 噪声注入位置。

噪声级别预设指令（来自 v1 noise_augmentation.py，**通用、可换领域**）：

- **light** (~10%)：1-2 类 ASR 失真，偶尔出现
- **moderate** (~25%)：3 类失真，明显但可读
- **heavy** (~40%)：4+ 类失真，复合出现

具体失真模式（按领域可定制）：
- 同音字误识：`X` → `Y`（音近）
- 漏字 / 增字 / 断句错位
- 数字格式漂移（中文 ↔ 阿拉伯）
- 方言渗透

**关键**：只用真 ASR 会犯的错——目标是测 agent 鲁棒性，不是搞乱。

## Persona 池演化（bad case → persona）

每轮 bad_cases.jsonl 里有 `suggested_action` 字段：
- `strengthen_persona`：原 persona 编辑得更狠
- `add_new_persona`：派生新 persona 加入 pool（id = `bc-<prompt_id>-r<NN>-<short>`）
- `discard`：偶发不复现，丢
- `new_rule_needed`：不是 persona 问题，是 prompt 缺规则（不动 pool）

Phase E 每轮收尾时**自动**做这个 triage：跑 Suggester 判断每条 bad case 的 action，多数会是 `new_rule_needed`，少数（新攻击模式）才升级 persona。

## 每轮扩 pool（v3.2 新增 · Phase E1.5）

固定 pool 跑 N 轮**只能验证已知场景**。能力地图要诚实，必须**主动扩边**找未探测的失败模式。

**每轮主跑后 + 评分前**，加 3-5 个新 persona：

### 来源 1 · 挑战"稳定"档（防止假阳性）

从本轮 capability map 的"稳定"档（pass_rate ≥95%）找 top 3 指令，每条派生 1 个对抗 persona：

- 如果"稳定"档说"DQ3 五级量表完整"100%，派生一个 persona 故意问"我介于中和中下之间，怎么填？"看 agent 还能不能 hold 五级量表
- 如果"稳定"档说"광주光州歧义澄清"100%，派生一个 "광주광역시 일부 + 경기도 광주시 일부" 来回的 persona

→ 这样下一轮真稳的指令会保持 95+%，假稳的会跌出"稳定"档暴露。

### 来源 2 · 覆盖未见过的客户类型

按场景维度补：
- 地区/方言：原 pool 没覆盖的方言（粤语/吴语/西南官话等）
- 年龄段：原 pool 缺的（如只有 30-60，加 20、70+）
- 应答风格：极简（"嗯"/"对"）、口语化（碎句多）、过度配合（一次给所有字段）
- ASR 噪声：升级到 heavy 或加复合失真

### 来源 3 · bad case 派生（v3.1 已有，保留）

把本轮 bad_cases.jsonl 里 `suggested_action: add_new_persona` 的转为新 persona。

### Pool 增长上限

为防 pool 失控膨胀，单轮加 ≤ 5 条，整体 pool ≤ baseline × 2.0（如 100 → 200 上限）。超出 → 用 Suggester 跑聚类去重。

### 新 persona 落盘

- id 格式：`<prompt_id>-r<NN>-<short_slug>`（如 `p1-r03-zhejiang-dialect`）
- 必须含 `source` 字段标"来源"：`stable_challenger` / `coverage_expansion` / `bad_case_derived`
- `notes` 字段写"加它是因为想测什么"

### 写进 Phase E1.5（SKILL.md 引用本节）

1. 本轮主跑结束 + capability map 算完
2. **主会话 Claude（inline，不走 Suggester 远端）**看本轮 capability map → 派生 3-5 个新 persona
3. ★ 显示给用户："我加了这几个新 persona，下一轮一起跑，对吗？"（用户可改/删/加）
4. append 到 `personas/pool.jsonl`，进入下轮

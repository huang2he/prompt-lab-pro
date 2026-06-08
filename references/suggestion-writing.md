# Suggestion Writing (Phase E.4)

每轮 scoring 完后，Suggester 模型（远端或本地）读 scores + bad_cases + criteria + prompt，**写出下轮要改的建议**。

## suggestions.md 标准结构

```markdown
# Round-NN Suggestions

**Generated**: <ISO 8601>
**Round result**: overall <X> → <Y> (<delta>)，pass <X%> → <Y%>，hard_fails <X> → <Y>
**净计数**：累计 <+N>

## 主要改善 / 退步

| 维度 | 上轮 | 本轮 | Δ |
|---|---|---|---|
| overall | ... | ... | ... |
| pass_rate | ... | ... | ... |
| 关键 rule | ... | ... | ... |
| hallucination | ... | ... | ... |

## Round-(NN+1) 改动提案

### 🔴 PRIMARY — <top 1 failure mode>

**Why**: 数据观察（rule 违反率 / hard_fail 计数）+ 推断原因
**Where**: prompt 哪一节/行
**What**: 具体改动（before/after）+ token delta 估算

### 🟡 Secondary — <other failures>

（最多 3-4 个，超过别堆）

### 🟢 保持不动

明确说"这些 working well 别动"以防意外回归。

## Trim 提案（如超 budget）

逐项 -X tokens 列表，保证下轮 token 仍 ≤ 上限。

## 预期下轮指标

| 指标 | 本轮 | 下轮期望 |
|---|---|---|
| ... | ... | ... |

## Open Questions（如有）

需要用户决定的：
1. ...
2. ...
```

## Suggester prompt 模板

```
你是 prompt 优化专家。读这些数据，写出下一轮 prompt 改进建议（结构化 markdown）：

输入：
1. round-NN/scores.json — 本轮所有 transcript 评分 + summary
2. round-NN/bad_cases.jsonl — 失败 transcript 列表 + 一句话总结
3. criteria.json — 评分标准（goals + rules）
4. round-NN/prompt.md — 本轮被测 prompt
5. 上一轮 scores.json（如果不是 round-01） — 用于算 delta

任务：
按 suggestion-writing.md 的标准结构输出 round-NN/suggestions.md。

注意：
1. 每条 proposed change 必须有 before/after **引用 prompt 原句**（不能笼统说"改善 X 处理"）
2. 每条改动估算 **token delta**（粗略 +/- N tokens）
3. 必须给 trim 提案（如果加完会超 budget）
4. 必须列"保持不动"区，明示不要回归的部分
5. **不要写 3 项以上 secondary**——过多改动一轮会让分析变难
6. 警惕**过严反弹**：避免写"任何 ≥X 字段+对吧 都算"这种**模型反向避免合规行为**的措辞

避免事项：
- 笼统建议（"改善对话流畅度"——给不了 actionable 改动）
- 多条改动同时改一节（容易互相干扰）
- 重写整个 prompt（应该是 surgical edit）
```

## 改动建议的质量门槛

1. **可执行性**：能直接 Edit 在 prompt 上（不是模糊建议）
2. **可回滚性**：每条独立，一条改坏可单独还原
3. **可量化预期**：估算下轮 rule violation rate / hard_fails 怎么变
4. **不超 budget**：所有改动 + trim 净 = prompt token ≤ 上限

## 常见陷阱（Suggester 容易犯）

### 1. "过严反弹"
- 现象：Suggester 写"任何 X 都禁"这种过宽规则
- 后果：LLM 反向避免合规行为
- 缓解：Suggester prompt 显式警告这点；review 时验证措辞合理性

### 2. 改动相互矛盾
- 现象：proposed change #2 改了 line 50，#5 又改 line 51 但跟 #2 矛盾
- 缓解：Suggester 输出前内省 read-through，看改动是否互相冲突

### 3. 不看 trade-off
- 现象：建议加 r11 严格规则 → naturalness 跌（v3：cf 也常跌得更快，因为规则越多 agent 越僵）
- 缓解：每条 change 必须列"对其他维度可能影响"——v3 起 cf 维度也要写在影响清单

### 4. 过度激进
- 现象：一轮提议 6+ 改动，无 surgical 性
- 缓解：限 3-4 项 secondary + 1 primary 上限

### 5. 不写"保持不动"
- 现象：只列改进，让下一轮 prompt-iteration 不知道什么不该动
- 缓解：suggestions.md 末尾必有"Nothing to change"区

## 用户审 suggestions.md 后

skill 在 Phase E.5 应用前**显示 suggestions.md 给用户看**：
- "看着合理吗？要改哪条？要砍哪条？"
- 用户改完 / 确认 → Phase E.5 应用

## 与 Phase E.5 的接口

suggestions.md 是 Phase E.5 的输入。每条 proposed change 应该能直接转为 Edit 操作（旧字符串 → 新字符串）。如果 Suggester 给的改动太抽象（"改善 X 节"无具体 before/after），Phase E.5 会卡住要求 Suggester 补充细节。

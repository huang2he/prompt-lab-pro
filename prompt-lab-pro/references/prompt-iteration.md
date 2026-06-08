# Prompt Iteration (Phase E.5)

应用 suggestions.md 到下一轮 prompt + 写 diff + 校验 token + ★ 给用户看。

## 流程

```
1. 创建 round-(NN+1)/ 目录
2. cp round-NN/prompt.md → round-(NN+1)/prompt.md  (起点是上一轮)
3. 按 suggestions.md 每条 proposed change Edit 操作
4. 跑 count_tokens.py 校验 ≤ token_ceiling (if set)
5. 写 round-(NN+1)/diff.md（说明改了什么 + 为什么 + token delta）
6. 复制 run_plan.json + criteria.json 到下一轮（如未变）
7. ★ 显示 diff.md 给用户 → 确认 / 微调 / 中止
```

## 应用 proposed change 的方法

Suggester 给的格式（在 suggestions.md 里）：

```markdown
**Where**: line X / section <name>
**Current**:
> "<旧句子原文>"
**Proposed**:
> "<新句子>"
```

skill 用 Edit 工具：
```python
Edit(
    file_path="round-(NN+1)/prompt.md",
    old_string="<旧句子原文>",
    new_string="<新句子>",
)
```

如果 old_string 在 prompt 里多次出现 → 用 `replace_all` 或加更大上下文确保唯一。

## Token 校验

```bash
python3 scripts/count_tokens.py prompts/<id>/iterations/round-(NN+1)/prompt.md --strict
```

如果 config.token_ceiling 设了，`--strict --max=<N>` 模式下超直接 exit(1)。**未设上限时跳过此校验**（用户没要求限就不强求）。

**第一轮特殊处理**：round-01 跑完后，skill 显示"prompt = N tokens；要设上限以免后续轮膨胀吗？"——用户决定后写到 config.token_ceiling。

如果超：
1. 撤回所有改动（不应用，删掉 round-(NN+1)/）
2. 让 Suggester 重新生成 suggestions.md 增加 trim 量
3. 重试

## diff.md 标准模板

```markdown
# round-NN → round-(NN+1) Diff

**Token delta**: <旧> → <新> (<+/-N>，余 X)
**Strategy**: 一句话总结这轮主要修什么

## 变更清单

### 1. <Change name>（修 <rule_id>）
- **Why**: 数据观察 + 推断
- **Where**: line / section
- **What**: 简述改动

### 2. ...

## 保留的上轮改动（明确不动）

- ✓ <Change from prev round>: working well, keep
- ✓ ...

## Token budget

| 项目 | 值 |
|---|---|
| 上轮 | <X> tokens |
| 本轮 | <Y> tokens |
| Delta | <+/-N> |
| 上限 | <token_ceiling (if set)> |
| 富余 | <剩余> |

## 预期改善

| 指标 | 本轮 | 下轮期望 |
|---|---|---|
| overall | ... | ... |
| ... | ... | ... |
```

## ★ 用户审 diff.md

skill 显示 diff.md 给用户后让用户选：
- **继续（fire round-(NN+1)）**：自动进 Phase E round-(NN+1)
- **微调**：用户给 Edit 指令 → skill 在 round-(NN+1)/prompt.md 上再改 → 重写 diff.md → 再问
- **回滚**：用户不满意，删 round-(NN+1)/，回到 Phase F 总结（用最佳的之前轮次作为最终版）
- **停**：直接进 Phase F

## criteria.json 的演化

默认每轮**复用同一份 criteria**（rule 集合不变）。但以下情况要重抽：

1. **prompt 结构性改了**（加了新章节 / 改了核心架构）→ 提示用户"prompt 结构变了，要重抽 criteria 吗？"
2. **bad_case 暴露新 failure pattern** 在现有 criteria 没有对应 rule → skill 在 suggestions.md 中提案添加新 rule → 用户审过 → 加进 criteria.extra_rules[] → 不 bump rubric 版本

如果 rule 集合显著变了（加 ≥3 条 hard_fail_boundary） → 应该 bump rubric 版本，曲线分段画。

## 跨轮 prompt 元数据

每轮 prompt.md 顶部可加注释（YAML frontmatter）：

```yaml
---
round: round-02
based_on: round-01
delta_tokens: +132
proposed_changes:
  - r11 ASR 残片处理强化
  - r13-a 信息确认前置门槛
---
```

skill 可选写入。不写也可以（diff.md 已经覆盖元数据）。

## 失败处理

### Edit 失败

如果某条 Edit 的 old_string 不在 prompt 里：
- 可能 Suggester 引用错原文
- 让 Suggester 看到 prompt 实际内容 → 重写该条 change
- 或让用户手动指明

### 多条 change 冲突

如果改动 #2 和 #5 改了同一段（顺序敏感）：
- 按 suggestions.md 顺序应用
- 应用后 read 整段 → 让 Suggester 看是否符合两边意图
- 不符 → 让 Suggester 合并两条 change

### 用户中途停

用户在 Phase E.6 ★ 处选"停"：
- 保留 round-(NN+1)/prompt.md（即便未跑过）
- 进 Phase F 用现有数据总结
- Phase F 推荐版本时把 round-(NN+1) 标记为"未验证"

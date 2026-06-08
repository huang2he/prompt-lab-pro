# 能力地图（Capability Map）— Phase F 主交付物

## 本文档解决什么问题

skill 之前的 Phase F 输出是"分数曲线 + 推荐版本 X"。问题是：
- 分数只对**当前 agent A 模型**有意义，换模型就废
- 用户拿到"3.93 分"不知道**这套 prompt 能用来做什么、不能做什么**
- 隐含"无限迭代会一直提升"，实际上**hard rule 加多了边际收益递减 + 反弹**

v3.2 起 Phase F **不输出单一分数**，输出**能力地图**：在该 model 下这套 prompt 哪些指令稳定、哪些不稳定、哪些做不到，每档给出后续行动。

## 三档分类标准

按每条指令的最终 `pass_rate`（终轮 transcripts 中正确遵循的比例）：

| 档位 | 标准 | 含义 | 下一步 |
|---|---|---|---|
| **稳定遵循** | `pass_rate ≥ 95%` | 该 model + 该 prompt 已经稳定承载这条指令 | 不动，作为基线 |
| **不稳定** | `40% ≤ pass_rate < 95%` | 有空间但靠 prompt 改难突破天花板 | 可再迭代 1-2 轮（受 size budget 约束）|
| **完全做不到** | `pass_rate < 40%` | 该 model 上限，再加 hard rule 收益极小且容易反弹 | **承认上限**，给 escalation 路径 |

阈值是**软建议**：用户场景可以提到 90%/30% 或降到 99%/50%。但**永远是三档不是两档**——必须存在"做不到"档来强制承认上限。

## 输出文件格式（capability_map.md）

放在 `<workspace>/capability_map.md`。结构固定：

```markdown
# Capability Map · <scenario>

**Config**: agent A = <model>, prompt = round-N/prompt.md (<X> tokens), 
            evaluated on <M> transcripts × <K> persona

---

## ✅ 稳定遵循 (≥95% pass_rate) · <N> 条

| 指令 | pass_rate | 改善轨迹 |
|---|---|---|
| <指令文本> | 50/50 (100%) | r1=12/50 → r3=50/50 |
| ... | ... | ... |

→ 这些指令在该 model 下已经稳定，不需要再迭代。直接作为部署基线。

---

## ⚠️ 不稳定 (40-95%) · <M> 条

| 指令 | pass_rate | 改善轨迹 | 主要失败模式 |
|---|---|---|---|
| 终止后不重复인사 | 37/50 (74%) | r1=34/50 → r3=37/50 | LLM 必须输出非空，礼貌应答触发 loop |
| ... | ... | ... | ... |

→ **下一轮优化重点**。但注意 **prompt size budget**（≤ +10%），不能堆 rule。
   Suggester 应该考虑：旧规则合并/精简 → 让出预算给新规则。

---

## ❌ 完全做不到 (<40%) · <L> 条

| 指令 | pass_rate | 试过的修复 | 为什么 prompt 改不动 |
|---|---|---|---|
| 30 turn 内问完整问卷 | 16/50 (32%) | round-2 加 §11.5 / round-3 加 §4.5 | 状态维持是 LLM 自身限制，加 rule 反触发 trade-off |
| ... | ... | ... | ... |

→ **不要再加 prompt 规则**。Escalation 路径：

1. **换 model**（按 ROI 优先级排）
   - gpt-4o-mini → gpt-4o：预计完成率 32% → ~55%
   - gpt-4o → gpt-5-chat-latest：预计 → ~80%
   - 成本：每通 token ~1.5x / ~5x
2. **任务拆分**：把 30 turn 长任务拆成 N 个独立 sub-agent（每个上下文短、状态简单）
3. **接受 + fallback**：检测 agent 失败 → 升级人工 / 强 model 重跑

---

## 🔧 辅助工具问题（非 agent prompt 责任，独立诊断）

| 问题 | 出现率 | 建议 |
|---|---|---|
| end_checker 误判 "잘 모르겠어요" → end | 14/50 | 换 end_checker 用更强 model；或重写 end_description |
| dispatcher worker timeout | 0/50 | OK |
| persona 中途主动退出 (设计如此) | 5/50 | 不算失败 |

→ 这些**跟 agent A 的指令遵循无关**，单独优化。

---

## 部署建议（不输出"唯一最佳"，按业务取向 3 选 1）

- **完成率优先**：选 round-2（完成率 16/50，max_turns hit 仅 9/50）
- **不漏客户优先**：选 round-3（greet-only 1/50）
- **不稳定指令的 pareto 前沿**：选 round-X（trade-off 表附后）

trade-off 矩阵：
```
round | DQ5 done | BC2 loop | greet-only | total prompt tokens
r-01  |    13    |    16    |     3      |     3716
r-02  |    16    |     8    |     6      |     4502 (+21%) 
r-03  |    16    |    13    |     1      |     4892 (+32%) ← 已超 budget
```

→ size budget 提醒：r-03 比 r-01 超预算 (+32% vs +30% 阈值)。建议合并 §4.5 和 §11.5 内容压回 +20% 以内。

---

## 决策算法（写给 Phase F 实现）

```python
def classify_capabilities(per_instruction_pass_rate, threshold_stable=0.95, threshold_doable=0.4):
    stable, unstable, undoable = [], [], []
    for instr, rate in per_instruction_pass_rate.items():
        if rate >= threshold_stable:
            stable.append((instr, rate))
        elif rate >= threshold_doable:
            unstable.append((instr, rate))
        else:
            undoable.append((instr, rate))
    return stable, unstable, undoable

def escalation_for_undoable(instr, all_runs):
    """对每条 undoable 指令，看跨轮轨迹决定 escalation 类型。"""
    rates = [r.pass_rate(instr) for r in all_runs]
    if max(rates) < 0.2:
        return "this model literally can't do it — must switch model"
    elif rates[-1] < rates[-2]:
        return "rule additions are causing regression — undo or task split"
    else:
        return "slight improving but ceiling near; switch model or accept + fallback"
```

## 何时停迭代

Skill 在 Phase E 末（每轮 E7 gate）应该主动建议停止当：

1. **"不稳定"档**的 top-3 指令连续 2 轮 pass_rate 没动 ± 3pp → 模型已经到上限
2. **size budget 用完**（prompt 已 ≥ +30% vs baseline）且没有可精简空间 → 该停了
3. **新加规则让"稳定"档掉档**（regression）→ 立即回滚 + 停止
4. **用户明确说够了** → 停

## 与 leaderboard.json 的关系

`leaderboard.json` 仍记跨轮 KPI（不去掉，向下兼容）。但 Phase F 主交付物**不是 leaderboard 表，是 capability_map.md**。

leaderboard.json 适合机器对比；capability_map.md 适合人读 + 决策部署。

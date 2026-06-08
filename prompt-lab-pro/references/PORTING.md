# 在非 Claude Code 宿主中运行 prompt-lab

本文件只针对**非 Claude Code 宿主**（Codex CLI / OpenClaw / Qwen agent / 自研 LLM agent 等）。
在 Claude Code 中直接走 SKILL.md 主流程即可，无须阅读本文。

---

## 1. 兼容性总览

prompt-lab 的可移植性按层划分：

| 层 | 内容 | 跨宿主可移植性 |
|---|---|---|
| **被测对象** | 远端 dispatcher 调用的 agent A / agent B / end_checker | ✓ 完全自由（模型/厂商任意） |
| **指令文档** | SKILL.md + 13 份 references/*.md | ✓ 纯 Markdown，可整体作为 system prompt 注入 |
| **6-subagent 并行打分** | scoring-pipeline.md Phase E2 | △ 需要替换为该宿主的并行机制，或回退到串行 |
| **Skill 自动 trigger** | description 字段触发词 | ✗ 仅 Claude Code 有该机制，其他宿主需手动加载 |
| **TodoWrite / Task tool** | 进度跟踪 | △ 改用宿主的等价物（Codex 的 task list / 自行管理） |

**结论**：被测对象部分天生 vendor-agnostic；宿主侧需要做的事集中在「加载 skill 内容 + 处理 6-subagent 并行」两点。

---

## 2. 加载 skill 内容（所有非 CC 宿主通用）

由于其它宿主没有"用户说触发词就自动加载 skill"的协议，需要**手动注入**。两种方式：

### 方式 A：把 SKILL.md 当 system prompt（推荐）

```bash
# Codex CLI 示例
codex --system-prompt-file ~/.claude/skills/prompt-lab/SKILL.md "用 prompt-lab 帮我迭代这个 prompt..."

# 或通用 shell：把 SKILL.md 内容拼到 prompt 前
SKILL_TEXT=$(cat ~/.claude/skills/prompt-lab/SKILL.md)
your-agent "$SKILL_TEXT

---

用户请求：用 prompt-lab 帮我迭代汽车外呼 prompt"
```

### 方式 B：把 SKILL.md 作为外部知识库挂载

把 `~/.claude/skills/prompt-lab/` 整个目录挂为该宿主的知识库 / 工具调用上下文，
然后告诉 agent："参考目录 `prompt-lab/SKILL.md` 的 SOP 帮我跑 6 阶段流程"。

**references/*.md 不需要预加载**——按 SKILL.md 中 Phase A/B/C/D/E/F 的指示按需 Read 就行，
这套 progressive disclosure 与宿主无关。

---

## 3. 处理 6-subagent 并行打分（Phase E2）

这是唯一一处对 Claude Code 特性有依赖的地方。`references/scoring-pipeline.md` 默认让宿主同时 dispatch 6 个独立上下文的子任务，
每个负责 ~50 通对话的评分。可选三种替代方案：

### 方案 1：宿主原生并行 subagent（首选）

- **Codex CLI**：用 `codex agent.spawn` 或 task 并行 API（具体语法以 Codex 当前版本为准）。
- **OpenClaw**：用 OpenClaw 的 sub-agent / parallel-task API。
- **自研 agent**：起 6 个独立 conversation，每个塞 ~50 通对话的评分 batch。

输出契约不变：每个 subagent 写一份 `judgments_<batch>.json`，主 agent 跑 `scripts/merge_scores.py` 合并。

### 方案 2：单宿主串行 6 batch（无并行能力时的 fallback）

如果宿主完全没有 subagent / 并行 task 机制（很多轻量级 agent CLI 是这样），改成 6 次顺序调用：

```
for batch in batch_1..batch_6:
    把 batch 的 ~50 通对话 + rubric + persona 信息塞进 prompt
    让 agent 输出 judgments_<batch>.json
    保存
合并 6 份 judgments → scores.json
```

代价：~6× wall time（评分阶段从 ~5 min 拖到 ~30 min）。结果一致性不变。

### 方案 3：把 6 batch 合并成 1 个长 batch（最简，但精度可能下降）

把 ~300 通对话一次性塞进单 batch 评分。
**注意**：单 batch 超过 ~50 通后，宿主模型对 rubric 的注意力会衰减，评分质量下降。建议只在快速 smoke 时用，正式轮次仍走方案 1/2。

---

## 4. TodoWrite / 进度跟踪

SKILL.md 在 Phase D/E 会用 TodoWrite 跟进度。Claude Code 之外宿主无此工具，可：

- **有等价物**：替换为宿主自己的 task list / TODO 管理。
- **没有**：在 workspace 下写一个 `progress.md`，每完成一步 append 一行。

这一项纯 cosmetic，不影响 skill 正确性。

---

## 5. 被测对象（agent A / agent B / end_checker）

**完全不受宿主影响**。Phase A intake 时用户填的 `dispatcher_url` 决定了被测 agent 跑在哪：

- 远端是 qwen-plus → 测的就是 qwen 表现
- 远端是 GPT-4o → 测的就是 GPT 表现
- 远端是 Claude Haiku → 测的就是 Claude 表现

dispatcher URL + API key 由用户在 intake 时提供，prompt-lab 不持有任何凭证，也不绑定任何模型厂商。

---

## 6. 已知限制与建议

| 限制 | 影响 | 缓解方式 |
|---|---|---|
| 非 CC 宿主无自动 trigger | 用户得手动 `cat SKILL.md` 注入 | 写个 wrapper 脚本（`prompt-lab-codex.sh` 之类） |
| 部分宿主对长 system prompt 有截断 | SKILL.md ~280 行，多数宿主可承受；若超限，把详细 reference 移到外部并改 Read 调用 | 检查宿主 system prompt 长度限制 |
| 宿主无 Read 文件能力（极端 case） | 无法按需加载 references/*.md | 把全部 references 拼进 system prompt（约 ~5K 行），对话 token 占用变大 |
| 宿主无 Bash / 文件写入 | scripts/ 无法跑、workspace 无法落盘 | 这种宿主不适合跑 prompt-lab，建议换支持 shell 的 agent |

---

## 7. 验证移植是否成功

跑一次 smoke probe（Phase D）就能验证：

1. 注入 SKILL.md，让 agent 执行 Phase A intake（多轮问答收集 9 个输入）。
2. Phase B 在 workspace 建目录、写 config.json。
3. Phase D 用 1-3 个 persona × 1 repeat 跑通 chat→chat_id→poll→transcripts.jsonl 全链路。

如果 Phase D 拿到至少 1 条 transcript 且能产出 scores.json，整套就跑得通。
正式迭代时再扩到完整 persona 池 + 6 subagent 评分。

---

## 8. 反馈

非 CC 宿主跑通后，欢迎在 GitHub issue 里报告：
- 宿主名称 + 版本
- 用的哪个方案（1/2/3）
- 遇到的坑

地址：https://github.com/huang2he/prompt-lab/issues

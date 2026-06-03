# prompt-lab-pro

Dispatcher-native upgrade of [prompt-lab](https://github.com/huang2he/prompt-lab).

继承 prompt-lab v3.4 的 6 阶段 SOP（A→F）+ rubric v3（5 维度含 `conversation_flow`）+ 三档分类 + capability map + Case A-D Round-to-Round 决策树。**两处升级**：

1. **Phase E.2 评分** 走 dispatcher `/evaluation_batch`（替代 6-subagent dispatch）
   - 本地 Claude 主会话拼 `evaluator_prompt.md`（思考工作）
   - dispatcher 服务端并发执行（执行工作）
   - Self-consistency + multi-evaluator ensemble
   - 实测比 prompt-lab subagent 模式快 2.6×，且不撞 Claude rate limit

2. **Phase E ASR 噪声** 映射到 dispatcher 原生字段
   - `user.silence_rate / silence_message / asr_failure_rate / asr_failure_message`
   - dispatcher 按概率确定性插入扰动，每轮可重放
   - 不再让 user-side LLM 假装演噪声

## 安装

prompt-lab-pro 大部分 references 和 2 个 scripts 通过**相对路径 symlink** 共享 prompt-lab 仓库的内容。所以**两个 repo 必须是兄弟目录**：

```bash
# 1. 先克隆 prompt-lab（必需 — 提供共享 references/scripts）
cd ~/somewhere/MY\ PROJECT/
git clone https://github.com/huang2he/prompt-lab.git prompt-lab-skill-repo

# 2. 再克隆 prompt-lab-pro（兄弟目录）
git clone https://github.com/huang2he/prompt-lab-pro.git prompt-lab-pro-skill-repo

# 3. 安装到 Claude Code skills 目录
ln -sf "$(pwd)/prompt-lab-pro-skill-repo/prompt-lab-pro" ~/.claude/skills/prompt-lab-pro
ln -sf "$(pwd)/prompt-lab-skill-repo/prompt-lab" ~/.claude/skills/prompt-lab  # 如未装
```

目录结构（关键）：

```
MY PROJECT/
├── prompt-lab-skill-repo/          ← prompt-lab 仓库
│   └── prompt-lab/                 ← skill 真身
│       ├── SKILL.md
│       ├── scripts/
│       │   ├── load_dispatcher.py  ← prompt-lab-pro 共享
│       │   └── network_mode.py     ← prompt-lab-pro 共享
│       ├── references/             ← 大部分被 pro 共享
│       └── .env                    ← token 持久化文件（两个 skill 共用）
│
└── prompt-lab-pro-skill-repo/      ← 本仓库
    └── prompt-lab-pro/
        ├── SKILL.md                ← 实体（pro 独有）
        ├── scripts/
        │   ├── load_dispatcher.py  → ../../../prompt-lab-skill-repo/prompt-lab/scripts/load_dispatcher.py
        │   ├── network_mode.py     → ../../../prompt-lab-skill-repo/prompt-lab/scripts/network_mode.py
        │   ├── run_round.py        ← 实体（fork：加 ASR 噪声字段）
        │   └── judge_via_dispatcher.py  ← 实体（pro 独有：dispatcher 评分）
        ├── references/
        │   ├── intake.md            ← 实体（fork：Q2/Q3-D 改）
        │   ├── scoring-pipeline.md  ← 实体（fork：E.2 改写）
        │   ├── api-call-params.md   ← 实体（fork：加 /evaluation_batch）
        │   └── <12 个 .md>          → ../../../prompt-lab-skill-repo/prompt-lab/references/...
        ├── .env.example             → ../../prompt-lab-skill-repo/prompt-lab/.env.example
        └── .gitignore               → ../../prompt-lab-skill-repo/prompt-lab/.gitignore
```

## 首次使用

```bash
# 1. 配 dispatcher token（与 prompt-lab 共享）
python3 ~/.claude/skills/prompt-lab/scripts/load_dispatcher.py --save-token <TOKEN>

# 2. 在 Claude Code 里触发
# 说"用 prompt-lab-pro 跑 …"，或在支持 slash 命令的 UI 里 /prompt-lab-pro
```

## 何时用 prompt-lab-pro（vs 上游 prompt-lab）

| 场景 | 选哪个 |
|---|---|
| 首次跑 prompt 优化、想学方法论 | `prompt-lab` |
| **大样本评分**（≥30 通） | **prompt-lab-pro** |
| **多 evaluator ensemble**（cross-model 评分对照） | **prompt-lab-pro** |
| **要求 self-consistency**（同 transcript 评 3 次取众数） | **prompt-lab-pro** |
| **ASR 鲁棒性测试要可重放** | **prompt-lab-pro** |
| dispatcher 不可达 / 仅本地评分 | `prompt-lab`（inline 兜底） |

## 详细文档

- [`SKILL.md`](prompt-lab-pro/SKILL.md) — 总体设计 + Phase A-F 流程
- [`references/scoring-pipeline.md`](prompt-lab-pro/references/scoring-pipeline.md) — E.2a/b 评分链路 + Self-consistency
- [`references/intake.md`](prompt-lab-pro/references/intake.md) — Q2 ASR 噪声映射 + Q3-D Judge 配置
- [`references/api-call-params.md`](prompt-lab-pro/references/api-call-params.md) — `/evaluation_batch` 完整 schema

## License

跟 prompt-lab 一致。

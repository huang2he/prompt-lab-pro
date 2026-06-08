# prompt-lab-pro

Dispatcher-native upgrade of [prompt-lab](https://github.com/huang2he/prompt-lab). End-to-end prompt iteration SOP — eval, score, rewrite, repeat.

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

> **v1.1 已独立**：所有 references 和 scripts 都 inline 在本仓库，不再依赖 prompt-lab 共存。

---

## 一键安装

```bash
git clone https://github.com/huang2he/prompt-lab-pro.git ~/.claude/skills/prompt-lab-pro
```

完成。重启 Claude Code 即可识别 skill。

> 如果 `~/.claude/skills/prompt-lab-pro` 已存在，先删了再 clone：`rm -rf ~/.claude/skills/prompt-lab-pro && git clone ...`

---

## 上手

详见 [QUICKSTART.md](QUICKSTART.md) — 5 分钟从安装到出第一份评分。

最简启动方式（在 Claude Code 里输入）：

```
用 prompt-lab-pro 帮我评估 ~/path/to/your/prompt.md
```

首次运行时 skill 会问 dispatcher 凭证（向项目负责人拿，Basic Auth 或 X-Access-Token），落盘到 `~/.claude/skills/prompt-lab-pro/.env`，之后不再问。

---

## 何时用 prompt-lab-pro（vs 上游 prompt-lab）

| 场景 | 选哪个 |
|---|---|
| 首次跑 prompt 优化、想学方法论 | `prompt-lab` |
| **大样本评分**（≥30 通） | **prompt-lab-pro** |
| **多 evaluator ensemble**（cross-model 评分对照） | **prompt-lab-pro** |
| **要求 self-consistency**（同 transcript 评 3 次取众数） | **prompt-lab-pro** |
| **ASR 鲁棒性测试要可重放** | **prompt-lab-pro** |
| dispatcher 不可达 / 仅本地评分 | `prompt-lab`（inline 兜底） |

---

## 目录结构

```
prompt-lab-pro/                      ← repo 根 = skill 入口
├── SKILL.md                          总体设计 + Phase A-F 流程
├── QUICKSTART.md                     5 分钟新手指南
├── .env.example                      dispatcher 凭证模板
├── .gitignore                        屏蔽 .env / __pycache__
├── scripts/
│   ├── load_dispatcher.py            URL+token 解析（首次问 token 后落盘）
│   ├── network_mode.py               海外/国内 base_url → network/proxy 路由
│   ├── run_round.py                  Phase D smoke + Phase E.1 simulation
│   └── judge_via_dispatcher.py       Phase E.2b dispatcher 评分（ensemble + self-consistency）
└── references/
    ├── intake.md                     Q0-Q8 引导 + Q2 ASR 噪声映射 + Q3-D Judge
    ├── scoring-pipeline.md           E.2a 拼 evaluator.prompt + E.2b dispatcher 执行
    ├── api-call-params.md            /chat /simulation /evaluation_batch schema
    ├── criteria-extraction.md        Phase C 动态抽 criteria
    ├── persona-sources.md            Q2 三选一（已有 / 自动抽 / 真实 transcript）
    ├── smoke-probe.md                Phase D 探活
    ├── iterate-loop.md               Phase E 主循环
    ├── capability-map.md             Phase F 能力地图
    ├── rubric-framework.md           5 维度 + 1-5 制 + hard_fails 6 类
    ├── failure-types.md              hard_fails 闭枚举
    ├── suggestion-writing.md         E.4 改写建议生成
    ├── prompt-iteration.md           E.5 应用 suggestions → round-(K+1)
    ├── workspace-layout.md           workspace 目录约定
    ├── dashboard-build.md            可选：把跑分结果出 dashboard
    ├── PORTING.md                    在 Codex / Cursor / 自研宿主上跑
    └── criteria-templates/
        └── voice-outbound-zh-auto-sales.json
```

> 注：`voice-outbound-zh-auto-sales.json` 是汽车外呼场景的**预制评分标尺**模板，仅用于「N 个 prompt 在同标尺下横向对比」的场景。新业务跑时 skill 走默认动态抽取，不套用这个模板。

---

## Dispatcher API（外部依赖）

skill 通过 HTTP 调用一个 dispatcher 服务（baked-in URL `http://47.100.137.178:8080`）：

- `GET /healthz` — 探活
- `POST /chat` — Phase D smoke
- `POST /simulation` — Phase E.1 批量异步对话
- `POST /evaluation_batch` — Phase E.2b 批量评分

完整 API 契约由 dispatcher 自服务：

```bash
curl -u USERNAME:PASSWORD http://47.100.137.178:8080/skill.md
```

鉴权方式（择一）：
- **HTTP Basic Auth**（推荐）：`-u USERNAME:PASSWORD`
- **X-Access-Token**（legacy）：`-H "X-Access-Token: <TOKEN>"`

> **要部署自己的 dispatcher 实例**：设 env var `PROMPT_LAB_DISPATCHER_URL=http://your-host:port`，skill 自动用新地址。

---

## 详细文档

- [QUICKSTART.md](QUICKSTART.md) — 5 分钟新手指南
- [SKILL.md](SKILL.md) — 总体设计 + Phase A-F 流程
- [references/scoring-pipeline.md](references/scoring-pipeline.md) — E.2a/b 评分链路 + Self-consistency
- [references/intake.md](references/intake.md) — Q2 ASR 噪声映射 + Q3-D Judge 配置
- [references/api-call-params.md](references/api-call-params.md) — `/evaluation_batch` 完整 schema

---

## License

跟 prompt-lab 一致。

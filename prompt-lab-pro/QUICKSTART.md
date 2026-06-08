# prompt-lab-pro · 5 分钟上手指南

> 面向第一次使用的小白用户。不需要前置经验，跟着每一步做即可。

## 它能做什么

你给一个 prompt（聊天机器人 / 语音 agent / 外呼客服），它会：

1. 自动生成模拟客户跟你的 prompt 对话 N 通
2. 用 LLM 评委按多维度打分
3. 找出 prompt 在哪些场景稳定/不稳定/做不到
4. 给出下一版 prompt 的改写建议

适合：聊天机器人、语音 agent、外呼客服等**多轮对话**类 prompt。
不适合：一次性生成任务（写文案/翻译/总结）。

---

## 准备工作

### 1. 装 Claude Code CLI（一次性）

去 https://claude.com/claude-code 下载安装。
安装完打开终端，输入 `claude` 确认能进对话界面。需要登录 Anthropic 账号。

> **必须用 Claude Code**：本工具是 Claude Code 的 skill 插件，网页版 claude.ai 不能用。

### 2. 装 git 和 Python（一次性）

终端检查：

```bash
git --version       # 没有就装 https://git-scm.com
python3 --version   # 没有就装 https://www.python.org/downloads/
```

### 3. 拿到 dispatcher 凭证

找项目负责人要：

- HTTP Basic Auth：`username` + `password`（**推荐**）
- 或 X-Access-Token（legacy，仍兼容）

**dispatcher 地址 baked-in 在 skill 里（`http://47.100.137.178:8080`），你不用填**。如需自部署，请设 env var `PROMPT_LAB_DISPATCHER_URL`。

---

## 安装 skill

终端执行（复制粘贴运行）：

```bash
mkdir -p ~/.claude/skills
cd ~/.claude/skills
git clone https://github.com/huang2he/prompt-lab-pro.git
```

> **注意**：v1.1 起 skill 已完全独立，**不再依赖 prompt-lab**。只 clone 这一个 repo 就够。

完成。重启 Claude Code（如果在运行中），它会自动识别新 skill。

---

## 第一次跑（约 15 分钟，多数时间是等评分）

### Step 1 · 准备你的 prompt 文件

在任意位置建一个 `.md` 文件，里面写你的 prompt。例如：

```bash
mkdir -p ~/my-prompt-test
cat > ~/my-prompt-test/prompt.md <<'EOF'
你是一名客服，帮用户解决退款问题。说话简短自然，每轮一个问题。
...（你的 prompt 全文）...
EOF
```

> 也可以直接用现有的 prompt 文件路径，不必新建。

### Step 2 · 在 Claude Code 里启动

```
claude
```

进入对话后输入：

```
用 prompt-lab-pro 帮我评估 ~/my-prompt-test/prompt.md
```

接下来 skill 会**逐 phase 引导提问**，按下面回答即可。

---

## 逐 Phase 怎么回答

skill 按 A → F 6 个阶段走。每个阶段它问、你答。

### Phase A · 输入收集

| 问题 | 怎么答 |
|---|---|
| **dispatcher token**（仅首次） | 粘贴负责人给的 token，会落 `~/.claude/skills/prompt-lab-pro/.env` 持久化 |
| **Q1 基准 prompt** | 输入你的 prompt 文件路径 `~/my-prompt-test/prompt.md` |
| **Q2 测试集来源**（三选一） | **没有现成数据选 (b)** 从 Q1 prompt 自动抽 persona；有 persona JSON 选 (a)；有真实通话记录选 (c) |
| **Q2 ASR 噪声等级** | 第一次选 **「不加」** 或 **「全 light」**；调通了再加 |
| **Q3-A Agent A**（被测模型） | 输入 base_url + API key + 模型名（如 `qwen-plus`）。不知道用啥就问负责人 |
| **Q3-B Agent B**（模拟客户） | 通常和 A 一样的 provider 但用更便宜模型（如 `qwen-flash`）省钱 |
| **Q3-C end_checker** | 选默认或同 A |
| **Q3-D Judge 评委** | 选默认 `gpt-5.5` 或 `qwen-plus`（**强制远端 dispatcher**） |
| **Q4 跑几轮迭代** | 第一次选 **1 轮**，看效果再决定 |
| **Q5 K 每 persona 跑几次** | 选默认 **K=1** |
| **Q6 开场白** | 看你 prompt 场景。客服类填 `您好，请问有什么可以帮您？` |
| **Q7 场景描述** | 一句话描述业务，如「电商退款客服」 |
| **Q8 workspace 路径** | 默认就行，生成在 `~/prompt-lab-workspaces/` |

> **高级参数**（max_turns / temperature / timeout）默认即可，问完上面再统一调。

### Phase B · 落盘 config（自动）

skill 自动建 workspace 目录、写 `config.json`，不需要你操作。看到进 Phase C 就行。

### Phase C · Criteria 签字 ★ 用户 gate

skill **基于你 Q1 的 prompt 动态抽出**评分维度（不是套用现成模板）：

- Suggester 读你的 prompt → 抽 `business_goals[]` + `behavior_rules[]`
- 每条规则带 `prompt_source` 指明源自 prompt 的哪一段
- 显示给你看 → 你 **gate**：「通过 / 改第 N 条 / 重抽」
- 第二次 gate：可手动加自然语言规则（如「30 秒内必须自报家门」），Claude 帮转结构化

**所以同事跑不同业务（电商 / 银行 / 教育）时，抽出来的 criteria 完全不同。**

跨轮规则：round-01 签字后 round-02+ 默认复用；prompt 结构大改时 skill 主动问要不要重抽。

> ⚠️ `references/criteria-templates/voice-outbound-zh-auto-sales.json` 是个**汽车外呼场景的预制模板**，仅用于「多个 prompt 在同标尺下横向对比」的场景，不会默认套用。新业务不用管它。

### Phase D · Smoke probe（约 1 分钟）

skill 跑 3 通对话探活，看 dispatcher 是否通、模型是否能正常响应。
**看到「smoke probe pass」就继续，看到 fail 检查 token / 网络**。

### Phase E · 主循环 · Round 01（约 10-15 分钟）

跑你设定的 persona 数（默认 30-50）：

1. **E.1** 跑 simulation（每个 persona 跑 K 次对话）
2. **E.2a** Claude 拼评分 prompt → 让你 gate（看一眼 OK 放过）
3. **E.2b** dispatcher 并发评分（这步等的时间最长，约 5-8 分钟）
4. **E.3** 显示三档结果：**稳定 ≥95% / 不稳定 40-95% / 做不到 <40%**
5. **E.4** 生成 suggestions（改写建议）
6. **E.6** 显示 prompt diff（你看一眼新 prompt 改了什么）
7. **E.7** ★ 用户决定：再跑一轮 / 收尾 / 调参数

### Phase F · 收尾

输出 capability map：你的 prompt 在哪些场景**稳定能做**、哪些**不稳定**、哪些**做不到**。

**这才是最终交付物**——不是单一分数，是一张「能力地图」。

---

## 跑完看什么文件

workspace 目录长这样：

```
~/prompt-lab-workspaces/<场景名>-<时间戳>/
├── config.json                ← 这次的配置
├── round-01/
│   ├── prompt.md              ← 这轮用的 prompt（=Q1 输入）
│   ├── transcripts.jsonl      ← 跑出来的对话（N 通）
│   ├── judgments.jsonl        ← 评委逐条评分
│   ├── judgments_aggregated.json  ← 汇总分数
│   └── suggestions.md         ← 模型给的下一轮改写建议
└── round-02/
    └── prompt.md              ← 已经按 suggestions 改好的下一版 prompt
```

**重点看 3 个文件**：
1. `judgments_aggregated.json` — 总分 + 各维度分
2. `suggestions.md` — 模型说该怎么改
3. `transcripts.jsonl` — 找几通你最关心的看实际对话好不好

---

## dispatcher 是什么

`dispatcher`（运行在 `http://47.100.137.178:8080`）是后端的 LLM 调度服务。skill 把 N 通对话 + N 次评分都 fan-out 给 dispatcher 服务端并发执行，比本地跑快 ~2.6×、不撞 Claude 限。

skill 跟它打交道的接口：
- `GET /healthz` — 探活
- `POST /chat` — 单次对话（Phase D smoke）
- `POST /simulation` — 批量异步对话（Phase E.1）
- `POST /evaluation_batch` — 批量评分（Phase E.2b）

完整 API 契约：
```bash
curl -u USERNAME:PASSWORD http://47.100.137.178:8080/skill.md
```

---

## 常见问题

**Q：dispatcher token 在哪里改？**
A：`~/.claude/skills/prompt-lab-pro/.env`，直接编辑。

**Q：报 "dispatcher unreachable" 或 502？**
A：先 `curl -u user:pass http://47.100.137.178:8080/healthz` 探活。如果 502 是上游服务挂了，联系负责人。

**Q：跑了一通发现 prompt 改错方向，能回退吗？**
A：能。每轮的 `round-N/prompt.md` 都存着，cp 回 Q1 用旧版重跑即可。

**Q：分数不高，是 prompt 烂还是评委严？**
A：看 Phase E.3 的「三档分布」。如果「做不到 <40%」的项很多，是 prompt 真的弱；如果只是没拿满分但「稳定 ≥95%」覆盖了核心需求，prompt 已经够用。

**Q：能不能并行跑多个 prompt 对比？**
A：能，但要并行启 N 个 Claude Code 会话，每个会话单独跑。或者跑完一个再换 Q1 跑下一个。

**Q：跑了几轮分数稳定了，怎么收尾？**
A：在 E.7 选「收尾」，进 Phase F 自动出 capability map。

---

## 不知道怎么办时

- skill 本身的 bug / 改进建议：https://github.com/huang2he/prompt-lab-pro/issues
- dispatcher / 凭证 / 网络问题：找项目负责人
- 不会写 prompt：先用 Q2 (b) 自动抽 persona 跑一遍，看模型生成的 persona 长什么样，倒推你应该怎么写 prompt

---

## 强烈建议

1. **第一次跑用 1 轮 + 30 persona**，跑通整条链路再扩大
2. **不要一开始就开 ASR 噪声**，调通基础再加干扰
3. **每轮 prompt 涨幅控制在 +10% 字数以内**，否则会变成"加一堆约束"而不是"优化"
4. **关注 capability map（Phase F），不要盯单一分数**——分数会受评委波动，能力地图反映 prompt 的真实边界

---

## 已验证状态（2026-06-08）

| 项 | 状态 |
|---|---|
| skill 自身独立（不依赖 prompt-lab repo） | ✅ v1.1 |
| dispatcher 4 个核心 API | ✅ healthz / chat / simulation / evaluation_batch 全部实测通过 |
| Phase A→F skill 引导流程 | 静态验证通过；首次端到端 skill 实测请自行走一遍小 case 验证 |

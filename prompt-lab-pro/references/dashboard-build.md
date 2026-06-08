# Dashboard Build (Phase F)

skill 在 Phase F（也包括每轮 E 结束）调 `scripts/build_dashboard.py` 生成 `dashboard.html`——单文件 HTML，纯 inline SVG/CSS，**无 CDN 依赖**，file:// 可开。

## 触发时机

- 每轮 Phase E 结束自动重建（覆盖原文件）
- Phase F 收尾最终一次（含所有轮总览）

## Dashboard 内容

按从上到下：

### 1. Hero KPI

5 个 KPI 卡（最新轮 vs round-01 baseline）：
- Overall mean
- Pass rate
- Hard fails 总数
- 当前 prompt token 数
- Transcripts 数

每卡含 delta vs round-01 + 上下箭头颜色编码。

### 2. Iteration Timeline

SVG 节点 + 箭头：
- 每轮一个圆点，圆点颜色按 overall（>=4.0 绿，3.5-4 黄，<3.5 红）
- 圆点之间连线 + 上方标 ↑/↓ delta
- 圆点下方标 round name + pass rate

### 3. Score Evolution Charts

两张曲线图并排：
- Overall + Pass Rate（pass×5 同图）
- 5 dimensions（instruction_adherence / goal_completion / conversation_flow / asr_robustness / naturalness）— v3 起，v2 段的 cf 列画 N/A

### 4. Token Budget + Hard Fails

两张曲线：
- prompt token 数曲线（若 config.token_ceiling 设了则含红线，否则不画）
- Hard fails 总数曲线

### 5. Rule Violation Heatmap

行 = rule（按 max 违反率排序），列 = round，单元格颜色按违反率深浅。

### 6. Goal Completion Matrix

行 = goal（g1-g7），列 = round，单元格颜色按 done 率（绿→红）。

### 7. 每轮详情区

每轮一个 `<details>` 折叠块：
- 基础统计表
- 上一轮的 delta
- diff.md 节选（折叠）
- suggestions.md 节选（折叠）
- Top 5 bad cases，**内联完整 transcript 对话**（也折叠）

### 8. 轮间 Delta 表

简表展示每轮 → 下轮的 overall delta + 方向。

## 文件输出

```
<workspace>/prompts/<id>/iterations/dashboard.html
```

文件大小：
- 1 轮 ~30KB
- 5 轮（含 inline transcripts）~150KB
- 极端情况（10 轮、巨长 transcripts）~500KB

## 单文件 + 无依赖原则

- 所有 SVG 直接 inline
- 所有 CSS 直接 `<style>` inline
- 无 CDN（如 Chart.js）
- 无 fetch（不读外部文件）
- file:// 直接打开

## 实现：scripts/build_dashboard.py

skill 在 Phase B bootstrap 时把该脚本拷到 `<workspace>/scripts/`。脚本读：
- 所有 round-*/scores.json
- prompts/<id>/iterations/round-*/diff.md / suggestions.md / transcripts.jsonl
- 用 tiktoken（pip install 时已经装）算 prompt token

输出 → dashboard.html（覆盖写入）。

## 中文场景注意

dashboard 文字默认 system font，对中文友好（`-apple-system, "Helvetica Neue", "PingFang SC"`）。在英文场景换成 `sans-serif` 即可。

## 用户怎么看 dashboard

skill 完成 Phase F 后告诉用户：
> "Dashboard 在 `<path>`，浏览器打开（或 macOS 用 `open <path>`）。"

不主动 open（避免烦），让用户自决。

## 如果脚本失败

build_dashboard.py 跑失败：
- 显示错误给用户
- 让用户修复（一般是某轮 scores.json 损坏）
- skill 退而求其次：显示文本格式的总结

不让 dashboard 失败阻塞 Phase F 主任务（用户拿到 round 总结比有 HTML 更重要）。

# Pipeline 优化建议

基于 2026-03-01 运行 `main.py --last_n 2 --force-summary` 的观察。

---

## P0 — Bug

### 1. 空 `_parsed.txt` 被送入 summary

`2026-02-23` 的 `_parsed.txt` 是 0 字节（parse 失败但创建了空文件），summary 仍然把它收进去。LLM 输出了"转录内容尚未提供，无法进行分析"——浪费 tokens 且报告质量下降。

**涉及文件:** `main.py` (L229-243), `src/agent/parser.py`

**修复方案（二选一）:**
- `main.py` 收集 parsed 文件时加 `os.path.getsize(path) > 0` 过滤
- `parser.py` 在 parse 失败时不创建/删除空文件，避免 `_parsed.txt` 存在但为空

---

## P1 — 可靠性

### 2. `Impersonate 'chrome_101' does not exist` 警告

日志出现 `Impersonate 'chrome_101' does not exist, using 'random'`，来自 `ddgs` (DuckDuckGo search) 库。目前 fallback 到 random 能工作，但将来可能导致搜索失败。

**修复方案:** 升级 `ddgs` 或显式指定有效的 impersonate profile。

### 3. `get_latest_videos` 没有异常处理

`youtube.py:102` 的 `ydl.extract_info()` 网络超时或 YouTube 返回错误时会直接抛异常，导致整个 pipeline 崩溃。

**涉及文件:** `src/pipeline/youtube.py`

**修复方案:** 加 try/except，失败时返回空列表并打印警告，保持和之前 RSS 的容错行为一致。

---

## P2 — 效率

### 4. `.report/` 目录累积历史报告无清理机制

每次 `--force-summary` 都生成新文件，没有清理。

**修复方案:** 保留最近 N 份或按日期去重。

---

## P3 — 代码质量

### 5. `main.py` 和 `cli.py` 大量重复逻辑

`main.py` 的 Phase 1 基本是 `cmd_check` + `cmd_transcript` + `cmd_parse` 的手动内联版本。改了任何一个步骤的逻辑，两边都要同步改。

**修复方案:** 让 `main.py` 直接调用 `cli.py` 里的子命令函数，或抽取公共函数。

### 6. `approximate_date` 拿不到时 fallback 到 `datetime.now()`

`youtube.py:120` — 如果 `timestamp` 为 None，存储目录名会用当前时间而非发布时间。

**修复方案:** verbose 模式下打一条警告，提示该视频的发布时间为近似值。

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.agent.claude_runner import run_claude_session

SINGLE_PROMPT = """\
你是一个专业的财经视频内容分析助手。以下是来自 YouTube 频道「{channel_name}」于 {date} 发布的视频转录文本。

## 任务

请对以下转录内容进行结构化总结：

### 1. 市场观点总结
总结博主对当前市场的整体观点和判断，包括对大盘走势、宏观经济、政策影响等方面的看法。
- 使用 Markdown 无序列表（bullet list）分点总结，按主题分条
- 每条可包含嵌套子点补充细节

### 2. 买入/加仓/卖出点位提取
从文本中提取博主明确提到的操作建议：
- **买入点位**：股票代码 + 建议买入价格/区间 + 理由
- **加仓点位**：股票代码 + 建议加仓价格/区间 + 理由
- **卖出点位**：股票代码 + 建议卖出价格/区间 + 理由

### 3. 卖出点位补充规则
- **如果博主没有推荐任何具体的买入/加仓/卖出操作**，则不使用搜索工具，只总结内容和观点即可。
- **如果博主推荐了买入或加仓但没有给出卖出点位**，请对每只缺少卖出点位的股票**逐只**使用 web search 查询该股票的目标价（target price）或阻力位（resistance level），并在结果中标注"（网络搜索补充）"。
- Web search 仅用于查询卖出点位，不用于其他用途。

## 输出格式

请使用 Markdown 格式输出，严格按照以下模板。不要输出转录文本，不要添加思考过程或前言。

---

# {channel_name} — 视频标题

**日期：** {date}

## 市场观点

- 主题一：概述
  - 补充细节
  - 补充细节
- 主题二：概述
  - 补充细节

## 买入建议

| 股票代码 | 买入价格/区间 | 理由 |
|---------|-------------|------|
| XXXX | $xxx - $xxx | 理由 |

（如果没有买入建议，写"无（简短原因，例如：本期为宏观展望，未涉及具体标的）"，不需要表格）

## 加仓建议

| 股票代码 | 加仓价格/区间 | 理由 |
|---------|-------------|------|
| XXXX | $xxx - $xxx | 理由 |

（如果没有加仓建议，写"无（简短原因）"，不需要表格）

## 卖出建议

| 股票代码 | 卖出价格/区间 | 理由 |
|---------|-------------|------|
| XXXX | $xxx - $xxx | 理由 |

（如果没有卖出建议且没有买入/加仓建议，写"无（简短原因）"，不需要表格）
（如果有买入/加仓但缺少卖出建议，使用搜索补充，在理由列标注"（网络搜索补充）"）

## 转录文本

{text}
"""

MULTI_PROMPT = """\
你是一个专业的财经视频内容分析助手。以下是来自多个 YouTube 频道的多期视频转录文本。

## 任务

### 第一部分：综合交易信号汇总
先汇总所有频道/视频中提到的买入、加仓、卖出建议，合并到统一的表格中，表格需包含"来源频道"列标注出处。

### 第二部分：逐期市场观点
然后对每一期视频**逐期单独**总结市场观点（不再重复交易信号，已在第一部分汇总）。
- 使用 Markdown 无序列表（bullet list）分点总结，按主题分条
- 每条可包含嵌套子点补充细节

### 卖出点位补充规则
- **如果所有频道都没有推荐任何具体的买入/加仓/卖出操作**，则不使用搜索工具。
- **如果有频道推荐了买入或加仓但没有给出卖出点位**，请对每只缺少卖出点位的股票**逐只**使用 web search 查询该股票的目标价（target price）或阻力位（resistance level），并在结果中标注"（网络搜索补充）"。
- Web search 仅用于查询卖出点位，不用于其他用途。

## 输出格式

请使用 Markdown 格式输出，严格按照以下模板。不要输出转录文本，不要添加思考过程或前言。

# 每日美股总结

**日期：** {date}

## 综合交易信号

### 买入建议

| 股票代码 | 价格/区间 | 理由 | 来源频道 |
|---------|----------|------|---------|
| XXXX | $xxx - $xxx | 理由 | 频道名 |

（如果所有频道都没有买入建议，写"无——本期各频道均为宏观市场展望，未涉及具体操作建议。"，不需要表格）

### 加仓建议

| 股票代码 | 价格/区间 | 理由 | 来源频道 |
|---------|----------|------|---------|
| XXXX | $xxx - $xxx | 理由 | 频道名 |

（如果没有加仓建议，写"无（简短原因）"，不需要表格）

### 卖出建议

| 股票代码 | 价格/区间 | 理由 | 来源频道 |
|---------|----------|------|---------|
| XXXX | $xxx - $xxx | 理由 | 频道名 |

（如果没有卖出建议且没有买入/加仓建议，写"无（简短原因）"，不需要表格）
（如果有买入/加仓但缺少卖出建议，使用搜索补充，在理由列标注"（网络搜索补充）"）

---

# 频道名 — 视频标题

**日期：** YYYY-MM-DD

## 市场观点

- 主题一：概述
  - 补充细节
- 主题二：概述

（此处只写市场观点，买入/加仓/卖出已在上方综合交易信号中汇总，不再重复）

---

（下一期视频，同上格式）

## 转录文本

{texts}
"""

REPORT_DIR = ".storage/reports"


@dataclass
class SummaryResult:
    success: bool
    output_path: str | None = None
    error: str | None = None


def extract_info_from_path(parsed_path: str) -> tuple[str, str]:
    """Extract channel_name and date from parsed file path.

    Path structure: .storage/{channel_name}/transcript/{timestamp_dir}/{title}_parsed.txt
    timestamp_dir format: YYYY-MM-DD-HH-MM-SS -> date = first 10 chars
    """
    parts = Path(parsed_path).parts
    for i, part in enumerate(parts):
        if part == "transcript" and i > 0 and i + 1 < len(parts):
            channel_name = parts[i - 1]
            timestamp_dir = parts[i + 1]
            date = timestamp_dir[:10]  # YYYY-MM-DD
            return channel_name, date
    return "Unknown", "Unknown"


def _get_title(parsed_path: str) -> str:
    """Extract video title from parsed file path stem."""
    title = Path(parsed_path).stem
    if title.endswith("_parsed"):
        title = title[: -len("_parsed")]
    return title


def _blockquote(text: str) -> str:
    """Wrap text in Markdown blockquote."""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def _append_transcript_single(claude_output: str, text: str) -> str:
    """Append a single transcript to Claude's output via f-string."""
    return f"{claude_output}\n\n## 转录文本\n\n{_blockquote(text)}"


def _append_transcripts_multi(claude_output: str, parsed_files: list[tuple[str, str, str]], texts: list[str]) -> str:
    """Append all transcripts at the end of Claude's output."""
    sections = []
    for (parsed_path, channel_name, _date), text in zip(parsed_files, texts):
        title = _get_title(parsed_path)
        sections.append(f"### {channel_name} — {title}\n\n{_blockquote(text)}")

    transcript_block = "\n\n".join(sections)
    return f"{claude_output}\n\n---\n\n## 转录文本\n\n{transcript_block}"


def summarize_transcript(
    parsed_path: str,
    channel_name: str,
    date: str,
    verbose: bool = False,
) -> SummaryResult | None:
    """Single-file mode: one _parsed.txt -> one _summary.txt in the same directory.
    Returns SummaryResult on success/failure, None if skipped."""
    path = Path(parsed_path)
    if not path.exists():
        return SummaryResult(success=False, error=f"File not found: {parsed_path}")

    stem = path.stem
    if stem.endswith("_parsed"):
        output_name = stem[: -len("_parsed")] + "_summary.txt"
    else:
        output_name = stem + "_summary.txt"
    output_path = path.with_name(output_name)

    if output_path.exists():
        if verbose:
            print(f"  Skipping (already exists): {output_path}")
        return None

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        if verbose:
            print(f"  Skipping (empty file): {parsed_path}")
        return None

    prompt = SINGLE_PROMPT.format(
        channel_name=channel_name,
        date=date,
        text=text,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f".logs/claude/summary_{timestamp}.jsonl"

    try:
        result = run_claude_session(
            prompt=prompt,
            tools="WebSearch,WebFetch",
            allowed_tools=["WebSearch", "WebFetch"],
            model="sonnet",
            log_path=log_path,
        )

        if not result.success:
            return SummaryResult(success=False, error=result.error)

        # Append transcript via f-string (not generated by Claude)
        final_output = _append_transcript_single(result.output, text)
        output_path.write_text(final_output, encoding="utf-8")
        return SummaryResult(
            success=True,
            output_path=str(output_path),
        )
    except Exception as e:
        return SummaryResult(success=False, error=str(e))


def summarize_batch(
    parsed_files: list[tuple[str, str, str]],
    verbose: bool = False,
) -> SummaryResult:
    """Batch mode: multiple _parsed.txt -> one report in .storage/reports/summary_{timestamp}.txt.

    parsed_files: list of (parsed_path, channel_name, date)
    """
    # Read all texts upfront
    texts = []
    for parsed_path, _channel_name, _date in parsed_files:
        texts.append(Path(parsed_path).read_text(encoding="utf-8"))

    if len(parsed_files) == 1:
        parsed_path, channel_name, date = parsed_files[0]
        prompt = SINGLE_PROMPT.format(
            channel_name=channel_name,
            date=date,
            text=texts[0],
        )
    else:
        # Build concatenated text block for multi-video prompt
        sections = []
        for (parsed_path, channel_name, date), text in zip(parsed_files, texts):
            title = _get_title(parsed_path)
            sections.append(
                f"--- 频道：{channel_name} | 日期：{date} | 标题：{title} ---\n\n{text}"
            )
        combined_texts = "\n\n".join(sections)

        # Use the date from the first file for the report header
        report_date = parsed_files[0][2]
        prompt = MULTI_PROMPT.format(date=report_date, texts=combined_texts)

    ts_log = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f".logs/claude/summary_batch_{ts_log}.jsonl"

    try:
        result = run_claude_session(
            prompt=prompt,
            tools="WebSearch,WebFetch",
            allowed_tools=["WebSearch", "WebFetch"],
            model="sonnet",
            log_path=log_path,
        )

        if not result.success:
            return SummaryResult(success=False, error=result.error)

        # Append transcripts via f-string (not generated by Claude)
        if len(parsed_files) == 1:
            final_output = _append_transcript_single(result.output, texts[0])
        else:
            final_output = _append_transcripts_multi(result.output, parsed_files, texts)

        # Output to .storage/reports/summary_{timestamp}.txt
        report_dir = Path(REPORT_DIR)
        report_dir.mkdir(parents=True, exist_ok=True)
        ts_report = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        output_path = report_dir / f"summary_{ts_report}.txt"
        output_path.write_text(final_output, encoding="utf-8")

        return SummaryResult(
            success=True,
            output_path=str(output_path),
        )
    except Exception as e:
        return SummaryResult(success=False, error=str(e))

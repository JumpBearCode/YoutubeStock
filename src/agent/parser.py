from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.agent.claude_runner import run_claude_session

INSTRUCTIONS = """\
你是一个专业的文字整理助手，负责将 YouTube 视频自动转录（Whisper）生成的逐行碎片文本重组为自然、可读的段落。

## 任务要求

1. **段落合并**：将逐行碎片合并为语义连贯的自然段落，按主题分段。
2. **保持原意**：严格保留原始内容的含义，不添加、删除或评论任何信息。
3. **修正音译**：转录中的股票代码和公司名称常被语音识别错误地音译为中文，请修正为正确的名称和代码：
   - 按摩店 → AMD
   - 女大 / 女达 → NVIDIA(英伟达)
   - 奈飞 → Netflix(NFLX)
   - 甲骨文 → Oracle(ORCL)
   - 谷歌 → Google(GOOGL)
   - 苹果 → Apple(AAPL)
   - 亚马逊 → Amazon(AMZN)
   - 微软 → Microsoft(MSFT)
   - 特斯拉 → Tesla(TSLA)
   - 脸书 / Meta → Meta(META)
   - 台积电 → TSMC(TSM)
   - 博通 → Broadcom(AVGO)
   - 高通 → Qualcomm(QCOM)
   - 超微 / 超微电脑 → Super Micro(SMCI)
   - Palantir → Palantir(PLTR)
   - 标普 → S&P 500
   - 纳斯达克 / 纳指 → NASDAQ
   - 道指 / 道琼斯 → Dow Jones
   - 其他类似的音译错误也请根据上下文修正
4. **缩写处理**：在首次出现时展开常见缩写，如 EPS（每股收益）、PE（市盈率）、AWS（Amazon Web Services）等。
5. **输出语言**：保持中文输出，仅将公司/股票相关引用修正为正确的英文名称和代码。
6. **纯净输出**：只输出整理后的文本，不要添加标题、总结、评论或任何额外内容。
"""


@dataclass
class ParseResult:
    success: bool
    output_path: str | None = None
    error: str | None = None


def parse_transcript(txt_path: str, model: str = "sonnet", verbose: bool = False) -> ParseResult | None:
    """Returns ParseResult on success/failure, None if skipped (already exists)."""
    path = Path(txt_path)
    if not path.exists():
        print(f"  Error: File not found: {txt_path}")
        return ParseResult(success=False, error=f"File not found: {txt_path}")

    output_path = path.with_name(path.stem + "_parsed.txt")
    if output_path.exists():
        if verbose:
            print(f"  Skipping (already exists): {output_path}")
        return None

    text_content = path.read_text(encoding="utf-8")
    if not text_content.strip():
        print(f"  Skipping (empty file): {txt_path}")
        return None

    prompt = INSTRUCTIONS + "\n\n---\n\n" + text_content

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f".logs/claude/parse_{timestamp}.jsonl"

    try:
        result = run_claude_session(
            prompt=prompt,
            tools="",
            model=model,
            log_path=log_path,
        )

        if not result.success:
            print(f"  Parse FAILED: {result.error}")
            return ParseResult(success=False, error=result.error)

        output_path.write_text(result.output, encoding="utf-8")
        return ParseResult(
            success=True,
            output_path=str(output_path),
        )
    except Exception as e:
        print(f"  Parse FAILED: {e}")
        return ParseResult(success=False, error=str(e))

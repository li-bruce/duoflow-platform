"""Replace this pure business function; it has no platform or web dependency."""

MAX_TEXT_LENGTH = 20_000


def analyze(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"text"}:
        raise ValueError("expected exactly the text field")
    text = payload["text"]
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT_LENGTH or not text.strip():
        raise ValueError("text outside supported bounds")
    # Preserve line boundaries, remove surrounding whitespace and collapse
    # whitespace within a line. Counts are Unicode code points, not graphemes.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(" ".join(line.split()) for line in text.split("\n")).strip()
    data = {
        "normalized_text": normalized,
        "character_count": len(normalized),
        "line_count": len(normalized.split("\n")),
        "segment_count": len(normalized.split()),
    }
    return {
        "summary": f"已整理文本：{data['character_count']} 个字符，{data['line_count']} 行。",
        "data": data,
    }

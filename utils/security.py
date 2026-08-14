"""输入清洗与通用解析工具。"""

import re

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

_MAX_TEXT_LENGTH = 50
_UID_MAX_LENGTH = 30


def sanitize_text(text: str) -> str:
    """剥离控制字符并截断，防止构造多行伪造消息。"""
    if not text:
        return ""
    return _CTRL_RE.sub("", str(text)).strip()[:_MAX_TEXT_LENGTH]


def sanitize_uid(text: str) -> str:
    """球员 UID 清洗：剥离控制字符、截断，保持原始字符（允许数字/字母等）。"""
    if not text:
        return ""
    return _CTRL_RE.sub("", str(text)).strip()[:_UID_MAX_LENGTH]


def sanitize_filename(text: str) -> str:
    """文件名清洗：仅保留安全字符，防止路径穿越。"""
    cleaned = _CTRL_RE.sub("", str(text or "")).strip()
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]', "_", cleaned)
    if cleaned in ("", ".", ".."):
        return "file"
    return cleaned


def parse_int(raw: str, min_val: int | None = None, max_val: int | None = None) -> int:
    try:
        val = int(str(raw).strip())
    except (ValueError, TypeError):
        raise ValueError(f"非法整数: {raw}")
    if min_val is not None and val < min_val:
        raise ValueError(f"数值 {val} 小于下限 {min_val}")
    if max_val is not None and val > max_val:
        raise ValueError(f"数值 {val} 大于上限 {max_val}")
    return val


def parse_num(raw) -> float:
    """解析非负数值（整数或小数），用于比赛数据值。"""
    s = str(raw).strip()
    if not re.match(r"^\d+(\.\d+)?$", s):
        raise ValueError(f"非法数值: {raw}（需为非负数字，如 2 或 1.5）")
    return float(s)


def parse_date(raw: str) -> str:
    """解析日期为 YYYY-MM-DD；容忍 2026-08-14 / 2026/08/14 / 20260814 形式。

    严格校验月/日取值（拒绝 13 月、2 月 30 日等非法日期）。
    """
    from datetime import date

    s = str(raw).strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if not m:
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if not m:
        raise ValueError(f"日期需为 YYYY-MM-DD 形式: {raw}（例: 2026-08-14）")
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        date(y, mo, d)
    except ValueError as e:
        raise ValueError(f"非法日期: {raw}") from e
    return f"{y:04d}-{mo:02d}-{d:02d}"

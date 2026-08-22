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


def fmt_xp(v) -> str:
    """经验值展示：整值去小数点（10.0→"10"），小数保留 1 位（12.5→"12.5"）。"""
    if v is None:
        return "0"
    f = float(v)
    if f == int(f):
        return str(int(f))
    return f"{f:.1f}".rstrip("0").rstrip(".")


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


def normalize_name(text: str) -> str:
    """姓名归一化：小写并去除分隔符（空白/连字符/撇号等），用于匹配。

    保留字母数字与 CJK 字符，其余（空格、-、'、. 等）全部移除，
    使 "Van Dijk" / "vandijk" / "van-dijk" / "van Dijk" 归一到同一键。
    """
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(text or "").lower())


def _edit_distance(a: str, b: str) -> int:
    """编辑距离（DP）：姓名较短，O(m*n) 足够。"""
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n]


def name_similar(a: str, b: str) -> bool:
    """姓名相似度（容错匹配）：先归一化，再按归一化长度分级允许编辑距离。

    长度 <4 仅精确匹配（防 2 字名误配）；4~9 允许 1 处字母差异；≥10 允许 2 处。
    """
    x, y = normalize_name(a), normalize_name(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if len(x) < 4 or len(y) < 4:
        return False
    limit = 2 if (len(x) >= 10 or len(y) >= 10) else 1
    return _edit_distance(x, y) <= limit


def find_by_name(ref: str, players: list, exact_index: dict | None = None) -> tuple:
    """在球员列表中按姓名查找：归一化精确命中优先，否则长度分级模糊容错。

    exact_index 为可选的 归一化姓名→[球员] 预建索引（批量导入场景免于逐行扫描）。
    返回 (命中球员列表, 是否精确命中)；未命中返回 ([], False)。
    """
    key = normalize_name(ref)
    if exact_index is not None:
        exact = exact_index.get(key) or []
    else:
        exact = [p for p in players if normalize_name(p["name"]) == key]
    if exact:
        return exact, True
    return [p for p in players if name_similar(p["name"], ref)], False

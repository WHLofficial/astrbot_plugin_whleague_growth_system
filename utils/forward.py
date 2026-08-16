"""多行/长文本反馈自动转 QQ 合并转发卡片（防刷屏）。

触发条件：反馈为纯文本且**行数** ≥ 阈值（列表类反馈如排行、球员名单、
成长期明细在手机 QQ 上会一屏装不下，视为刷屏）；触发后按行聚合分块，
每块一条转发节点（Node + Nodes，name/uin 用机器人自身），并显式关闭
t2i 防止框架抢先转图。含图片/文件等非纯文本消息链不转换。
"""

from astrbot.api.event import MessageEventResult
from astrbot.api.message_components import Node, Nodes, Plain

TRUNCATE_HINT = "…（其余省略，可用 /成长导出 获取完整数据）"


def count_lines(text: str) -> int:
    """文本行数（按换行符计，空文本为 0 行）。"""
    if not text:
        return 0
    return text.count("\n") + 1


def chunk_lines(text: str, max_chars: int) -> list[str]:
    """按行聚合分块：每块不超过 max_chars，不拆断单行（单行超限按字符拆）。

    行分隔符随行保留（splitlines(keepends=True)），块间拼接可完整还原原文。
    """
    if not text:
        return []
    chunks: list[str] = []
    buf = ""
    for line in text.splitlines(keepends=True):
        if len(line) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(line), max_chars):
                chunks.append(line[i : i + max_chars])
            continue
        if buf and len(buf) + len(line) > max_chars:
            chunks.append(buf)
            buf = line
        else:
            buf += line
    if buf:
        chunks.append(buf)
    return chunks


def maybe_forward_result(
    event,
    result: MessageEventResult,
    line_threshold: int,
    node_max_chars: int,
    max_nodes: int,
) -> MessageEventResult:
    """纯文本反馈行数达到阈值时转为合并转发卡片，否则原样返回。

    line_threshold<=0 表示关闭；节点数超过 max_nodes 截断并附提示。
    """
    if not line_threshold or line_threshold <= 0:
        return result
    chain = getattr(result, "chain", None)
    if not chain or not all(isinstance(c, Plain) for c in chain):
        return result
    text = "".join(getattr(c, "text", "") for c in chain)
    if count_lines(text) < line_threshold:
        return result

    chunks = chunk_lines(text, node_max_chars)
    if len(chunks) > max_nodes:
        chunks = chunks[:max_nodes]
        chunks[-1] = chunks[-1] + "\n" + TRUNCATE_HINT
    nodes = [
        Node(uin=event.get_self_id(), name="AstrBot", content=[Plain(seg)])
        for seg in chunks
    ]
    new = event.chain_result([Nodes(nodes=nodes)])
    new.use_t2i(False)
    return new

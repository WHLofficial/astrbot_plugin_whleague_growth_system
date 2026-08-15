"""统一回复文案：用法提示、权限提示、分级帮助与错误引导。

所有命令的提示/错误文案集中在此，保证格式一致（简洁 + 修正引导），
便于维护与单测。业务成功文案（✅🎉📄 等）保留在各 handler 内。
"""

# 错误统一前缀（由 handler 层在透传异常时拼装）
WARN = "⚠️ "

# 常用修正引导（服务层 raise 时拼在报错原文后）
HINT_VIEW_RULE = "（可用 /成长规则 查看当前规则）"
HINT_VIEW_PLAYERS = "（可用 /成长球员 查看球员名单）"
HINT_DATE_FORMAT = "（例: 2026-08-14）"
HINT_NUM_FORMAT = "（需为非负数字，如 2 或 1.5）"


def usage(cmd: str, args: str, example: str | None = None) -> str:
    """统一用法提示：`用法: /成长查询 <球员ID>`，可附 `例: ...`。"""
    text = f"用法: /{cmd} {args}".rstrip()
    if example:
        text += f"\n例: {example}"
    return text


def deny() -> str:
    """权限不足提示，附带 admin_ids 引导（应对 is_admin() 判定失效的场景）。"""
    return (
        "该命令需要管理员权限。\n"
        "若你已是管理员仍见此提示，请在插件配置 admin_ids 中加入你的 QQ。"
    )


_PLAYER_HELP = (
    "【成长系统】\n"
    "· /成长：帮助\n"
    "· /成长规则：查看当前规则\n"
    "· /成长查询 <球员ID>：球员成长档案\n"
    "· /成长排行 [页]：当期经验排行\n"
    "· /成长排行 生涯 [页]：生涯经验排行\n"
    "· /成长球员 [页]：球员名单\n"
    "· /成长期状态：当前成长期信息\n"
    "· /成长预览：当前成长期成长数据预览"
)

_ADMIN_HELP = (
    "管理命令：\n"
    "· /成长上报 <球员ID> <日期> <数据项=值>...\n"
    "· /成长推进 <新名称> [保留|清零]\n"
    "· /成长导出 [期号]：导出成长期成长数据（Excel，自动降级 CSV/TXT）\n"
    "· /成长导入文件 <文件名> [类型]\n"
    "· /成长确认导入 <文件名> [类型]\n"
    "· /成长导入列表\n"
    "· /成长设置 <键> <值>\n"
    "· /成长查看配置\n\n"
    "群内发送 规则_*.json/csv/xlsx、球员_*.csv/xlsx、比赛_*.csv/xlsx 文件可自动识别并预览导入。"
)


def build_help(is_admin: bool) -> str:
    """按权限生成帮助文本：管理命令段仅对管理员展示。"""
    if is_admin:
        return _PLAYER_HELP + "\n\n" + _ADMIN_HELP
    return _PLAYER_HELP + "\n\n（部分管理命令仅管理员可见）"

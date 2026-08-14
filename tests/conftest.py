"""pytest 配置：安装 astrbot 桩并将插件目录加入 sys.path。

测试以插件包全名（astrbot_plugin_whleague_growth_system.*）导入，
因此将 PLUGINS_DIR（data/plugins）加入 sys.path，与谈判系统测试一致。
"""

import os
import sys

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGINS_DIR = os.path.dirname(_PLUGIN_ROOT)
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from stubs import install_stubs  # noqa: E402

install_stubs()

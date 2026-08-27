"""astrbot.api 桩：测试环境无 AstrBot 运行时，替换 logger/event/star 依赖。

安全说明：本模块仅存在于 tests/ 目录，不随插件运行加载。
"""

import sys
import types


class _Logger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class _Star:
    def __init__(self, context=None):
        self.context = context


def install_stubs():
    if "astrbot" in sys.modules:
        return
    astrbot_pkg = types.ModuleType("astrbot")
    astrbot_pkg.__path__ = []
    api_pkg = types.ModuleType("astrbot.api")
    api_pkg.logger = _Logger()

    event_pkg = types.ModuleType("astrbot.api.event")
    event_pkg.MessageEventResult = types.SimpleNamespace
    event_pkg.MessageChain = types.SimpleNamespace
    event_pkg.AstrMessageEvent = object

    filter_mod = types.ModuleType("astrbot.api.event.filter")
    filter_mod.regex = lambda *a, **k: (lambda fn: fn)
    filter_mod.command = lambda *a, **k: (lambda fn: fn)
    filter_mod.event_message_type = lambda *a, **k: (lambda fn: fn)
    filter_mod.EventMessageType = types.SimpleNamespace(GROUP_MESSAGE="group_message")
    event_pkg.filter = filter_mod
    sys.modules["astrbot.api.event.filter"] = filter_mod

    star_pkg = types.ModuleType("astrbot.api.star")
    star_pkg.Context = object
    star_pkg.Star = _Star
    star_pkg.register = lambda *a, **k: (lambda cls: cls)
    sys.modules["astrbot.api.star"] = star_pkg

    mc_pkg = types.ModuleType("astrbot.api.message_components")
    mc_pkg.File = object
    mc_pkg.Plain = object
    mc_pkg.Node = object
    mc_pkg.Nodes = object
    sys.modules["astrbot.api.message_components"] = mc_pkg

    # astrbot.api.web 桩：仅覆盖插件 WebAPI 用到的响应 helper 与请求代理。
    # request 为可变替身，测试按需替换 query/username/json/files 属性。
    web_pkg = types.ModuleType("astrbot.api.web")

    class _JsonResponse(dict):
        def __init__(self, data=None, **kwargs):
            super().__init__(data if isinstance(data, dict) else {"data": data})
            self.status_code = kwargs.get("status_code", 200)

    web_pkg.json_response = lambda data=None, **k: _JsonResponse({"status": "ok", "data": data})
    web_pkg.error_response = lambda message, **k: _JsonResponse(
        {"status": "error", "message": message}, status_code=k.get("status_code", 400)
    )
    web_pkg.file_response = lambda path, **k: str(path)
    web_pkg.stream_response = lambda content, **k: content

    class _StubFiles:
        def __init__(self):
            self._mapping = {}

        def get(self, key):
            return self._mapping.get(key)

    class _StubRequest:
        def __init__(self):
            from types import SimpleNamespace

            self.query = SimpleNamespace(get=lambda k, d=None, t=None: d)
            self.username = None
            self._json_default = {}
            self._json_body = None
            self._files = _StubFiles()

        async def json(self, default=None):
            if self._json_body is not None:
                return self._json_body
            return default

        async def form(self):
            return SimpleNamespace()

        async def files(self):
            return self._files

    web_pkg.request = _StubRequest()

    sys.modules["astrbot.api.web"] = web_pkg

    sys.modules["astrbot"] = astrbot_pkg
    sys.modules["astrbot.api"] = api_pkg
    sys.modules["astrbot.api.event"] = event_pkg

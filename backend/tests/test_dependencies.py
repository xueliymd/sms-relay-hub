"""运行时依赖声明守卫。

容器只执行 `pip install .`（即 pyproject 的 `dependencies`），而本地 venv 因为装了
`dev` 额外依赖，会把 `requests` 这类**传递依赖顺带带进来**——`requests` 就是被
`testcontainers → docker` 带进来的。于是「本地 252 项全绿、容器首启
ModuleNotFoundError、进程秒退、健康检查永远不通过」这种最贵的故障，此前没有任何
测试能发现。

这个测试把「`backend/app` 里 import 的第三方顶层模块必须在 pyproject 里声明」锁死。
不要删除；新增第三方依赖时同步改 pyproject，而不是改这里。
"""

import ast
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"

# import 名与发行名不一致的少数特例
IMPORT_TO_DISTRIBUTION = {
    "argon2": "argon2-cffi",
    "paho": "paho-mqtt",
}

# 由已声明依赖保证存在、但被直接 import 的传递依赖
TRANSITIVE_ALLOWED = {
    "pydantic",  # fastapi / pydantic-settings 的硬依赖
}

STDLIB = set(sys.stdlib_module_names) | {"__future__"}


def _normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _declared_dependencies() -> set[str]:
    text = (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.S | re.M)
    assert block, "backend/pyproject.toml is missing [project] dependencies"
    declared = set()
    for item in re.findall(r'"([^"]+)"', block.group(1)):
        # 去掉 extras 与版本约束：psycopg[binary]>=3.2,<4.0 -> psycopg
        declared.add(_normalize(re.split(r"[<>=!\[; ]", item)[0]))
    return declared


def _imported_top_level_modules() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] if node.level == 0 else []
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top:
                    found.setdefault(top, set()).add(str(path.relative_to(BACKEND_ROOT)))
    return found


def test_every_runtime_import_is_a_declared_dependency() -> None:
    declared = _declared_dependencies()
    undeclared = []
    for module, files in sorted(_imported_top_level_modules().items()):
        if module in STDLIB or module == "app":
            continue
        distribution = _normalize(IMPORT_TO_DISTRIBUTION.get(module, module))
        if distribution in declared or module in TRANSITIVE_ALLOWED:
            continue
        undeclared.append(f"{module} (imported by {', '.join(sorted(files))})")
    assert not undeclared, (
        "these modules are imported by backend/app but are not declared in "
        "backend/pyproject.toml [project] dependencies. The container installs only "
        "those, so it fails at import time with ModuleNotFoundError and the app "
        "container exits before its health check can ever pass: " + "; ".join(undeclared)
    )


def test_requests_is_declared_because_forwarding_posts_to_push_channels() -> None:
    """回归：`requests` 只被 testcontainers→docker 传递带进本地 venv，容器里没有。"""
    assert "requests" in _declared_dependencies()
    assert "requests" in _imported_top_level_modules()

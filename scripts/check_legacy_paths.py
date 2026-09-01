"""Legacy-path consistency gate.

用于阻止仓库重新引用已经迁移或废弃的文件路径。

当前代码、Workflow、配置、测试以及 docs 下的有效文档都必须使用当前规范路径。
历史路径只允许出现在 CHANGELOG.md 中；其他历史信息通过 Git 和 Pull Request 追溯。

检查范围：
  - 扫描所有 Git tracked files（git ls-files）。
  - 忽略构建输出、依赖目录和其他非源码目录。
  - 使用精确的文件级规则检查废弃路径。
  - 顶层非法目录仍由 CI 中的 layout-check 负责检查。

运行：

    python scripts/check_legacy_paths.py

发现废弃路径时返回 exit code 1。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


# 废弃路径 -> 当前规范路径。
#
# 使用 [\\/] 同时匹配 Windows 和 POSIX 路径形式。
# 规则应保持精确，不使用单独的目录名等宽泛模式，
# 避免误伤 apps/desktop/src 等当前合法路径。
FORBIDDEN: dict[str, str] = {
    r"packaging[/\\]delta-server-version\.txt": (
        "packaging/server/delta-server-version.txt"
    ),
    r"packaging[/\\]delta-server\.spec": "packaging/server/delta-server.spec",
    r"packaging[/\\]server_entry\.py": "packaging/server/server_entry.py",
    r"packaging[/\\]build_portable\.ps1": (
        "packaging/portable/build_portable.ps1"
    ),
    r"packaging[/\\]scan_portable_paths\.ps1": (
        "packaging/portable/scan_portable_paths.ps1"
    ),
}


# CHANGELOG 是唯一允许保留历史路径的当前文件。
#
# governance、architecture、operations、UPSTREAM 等均属于当前有效文档，
# 不得继续引用已经废弃的路径。
HISTORY_EXEMPT = ("CHANGELOG.md",)


# 不需要扫描的构建输出和依赖目录。
_SKIP_PARTS = {
    ".git",
    "node_modules",
    "target",
    "__pycache__",
    ".venv",
    "dist",
    "releases",
}


def _tracked_files() -> list[Path]:
    """返回仓库中所有 Git tracked files。"""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    return [REPO / line for line in out.splitlines() if line]


def _is_exempt(path: Path) -> bool:
    """判断文件是否属于允许保留历史路径的例外。"""
    rel = path.relative_to(REPO).as_posix()
    return rel in HISTORY_EXEMPT


def violations_for(text: str, rel_path: str) -> list[str]:
    """返回单个文件中的废弃路径引用。"""
    return [
        (
            f"{rel_path}:{text.count(chr(10), 0, match.start()) + 1}: "
            f"deprecated path -> use {canonical}"
        )
        for pattern, canonical in FORBIDDEN.items()
        for match in re.finditer(pattern, text)
    ]


def find_violations() -> list[str]:
    """扫描仓库并返回所有废弃路径引用。"""
    violations: list[str] = []

    for path in _tracked_files():
        if _is_exempt(path) or _SKIP_PARTS & set(path.parts):
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        violations.extend(
            violations_for(
                text,
                path.relative_to(REPO).as_posix(),
            )
        )

    return violations


def main() -> int:
    violations = find_violations()

    if violations:
        print(
            "legacy-path gate FAILED — "
            "deprecated paths referenced outside CHANGELOG.md:"
        )

        for violation in violations:
            print(f"  {violation}")

        return 1

    print("legacy-path gate clean: no deprecated path references")
    return 0


if __name__ == "__main__":
    sys.exit(main())
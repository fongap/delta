from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_r6_architecture.py"
SPEC = importlib.util.spec_from_file_location("check_r6_architecture", SCRIPT)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    for target in guard.SCAN_TARGETS:
        path = tmp_path / target
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[project]\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.mark.parametrize(
    ("relative_path", "contents", "rule_id"),
    [
        ("services/api.py", "from fastapi import FastAPI\n", "python-web-backend"),
        ("pyproject.toml", '[project]\ndependencies = ["uvicorn==1"]\n', "python-web-dependency"),
        ("packaging/build.ps1", "pyinstaller delta-server\n", "server-packaging"),
        ("apps/desktop/src/transport.ts", 'const url = "ws://127.0.0.1:8765/ws/session";\n', "desktop-legacy-transport"),
        ("core/engine.py", "class TurnEngine:\n    pass\n", "python-turn-authority"),
        ("core/runner.py", "from providers.router import ProviderRouter\n", "python-provider-authority"),
        ("core/runner.py", "import aisuite\n", "aisuite-application-runtime"),
        ("apps/desktop/src/view.tsx", "export const PersonaView = () => null;\n", "persona-platform"),
    ],
)
def test_each_content_rule_is_enforced(
    repository: Path, relative_path: str, contents: str, rule_id: str
) -> None:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")

    violations, evaluated = guard.scan_repository(repository)

    assert rule_id in evaluated
    assert any(rule_id in violation for violation in violations)


@pytest.mark.parametrize("relative_path", tuple(guard.STRUCTURAL_BANS))
def test_each_structural_rule_is_enforced(repository: Path, relative_path: str) -> None:
    path = repository / relative_path
    if Path(relative_path).suffix:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("retired", encoding="utf-8")
    else:
        path.mkdir(parents=True, exist_ok=True)

    violations, evaluated = guard.scan_repository(repository)

    assert f"path:{relative_path}" in evaluated
    assert any(relative_path in violation for violation in violations)


def test_clean_native_architecture_passes(repository: Path) -> None:
    source = repository / "apps/desktop/src/runtimeTransport.ts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('invoke("runtime_run", { modelId: "gpt-5.5" });\n', encoding="utf-8")
    rust = repository / "core/runtime-native/src/runtime.rs"
    rust.parent.mkdir(parents=True, exist_ok=True)
    rust.write_text("pub struct RuntimeHost;\n", encoding="utf-8")

    violations, evaluated = guard.scan_repository(repository)

    assert violations == []
    assert {rule.id for rule in guard.RULES}.issubset(evaluated)


def test_exemptions_are_narrow_and_documented() -> None:
    known_rules = {rule.id for rule in guard.RULES}
    for exemption in guard.EXEMPT_FILES.values():
        assert exemption.rules
        assert exemption.rules <= known_rules
        assert len(exemption.reason.strip()) >= 20

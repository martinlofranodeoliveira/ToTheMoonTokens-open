from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.collect_ci_evidence import CHECKS
from scripts.collect_ci_evidence import main as collect_ci_evidence_main


def test_ci_evidence_runs_named_frontend_saas_e2e_target() -> None:
    checks_by_name = {check.name: check for check in CHECKS}

    assert checks_by_name["frontend_saas_e2e"].command == ["make", "web-next-e2e-saas"]
    assert checks_by_name["frontend_saas_e2e"].prerequisites == ["node", "npm", "make"]
    assert checks_by_name["frontend_fast_check"].command == ["make", "web-next-fast-check"]
    assert checks_by_name["frontend_fast_check"].prerequisites == ["node", "npm", "make"]


def test_frontend_saas_e2e_npm_script_exists() -> None:
    package_json = json.loads((Path(__file__).resolve().parents[3] / "apps/web-next/package.json").read_text())

    assert package_json["scripts"]["test:e2e:saas"] == "playwright test tests/e2e/saas.spec.ts"


def test_makefile_e2e_targets_forward_playwright_args() -> None:
    makefile = (Path(__file__).resolve().parents[3] / "Makefile").read_text()

    assert "PLAYWRIGHT_ARGS ?=" in makefile
    assert "$(NPM) run test:e2e -- $(PLAYWRIGHT_ARGS)" in makefile
    assert "$(NPM) run test:e2e:saas -- $(PLAYWRIGHT_ARGS)" in makefile
    assert "$(NPM) run test:e2e:public-smoke -- $(PLAYWRIGHT_ARGS)" in makefile


def test_makefile_web_next_prereqs_checks_frontend_build_prerequisites_only() -> None:
    makefile = (Path(__file__).resolve().parents[3] / "Makefile").read_text()

    assert "web-next-prereqs:" in makefile
    assert "$(PYTHON) ./scripts/collect_ci_evidence.py --only frontend_build --check-prerequisites" in makefile


def test_makefile_backend_ci_evidence_target_runs_backend_checks_only() -> None:
    makefile = (Path(__file__).resolve().parents[3] / "Makefile").read_text()

    assert "backend-ci-evidence:" in makefile
    assert (
        "$(PYTHON) ./scripts/collect_ci_evidence.py --only backend_compile --only backend_lint --only backend_tests"
        in makefile
    )


def test_makefile_frontend_fast_check_target_runs_public_smoke() -> None:
    makefile = (Path(__file__).resolve().parents[3] / "Makefile").read_text()

    assert "web-next-fast-check: web-next-typecheck web-next-e2e-public-smoke generated-artifacts-check" in makefile
    assert "frontend-fast-ci-evidence:" in makefile
    assert "$(PYTHON) ./scripts/collect_ci_evidence.py --only frontend_fast_check" in makefile


def test_ci_evidence_can_list_selected_checks(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["collect_ci_evidence.py", "--only", "backend_compile", "--list-checks"],
    )

    assert collect_ci_evidence_main() == 0

    assert capsys.readouterr().out == "backend_compile\n"


def test_ci_evidence_backend_only_selection_lists_backend_checks(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_ci_evidence.py",
            "--only",
            "backend_compile",
            "--only",
            "backend_lint",
            "--only",
            "backend_tests",
            "--list-checks",
        ],
    )

    assert collect_ci_evidence_main() == 0

    assert capsys.readouterr().out == "backend_compile\nbackend_lint\nbackend_tests\n"


def test_ci_evidence_checks_selected_prerequisites_without_writing_evidence(monkeypatch, capsys, tmp_path) -> None:
    output_path = tmp_path / "ci-evidence.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_ci_evidence.py",
            "--only",
            "frontend_build",
            "--check-prerequisites",
            "--output",
            str(output_path),
        ],
    )

    assert collect_ci_evidence_main() == 0

    assert capsys.readouterr().out == "All prerequisites are available.\n"
    assert not output_path.exists()


def test_ci_evidence_reports_missing_selected_prerequisites(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["collect_ci_evidence.py", "--only", "frontend_build", "--check-prerequisites"])
    monkeypatch.setattr("scripts.collect_ci_evidence.shutil.which", lambda name: None if name == "node" else f"/bin/{name}")

    assert collect_ci_evidence_main() == 1

    assert capsys.readouterr().out == "frontend_build: missing prerequisites: node\n"


def test_ci_evidence_records_requested_task(monkeypatch, tmp_path) -> None:
    output_path = tmp_path / "ci-evidence.json"

    monkeypatch.setattr(
        sys,
        "argv",
        ["collect_ci_evidence.py", "--task", "GEN-1143", "--output", str(output_path)],
    )
    monkeypatch.setattr("scripts.collect_ci_evidence.run_check", lambda check: {"name": check.name, "status": "passed"})

    assert collect_ci_evidence_main() == 0

    assert json.loads(output_path.read_text())["task"] == "GEN-1143"


def test_ci_evidence_can_forward_playwright_args(monkeypatch, tmp_path) -> None:
    output_path = tmp_path / "ci-evidence.json"
    commands: list[list[str]] = []

    def fake_run_command(name: str, command: list[str], prerequisites: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"name": name, "command": " ".join(command), "status": "passed"}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_ci_evidence.py",
            "--only",
            "frontend_saas_e2e",
            "--playwright-args",
            '--grep "bot modes preserve paper scanner"',
            "--output",
            str(output_path),
        ],
    )
    monkeypatch.setattr("scripts.collect_ci_evidence.run_command", fake_run_command)

    assert collect_ci_evidence_main() == 0

    assert commands == [["make", "web-next-e2e-saas", 'PLAYWRIGHT_ARGS=--grep "bot modes preserve paper scanner"']]
    payload = json.loads(output_path.read_text())
    assert payload["checks"][0]["command"] == 'make web-next-e2e-saas PLAYWRIGHT_ARGS=--grep "bot modes preserve paper scanner"'

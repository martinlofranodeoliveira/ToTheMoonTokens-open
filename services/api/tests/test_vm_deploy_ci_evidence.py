from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_collector_module():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "collect_vm_deploy_ci_evidence.py"
    spec = importlib.util.spec_from_file_location("collect_vm_deploy_ci_evidence", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_vm_deploy_collector_separates_static_assertions_from_executed_evidence(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    module = _load_collector_module()

    assert module.main() == 0
    payload = json.loads(Path("ops/evidence/vm-deploy-ci-evidence.json").read_text(encoding="utf-8"))

    assert payload["executed_checks"] == []
    assert payload["intended_checks"] == ["make api-baseline", "make web-next-build"]
    assert "checks" not in payload

    assertions = {assertion["name"]: assertion for assertion in payload["repository_assertions"]}
    assert assertions["gitlab_pipeline_present"]["kind"] == "repository_text_assertion"
    assert assertions["gitlab_pipeline_present"]["present"] is True
    assert assertions["migration_step_configured"]["kind"] == "repository_text_assertion"
    assert assertions["migration_step_configured"]["present"] is True
    assert assertions["route_contract_test_present"]["kind"] == "repository_file_assertion"
    assert assertions["route_contract_test_present"]["present"] is True
    assert assertions["immutable_digest_images_configured"]["kind"] == "repository_text_assertion"
    assert "assertions" in assertions["immutable_digest_images_configured"]
    assert assertions["compose_file_present"]["kind"] == "repository_text_assertion"
    assert assertions["compose_file_present"]["present"] is True
    assert payload["vm_deploy"]["route_contract"]["present"] is True
    assert "present" in payload["vm_deploy"]["immutable_digest_images"]


def test_gitlab_vm_deploy_pipeline_keeps_generated_artifact_hygiene_gate():
    pipeline = Path(__file__).resolve().parents[3] / ".gitlab-ci.yml"
    pipeline_text = pipeline.read_text(encoding="utf-8")

    assert "generated-artifacts-hygiene:" in pipeline_text
    assert "make generated-artifacts-check" in pipeline_text
    assert pipeline_text.count("- generated-artifacts-hygiene") >= 2


def test_vm_deploy_script_cleans_remote_secret_env_on_all_exits():
    deploy_script = Path(__file__).resolve().parents[3] / "scripts" / "deploy_gcp_vm.sh"
    script_text = deploy_script.read_text(encoding="utf-8")

    cleanup_index = script_text.find("cleanup_remote_artifacts()")
    trap_index = script_text.find("trap cleanup_remote_artifacts EXIT")
    install_index = script_text.find('install -m 0600 "$remote_env"')
    success_health_index = script_text.find("curl -fsS http://127.0.0.1/ready")

    assert cleanup_index != -1
    assert trap_index != -1
    assert cleanup_index < trap_index < install_index < success_health_index
    assert 'rm -f "$remote_archive" "$remote_env"' in script_text


def test_vm_deploy_script_gates_release_on_readiness_not_liveness():
    deploy_script = Path(__file__).resolve().parents[3] / "scripts" / "deploy_gcp_vm.sh"
    script_text = deploy_script.read_text(encoding="utf-8")
    success_block = script_text.split("ln -sfn \"$release_dir\" \"$deploy_root/current\"", 1)[1]

    assert "curl -fsS http://127.0.0.1/ready" in success_block
    assert "curl -fsS http://127.0.0.1/health" not in success_block


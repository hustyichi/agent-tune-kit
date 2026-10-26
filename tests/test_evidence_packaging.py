from tests.test_release_scripts import load_script


def test_release_gate_requires_evidence_modules_and_import_template():
    gate = load_script("check-release.py")
    for path in [
        "agent_tune_kit/results/evidence.py",
        "agent_tune_kit/execution/tracing.py",
        "agent_tune_kit/plugin_payload/agent-tune-kit/templates/.atk/runner/import_evidence.py.md",
    ]:
        assert path in gate.WHEEL_REQUIRED_PATHS

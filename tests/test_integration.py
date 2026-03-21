"""Integration tests for the lr pipeline executor."""

import json
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
LR = str(PROJECT_ROOT / "lr")
LR_UPDATE = str(PROJECT_ROOT / "lr-update")


def create_test_issue(env: dict, prefix: str = "lr-integration-test") -> str:
    """Create a Linear issue for testing, return its identifier."""
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    title = f"{prefix} {timestamp}"
    result = subprocess.run(
        [LR_UPDATE, "new", title],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"Failed to create issue: {result.stderr}"
    identifier = result.stdout.strip().split()[-1]
    return identifier


class TestLrIntegration:
    """End-to-end integration tests for lr."""

    def test_successful_pipeline_with_interpret(self, lr_env):
        """Full happy path: lr runs pipeline, writes .lr-interpret, interpret script posts to Linear."""
        env = lr_env["env"]
        issue_id = create_test_issue(env)

        # Step 1: Run lr with --interpret
        result = subprocess.run(
            [
                LR,
                "--issue", issue_id,
                "--remote-dir", lr_env["remote_dir"],
                "--interpret",
                "snakemake", "-j1",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"lr failed (exit {result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

        # Results were SCP'd back
        issue_dir = lr_env["results_dir"] / issue_id
        result_dirs = [p for p in issue_dir.iterdir() if p.is_dir()] if issue_dir.exists() else []
        assert len(result_dirs) >= 1, (
            f"No result timestamp directories found for {issue_id}.\n"
            f"Contents of results_dir: {list(lr_env['results_dir'].rglob('*'))}\n"
            f"lr stdout:\n{result.stdout}"
        )
        latest = sorted(result_dirs)[-1]
        assert list(latest.rglob("metrics.json")), "metrics.json not found in results"
        assert list(latest.rglob("confusion_matrix.png")), "confusion_matrix.png not found"
        assert list(latest.rglob("pca_scatter.png")), "pca_scatter.png not found"
        assert list(latest.rglob("predictions.csv")), "predictions.csv not found"

        # Metrics file is valid JSON with expected keys
        metrics_files = list(latest.rglob("metrics.json"))
        metrics = json.loads(metrics_files[0].read_text())
        assert "accuracy" in metrics
        assert metrics["accuracy"] > 0.8

        # Results copied to guest mount
        assert list(lr_env["guest_mount"].rglob("metrics.json")), "metrics.json not in guest mount"

        # .lr-interpret request was written
        interpret_files = list(lr_env["guest_mount"].rglob(".lr-interpret"))
        assert interpret_files, (
            ".lr-interpret not found in guest mount — interpretation handoff failed"
        )
        interpret_request = json.loads(interpret_files[0].read_text())
        assert interpret_request["issue_id"] == issue_id
        assert interpret_request["exit_code"] == 0

        # Step 2: Run interpret script (simulates /interpret in Claude Code)
        # Uses mock claude from tests/bin/ on PATH
        interpret_result = subprocess.run(
            [lr_env["interpret"], issue_id],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

        assert interpret_result.returncode == 0, (
            f"interpret failed (exit {interpret_result.returncode}):\n"
            f"STDOUT:\n{interpret_result.stdout}\n"
            f"STDERR:\n{interpret_result.stderr}"
        )

        # .lr-interpret should be consumed
        interpret_files = list(lr_env["guest_mount"].rglob(".lr-interpret"))
        assert not interpret_files, ".lr-interpret still present after interpret ran"

        # Issue should be in "Ready for Review" (set by interpret)
        status_result = subprocess.run(
            [LR_UPDATE, "list", "--status", "Ready for Review"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert issue_id in status_result.stdout, (
            f"Issue {issue_id} not in 'Ready for Review' state. "
            f"list output: {status_result.stdout}"
        )

    def test_successful_pipeline_no_interpret(self, lr_env):
        """Without --interpret: results SCP'd, Linear updated to Done, no interpretation."""
        env = lr_env["env"]
        issue_id = create_test_issue(env)

        result = subprocess.run(
            [
                LR,
                "--issue", issue_id,
                "--remote-dir", lr_env["remote_dir"],
                "snakemake", "-j1",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"lr failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        # Results were SCP'd
        result_dirs = list(lr_env["results_dir"].glob(f"{issue_id}/*"))
        assert len(result_dirs) >= 1

        # Issue should be Done (not Ready for Review)
        status_result = subprocess.run(
            [LR_UPDATE, "list", "--status", "Done"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert issue_id in status_result.stdout, (
            f"Issue {issue_id} not in 'Done' state"
        )

    def test_failed_pipeline(self, lr_env):
        """Failing snakemake: Linear gets failure update, no interpretation."""
        env = lr_env["env"]
        issue_id = create_test_issue(env)

        result = subprocess.run(
            [
                LR,
                "--issue", issue_id,
                "--remote-dir", lr_env["remote_dir"],
                "--interpret",
                "snakemake", "-j1", "nonexistent_target",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

        # lr itself should succeed (it reports failure, doesn't propagate)
        assert result.returncode == 0, (
            f"lr should exit 0 even on pipeline failure:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        # Issue should have a failure comment — verify by reading issue comments
        # (we can check lr stdout for the failure message)
        assert "fail" in result.stdout.lower() or "Failed" in result.stdout, (
            f"Expected failure indication in lr output:\n{result.stdout}"
        )

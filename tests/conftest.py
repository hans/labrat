import json
import os
import subprocess
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import docker
import pytest

TESTS_DIR = Path(__file__).parent
DOCKER_DIR = TESTS_DIR / "docker"
PROJECT_ROOT = TESTS_DIR.parent


class AnthropicMockHandler(BaseHTTPRequestHandler):
    """Mock server for the Anthropic API."""

    requests_log: list = []

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        data = json.loads(body)

        self.requests_log.append({"path": self.path, "body": data})

        response = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "**Analysis Summary**\n\n"
                        "The Random Forest classifier achieved high accuracy on the Iris dataset. "
                        "All three species were classified with >90% precision. "
                        "Setosa was perfectly separated; minor confusion between versicolor and virginica.\n\n"
                        "**Next steps:** Try SVM or gradient boosting for comparison."
                    ),
                }
            ],
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="session")
def anthropic_mock():
    """Run a mock Anthropic API server."""
    AnthropicMockHandler.requests_log = []
    server = HTTPServer(("127.0.0.1", 0), AnthropicMockHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {
        "url": f"http://127.0.0.1:{port}",
        "requests": AnthropicMockHandler.requests_log,
    }
    server.shutdown()


@pytest.fixture(scope="session")
def ssh_keypair(tmp_path_factory):
    """Generate an ephemeral SSH keypair."""
    ssh_dir = tmp_path_factory.mktemp("ssh")
    key_path = ssh_dir / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
        check=True,
    )
    return str(key_path), str(key_path) + ".pub"


@pytest.fixture(scope="session")
def remote_container(ssh_keypair):
    """Build and run the remote server Docker container with sshd + snakemake."""
    client = docker.from_env()
    private_key, public_key = ssh_keypair

    # Build image
    image, build_logs = client.images.build(
        path=str(DOCKER_DIR),
        tag="lr-test-remote",
        rm=True,
    )

    # Run container
    container = client.containers.run(
        "lr-test-remote",
        detach=True,
        ports={"22/tcp": None},
    )

    try:
        # Copy public key into container
        pub_key_content = Path(public_key).read_text().strip()
        container.exec_run(
            [
                "bash",
                "-c",
                f"echo '{pub_key_content}' > /home/testuser/.ssh/authorized_keys && "
                f"chmod 600 /home/testuser/.ssh/authorized_keys && "
                f"chown testuser:testuser /home/testuser/.ssh/authorized_keys",
            ]
        )

        # Get mapped port
        container.reload()
        host_port = container.ports["22/tcp"][0]["HostPort"]

        # Write SSH config file for both ssh and scp
        ssh_config_path = Path(private_key).parent / "ssh_config"
        ssh_config_path.write_text(
            f"Host lr-test-remote\n"
            f"    HostName localhost\n"
            f"    Port {host_port}\n"
            f"    User testuser\n"
            f"    IdentityFile {private_key}\n"
            f"    StrictHostKeyChecking no\n"
            f"    UserKnownHostsFile /dev/null\n"
        )

        ssh_opts = f"-F {ssh_config_path}"

        # Wait for sshd to be ready
        for attempt in range(15):
            result = subprocess.run(
                f"ssh {ssh_opts} lr-test-remote echo ready",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError(
                f"SSH to container not ready after 15 attempts. "
                f"stderr: {result.stderr}"
            )

        yield {
            "container": container,
            "ssh_options": ssh_opts,
            "remote_host": "lr-test-remote",
            "remote_dir": "/home/testuser/pipeline",
        }
    finally:
        container.stop()
        container.remove(force=True)


@pytest.fixture()
def lr_env(remote_container, anthropic_mock, tmp_path):
    """Build the environment dict for running lr."""
    results_dir = tmp_path / "results"
    guest_mount = tmp_path / "guest"
    results_dir.mkdir()
    guest_mount.mkdir()

    # Source ~/.lr/config to get LINEAR_API_KEY and LINEAR_TEAM_ID
    config_path = Path.home() / ".lr" / "config"
    env = os.environ.copy()
    if config_path.exists():
        result = subprocess.run(
            [
                "bash", "-c",
                f"set -a && source {config_path} && "
                f"echo LINEAR_API_KEY=$LINEAR_API_KEY && "
                f"echo LINEAR_TEAM_ID=$LINEAR_TEAM_ID",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        for line in result.stdout.strip().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                if v:
                    env[k] = v

    assert env.get("LINEAR_API_KEY"), "LINEAR_API_KEY must be set in ~/.lr/config"
    assert env.get("LINEAR_TEAM_ID"), "LINEAR_TEAM_ID must be set in ~/.lr/config"

    rc = remote_container

    # Create a test-specific ~/.lr/config in tmp_path so the real config
    # doesn't override our env vars
    fake_home = tmp_path / "home"
    fake_lr_dir = fake_home / ".lr"
    fake_lr_dir.mkdir(parents=True)
    (fake_lr_dir / "config").write_text(
        f'LINEAR_API_KEY="{env["LINEAR_API_KEY"]}"\n'
        f'LINEAR_TEAM_ID="{env["LINEAR_TEAM_ID"]}"\n'
    )

    env.update(
        {
            "HOME": str(fake_home),
            "LR_REMOTE_HOST": rc["remote_host"],
            "LR_SSH_OPTIONS": rc["ssh_options"],
            "LR_RESULTS_DIR": str(results_dir),
            "LR_GUEST_MOUNT": str(guest_mount),
            "ANTHROPIC_API_KEY": "sk-ant-test-fake-key",
            "ANTHROPIC_API_URL": anthropic_mock["url"],
        }
    )

    yield {
        "env": env,
        "results_dir": results_dir,
        "guest_mount": guest_mount,
        "remote_dir": rc["remote_dir"],
        "anthropic_requests": anthropic_mock["requests"],
    }


def create_test_issue(env: dict, prefix: str = "lr-integration-test") -> str:
    """Create a Linear issue for testing, return its identifier."""
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    title = f"{prefix} {timestamp}"
    result = subprocess.run(
        [str(PROJECT_ROOT / "lr-update"), "new", title],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"Failed to create issue: {result.stderr}"
    # Output is like "Created JON-42"
    identifier = result.stdout.strip().split()[-1]
    return identifier

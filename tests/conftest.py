import os
import subprocess
import sys
import time
from pathlib import Path

import docker
import pytest

TESTS_DIR = Path(__file__).parent
DOCKER_DIR = TESTS_DIR / "docker"
MOCK_BIN_DIR = TESTS_DIR / "bin"
PROJECT_ROOT = TESTS_DIR.parent
INTERPRET_CMD = [sys.executable, "-m", "lrlib.interpret", "--host"]


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
def lr_env(remote_container, tmp_path):
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

    # Point Agent SDK to mock claude and skip version check
    env["PATH"] = f"{MOCK_BIN_DIR}:{env.get('PATH', '')}"
    env["CLAUDE_CLI_PATH"] = str(MOCK_BIN_DIR / "claude")
    env["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    env.update(
        {
            "HOME": str(fake_home),
            "LR_REMOTE_HOST": rc["remote_host"],
            "LR_SSH_OPTIONS": rc["ssh_options"],
            "LR_RESULTS_DIR": str(results_dir),
            "LR_GUEST_MOUNT": str(guest_mount),
            "LR_RESULTS_MOUNT": str(guest_mount / "lr-results"),
        }
    )

    yield {
        "env": env,
        "results_dir": results_dir,
        "guest_mount": guest_mount,
        "remote_dir": rc["remote_dir"],
        "interpret_cmd": INTERPRET_CMD,
    }

import os
import subprocess
from pathlib import Path


def test_compose_smoke_preserves_explicit_environment_overrides(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env_file = tmp_path / "smoke.env"
    env_file.write_text(
        "\n".join(
            [
                "COMPOSE_PROJECT_NAME=unsafe-staging-project",
                "CYBERTEAM_NETWORK_NAME=unsafe-staging-network",
                "CYBERTEAM_CONTAINER_PREFIX=unsafe-staging-prefix",
                "COMPOSE_SMOKE_SKIP_UP=0",
                "COMPOSE_SMOKE_CLEANUP=1",
                "API_BASE=https://wrong.example.test",
                "OWNER_EMAIL=owner-from-file@example.test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    capture_path = tmp_path / "captured-env.txt"
    fake_python = tmp_path / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$COMPOSE_PROJECT_NAME\" \"$CYBERTEAM_NETWORK_NAME\" "
        "\"$CYBERTEAM_CONTAINER_PREFIX\" "
        "\"$API_BASE\" \"$OWNER_EMAIL\" "
        "> \"$CAPTURE_PATH\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}:{env['PATH']}",
            "CAPTURE_PATH": str(capture_path),
            "COMPOSE_SMOKE_ENV_FILE": str(env_file),
            "COMPOSE_PROJECT_NAME": "isolated-smoke-project",
            "COMPOSE_SMOKE_SKIP_UP": "1",
            "COMPOSE_SMOKE_CLEANUP": "0",
            "API_BASE": "https://isolated.example.test",
        }
    )

    subprocess.run(
        ["bash", str(root / "scripts" / "compose-smoke.sh")],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert capture_path.read_text(encoding="utf-8").splitlines() == [
        "isolated-smoke-project",
        "isolated-smoke-project-network",
        "isolated-smoke-project",
        "https://isolated.example.test",
        "owner-from-file@example.test",
    ]

    env["CYBERTEAM_NETWORK_NAME"] = "explicit-smoke-network"
    env["CYBERTEAM_CONTAINER_PREFIX"] = "explicit-smoke-prefix"
    subprocess.run(
        ["bash", str(root / "scripts" / "compose-smoke.sh")],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    captured = capture_path.read_text(encoding="utf-8").splitlines()
    assert captured[1] == "explicit-smoke-network"
    assert captured[2] == "explicit-smoke-prefix"

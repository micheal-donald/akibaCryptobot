"""Deploy a CI image through the local Dokploy API; run as root over SSH."""

import fcntl
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

ROOT = Path("/opt/akibabot")
STATE = Path("/etc/akibabot/dokploy.json")


def command(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def api(state, method, payload):
    request = urllib.request.Request(
        state["url"] + "/api/" + method,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-api-key": state["api_key"]},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def healthy(image=None):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            actual = subprocess.check_output(
                ["docker", "inspect", "-f", "{{.Config.Image}}", "akiba-cryptobot"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            with urllib.request.urlopen(
                "http://127.0.0.1:8000/api/health", timeout=5
            ) as response:
                health = json.load(response)
            if (image is None or actual == image) and health.get("status") == "ok":
                print("Container image and API health verified", flush=True)
                return
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
        time.sleep(3)
    raise RuntimeError("Container did not become healthy within 300 seconds")


def main():
    state = json.loads(STATE.read_text())
    lock = open("/run/akiba-deploy.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    if sys.argv[1] == "--restart":
        command("chown", "-R", "1000:1000", str(ROOT / "models"))
        command("docker", "restart", "akiba-cryptobot")
        healthy()
        return
    image = sys.argv[1]
    if not re.fullmatch(
        r"ghcr\.io/micheal-donald/akibacryptobot@sha256:[a-f0-9]{64}", image
    ):
        raise ValueError("Expected the immutable CI image digest")
    token = sys.stdin.read().strip()
    if not token:
        raise ValueError("GHCR pull credential is required on stdin")
    dokploy = (
        subprocess.check_output(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.swarm.service.name=dokploy",
                "--format",
                "{{.ID}}",
            ],
            text=True,
        )
        .strip()
        .splitlines()[0]
    )
    command(
        "docker",
        "exec",
        "-i",
        dokploy,
        "docker",
        "login",
        "ghcr.io",
        "--username",
        "micheal-donald",
        "--password-stdin",
        input=token,
        stdout=subprocess.DEVNULL,
    )
    command("docker", "exec", dokploy, "docker", "pull", image)
    compose = (ROOT / "deploy/dokploy/compose.yml").read_text()
    previous = state.get("image")
    first = previous is None
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if first:
        backup = STATE.parent / "crontab.before-dokploy"
        backup.write_text(cron)
        backup.chmod(0o600)
        filtered = "".join(
            line + "\n" for line in cron.splitlines() if "/opt/akibabot" not in line
        )
        command("crontab", "-", input=filtered)
        command("systemctl", "disable", "--now", "akibabot.service")
        for directory in ("data", "logs", "models", "models_staging"):
            (ROOT / directory).mkdir(exist_ok=True)
            command("chown", "-R", "1000:1000", str(ROOT / directory))
    env = (ROOT / ".env").read_text()
    env = "\n".join(
        line for line in env.splitlines() if not line.startswith("AKIBA_IMAGE=")
    )
    env += "\nAKIBA_IMAGE=" + image + "\n"
    try:
        api(
            state,
            "compose.update",
            {
                "composeId": state["composeId"],
                "composeFile": compose,
                "sourceType": "raw",
                "randomize": False,
                "isolatedDeployment": False,
            },
        )
        api(
            state,
            "compose.saveEnvironment",
            {"composeId": state["composeId"], "env": env, "createEnvFile": True},
        )
        api(
            state,
            "compose.deploy",
            {"composeId": state["composeId"], "title": "CI " + image[-12:]},
        )
        healthy(image)
    except Exception:
        if first:
            # Never revive systemd while the container can still trade.
            subprocess.run(
                ["docker", "update", "--restart=no", "akiba-cryptobot"],
                capture_output=True,
            )
            subprocess.run(
                ["docker", "stop", "-t", "120", "akiba-cryptobot"], capture_output=True
            )
            command("systemctl", "enable", "--now", "akibabot.service")
            command("crontab", "-", input=cron)
        elif previous:
            old_env = env.replace("AKIBA_IMAGE=" + image, "AKIBA_IMAGE=" + previous)
            api(
                state,
                "compose.saveEnvironment",
                {
                    "composeId": state["composeId"],
                    "env": old_env,
                    "createEnvFile": True,
                },
            )
            api(
                state,
                "compose.deploy",
                {"composeId": state["composeId"], "title": "Automatic rollback"},
            )
            healthy(previous)
        raise
    state["previous_image"] = previous
    state["image"] = image
    STATE.write_text(json.dumps(state))
    STATE.chmod(0o600)
    print("Dokploy deployment completed", flush=True)


if __name__ == "__main__":
    main()

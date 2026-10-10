"""Deploy the Freqtrade image with Dokploy, preserving a rollback snapshot.

Runs on the existing Vultr server under the shared deployment lock. Never prints
API secrets. First cutover archives the stopped legacy SQLite stores and disables
its container restart. Later deployments preserve the Freqtrade database.
"""

import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path("/opt/akibabot")
STATE = Path("/etc/akibabot/dokploy.json")


def command(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def api(state, method, payload, *, get=False):
    url = state["url"] + "/api/" + method
    if get:
        url += "?" + urllib.parse.urlencode(payload)
    request = urllib.request.Request(
        url,
        data=None if get else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-api-key": state["api_key"]},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def secure_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(data)
    path.chmod(0o600)


def prepare():
    userdir = ROOT / "freqtrade/user_data"
    userdir.mkdir(parents=True, exist_ok=True)
    command("chown", "1000:1000", str(userdir))
    auth = STATE.parent / "freqtrade-api.json"
    if not auth.exists():
        secure_write(
            auth,
            json.dumps(
                {
                    "api_server": {
                        "username": "akiba",
                        "password": secrets.token_urlsafe(32),
                        "jwt_secret_key": secrets.token_urlsafe(48),
                        "ws_token": secrets.token_urlsafe(32),
                    }
                }
            ),
        )
    command("chown", "1000:1000", str(auth))


def healthy(image):
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        try:
            for name in ("akiba-freqtrade", "akiba-portfolio"):
                actual = subprocess.check_output(
                    ["docker", "inspect", "-f", "{{.Config.Image}}", name],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
                if actual != image:
                    raise ValueError("Image mismatch")
            with urllib.request.urlopen(
                "http://127.0.0.1:8000/api/portfolio", timeout=10
            ) as response:
                status = json.load(response)
            if (
                status.get("healthy")
                and status.get("mode") == "dry_run"
                and status.get("engine") == "freqtrade"
                and status.get("strategy") == "AkibaADXPaper"
                and set(status.get("pairs", [])) == {"SOL/USDT", "BTC/USDT", "ETH/USDT"}
            ):
                print(
                    "Both images, engine heartbeat, strategy and dry-run mode verified",
                    flush=True,
                )
                return
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
        time.sleep(5)
    raise RuntimeError("Freqtrade deployment failed health verification")


def set_compose(state, compose, env, title):
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
    api(state, "compose.deploy", {"composeId": state["composeId"], "title": title})


def main():
    image = sys.argv[1]
    if not re.fullmatch(
        r"ghcr\.io/micheal-donald/akibacryptobot@sha256:[a-f0-9]{64}", image
    ):
        raise ValueError("An immutable CI-published image is required")
    lock = open("/run/akiba-deploy.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    state = json.loads(STATE.read_text())
    previous = api(state, "compose.one", {"composeId": state["composeId"]}, get=True)
    token = sys.stdin.read().strip()
    if not token:
        raise ValueError("GHCR pull credential required on stdin")
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
    prepare()
    backup = STATE.parent / (
        "before-freqtrade-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )
    backup.mkdir(mode=0o700)
    secure_write(backup / "dokploy.json", json.dumps(state))
    secure_write(backup / "compose.json", json.dumps(previous))
    first = state.get("engine") != "freqtrade"
    compose = (ROOT / "deploy/dokploy/compose.yml").read_text()
    # Do not inherit the old exchange/Telegram/ML environment into either service.
    env = "AKIBA_IMAGE=" + image + "\n"
    try:
        if first:
            # Confirm this is the paper service before stopping or archiving it.
            with urllib.request.urlopen(
                "http://127.0.0.1:8000/api/status", timeout=10
            ) as response:
                old = json.load(response)
            old = old.get("data", old)
            if old.get("trading_mode") != "paper":
                raise RuntimeError(
                    "Legacy engine is not confirmed paper; refusing cutover"
                )
            command("docker", "update", "--restart=no", "akiba-cryptobot")
            command("docker", "stop", "-t", "120", "akiba-cryptobot")
            if (ROOT / "data").exists():
                shutil.copytree(ROOT / "data", backup / "legacy-data")
            command("systemctl", "disable", "akibabot.service")
        set_compose(state, compose, env, "Freqtrade dry-run " + image[-12:])
        healthy(image)
    except Exception:
        # Stop the replacement before restoring the complete old compose AND env.
        for name in ("akiba-freqtrade", "akiba-portfolio"):
            subprocess.run(
                ["docker", "update", "--restart=no", name], capture_output=True
            )
            subprocess.run(["docker", "stop", "-t", "90", name], capture_output=True)
        set_compose(
            state,
            previous["composeFile"],
            previous.get("env") or "",
            "Migration rollback",
        )
        print("Rollback requested. Archived state: " + str(backup), flush=True)
        raise
    state["previous_image"] = state.get("image")
    state["image"] = image
    state["engine"] = "freqtrade"
    state["migration_backup"] = str(backup)
    secure_write(STATE, json.dumps(state))
    print("Freqtrade deployment complete; rollback archive: " + str(backup), flush=True)


if __name__ == "__main__":
    main()

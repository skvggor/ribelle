"""Run a temporary, pinned SonarQube instance and enforce its quality gate."""

import base64
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SONAR_IMAGE = (
    "sonarqube:26.9.0.129388-community"
    "@sha256:c0f1160bccfa435db4168c2d7df69af3c3ea7bfeb87395613014048fb33a68c1"
)
SCANNER_IMAGE = (
    "sonarsource/sonar-scanner-cli:12.2.0.4256_8.1.0"
    "@sha256:a3f4215076706c95a17a68c19322ee916e40a3acd081a8c1a1e839e0194afa57"
)
CONTAINER_NAME = "ribelle-sonar"
PROJECT_KEY = "ribelle"
GATE_NAME = "ribelle"
SERVER_PORT = int(os.environ.get("SONAR_PORT", "9000"))
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"
BOOT_TIMEOUT_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 20
ADMIN_CREDENTIALS = "admin:admin"
KEEP_ALIVE = os.environ.get("SONAR_KEEP_ALIVE") == "1"
QUALITY_GATE_CONDITIONS = (
    {"metric": "bugs", "op": "GT", "error": "0"},
    {"metric": "vulnerabilities", "op": "GT", "error": "0"},
    {"metric": "coverage", "op": "LT", "error": "80"},
    {"metric": "duplicated_lines_density", "op": "GT", "error": "0"},
    {"metric": "code_smells", "op": "GT", "error": "0"},
)


def run(command: list[str], *, capture_output: bool = False, check: bool = True) -> str:
    result = subprocess.run(
        command,
        capture_output=capture_output,
        check=check,
        text=True,
    )
    return result.stdout or ""


def request_json(
    path: str, data: dict[str, str] | None = None, token: str | None = None
) -> dict[str, object]:
    headers = {"Accept": "application/json"}
    if token is None:
        credentials = base64.b64encode(ADMIN_CREDENTIALS.encode()).decode()
        headers["Authorization"] = f"Basic {credentials}"
    else:
        headers["Authorization"] = f"Bearer {token}"
    request_data = urlencode(data).encode() if data is not None else None
    if request_data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(f"{SERVER_URL}{path}", data=request_data, headers=headers)
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        response_body = response.read()
    if not response_body:
        return {}
    payload = json.loads(response_body)
    if not isinstance(payload, dict):
        raise RuntimeError(f"SonarQube returned invalid JSON from {path}")
    return cast(dict[str, object], payload)


def is_container_running() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "--format={{.State.Running}}", CONTAINER_NAME],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def wait_until_ready() -> None:
    deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not is_container_running():
            raise RuntimeError("SonarQube stopped before it was ready")
        try:
            status = request_json("/api/system/status")
            if status.get("status") == "UP":
                return
        except (OSError, RuntimeError):
            pass
        time.sleep(2)
    raise RuntimeError(f"SonarQube did not become ready within {BOOT_TIMEOUT_SECONDS} seconds")


def create_analysis_token() -> str:
    response = request_json(
        "/api/user_tokens/generate",
        {"name": "ribelle-scan", "login": "admin", "type": "GLOBAL_ANALYSIS_TOKEN"},
    )
    token = response.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("SonarQube did not return an analysis token")
    return token


def install_quality_gate() -> None:
    request_json("/api/qualitygates/create", {"name": GATE_NAME})
    gate = request_json(f"/api/qualitygates/show?name={GATE_NAME}")
    conditions = gate.get("conditions", [])
    if not isinstance(conditions, list):
        raise RuntimeError("SonarQube returned invalid quality gate conditions")
    for condition in conditions:
        if isinstance(condition, dict) and isinstance(condition.get("id"), str):
            request_json("/api/qualitygates/delete_condition", {"id": condition["id"]})
    for condition in QUALITY_GATE_CONDITIONS:
        request_json(
            "/api/qualitygates/create_condition",
            {"gateName": GATE_NAME, **condition},
        )
    request_json("/api/qualitygates/set_as_default", {"name": GATE_NAME})


def scanner_command(project_directory: Path, token: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "--volume",
        f"{project_directory}:/usr/src",
        "--workdir",
        "/usr/src",
        "--env",
        f"SONAR_HOST_URL={SERVER_URL}",
        "--env",
        f"SONAR_TOKEN={token}",
        SCANNER_IMAGE,
    ]


def quality_gate_passed() -> bool:
    status = request_json(f"/api/qualitygates/project_status?projectKey={PROJECT_KEY}")
    project_status = status.get("projectStatus")
    return isinstance(project_status, dict) and project_status.get("status") == "OK"


def report_issues(token: str) -> int:
    response = request_json(
        f"/api/issues/search?componentKeys={PROJECT_KEY}&ps=500",
        token=token,
    )
    issues = response.get("issues", [])
    if not isinstance(issues, list):
        raise RuntimeError("SonarQube returned an invalid issues report")
    issue_records = [issue for issue in issues if isinstance(issue, dict)]
    severities = Counter(
        issue["severity"] for issue in issue_records if isinstance(issue.get("severity"), str)
    )
    print(f"SonarQube issues: {len(issue_records)}")
    for severity, count in sorted(severities.items()):
        print(f"  {severity}: {count}")
    for issue in issue_records:
        component = issue.get("component", PROJECT_KEY)
        path = component.removeprefix(f"{PROJECT_KEY}:") if isinstance(component, str) else "?"
        line = issue.get("line", "?")
        message = issue.get("message", "")
        print(f"  {path}:{line} {message}")
    return len(issue_records)


def revoke_analysis_token() -> None:
    request_json("/api/user_tokens/revoke", {"name": "ribelle-scan"})


def start_container() -> None:
    existing = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{CONTAINER_NAME}$", "--format", "{{.ID}}"],
        capture_output=True,
        check=True,
        text=True,
    )
    if existing.stdout.strip():
        raise RuntimeError(
            f"Container {CONTAINER_NAME} already exists; remove it or choose another name"
        )
    run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            CONTAINER_NAME,
            "--publish",
            f"127.0.0.1:{SERVER_PORT}:9000",
            "--ulimit",
            "nofile=65536:65536",
            "--env",
            "SONAR_WEB_JAVAOPTS=-Xmx1G",
            "--env",
            "SONAR_ES_JAVAOPTS=-Xmx1G",
            SONAR_IMAGE,
        ]
    )


def remove_container() -> None:
    subprocess.run(["docker", "rm", "--force", CONTAINER_NAME], check=False)


def project_directory() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    project_root = project_directory()
    analysis_token: str | None = None
    container_started = False
    try:
        subprocess.run(
            [sys.executable, "-m", "pytest", "--cov-report=xml:coverage.xml"],
            cwd=project_root,
            check=True,
        )
        start_container()
        container_started = True
        print(f"Waiting for SonarQube at {SERVER_URL}.")
        wait_until_ready()
        analysis_token = create_analysis_token()
        install_quality_gate()
        print("Analysing the project with the pinned SonarScanner image.")
        scanner_result = subprocess.run(
            scanner_command(project_root, analysis_token),
            check=False,
        )
        if scanner_result.returncode != 0:
            print(f"SonarScanner exited with {scanner_result.returncode}.")
        issue_count = report_issues(analysis_token)
        passed = quality_gate_passed()
        print(f"Quality gate: {'passed' if passed else 'failed'}; {issue_count} issues.")
        return 0 if passed and scanner_result.returncode == 0 else 1
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"SonarQube scan failed: {error}", file=sys.stderr)
        return 1
    finally:
        if analysis_token is not None:
            try:
                revoke_analysis_token()
            except (OSError, RuntimeError, ValueError):
                pass
        if container_started and not KEEP_ALIVE:
            remove_container()
        elif container_started:
            print(f"Leaving SonarQube running at {SERVER_URL}.")


if __name__ == "__main__":
    raise SystemExit(main())

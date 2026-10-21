from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from subprocess import CompletedProcess
from urllib.request import Request

import pytest

from ribelle import sonar_scan


def test_project_directory_points_to_the_repository_root() -> None:
    project_directory = sonar_scan.project_directory()

    assert (project_directory / "pyproject.toml").is_file()
    assert (project_directory / "tests").is_dir()


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *arguments: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class FakeEmptyResponse(FakeResponse):
    def read(self) -> bytes:
        return b""


def test_scanner_command_mounts_the_project_and_passes_token_through_environment() -> None:
    command = sonar_scan.scanner_command(Path("/project"), "secret-token")

    assert command[command.index("--volume") + 1] == "/project:/usr/src"
    assert "SONAR_TOKEN=secret-token" in command
    assert sonar_scan.SCANNER_IMAGE in command
    assert "secret-token" not in " ".join(command[: command.index("--env")])


def test_request_json_uses_basic_auth_for_administration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def fake_urlopen(request: Request, timeout: int) -> FakeResponse:
        requests.append(request)
        return FakeResponse({"status": "UP"})

    monkeypatch.setattr(sonar_scan, "urlopen", fake_urlopen)

    assert sonar_scan.request_json("/api/system/status") == {"status": "UP"}
    authorization_header = requests[0].get_header("Authorization")
    assert authorization_header is not None
    assert authorization_header.startswith("Basic ")


def test_request_json_uses_bearer_auth_for_analysis_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def fake_urlopen(request: Request, timeout: int) -> FakeResponse:
        requests.append(request)
        return FakeResponse({"issues": []})

    monkeypatch.setattr(sonar_scan, "urlopen", fake_urlopen)

    sonar_scan.request_json("/api/issues/search", token="analysis-token")

    assert requests[0].get_header("Authorization") == "Bearer analysis-token"


def test_request_json_encodes_form_data_and_rejects_non_object_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def fake_urlopen(request: Request, timeout: int) -> FakeResponse:
        requests.append(request)
        return FakeResponse({"accepted": True})

    monkeypatch.setattr(sonar_scan, "urlopen", fake_urlopen)

    assert sonar_scan.request_json("/api/example", {"project": "ribelle"}) == {"accepted": True}
    assert requests[0].data == b"project=ribelle"
    assert requests[0].get_header("Content-type") == "application/x-www-form-urlencoded"

    monkeypatch.setattr(
        sonar_scan,
        "urlopen",
        lambda request, timeout: FakeResponse(["not", "an", "object"]),
    )
    with pytest.raises(RuntimeError, match="invalid JSON"):
        sonar_scan.request_json("/api/example")


def test_request_json_accepts_successful_responses_without_a_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sonar_scan,
        "urlopen",
        lambda request, timeout: FakeEmptyResponse({}),
    )

    assert sonar_scan.request_json("/api/qualitygates/delete_condition", {"id": "condition"}) == {}


def test_install_quality_gate_replaces_default_conditions_with_project_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, dict[str, str] | None]] = []

    def fake_request_json(
        path: str,
        data: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, object]:
        requests.append((path, data))
        if path.endswith("/show?name=ribelle"):
            return {"conditions": [{"id": "default-condition"}]}
        return {}

    monkeypatch.setattr(sonar_scan, "request_json", fake_request_json)

    sonar_scan.install_quality_gate()

    assert requests[0] == ("/api/qualitygates/create", {"name": "ribelle"})
    assert ("/api/qualitygates/delete_condition", {"id": "default-condition"}) in requests
    assert sum(path.endswith("/create_condition") for path, _ in requests) == 5
    assert requests[-1] == (
        "/api/qualitygates/set_as_default",
        {"name": "ribelle"},
    )


def test_install_quality_gate_rejects_invalid_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sonar_scan,
        "request_json",
        lambda path, data=None, token=None: {"conditions": "invalid"},
    )

    with pytest.raises(RuntimeError, match="invalid quality gate conditions"):
        sonar_scan.install_quality_gate()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"projectStatus": {"status": "OK"}}, True),
        ({"projectStatus": {"status": "ERROR"}}, False),
        ({"projectStatus": None}, False),
    ],
)
def test_quality_gate_reports_its_actual_status(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
    expected: bool,
) -> None:
    monkeypatch.setattr(sonar_scan, "request_json", lambda path: response)

    assert sonar_scan.quality_gate_passed() is expected


def test_create_analysis_token_requires_a_nonempty_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sonar_scan,
        "request_json",
        lambda path, data=None, token=None: {"token": "analysis-token"},
    )
    assert sonar_scan.create_analysis_token() == "analysis-token"

    monkeypatch.setattr(
        sonar_scan,
        "request_json",
        lambda path, data=None, token=None: {"token": ""},
    )
    with pytest.raises(RuntimeError, match="did not return an analysis token"):
        sonar_scan.create_analysis_token()


def test_report_issues_counts_severities_and_prints_locations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sonar_scan,
        "request_json",
        lambda path, data=None, token=None: {
            "issues": [
                {
                    "severity": "MAJOR",
                    "component": "ribelle:src/ribelle/cli.py",
                    "line": 42,
                    "message": "Consider simplifying this branch",
                },
                {"severity": "MINOR", "component": "ribelle:tests/test_cli.py"},
                None,
            ]
        },
    )

    assert sonar_scan.report_issues("analysis-token") == 2
    report = capsys.readouterr().out
    assert "MAJOR: 1" in report
    assert "src/ribelle/cli.py:42 Consider simplifying this branch" in report
    assert "tests/test_cli.py:?" in report


def test_report_issues_rejects_invalid_issue_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sonar_scan,
        "request_json",
        lambda path, data=None, token=None: {"issues": "invalid"},
    )

    with pytest.raises(RuntimeError, match="invalid issues report"):
        sonar_scan.report_issues("analysis-token")


def test_wait_until_ready_returns_when_the_server_reports_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sonar_scan, "is_container_running", lambda: True)
    monkeypatch.setattr(sonar_scan, "request_json", lambda path: {"status": "UP"})

    sonar_scan.wait_until_ready()


def test_wait_until_ready_retries_transient_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: Iterator[dict[str, str] | OSError] = iter(
        [ConnectionResetError("server is starting"), {"status": "UP"}]
    )
    delays: list[int] = []

    def fake_request(path: str) -> dict[str, str]:
        response = next(responses)
        if isinstance(response, OSError):
            raise response
        return response

    monkeypatch.setattr(sonar_scan, "is_container_running", lambda: True)
    monkeypatch.setattr(sonar_scan, "request_json", fake_request)
    monkeypatch.setattr(sonar_scan.time, "sleep", delays.append)

    sonar_scan.wait_until_ready()

    assert delays == [2]


def test_wait_until_ready_stops_if_the_container_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sonar_scan, "is_container_running", lambda: False)

    with pytest.raises(RuntimeError, match="stopped before it was ready"):
        sonar_scan.wait_until_ready()


def test_container_status_requires_a_running_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter(
        [
            CompletedProcess(args=[], returncode=0, stdout="true\n", stderr=""),
            CompletedProcess(args=[], returncode=1, stdout="", stderr="not found"),
        ]
    )
    monkeypatch.setattr(sonar_scan.subprocess, "run", lambda *args, **options: next(results))

    assert sonar_scan.is_container_running()
    assert not sonar_scan.is_container_running()


def test_start_container_refuses_to_remove_or_replace_an_existing_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[Sequence[str]] = []

    def fake_run(command: Sequence[str], **options: object) -> CompletedProcess[str]:
        commands.append(command)
        return CompletedProcess(args=command, returncode=0, stdout="existing-id\n", stderr="")

    monkeypatch.setattr(sonar_scan.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="already exists"):
        sonar_scan.start_container()

    assert len(commands) == 1


def test_start_container_uses_a_pinned_server_and_loopback_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sonar_scan.subprocess,
        "run",
        lambda command, **options: CompletedProcess(
            args=command,
            returncode=0,
            stdout="" if command[1] == "ps" else "container-id",
            stderr="",
        ),
    )
    server_commands: list[list[str]] = []
    monkeypatch.setattr(sonar_scan, "run", lambda command: server_commands.append(command) or "")

    sonar_scan.start_container()

    assert "127.0.0.1:9000:9000" in server_commands[0]
    assert sonar_scan.SONAR_IMAGE in server_commands[0]


def test_remove_container_ignores_a_missing_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sonar_scan.subprocess,
        "run",
        lambda command, **options: CompletedProcess(args=command, returncode=1),
    )

    sonar_scan.remove_container()


def test_main_removes_the_temporary_server_after_a_successful_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[Sequence[str]] = []
    scanner_commands: list[Sequence[str]] = []

    def fake_subprocess_run(
        command: Sequence[str],
        *,
        capture_output: bool = False,
        check: bool = False,
        text: bool = False,
        cwd: Path | None = None,
    ) -> CompletedProcess[str]:
        commands.append(command)
        if sonar_scan.SCANNER_IMAGE in command:
            scanner_commands.append(command)
        return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sonar_scan.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(sonar_scan, "wait_until_ready", lambda: None)
    monkeypatch.setattr(sonar_scan, "create_analysis_token", lambda: "temporary-token")
    monkeypatch.setattr(sonar_scan, "install_quality_gate", lambda: None)
    monkeypatch.setattr(sonar_scan, "report_issues", lambda token: 0)
    monkeypatch.setattr(sonar_scan, "quality_gate_passed", lambda: True)
    monkeypatch.setattr(sonar_scan, "revoke_analysis_token", lambda: None)
    monkeypatch.setattr(sonar_scan, "remove_container", lambda: commands.append(["remove"]))

    assert sonar_scan.main() == 0
    assert scanner_commands
    assert commands[-1] == ["remove"]


def test_main_keeps_the_server_for_inspection_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sonar_scan, "KEEP_ALIVE", True)
    monkeypatch.setattr(
        sonar_scan.subprocess,
        "run",
        lambda command, **options: CompletedProcess(
            args=command,
            returncode=1 if sonar_scan.SCANNER_IMAGE in command else 0,
            stdout="",
            stderr="",
        ),
    )
    monkeypatch.setattr(sonar_scan, "wait_until_ready", lambda: None)
    monkeypatch.setattr(sonar_scan, "create_analysis_token", lambda: "temporary-token")
    monkeypatch.setattr(sonar_scan, "install_quality_gate", lambda: None)
    monkeypatch.setattr(sonar_scan, "report_issues", lambda token: 1)
    monkeypatch.setattr(sonar_scan, "quality_gate_passed", lambda: False)
    monkeypatch.setattr(sonar_scan, "revoke_analysis_token", lambda: None)
    monkeypatch.setattr(sonar_scan, "remove_container", lambda: pytest.fail("container removed"))

    assert sonar_scan.main() == 1
    assert "Leaving SonarQube running" in capsys.readouterr().out


def test_main_still_removes_the_container_when_token_revocation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_calls: list[str] = []

    def fail_to_revoke_token() -> None:
        raise RuntimeError("token revocation failed")

    monkeypatch.setattr(
        sonar_scan.subprocess,
        "run",
        lambda command, **options: CompletedProcess(args=command, returncode=0, stdout=""),
    )
    monkeypatch.setattr(sonar_scan, "wait_until_ready", lambda: None)
    monkeypatch.setattr(sonar_scan, "create_analysis_token", lambda: "temporary-token")
    monkeypatch.setattr(sonar_scan, "install_quality_gate", lambda: None)
    monkeypatch.setattr(sonar_scan, "report_issues", lambda token: 0)
    monkeypatch.setattr(sonar_scan, "quality_gate_passed", lambda: True)
    monkeypatch.setattr(sonar_scan, "revoke_analysis_token", fail_to_revoke_token)
    monkeypatch.setattr(sonar_scan, "remove_container", lambda: cleanup_calls.append("removed"))

    assert sonar_scan.main() == 0
    assert cleanup_calls == ["removed"]

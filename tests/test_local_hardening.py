"""Local hardening: ports on 127.0.0.1 only, no default credentials, and a safe owner rotation.

Every published port listens on 127.0.0.1 only, so nothing on another
machine reaches the warehouse, Dagster or MinIO. `make up` and
`make rotate-owner-password` refuse to start while WAREHOUSE_PASSWORD,
MINIO_ROOT_USER or MINIO_ROOT_PASSWORD in `.env` is empty or still the
`.env.example` default; the check names the variable, never a value.
`make rotate-owner-password` sets the running owner role's password from
`.env` through the warehouse container's local socket, which needs no
password, so it cannot lock the owner out.

Everything here reads files or runs `make -n` and the credential check on
invented values; nothing starts a container or connects.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK = REPO_ROOT / "docker" / "check-credentials.sh"
ROTATE_SQL = REPO_ROOT / "docker" / "warehouse" / "rotate-owner-password.sql"
GUARDED = ("WAREHOUSE_PASSWORD", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD")
PASSED_DOWN = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"}
INVENTED = {
    "WAREHOUSE_PASSWORD": "invented_owner_pw_4e1a",
    "MINIO_ROOT_USER": "invented_minio_user_7b2c",
    "MINIO_ROOT_PASSWORD": "invented_minio_pw_9d3f",
}


def example_value(name: str) -> str:
    match = re.search(rf"^{name}=(.*)$", (REPO_ROOT / ".env.example").read_text("utf-8"), re.M)
    assert match is not None, name
    return match.group(1).strip()


def published_ports() -> list[tuple[str, object]]:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return [
        (service, port)
        for service, spec in compose["services"].items()
        for port in spec.get("ports", [])
    ]


def test_some_service_publishes_a_port() -> None:
    assert len(published_ports()) == 4


def test_every_published_port_listens_on_127_0_0_1_only() -> None:
    open_ports = [
        (service, port)
        for service, port in published_ports()
        if not (
            (isinstance(port, str) and port.startswith("127.0.0.1:"))
            or (isinstance(port, dict) and port.get("host_ip") == "127.0.0.1")
        )
    ]
    assert open_ports == []


def run_check(values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    environment = {name: value for name, value in os.environ.items() if name not in GUARDED}
    environment.update(values)
    return subprocess.run(
        [bash, str(CHECK)], capture_output=True, text=True, check=False, env=environment
    )


def test_invented_values_pass_the_check_silently() -> None:
    result = run_check(INVENTED)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


@pytest.mark.parametrize("name", GUARDED)
def test_a_default_value_is_refused_by_name_without_printing_it(name: str) -> None:
    result = run_check({**INVENTED, name: example_value(name)})
    assert result.returncode != 0
    assert name in result.stderr
    output = result.stdout + result.stderr
    shown = [value for value in (*INVENTED.values(), example_value(name)) if value in output]
    assert shown == [], f"the refusal for {name} prints a value"


@pytest.mark.parametrize("name", GUARDED)
def test_an_empty_or_missing_value_is_refused(name: str) -> None:
    assert run_check({**INVENTED, name: ""}).returncode != 0
    missing = {key: value for key, value in INVENTED.items() if key != name}
    assert run_check(missing).returncode != 0


def make_dry_run(goal: str, tmp_path: Path) -> list[str]:
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    (tmp_path / ".env").write_text(
        "".join(f"{name}={value}\n" for name, value in INVENTED.items()), encoding="utf-8"
    )
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in PASSED_DOWN and name not in GUARDED
    }
    result = subprocess.run(
        [make, "--no-print-directory", "-n", "-f", str(REPO_ROOT / "Makefile"), goal],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert not [value for value in INVENTED.values() if value in result.stdout + result.stderr]
    return result.stdout.splitlines()


def first_line(lines: list[str], text: str) -> int:
    return next(index for index, line in enumerate(lines) if text in line)


def test_make_up_checks_the_credentials_before_it_starts_anything(tmp_path: Path) -> None:
    lines = make_dry_run("up", tmp_path)
    assert first_line(lines, "check-credentials.sh") < first_line(lines, " up --build")


def test_the_rotation_checks_starts_the_warehouse_and_logs_in_over_the_socket(
    tmp_path: Path,
) -> None:
    lines = make_dry_run("rotate-owner-password", tmp_path)
    text = "\n".join(lines)
    check = first_line(lines, "check-credentials.sh")
    start = first_line(lines, "up --detach warehouse")
    rotate = first_line(lines, "rotate-owner-password.sql")
    assert check < start < rotate
    # The new password goes in by name only, and psql gets no -h, so it uses
    # the container's local socket, which the image trusts, not a password.
    assert "-e WAREHOUSE_PASSWORD" in text
    psql = lines[rotate][lines[rotate].index("psql") :]
    assert " -h " not in psql
    assert "PGPASSWORD" not in text


def test_the_image_keeps_its_default_local_socket_trust() -> None:
    """Nothing in the build or Compose changes how a local-socket login is checked."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["warehouse"]["environment"]
    assert "POSTGRES_INITDB_ARGS" not in environment
    assert "POSTGRES_HOST_AUTH_METHOD" not in environment
    files = [REPO_ROOT / "docker" / "warehouse" / "Dockerfile"]
    files += sorted((REPO_ROOT / "docker" / "warehouse" / "initdb").iterdir())
    assert [path.name for path in files if "pg_hba" in path.read_text(encoding="utf-8")] == []


def test_the_rotation_sql_prints_and_logs_no_value() -> None:
    text = ROTATE_SQL.read_text(encoding="utf-8")
    assert "\\getenv owner_password WAREHOUSE_PASSWORD" in text
    assert "\\gexec" not in text and "\\echo" not in text
    assert "\\set SHOW_CONTEXT never" in text and "\\set VERBOSITY terse" in text
    assert text.index("set log_transaction_sample_rate to 0;") < text.index("\nbegin;")
    for setting in (
        "log_statement to 'none'",
        "log_min_error_statement to panic",
        "log_min_duration_statement to -1",
        "log_min_duration_sample to -1",
        "log_error_verbosity to terse",
    ):
        assert f"set local {setting};" in text, setting
    assert re.search(r"raise exception '[^']*', sqlstate", text)
    raises = re.findall(r"raise exception ([^;]*);", text)
    assert raises
    assert [message for message in raises if "current_setting" in message] == []

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_postgres_provisioning_runs_for_new_and_existing_volumes() -> None:
    compose = (_PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    provisioner = (_PROJECT_ROOT / "init-sql" / "99-create-app-reader.sh").read_text(
        encoding="utf-8"
    )

    assert "./init-sql:/docker-entrypoint-initdb.d" in compose
    assert "postgres-init:" in compose
    assert 'command: ["sh", "/init-sql/99-create-app-reader.sh"]' in compose
    assert (
        "postgres-init:\n        condition: service_completed_successfully" in compose
    )
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in provisioner
    assert "ALTER ROLE %I NOINHERIT NOSUPERUSER" in provisioner
    assert "REVOKE %I FROM %I" in provisioner
    assert "REVOKE TEMPORARY ON DATABASE %I FROM %I" in provisioner
    assert "REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC" in provisioner
    assert "NOT has_database_privilege(" in provisioner
    assert "'TEMPORARY'" in provisioner
    assert "database.datdba = app_role.oid" in provisioner
    assert "application_role_is_read_only" in provisioner


def test_neo4j_readiness_and_password_provisioning_avoid_shell_injection() -> None:
    compose = (_PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    services = yaml.safe_load(compose)["services"]
    provisioner = (_PROJECT_ROOT / "init-neo4j" / "01-create-reader.sh").read_text(
        encoding="utf-8"
    )

    assert set(services["neo4j"]["environment"]) == {
        "NEO4J_AUTH",
        "NEO4J_ACCEPT_LICENSE_AGREEMENT",
        "HEALTHCHECK_NEO4J_USER",
        "HEALTHCHECK_NEO4J_PASSWORD",
    }
    assert (
        '-u \\"$$HEALTHCHECK_NEO4J_USER\\" '
        '-p \\"$$HEALTHCHECK_NEO4J_PASSWORD\\"' in compose
    )
    assert set(services["neo4j-init"]["environment"]) == {
        "NEO4J_ACCEPT_LICENSE_AGREEMENT",
        "GRAPH_URI",
        "ADMIN_NEO4J_USER",
        "ADMIN_NEO4J_PASSWORD",
        "APP_NEO4J_USER",
        "APP_NEO4J_PASSWORD",
    }
    assert "-P \"{app_password: '$escaped_app_password'}\"" in provisioner
    assert "SET PASSWORD \\$app_password CHANGE NOT REQUIRED" in provisioner
    assert "SET PASSWORD '$APP_NEO4J_PASSWORD'" not in provisioner
    assert "escaped_app_password=" in provisioner


def test_neo4j_provisioning_rejects_admin_and_resets_all_roles() -> None:
    provisioner = (_PROJECT_ROOT / "init-neo4j" / "01-create-reader.sh").read_text(
        encoding="utf-8"
    )

    assert '[ "$APP_NEO4J_USER" = "$ADMIN_NEO4J_USER" ]' in provisioner
    assert "CREATE OR REPLACE USER" in provisioner
    assert "SHOW USERS YIELD user, roles" in provisioner
    assert "size(roles) = 2" in provisioner
    assert "'PUBLIC' IN roles" in provisioner
    assert "'reader' IN roles" in provisioner
    assert "role verification failed" in provisioner


def test_neo4j_provisioning_rejects_same_admin_and_app_user() -> None:
    shell = shutil.which("sh")
    if shell is None:
        git_shell = Path("C:/Program Files/Git/bin/sh.exe")
        if not git_shell.exists():
            pytest.skip("POSIX shell is unavailable")
        shell = str(git_shell)

    environment = os.environ | {
        "GRAPH_URI": "bolt://unused:7687",
        "ADMIN_NEO4J_USER": "same_user",
        "ADMIN_NEO4J_PASSWORD": "admin password # $",
        "APP_NEO4J_USER": "same_user",
        "APP_NEO4J_PASSWORD": "reader password # $",
    }
    result = subprocess.run(
        [shell, str(_PROJECT_ROOT / "init-neo4j" / "01-create-reader.sh")],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode != 0
    assert "must differ" in result.stderr


def test_ontology_seed_runs_as_admin_before_backend() -> None:
    compose = yaml.safe_load(
        (_PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    seed = services["ontology-seed"]

    assert seed["environment"]["ADMIN_NEO4J_USER"] == "${NEO4J_USER}"
    assert seed["environment"]["ADMIN_NEO4J_PASSWORD"] == "${NEO4J_PASSWORD}"
    assert seed["command"] == ["python", "-m", "ontology.seed"]
    assert (
        services["backend"]["depends_on"]["ontology-seed"]["condition"]
        == "service_completed_successfully"
    )
    assert "ADMIN_NEO4J_USER" not in services["backend"]["environment"]


def test_backend_mounts_schema_and_ontology_at_configured_paths() -> None:
    compose = yaml.safe_load(
        (_PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    backend = compose["services"]["backend"]

    assert backend["environment"]["SCHEMA_DIR"] == "/schema-data"
    assert (
        backend["environment"]["ONTOLOGY_PATH"]
        == "/ontology-data/manufacturing_terms.yaml"
    )
    assert "./schema:/schema-data:ro" in backend["volumes"]
    assert "./ontology:/ontology-data:ro" in backend["volumes"]
    assert (_PROJECT_ROOT / "schema" / "sql_schema.yaml").is_file()
    assert (_PROJECT_ROOT / "schema" / "graph_schema.yaml").is_file()
    assert (_PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml").is_file()


def test_backend_receives_openai_configuration_from_environment() -> None:
    compose = yaml.safe_load(
        (_PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    example_keys = {
        line.split("=", 1)[0]
        for line in (_PROJECT_ROOT / ".env.example")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    backend_environment = compose["services"]["backend"]["environment"]

    assert {"OPENAI_API_KEY", "OPENAI_MODEL"} <= example_keys
    assert backend_environment["OPENAI_API_KEY"] == "${OPENAI_API_KEY}"
    assert backend_environment["OPENAI_MODEL"] == "${OPENAI_MODEL}"

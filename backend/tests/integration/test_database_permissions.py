import os

import psycopg
import pytest
from neo4j import GraphDatabase
from neo4j.exceptions import ClientError
from psycopg.errors import InsufficientPrivilege


@pytest.mark.integration
def test_postgres_app_role_can_read_but_cannot_write() -> None:
    connection = psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_APP_USER"],
        password=os.environ["POSTGRES_APP_PASSWORD"],
    )
    try:
        assert connection.execute("SELECT 1").fetchone() == (1,)
        assert connection.execute("SELECT similarity('abc', 'abc')").fetchone() == (
            1.0,
        )
        privileges = connection.execute("""
            SELECT
              has_table_privilege(current_user, 'production.product', 'SELECT'),
              has_table_privilege(current_user, 'production.product', 'INSERT'),
              has_table_privilege(current_user, 'production.product', 'UPDATE'),
              has_table_privilege(current_user, 'production.product', 'DELETE')
            """).fetchone()
        assert privileges == (True, False, False, False)
        role_security = connection.execute("""
            SELECT
              NOT rolinherit,
              NOT rolsuper,
              NOT rolcreaterole,
              NOT rolcreatedb,
              NOT rolreplication,
              NOT rolbypassrls,
              NOT EXISTS (
                SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid
              ),
              NOT EXISTS (
                SELECT 1
                FROM pg_database
                WHERE datname = current_database()
                  AND datdba = pg_roles.oid
              )
            FROM pg_roles
            WHERE rolname = current_user
            """).fetchone()
        assert role_security == (True, True, True, True, True, True, True, True)
        assert connection.execute(
            "SELECT has_database_privilege("
            "current_user, current_database(), 'TEMPORARY')"
        ).fetchone() == (False,)

        with pytest.raises(InsufficientPrivilege):
            connection.execute("DELETE FROM production.product WHERE false")
        connection.rollback()

        with pytest.raises(InsufficientPrivilege):
            connection.execute("CREATE TABLE public.issue22_write_probe (id int)")
        connection.rollback()

        with pytest.raises(InsufficientPrivilege):
            connection.execute("CREATE TEMP TABLE issue22_temp_write_probe (id int)")
        connection.rollback()
    finally:
        connection.close()


@pytest.mark.integration
def test_neo4j_app_role_can_read_but_cannot_write() -> None:
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_APP_USER"], os.environ["NEO4J_APP_PASSWORD"]),
    )
    try:
        assert driver.execute_query("RETURN 1 AS value").records[0]["value"] == 1
        for query in (
            "CREATE (:Issue22WriteProbe)",
            "MATCH (n) SET n.issue22WriteProbe = true",
            "MATCH (n) DELETE n",
        ):
            with pytest.raises(ClientError, match="(?i)forbidden|permission"):
                driver.execute_query(query)
    finally:
        driver.close()

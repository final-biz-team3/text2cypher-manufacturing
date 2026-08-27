from typing import Any

import core.neo4j as neo4j


def test_driver_uses_application_reader_credentials(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    driver = object()

    def make_driver(uri: str, *, auth: tuple[str, str]) -> object:
        captured.update({"uri": uri, "auth": auth})
        return driver

    monkeypatch.setattr(neo4j, "_driver", None)
    monkeypatch.setattr(neo4j.AsyncGraphDatabase, "driver", make_driver)
    monkeypatch.setenv("NEO4J_APP_USER", "graph_reader")
    monkeypatch.setenv("NEO4J_APP_PASSWORD", "reader_password")

    assert neo4j.get_driver() is driver
    assert captured["auth"] == ("graph_reader", "reader_password")

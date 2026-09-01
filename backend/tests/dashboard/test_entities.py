import pytest

import dashboard.entities as entities
from dashboard.service import DashboardServiceError


async def test_rejects_unknown_entity_type() -> None:
    with pytest.raises(DashboardServiceError) as exc_info:
        await entities.get_entity_detail("category", "1")
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_ENTITY_TYPE"


async def test_rejects_non_numeric_id_for_numeric_entity() -> None:
    with pytest.raises(DashboardServiceError) as exc_info:
        await entities.get_entity_detail("product", "not-a-number")
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_ENTITY_ID"


async def test_missing_entity_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing_product(_entity_id: int):
        return None

    monkeypatch.setattr(entities, "_product_detail", missing_product)

    with pytest.raises(DashboardServiceError) as exc_info:
        await entities.get_entity_detail("product", "956")
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "ENTITY_NOT_FOUND"


async def test_neighbors_allow_only_depth_one() -> None:
    with pytest.raises(DashboardServiceError) as exc_info:
        await entities.get_entity_neighbors("product", "956", depth=2)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_DEPTH"

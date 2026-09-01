"""공통 부품 summary의 고정 core와 요청 scalar 보존 계약."""

import pytest

from tests.orchestrator.plan_outputs_test_support import (
    COMMON_COMPONENT_OUTPUTS,
    complete_outputs,
    plan_single_subquery,
)

COMMON_PRODUCTS = [
    {"productId": 8301, "productName": "Cedar Bike"},
    {"productId": 8302, "productName": "Quartz Bike"},
]


@pytest.mark.parametrize(
    ("query", "route_question", "selected_outputs", "expected"),
    [
        pytest.param(
            "Cedar Bike와 Quartz Bike에 모두 들어가는 공통 부품은?",
            "두 완제품의 BOM 공통 부품과 최소 깊이를 조회한다.",
            [
                "finishedProductIdA",
                "finishedProductIdB",
                "finishedProductId",
                "finishedProductName",
                "componentId",
                "componentName",
                "minDepthA",
                "minDepthB",
                "pathProductIds",
            ],
            COMMON_COMPONENT_OUTPUTS,
            id="core-without-paths",
        ),
        pytest.param(
            "Cedar Bike와 Quartz Bike의 현재 공통 부품을 보여줘",
            "BOM endDate를 현재 날짜로 필터하고 두 제품의 공통 부품을 조회한다",
            ["componentId", "componentName", "endDate"],
            COMMON_COMPONENT_OUTPUTS,
            id="route-filter-only-property",
        ),
        pytest.param(
            "Cedar Bike와 Quartz Bike의 BOM 유효 종료일이 2020년 이후인 공통 부품",
            None,
            ["componentId", "componentName", "endDate"],
            COMMON_COMPONENT_OUTPUTS,
            id="explicit-filter-only-property",
        ),
        pytest.param(
            (
                "Cedar Bike와 Quartz Bike에 모두 들어가는 부품 중 "
                "BOM 종료일이 미등록된 공통 부품은?"
            ),
            "두 완제품의 BOM 종료일이 NULL인 공통 부품과 최소 깊이를 조회한다.",
            ["componentId", "componentName", "startDate", "pathProductIds", "depth"],
            [*COMMON_COMPONENT_OUTPUTS, "endDate"],
            id="missing-bom-status",
        ),
        pytest.param(
            "Cedar Bike와 Quartz Bike의 공통 부품과 단위당 필요 수량을 보여줘",
            "두 완제품의 공통 부품과 BOM 수량을 조회한다.",
            [
                "componentId",
                "componentName",
                "quantityPerAssembly",
                "finishedProductName",
            ],
            [*COMMON_COMPONENT_OUTPUTS, "quantityPerAssembly"],
            id="requested-bom-scalar",
        ),
        pytest.param(
            (
                "Cedar Bike와 Quartz Bike의 공통 부품 중 활성 공급업체와 "
                "활성 상태를 보여줘"
            ),
            None,
            [
                "componentId",
                "componentName",
                "supplierId",
                "supplierName",
                "active",
            ],
            [
                *COMMON_COMPONENT_OUTPUTS,
                "supplierId",
                "supplierName",
                "active",
            ],
            id="owner-identity-for-property",
        ),
        pytest.param(
            (
                "Cedar Bike와 Quartz Bike의 BOM 종료일이 미등록된 공통 부품과 "
                "단위당 필요 수량을 보여줘"
            ),
            None,
            ["componentId", "componentName", "quantityPerAssembly", "endDate"],
            [*COMMON_COMPONENT_OUTPUTS, "quantityPerAssembly", "endDate"],
            id="missing-status-with-scalar",
        ),
    ],
)
def test_common_component_output_policy(
    query: str,
    route_question: str | None,
    selected_outputs: list[str],
    expected: list[str],
) -> None:
    assert (
        complete_outputs(
            query=query,
            route_question=route_question,
            selected_outputs=selected_outputs,
            tool="graph",
            entity=COMMON_PRODUCTS,
        )
        == expected
    )


async def test_common_component_policy_is_connected_to_the_node() -> None:
    query = "Cedar Bike와 Quartz Bike에 모두 들어가는 공통 부품은?"
    result, client = await plan_single_subquery(
        query=query,
        route_question="두 완제품의 BOM 공통 부품과 최소 깊이를 조회한다.",
        selected_outputs=["componentId", "componentName", "pathProductIds"],
        tool="graph",
        entity=COMMON_PRODUCTS,
        subquery_id="graph_common_components",
    )

    assert result["subqueries"][0]["requiredOutputs"] == COMMON_COMPONENT_OUTPUTS
    assert result["subqueries"][0]["question"] == query
    assert len(client.calls) == 1

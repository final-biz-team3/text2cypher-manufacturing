from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from evaluation.errors import ResultContractError
from evaluation.models import load_manifest
from evaluation.normalization import normalize_rows, normalized_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_normalizes_alias_decimal_date_null_array_and_ordering() -> None:
    rows = [
        {
            "product_id": 10,
            "amount": Decimal("2.1"),
            "as_of": date(2014, 8, 8),
            "optional": None,
            "path_ids": [10, 2],
        },
        {
            "product_id": 2,
            "amount": Decimal("10"),
            "as_of": date(2014, 8, 8),
            "optional": None,
            "path_ids": [2, 1],
        },
    ]

    normalized = normalize_rows(
        rows,
        required_outputs=("productId", "amount", "asOf", "optional", "pathIds"),
        aliases={},
        ordering=("amount DESC", "productId ASC"),
    )

    assert normalized[0] == {
        "productId": 2,
        "amount": "10.000000",
        "asOf": "2014-08-08",
        "optional": None,
        "pathIds": [2, 1],
    }
    assert normalized_sha256(normalized) == normalized_sha256(list(normalized))


def test_single_output_uses_the_only_column_when_alias_is_unambiguous() -> None:
    normalized = normalize_rows(
        [{"externalPartCount": 265}],
        required_outputs=("purchasedProductCount",),
        aliases={},
        ordering=(),
    )

    assert normalized == [{"purchasedProductCount": 265}]


def test_rejects_missing_required_field_when_multiple_columns_are_ambiguous() -> None:
    with pytest.raises(ResultContractError, match="필수 결과 필드"):
        normalize_rows(
            [{"wrong": 1, "other": 2}],
            required_outputs=("productId",),
            aliases={},
            ordering=(),
        )


def test_matches_approved_alias_across_camel_and_snake_case() -> None:
    normalized = normalize_rows(
        [{"productCategoryId": 2}],
        required_outputs=("categoryId",),
        aliases={"categoryId": ("product_category_id",)},
        ordering=(),
    )

    assert normalized == [{"categoryId": 2}]


def test_ordering_produces_the_same_hash_for_different_input_order() -> None:
    rows = [{"productId": 2}, {"productId": 1}]
    forward = normalize_rows(
        rows,
        required_outputs=("productId",),
        aliases={},
        ordering=("productId ASC",),
    )
    reverse = normalize_rows(
        list(reversed(rows)),
        required_outputs=("productId",),
        aliases={},
        ordering=("productId ASC",),
    )

    assert normalized_sha256(forward) == normalized_sha256(reverse)


def test_rejects_conflicting_values_from_two_approved_aliases() -> None:
    with pytest.raises(ResultContractError, match="alias 필드 값"):
        normalize_rows(
            [{"productId": 1, "product_id": 2}],
            required_outputs=("productId",),
            aliases={},
            ordering=(),
        )


def test_manifest_maps_only_semantically_equivalent_role_aliases() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    subquery = manifest.contracts["RQ13"].subqueries[0]

    normalized = normalize_rows(
        [
            {
                "startProductId": 680,
                "componentProductId": 486,
                "productIdPath": [680, 486],
            }
        ],
        required_outputs=("rootProductId", "componentId", "pathProductIds"),
        aliases=subquery.aliases,
        ordering=(),
    )

    assert normalized == [
        {
            "rootProductId": 680,
            "componentId": 486,
            "pathProductIds": [680, 486],
        }
    ]

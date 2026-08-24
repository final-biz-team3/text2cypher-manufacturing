"""동의어 YAML을 검증하는 모델."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TermConcept(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: "".join(
            word.capitalize() if index else word
            for index, word in enumerate(value.split("_"))
        )
    )

    concept_id: str
    concept_type: Literal["BUSINESS", "ACTION"]
    canonical: str
    target_type: str | None = None
    action_type: (
        Literal[
            "READ",
            "CREATE",
            "UPDATE",
            "DELETE",
            "SCHEMA_CHANGE",
            "PERMISSION_CHANGE",
        ]
        | None
    ) = None
    default_policy: Literal["ALLOW", "BLOCK"] | None = None
    terms: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_concept_fields(self) -> "TermConcept":
        if self.concept_type == "ACTION":
            if self.action_type is None or self.default_policy is None:
                raise ValueError(
                    "ACTION concept requires actionType and defaultPolicy."
                )
        elif self.action_type is not None or self.default_policy is not None:
            raise ValueError("BUSINESS concept cannot define action fields.")
        return self


class TermDictionary(BaseModel):
    version: str
    concepts: list[TermConcept]

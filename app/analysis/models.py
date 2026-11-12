from pydantic import BaseModel, ConfigDict, StrictStr, field_validator


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: StrictStr
    quote: StrictStr | None

    @field_validator("description", "quote")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("text must not be blank")
        return value


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: StrictStr
    strengths: list[Finding]
    mistakes: list[Finding]
    recommendations: list[StrictStr]

    @field_validator("summary")
    @classmethod
    def nonblank_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary must not be blank")
        return value

    @field_validator("recommendations")
    @classmethod
    def nonblank_recommendations(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("recommendations must not contain blank text")
        return values

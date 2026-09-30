from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrganizationAPMIDCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1, max_length=63)
    name: str | None = Field(default=None, max_length=100)
    description: str = Field(default='', max_length=4000)

    @field_validator('code')
    @classmethod
    def normalize_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized or any(not (ch.isalnum() or ch in {'-', '_'}) for ch in normalized):
            raise ValueError('APMID may contain only letters, digits, dash and underscore')
        return normalized


class OrganizationAPMIDUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None


class ProjectAPMIDRestrictionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    apmids: list[str] | None = Field(default=None, max_length=500)

    @field_validator('apmids')
    @classmethod
    def normalize_apmids(cls, value):
        if value is None:
            return None
        result = []
        for item in value:
            code = str(item).strip().upper()
            if code and code not in result:
                result.append(code)
        return result

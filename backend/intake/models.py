from enum import Enum

from pydantic import BaseModel, Field


class IntakeBranch(str, Enum):
    apartment_registration = "apartment_registration"
    registration_only = "registration_only"


class IntakeResident(BaseModel):
    id: str
    full_name: str | None = None
    phone: str | None = None
    is_main: bool = False


class IntakeCase(BaseModel):
    id: str
    phone: str
    branch: IntakeBranch
    step: str = Field(default="phone_verified")
    residents: list[IntakeResident] = Field(default_factory=list)
    status: str = Field(default="draft")

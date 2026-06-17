"""Shared Pydantic base for all Career Agent structured-output schemas.

Production conventions enforced for every model:
  - extra="forbid"        : reject unknown fields (catches LLM/JSON drift early)
  - validate_assignment   : re-validate on attribute set
  - str_strip_whitespace  : normalise stray whitespace from model/JSON output
  - frozen=False          : models are mutable within a node, snapshot on write
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CareerBaseModel(BaseModel):
    """Common config for every schema in the project."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )

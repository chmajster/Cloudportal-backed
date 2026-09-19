from dataclasses import dataclass

from fastapi import APIRouter


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """A self-contained router contribution to the backend application."""

    name: str
    router: APIRouter
    order: int = 1000
    prefix: str = '/api/v1'

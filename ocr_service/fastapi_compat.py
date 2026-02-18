from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@dataclass
class _Route:
    method: str
    path: str
    handler: Callable[..., Any]


class FastAPI:
    def __init__(self, title: str, version: str):
        self.title = title
        self.version = version
        self.routes: list[_Route] = []

    def post(self, path: str, response_model: Any | None = None):
        return self._register("POST", path)

    def get(self, path: str, response_model: Any | None = None):
        return self._register("GET", path)

    def _register(self, method: str, path: str):
        def decorator(func: Callable[..., Any]):
            self.routes.append(_Route(method=method, path=path, handler=func))
            return func

        return decorator

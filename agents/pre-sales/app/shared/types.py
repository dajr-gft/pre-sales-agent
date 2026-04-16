from __future__ import annotations

from typing import Generic, Literal, TypeVar, Union

from typing_extensions import NotRequired, TypedDict

T = TypeVar('T')


class ToolSuccess(TypedDict, Generic[T]):
    """Successful tool result."""

    status: Literal['success']
    data: T


class ToolError(TypedDict):
    """Failed tool result -- LLM uses this to recover gracefully."""

    status: Literal['error']
    error: str
    retryable: bool
    tool: NotRequired[str]
    suggestion: NotRequired[str]


class ToolNotFound(TypedDict):
    """Resource not found -- not an error, just empty."""

    status: Literal['not_found']
    error: str
    suggestion: NotRequired[str]


ToolResult = Union[ToolSuccess[T], ToolError, ToolNotFound]

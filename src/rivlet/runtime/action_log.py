from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path


class ActionLog(Protocol):
    """Graph action 与宿主日志设施之间的显式端口。"""

    def scope(self, path: Path) -> AbstractContextManager[None]: ...

    def append(self, path: Path, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class NoActionLog:
    """独立运行 Graph 时不接入宿主 action 日志。"""

    def scope(self, path: Path) -> AbstractContextManager[None]:
        del path
        return nullcontext()

    def append(self, path: Path, text: str) -> None:
        del path, text


NO_ACTION_LOG = NoActionLog()

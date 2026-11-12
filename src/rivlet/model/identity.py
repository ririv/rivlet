"""Declaration identities and mounted paths with display labels."""

from __future__ import annotations

from dataclasses import dataclass, field

from .._native import allocate_step_id
from ..errors import GraphError


@dataclass(frozen=True, order=True, slots=True)
class StepId:
    """One process-local declaration identity, independent of its mounts."""

    value: int = field(default_factory=allocate_step_id)

    def __post_init__(self) -> None:
        if self.value < 1:
            raise GraphError("step identity must be positive")

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, order=True, slots=True)
class StepPath:
    """One mounted position; display labels never participate in identity."""

    segments: tuple[int, ...]
    labels: tuple[str, ...] = field(default=(), compare=False, kw_only=True)

    # 深度 D 的构造/父子路径操作为 O(D) 时间与空间；展示标签与数值路径一起保存快照，
    # 不持有图对象或注册表，也不在取 labels 时重新遍历图。

    def __post_init__(self) -> None:
        if not self.segments:
            raise GraphError("step path must contain at least one segment")
        if any(segment < 0 for segment in self.segments):
            raise GraphError(
                f"step path segments must be non-negative: {self.segments}"
            )
        # 独立构造的数值路径默认按各段显示；编译挂载时显式绑定真实标签快照。
        if not self.labels:
            object.__setattr__(self, "labels", tuple(map(str, self.segments)))

    def child(self, index: int, *, label: str | None = None) -> StepPath:
        if index < 0:
            raise GraphError(f"step child index must be non-negative: {index}")
        segments = (*self.segments, index)
        child_label = ".".join(map(str, segments)) if label is None else label
        return StepPath(segments, labels=(*self.labels, child_label))

    def __str__(self) -> str:
        return ".".join(map(str, self.segments))

    @property
    def parent(self) -> StepPath | None:
        if len(self.segments) == 1:
            return None
        return StepPath(self.segments[:-1], labels=self.labels[:-1])

"""Agent 动态上下文组装与压缩入口。"""

from .compaction import CompactionEngine, CompactionResult, CompactionTrigger
from .manager import ContextManager

__all__ = ["CompactionEngine", "CompactionResult", "CompactionTrigger", "ContextManager"]

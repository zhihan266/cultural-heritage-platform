"""
文档切片算法的参数定义与约束校验。

集中配置字符窗口长度、重叠长度、边界搜索范围和分隔符优先级，
供 DocumentSplitter 决定切片位置；不涉及模型或存储参数。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SplitterConfig:
    """文档切片参数。"""

    # 每个切片允许包含的最大字符数。
    chunk_size: int = 800

    # 相邻切片之间重复保留的字符数。
    chunk_overlap: int = 120

    # 寻找切割边界时，切片至少应有的字符数。
    min_chunk_chars: int = 100

    # 只在窗口末尾一定比例的区域中寻找自然边界。
    boundary_search_ratio: float = 0.35

    # 切割边界优先级，从结构完整到结构较弱。
    separators: tuple[str, ...] = (
        "\n\n",
        "\n",
        "。",
        "！",
        "？",
        "；",
        "，",
        " ",
    )

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")

        if self.chunk_overlap < 0:
            raise ValueError("chunk_overlap 不能小于 0")

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                "chunk_overlap 必须小于 chunk_size"
            )

        if not 0 < self.min_chunk_chars <= self.chunk_size:
            raise ValueError(
                "min_chunk_chars 必须在 1 到 chunk_size 之间"
            )

        if not 0 < self.boundary_search_ratio <= 1:
            raise ValueError(
                "boundary_search_ratio 必须在 0 到 1 之间"
            )

        if not self.separators:
            raise ValueError("separators 不能为空")

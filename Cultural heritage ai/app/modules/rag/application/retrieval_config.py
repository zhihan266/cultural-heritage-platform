"""
稠密检索器的结果数量配置。

定义不可变的 TopK 参数，并在创建时校验其必须为正数，
供 DenseRetriever 使用默认检索数量或接受组合入口注入的配置。
"""

from dataclasses import dataclass

@dataclass(frozen=True)
class RetrievalConfig:
    """
    Dense Retriever 的基础配置
    返回最相关的top_k个值
    """

    top_k: int = 5

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError(
                "top_k 必须大于 0"
            )

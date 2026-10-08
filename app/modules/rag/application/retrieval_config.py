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

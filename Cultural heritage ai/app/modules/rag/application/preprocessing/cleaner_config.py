"""
文档文本清理的参数定义与校验。

集中定义最小正文字符数和连续空行上限，
由 DocumentCleaner 读取，控制过滤与空白压缩行为。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CleanerConfig:
    """文档清理配置。"""

    # 正文至少需要包含多少个非空白字符。
    min_content_chars: int = 1

    # 最多允许连续出现多少个空白行。
    max_blank_lines: int = 1

    def __post_init__(self) -> None:
        if self.min_content_chars < 1:
            raise ValueError(
                "min_content_chars 必须大于等于 1"
            )

        if self.max_blank_lines < 0:
            raise ValueError(
                "max_blank_lines 不能小于 0"
            )

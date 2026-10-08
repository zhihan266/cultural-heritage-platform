"""Infrastructure connection settings are supplied only by composition."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MilvusConfig:
    uri: str
    collection_name: str
    dimension: int
    metric_type: str
    token: str = ""

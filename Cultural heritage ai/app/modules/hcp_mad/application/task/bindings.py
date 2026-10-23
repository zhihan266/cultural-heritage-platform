"""HCP-MAD 发言和终局的不可变绑定值，不包含调度或数据库操作。"""

import re
from dataclasses import dataclass

@dataclass(frozen=True)
class DebateTurnBinding:
    """调用者读取服务端预约后提供的绑定；不是开启工作流或授予终止权限的凭证。"""

    task_id: str
    debate_id: str
    ai_id: str
    round_no: int
    turn_no: int
    revision: int
    owner_token: str
    generation: int

    def __post_init__(self) -> None:
        if (not isinstance(self.debate_id, str) or not re.fullmatch(r"[a-z0-9_-]{1,32}", self.debate_id)
                or any(not isinstance(value, str) or not value.strip() for value in
                       (self.task_id, self.ai_id, self.owner_token))
                or any(type(value) is not int or value < minimum for value, minimum in
                       ((self.round_no, 1), (self.turn_no, 1), (self.revision, 0), (self.generation, 1)))
                or len(self.stage) > 64):
            raise ValueError("Invalid debate turn binding")

    @property
    def stage(self) -> str:
        return f"debate_{self.debate_id}_t{self.turn_no}"


@dataclass(frozen=True)
class AgentFinalizationBinding:
    task_id: str
    debate_id: str
    final_key: str
    ai_id: str
    policy_version: str
    schema_version: int
    revision: int
    last_turn_no: int
    source_execution_id: str
    source_item_id: str

    def __post_init__(self) -> None:
        if (any(not isinstance(value, str) or not value.strip() for value in (
            self.task_id, self.debate_id, self.final_key, self.ai_id, self.policy_version,
            self.source_execution_id, self.source_item_id))
                or not re.fullmatch(r"[a-f0-9]{64}", self.final_key)
                or any(type(value) is not int or value < minimum for value, minimum in (
                    (self.schema_version, 1), (self.revision, 0), (self.last_turn_no, 1)))):
            raise ValueError("Invalid finalization binding")


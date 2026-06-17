"""旧版内存存储实现。

当前应用已经使用 MySQL，此模块仅保留作原型参考，不应在新业务代码中使用。
内存数据会在进程退出时丢失。
"""

from app.models.domain import Account, AiAgent, Role, Task


class MemoryStore:
    """使用 Python 字典模拟数据库的早期原型。"""

    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}
        self.ai_agents: dict[str, AiAgent] = {}
        self.tasks: dict[str, Task] = {}

    def get_or_create_account(self, username: str, role: Role) -> Account:
        for account in self.accounts.values():
            if account.username == username and account.role == role:
                return account

        account_id = f"{role.value}-{len(self.accounts) + 1}"
        account = Account(id=account_id, username=username, role=role)
        self.accounts[account.id] = account
        return account


store = MemoryStore()

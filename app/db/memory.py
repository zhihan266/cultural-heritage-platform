from app.models.domain import Account, AiAgent, Role, Task


class MemoryStore:
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

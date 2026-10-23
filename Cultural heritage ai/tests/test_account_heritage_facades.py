"""账号与遗产 Facade 的委托、公开模型和短会话边界。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from app.modules.account import repository as account_repository, service
from app.modules.account.application.facade import AccountFacade
from app.modules.account.models import Account
from app.modules.heritage import repository as heritage_repository
from app.modules.heritage.application import facade as heritage_module
from app.modules.heritage.application.facade import HeritageFacade
from app.modules.heritage.domain import HeritageSite


def test_authentication_methods_delegate_dynamically_to_original_services(monkeypatch):
    facade = AccountFacade()
    account = Account(id="account", username="test", score=1)
    register = Mock(return_value=account)
    login = Mock(return_value=(account, "token"))
    logout = Mock(return_value={"msg": "logout success"})
    monkeypatch.setattr(service, "register_service", register)
    monkeypatch.setattr(service, "login_service", login)
    monkeypatch.setattr(service, "logout_service", logout)

    assert facade.register("test", "password") is account
    assert facade.login("test", "password") == (account, "token")
    assert facade.logout("token") == {"msg": "logout success"}
    register.assert_called_once_with("test", "password")
    login.assert_called_once_with("test", "password")
    logout.assert_called_once_with("token")


@pytest.mark.parametrize("exists", [True, False])
def test_public_account_never_exposes_password_or_adds_missing_account_policy(monkeypatch, exists):
    row = SimpleNamespace(id="account", username="test", score=2.0, password_hash="private-hash")
    lookup = Mock(return_value=row if exists else None)
    monkeypatch.setattr(account_repository, "get_account_by_id", lookup)
    result = AccountFacade().get_public_account("account")
    lookup.assert_called_once_with("account")
    if exists:
        assert isinstance(result, Account)
        assert result.model_dump() == {"id": "account", "username": "test", "score": 2.0}
        assert not hasattr(result, "_sa_instance_state")
    else:
        assert result is None


@pytest.mark.parametrize("explicit_factory", [False, True])
def test_ranked_accounts_uses_requested_short_session_and_domain_conversion(monkeypatch, explicit_factory):
    facade = AccountFacade()
    context = MagicMock()
    db = context.__enter__.return_value
    factory = Mock(return_value=context)
    default_factory = Mock(side_effect=AssertionError("Explicit factory must be preserved"))
    monkeypatch.setattr(
        account_repository, "SessionLocal", default_factory if explicit_factory else factory,
    )
    rows = [
        SimpleNamespace(id="best", username="first", score=3.0, password_hash="private"),
        SimpleNamespace(id="next", username="second", score=1.0, password_hash="private"),
    ]
    lookup = Mock(return_value=rows)
    monkeypatch.setattr(account_repository, "list_account_rows_by_score", lookup)
    result = facade.ranked_accounts(**({"session_factory": factory} if explicit_factory else {}))
    assert [account.id for account in result] == ["best", "next"]
    assert all(isinstance(account, Account) for account in result)
    assert all(not hasattr(account, "password_hash") for account in result)
    factory.assert_called_once_with()
    lookup.assert_called_once_with(db)
    context.__exit__.assert_called_once_with(None, None, None)
    db.commit.assert_not_called()
    if explicit_factory:
        default_factory.assert_not_called()


@pytest.mark.parametrize("explicit_factory", [False, True])
def test_heritage_lookup_keeps_original_query_input_and_injected_session(monkeypatch, explicit_factory):
    facade = HeritageFacade()
    context = MagicMock()
    db = context.__enter__.return_value
    factory = Mock(return_value=context)
    default_factory = Mock(side_effect=AssertionError("Explicit factory must be preserved"))
    monkeypatch.setattr(
        heritage_module.db_session, "SessionLocal", default_factory if explicit_factory else factory,
    )
    site = HeritageSite(
        id="site", name="故宫", aliases=[], city="北京", province="北京",
        category="建筑", dynasty="明", built_year=None, description="结构化详情",
    )
    lookup = Mock(return_value=site)
    monkeypatch.setattr(heritage_repository, "find_by_name", lookup)
    result = facade.find_detail_by_name(
        " 故宫 ", **({"session_factory": factory} if explicit_factory else {}),
    )
    assert result is site
    lookup.assert_called_once_with(db, " 故宫 ")
    factory.assert_called_once_with()
    context.__exit__.assert_called_once_with(None, None, None)
    db.commit.assert_not_called()
    if explicit_factory:
        default_factory.assert_not_called()


@pytest.mark.parametrize("outcome", [None, RuntimeError("query unavailable")])
def test_heritage_missing_and_error_results_are_preserved_and_session_is_closed(monkeypatch, outcome):
    context = MagicMock()
    context.__exit__.return_value = False
    factory = Mock(return_value=context)
    lookup = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=None)
    monkeypatch.setattr(heritage_repository, "find_by_name", lookup)
    if isinstance(outcome, Exception):
        with pytest.raises(RuntimeError) as raised:
            HeritageFacade().find_detail_by_name("missing", session_factory=factory)
        assert raised.value is outcome
    else:
        assert HeritageFacade().find_detail_by_name("missing", session_factory=factory) is None
    context.__exit__.assert_called_once()

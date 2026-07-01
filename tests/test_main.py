import logging
import os
import sys
import types

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import main  # noqa: E402


@pytest.fixture(autouse=True)
def set_required_env(monkeypatch):
    monkeypatch.setenv("YNAB_ACCESS_TOKEN", "token")
    monkeypatch.setenv("YNAB_PLAN_NAME", "My Plan")
    monkeypatch.setenv("YNAB_SHARED_ACCOUNT_NAME", "shared")
    monkeypatch.setenv("YNAB_IOU_ACCOUNT_NAME", "iou")
    monkeypatch.setenv("YNAB_IOU_PERCENTAGE", "50")
    monkeypatch.setenv("YNAB_LOOKBACK_DAYS", "30")


class DummyApiException(Exception):
    def __init__(self, status):
        super().__init__(f"status={status}")
        self.status = status


class FakeTransaction(types.SimpleNamespace):
    pass


class FakeYNABClient:
    def __init__(self, *args, **kwargs):
        self.plan_id = "plan-1"
        self.shared_account_id = "shared-1"
        self.iou_account_id = "iou-1"
        self.transactions = []
        self.created_transactions = []
        self.updated_transactions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_plan_id_from_name(self, plan_name):
        return self.plan_id

    def get_account_id_from_name(self, plan_id, account_name):
        if account_name == "shared":
            return self.shared_account_id
        if account_name == "iou":
            return self.iou_account_id
        raise AssertionError(f"Unexpected account name: {account_name}")

    def fetch_new_transactions(self, plan_id, account_id, since_date):
        return self.transactions

    def create_iou_transaction(
        self, plan_id, iou_account_id, iou_percentage, transactions
    ):
        self.created_transactions.append(
            (plan_id, iou_account_id, iou_percentage, transactions)
        )

    def update_transactions_flag(self, plan_id, transactions):
        self.updated_transactions.append((plan_id, transactions))


class FailingClient(FakeYNABClient):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("boom")


def test_call_with_retries_retries_on_rate_limit(monkeypatch):
    client = main.YNABClient.__new__(main.YNABClient)
    monkeypatch.setattr(main.ynab, "ApiException", DummyApiException)

    sleep_calls = []
    monkeypatch.setattr(main.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    responses = iter([DummyApiException(429), DummyApiException(429), "ok"])

    def flaky_call():
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    assert client._call_with_retries(flaky_call, max_retries=3, delay_seconds=1) == "ok"
    assert sleep_calls == [1, 1]


def test_call_with_retries_returns_none_after_exhaustion(monkeypatch, caplog):
    client = main.YNABClient.__new__(main.YNABClient)
    monkeypatch.setattr(main.ynab, "ApiException", DummyApiException)
    monkeypatch.setattr(main.time, "sleep", lambda seconds: None)

    def always_rate_limited():
        raise DummyApiException(429)

    with caplog.at_level(logging.ERROR):
        assert (
            client._call_with_retries(
                always_rate_limited, max_retries=3, delay_seconds=1
            )
            is None
        )

    assert "Max retries (3) exhausted due to rate limiting" in caplog.text


def test_main_processes_transactions(monkeypatch):
    fake_client = FakeYNABClient()
    fake_client.transactions = [
        FakeTransaction(
            approved=True,
            category_id="cat-1",
            flag_color=None,
            transfer_account_id=None,
            var_date="2026-01-01",
            amount=1000,
            payee_name="Groceries",
        ),
        FakeTransaction(
            approved=True,
            category_id="cat-2",
            flag_color="green",
            transfer_account_id=None,
            var_date="2026-01-02",
            amount=2000,
            payee_name="Coffee",
        ),
    ]

    monkeypatch.setattr(main, "YNABClient", lambda *args, **kwargs: fake_client)

    main.main()

    assert len(fake_client.created_transactions) == 1
    assert len(fake_client.updated_transactions) == 1
    assert fake_client.created_transactions[0][0] == "plan-1"
    assert fake_client.created_transactions[0][1] == "iou-1"
    assert fake_client.created_transactions[0][2] == 50
    assert fake_client.created_transactions[0][3][0].payee_name == "Groceries"


def test_main_skips_processing_when_no_transactions_are_valid(monkeypatch):
    fake_client = FakeYNABClient()
    fake_client.transactions = [
        FakeTransaction(
            approved=False,
            category_id="cat-1",
            flag_color=None,
            transfer_account_id=None,
            var_date="2026-01-01",
            amount=1000,
            payee_name="Groceries",
        )
    ]

    monkeypatch.setattr(main, "YNABClient", lambda *args, **kwargs: fake_client)

    main.main()

    assert fake_client.created_transactions == []
    assert fake_client.updated_transactions == []


def test_main_exits_cleanly_when_client_init_fails(monkeypatch, caplog):
    monkeypatch.setattr(main, "YNABClient", FailingClient)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as excinfo:
            main.main()

    assert excinfo.value.code == 0
    assert "Startup/setup failed" in caplog.text

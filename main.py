from datetime import timedelta
from datetime import date
from typing import List
import os
import sys
import time
import logging
import ynab

# Configure logging from environment
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
try:
    LOG_LEVEL_NUM = getattr(logging, LOG_LEVEL)
except Exception:
    LOG_LEVEL_NUM = logging.INFO
logging.basicConfig(
    level=LOG_LEVEL_NUM,
    format="%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class YNABClient:
    """
    A wrapper around the YNAB API client to manage authorization,
    connections, and common API actions.
    """

    def __init__(self, access_token: str = None):
        token = access_token or os.getenv("YNAB_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("YNAB_ACCESS_TOKEN environment variable is not set.")
        self.configuration = ynab.Configuration(access_token=token)
        self.api_client = ynab.ApiClient(self.configuration)

    def __enter__(self):
        self.api_client.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.api_client.__exit__(exc_type, exc_val, exc_tb)

    def _call_with_retries(
        self, func, *args, max_retries: int = 3, delay_seconds: int = 60, **kwargs
    ):
        for attempt in range(1, max_retries + 1):
            try:
                return func(*args, **kwargs)
            except ynab.ApiException as e:
                status = getattr(e, "status", None)
                if status == 429:
                    logger.warning(
                        "Rate limited, attempt %d/%d, sleeping %ds",
                        attempt,
                        max_retries,
                        delay_seconds,
                    )
                    time.sleep(delay_seconds)
                    continue
                logger.error("Unrecoverable YNAB API error: %s", e)
                return None
        logger.error("Max retries (%d) exhausted due to rate limiting", max_retries)
        return None

    def list_plans(self):
        plans_api = ynab.PlansApi(self.api_client)
        plans_response = self._call_with_retries(plans_api.get_plans)
        if plans_response is None:
            logger.error("Failed to list plans due to API errors")
            return []
        return plans_response.data.plans

    def get_plan_id_from_name(self, plan_name: str) -> str:
        plans = self.list_plans()
        for plan in plans:
            if plan.name == plan_name:
                return str(plan.id)
        raise ValueError(
            f"Plan with name '{plan_name}' not found. "
            f"Available plans: {[plan.name for plan in plans]}"
        )

    def list_accounts(self, plan_id: str):
        accounts_api = ynab.AccountsApi(self.api_client)
        accounts_response = self._call_with_retries(
            accounts_api.get_accounts, str(plan_id)
        )
        if accounts_response is None:
            logger.error("Failed to list accounts for plan %s", plan_id)
            return []
        return accounts_response.data.accounts

    def get_account_id_from_name(self, plan_id: str, account_name: str) -> str:
        accounts = self.list_accounts(plan_id)
        for account in accounts:
            if account.name == account_name:
                return str(account.id)
        raise ValueError(
            f"Account with name '{account_name}' not found. "
            f"Available accounts: '{[account.name for account in accounts]}'"
        )

    def fetch_new_transactions(
        self, plan_id: str, account_id: str, since_date: date = None
    ) -> List[ynab.Transaction]:
        transactions_api = ynab.TransactionsApi(self.api_client)
        response = self._call_with_retries(
            transactions_api.get_transactions_by_account,
            plan_id=str(plan_id),
            account_id=str(account_id),
            since_date=since_date.isoformat() if since_date else None,
        )
        if response is None:
            logger.error("Failed to fetch transactions for account %s", account_id)
            return []
        logger.info("Retrieved %d transactions", len(response.data.transactions))
        return response.data.transactions

    def create_iou_transaction(
        self,
        plan_id: str,
        iou_account_id: str,
        iou_percentage: int,
        transactions: List[ynab.TransactionDetail],
    ):
        transactions_api = ynab.TransactionsApi(self.api_client)

        # Calculate subtransactions first so we can sum their exact integer amounts
        # to avoid mismatch errors when sending to the YNAB API (due to float rounding).
        subtransactions = []
        for t in transactions:
            # Multiply by -1 because an outflow (negative) in the shared account
            # should become an inflow (positive IOU) in the IOU account, and vice versa.
            sub_amount = int(t.amount * (iou_percentage / 100.0) * -1)
            subtransactions.append(
                ynab.SaveSubTransaction(
                    amount=sub_amount,
                    category_id=t.category_id,
                    payee_name=t.payee_name,
                )
            )

        total_amount = sum(sub.amount for sub in subtransactions)

        # Max date string works since they are ISO format (YYYY-MM-DD)
        max_date = max([t.var_date for t in transactions])

        new_tx = ynab.NewTransaction(
            account_id=str(iou_account_id),
            var_date=max_date,
            amount=total_amount,
            payee_name="Shared Costs",
            approved=True,
            subtransactions=subtransactions,
        )

        wrapper = ynab.PostTransactionsWrapper(transaction=new_tx)

        response = self._call_with_retries(
            transactions_api.create_transaction, plan_id=str(plan_id), data=wrapper
        )
        if response is None:
            logger.error("Failed to create IOU transaction for plan %s", plan_id)
            return None
        logger.info("Successfully created split transaction.")
        return response

    def update_transactions_flag(
        self, plan_id: str, transactions: List[ynab.TransactionDetail]
    ):
        transactions_api = ynab.TransactionsApi(self.api_client)

        # The PatchTransactionsWrapper expects a list of SaveTransactionWithIdOrImportId objects.
        # We construct them by extracting the relevant properties from the TransactionDetail objects we fetched.
        updates = []
        for t in transactions:
            if t.flag_color is None:
                updates.append(
                    ynab.SaveTransactionWithIdOrImportId(
                        id=t.id,
                        account_id=t.account_id,
                        var_date=t.var_date,
                        amount=t.amount,
                        flag_color=ynab.TransactionFlagColor("green"),
                    )
                )
        if not updates:
            logger.info("No transactions to update.")
            return None

        wrapper = ynab.PatchTransactionsWrapper(transactions=updates)
        response = self._call_with_retries(
            transactions_api.update_transactions, str(plan_id), data=wrapper
        )
        if response is None:
            logger.error("Failed to update transactions for plan %s", plan_id)
            return None
        logger.info("Successfully updated %d transactions.", len(updates))
        return response


def main():
    plan_name = os.getenv("YNAB_PLAN_NAME")
    shared_account_name = os.getenv("YNAB_SHARED_ACCOUNT_NAME")
    iou_account_name = os.getenv("YNAB_IOU_ACCOUNT_NAME")
    iou_percentage = int(os.getenv("YNAB_IOU_PERCENTAGE"))

    try:
        with YNABClient() as client:
            try:
                plan_id = client.get_plan_id_from_name(plan_name)
                shared_account_id = client.get_account_id_from_name(
                    plan_id, shared_account_name
                )
                iou_account_id = client.get_account_id_from_name(
                    plan_id, iou_account_name
                )
            except Exception as e:
                logger.error("Startup/setup failed: %s", e)
                sys.exit(0)
            lookback_days = int(os.getenv("YNAB_LOOKBACK_DAYS", "30"))
            since_date = date.today() - timedelta(days=lookback_days)

            new_transactions = client.fetch_new_transactions(
                plan_id, shared_account_id, since_date
            )
            for t in new_transactions:
                logger.debug(
                    "%s  %.2f  %s  [%s]",
                    t.var_date,
                    t.amount / 1000.0,
                    t.payee_name,
                    t.flag_color,
                )

            # Filter for transactions that are approved, categorized, and not already processed (flagged).
            transactions_to_process = [
                t
                for t in new_transactions
                if t.approved
                and t.category_id is not None
                and t.flag_color is None
                and t.transfer_account_id is None
            ]

            if transactions_to_process:
                client.create_iou_transaction(
                    plan_id, iou_account_id, iou_percentage, transactions_to_process
                )
                client.update_transactions_flag(plan_id, transactions_to_process)
            else:
                logger.info("No valid, unprocessed transactions found.")
    except Exception as e:
        logger.error("Startup/setup failed: %s", e)
        sys.exit(0)


if __name__ == "__main__":
    main()

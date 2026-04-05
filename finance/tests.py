from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Account, AccountCategory, Transaction
from .report_orchestrator import orchestrate_ai_report


class AIReportOrchestrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='alice',
            password='testpass123',
        )

        self.revenue_category = AccountCategory.objects.create(name='Revenue')
        self.expense_category = AccountCategory.objects.create(name='Expenses')
        self.asset_category = AccountCategory.objects.create(name='Assets')
        self.liability_category = AccountCategory.objects.create(name='Liabilities')

        self.sales_account = Account.objects.create(name='Sales', category=self.revenue_category)
        self.marketing_account = Account.objects.create(name='Marketing', category=self.expense_category)
        self.cash_account = Account.objects.create(name='Cash', category=self.asset_category)
        self.loan_account = Account.objects.create(name='Loan', category=self.liability_category)

    def test_expense_summary_uses_this_month_window(self):
        today = timezone.localdate()
        last_month_date = today.replace(day=1) - timedelta(days=1)

        Transaction.objects.create(
            user=self.user,
            account=self.marketing_account,
            amount=Decimal('500.00'),
            type='expense',
            description='Ad campaign',
            date=today,
        )
        Transaction.objects.create(
            user=self.user,
            account=self.marketing_account,
            amount=Decimal('250.00'),
            type='expense',
            description='Design work',
            date=today,
        )
        Transaction.objects.create(
            user=self.user,
            account=self.marketing_account,
            amount=Decimal('900.00'),
            type='expense',
            description='Old vendor bill',
            date=last_month_date,
        )

        payload = orchestrate_ai_report("What were my highest expenses this month?", self.user)

        self.assertEqual(payload['intent'], 'expense_summary')
        self.assertEqual(payload['period']['label'], 'This Month')
        self.assertEqual(payload['report_data']['transaction_count'], 2)
        self.assertEqual(payload['report_data']['total_amount'], Decimal('750.00'))
        self.assertEqual(payload['report_data']['top_transactions'][0]['description'], 'Ad campaign')

    def test_balance_sheet_returns_aggregated_snapshot(self):
        today = timezone.localdate()

        Transaction.objects.create(
            user=self.user,
            account=self.cash_account,
            amount=Decimal('5000.00'),
            type='asset',
            description='Cash in bank',
            date=today,
        )
        Transaction.objects.create(
            user=self.user,
            account=self.loan_account,
            amount=Decimal('1200.00'),
            type='liability',
            description='Business loan',
            date=today,
        )
        Transaction.objects.create(
            user=self.user,
            account=self.sales_account,
            amount=Decimal('3200.00'),
            type='income',
            description='Client payment',
            date=today,
        )
        Transaction.objects.create(
            user=self.user,
            account=self.marketing_account,
            amount=Decimal('700.00'),
            type='expense',
            description='Promotion spend',
            date=today,
        )

        payload = orchestrate_ai_report("Generate a balance sheet for me", self.user)

        self.assertEqual(payload['intent'], 'balance_sheet')
        self.assertEqual(payload['report_data']['total_assets'], Decimal('5000.00'))
        self.assertEqual(payload['report_data']['total_liabilities'], Decimal('1200.00'))
        self.assertEqual(payload['report_data']['retained_earnings'], Decimal('2500.00'))
        self.assertEqual(payload['report_data']['net_equity'], Decimal('3800.00'))

    @patch('finance.views.generate_ai_report', return_value='<p>Mock report</p>')
    def test_ai_report_view_uses_orchestrated_payload(self, mocked_generate_ai_report):
        today = timezone.localdate()
        Transaction.objects.create(
            user=self.user,
            account=self.sales_account,
            amount=Decimal('1000.00'),
            type='income',
            description='Invoice paid',
            date=today,
        )

        self.client.login(username='alice', password='testpass123')
        response = self.client.post(
            reverse('finance:ai_report'),
            {'question': 'Show my income summary'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mock report')
        mocked_generate_ai_report.assert_called_once()

        _, payload = mocked_generate_ai_report.call_args[0]
        self.assertEqual(payload['intent'], 'income_summary')
        self.assertEqual(payload['report_data']['transaction_count'], 1)


class AITransactionReviewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='bob',
            password='testpass123',
        )
        self.other_user = get_user_model().objects.create_user(
            username='charlie',
            password='testpass123',
        )
        self.client.login(username='bob', password='testpass123')
        self.expense_category = AccountCategory.objects.create(name='Expenses')
        self.utilities_account = Account.objects.create(name='Electricity', category=self.expense_category)

    @patch('finance.views.parse_transaction_with_gemini')
    def test_parse_step_shows_editable_draft_without_saving(self, mocked_parse):
        mocked_parse.return_value = {
            'type': 'expense',
            'category_name': 'utilities',
            'account_name': 'electricity',
            'amount': 5000,
            'description': 'Electricity bill payment',
        }

        response = self.client.post(
            reverse('finance:add_transaction'),
            {
                'action': 'parse',
                'description': 'Paid Rs 5000 for electricity bill',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Review Before Saving')
        self.assertContains(response, 'Electricity bill payment')
        self.assertEqual(Transaction.objects.count(), 0)

    def test_save_step_uses_edited_values(self):
        response = self.client.post(
            reverse('finance:add_transaction'),
            {
                'action': 'save',
                'original_description': 'Paid Rs 5000 for electricity bill',
                'description': 'Electricity bill for March',
                'type': 'expense',
                'category_name': 'Expenses',
                'account_name': 'Electricity',
                'amount': '4800.50',
                'date': timezone.localdate().isoformat(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Successfully recorded Expense')
        self.assertEqual(Transaction.objects.count(), 1)

        transaction = Transaction.objects.get()
        self.assertEqual(transaction.description, 'Electricity bill for March')
        self.assertEqual(transaction.amount, Decimal('4800.50'))
        self.assertEqual(transaction.type, 'expense')
        self.assertEqual(transaction.account.name, 'Electricity')
        self.assertEqual(transaction.account.category.name, 'Expenses')

    def test_edit_transaction_page_loads_existing_values(self):
        transaction = Transaction.objects.create(
            user=self.user,
            account=self.utilities_account,
            amount=Decimal('2500.00'),
            type='expense',
            description='Old electricity bill',
            date=timezone.localdate(),
        )

        response = self.client.get(reverse('finance:edit_transaction', args=[transaction.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Edit Transaction')
        self.assertContains(response, 'Old electricity bill')
        self.assertContains(response, '2500.00')

    def test_edit_transaction_updates_recorded_transaction(self):
        transaction = Transaction.objects.create(
            user=self.user,
            account=self.utilities_account,
            amount=Decimal('2500.00'),
            type='expense',
            description='Old electricity bill',
            date=timezone.localdate(),
        )

        response = self.client.post(
            reverse('finance:edit_transaction', args=[transaction.id]),
            {
                'description': 'Electricity bill updated',
                'type': 'expense',
                'category_name': 'Bills',
                'account_name': 'Power',
                'amount': '2750.00',
                'date': timezone.localdate().isoformat(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Updated Expense')

        transaction.refresh_from_db()
        self.assertEqual(transaction.description, 'Electricity bill updated')
        self.assertEqual(transaction.amount, Decimal('2750.00'))
        self.assertEqual(transaction.account.name, 'Power')
        self.assertEqual(transaction.account.category.name, 'Bills')

    def test_edit_transaction_is_limited_to_owner(self):
        transaction = Transaction.objects.create(
            user=self.other_user,
            account=self.utilities_account,
            amount=Decimal('2500.00'),
            type='expense',
            description='Private bill',
            date=timezone.localdate(),
        )

        response = self.client.get(reverse('finance:edit_transaction', args=[transaction.id]))

        self.assertEqual(response.status_code, 404)

    def test_all_transactions_page_shows_full_history_for_current_user(self):
        Transaction.objects.create(
            user=self.user,
            account=self.utilities_account,
            amount=Decimal('100.00'),
            type='expense',
            description='First bill',
            date=timezone.localdate(),
        )
        Transaction.objects.create(
            user=self.user,
            account=self.utilities_account,
            amount=Decimal('200.00'),
            type='expense',
            description='Second bill',
            date=timezone.localdate(),
        )
        Transaction.objects.create(
            user=self.other_user,
            account=self.utilities_account,
            amount=Decimal('300.00'),
            type='expense',
            description='Other user bill',
            date=timezone.localdate(),
        )

        response = self.client.get(reverse('finance:all_transactions'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'All Transactions')
        self.assertContains(response, 'First bill')
        self.assertContains(response, 'Second bill')
        self.assertNotContains(response, 'Other user bill')

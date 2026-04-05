import calendar
import re
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import Transaction


REPORT_LABELS = {
    'profit_loss': 'Profit & Loss',
    'balance_sheet': 'Balance Sheet',
    'cash_flow': 'Cash Flow',
    'expense_summary': 'Expense Summary',
    'income_summary': 'Income Summary',
    'general_overview': 'Financial Overview',
}


def orchestrate_ai_report(question, user):
    """
    Determine the report intent, apply a date window, and fetch the most
    relevant aggregates so the LLM receives focused financial context.
    """
    cleaned_question = (question or '').strip()
    intent = detect_report_intent(cleaned_question)
    period = detect_reporting_period(cleaned_question)

    transactions = (
        Transaction.objects.filter(user=user)
        .select_related('account', 'account__category')
        .order_by('-date', '-created_at')
    )

    if period['start'] and period['end']:
        transactions = transactions.filter(date__range=(period['start'], period['end']))

    dataset = build_dataset(transactions, intent)

    return {
        'question': cleaned_question,
        'intent': intent,
        'report_label': REPORT_LABELS[intent],
        'period': period,
        'report_data': dataset,
    }


def detect_report_intent(question):
    lowered = question.lower()

    if any(term in lowered for term in ('balance sheet', 'net worth', 'equity', 'liabilities', 'assets')):
        return 'balance_sheet'

    if any(term in lowered for term in ('cash flow', 'cashflow', 'inflow', 'outflow', 'burn rate')):
        return 'cash_flow'

    if any(term in lowered for term in ('profit and loss', 'profit & loss', 'p&l', 'income statement')):
        return 'profit_loss'

    expense_terms = ('expense', 'expenses', 'spend', 'spent', 'cost', 'costs')
    income_terms = ('income', 'revenue', 'sales', 'earnings', 'earned')

    if any(term in lowered for term in expense_terms):
        return 'expense_summary'

    if any(term in lowered for term in income_terms):
        return 'income_summary'

    return 'general_overview'


def detect_reporting_period(question):
    lowered = question.lower()
    today = timezone.localdate()

    if 'today' in lowered:
        return _period_payload('Today', today, today)

    if 'yesterday' in lowered:
        yesterday = today - timedelta(days=1)
        return _period_payload('Yesterday', yesterday, yesterday)

    if 'this week' in lowered:
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return _period_payload('This Week', start, end)

    if 'last week' in lowered:
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return _period_payload('Last Week', start, end)

    if 'this month' in lowered:
        start = today.replace(day=1)
        end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return _period_payload('This Month', start, end)

    if 'last month' in lowered:
        last_month_end = today.replace(day=1) - timedelta(days=1)
        start = last_month_end.replace(day=1)
        return _period_payload('Last Month', start, last_month_end)

    if 'this year' in lowered:
        start = today.replace(month=1, day=1)
        end = today.replace(month=12, day=31)
        return _period_payload('This Year', start, end)

    if 'last year' in lowered:
        start = today.replace(year=today.year - 1, month=1, day=1)
        end = today.replace(year=today.year - 1, month=12, day=31)
        return _period_payload('Last Year', start, end)

    month_match = re.search(
        r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b',
        lowered,
    )
    if month_match:
        month_number = list(calendar.month_name).index(month_match.group(1).title())
        year_match = re.search(r'\b(20\d{2})\b', lowered)
        year = int(year_match.group(1)) if year_match else today.year
        start = today.replace(year=year, month=month_number, day=1)
        end = start.replace(day=calendar.monthrange(year, month_number)[1])
        return _period_payload(start.strftime('%B %Y'), start, end)

    return _period_payload('All Time', None, None)


def _period_payload(label, start, end):
    return {
        'label': label,
        'start': start.isoformat() if start else None,
        'end': end.isoformat() if end else None,
    }


def build_dataset(transactions, intent):
    if intent == 'profit_loss':
        return build_profit_loss_dataset(transactions)

    if intent == 'balance_sheet':
        return build_balance_sheet_dataset(transactions)

    if intent == 'cash_flow':
        return build_cash_flow_dataset(transactions)

    if intent == 'expense_summary':
        return build_type_summary_dataset(transactions, 'expense')

    if intent == 'income_summary':
        return build_type_summary_dataset(transactions, 'income')

    return build_general_overview_dataset(transactions)


def build_profit_loss_dataset(transactions):
    total_income = aggregate_total(transactions, 'income')
    total_expenses = aggregate_total(transactions, 'expense')

    return {
        'transaction_count': transactions.count(),
        'total_income': total_income,
        'total_expenses': total_expenses,
        'net_profit': total_income - total_expenses,
        'income_breakdown': account_breakdown(transactions, 'income'),
        'expense_breakdown': account_breakdown(transactions, 'expense'),
        'top_expenses': top_transactions(transactions, 'expense'),
        'top_income': top_transactions(transactions, 'income'),
    }


def build_balance_sheet_dataset(transactions):
    total_assets = aggregate_total(transactions, 'asset')
    total_liabilities = aggregate_total(transactions, 'liability')
    retained_earnings = aggregate_total(transactions, 'income') - aggregate_total(transactions, 'expense')

    return {
        'transaction_count': transactions.count(),
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'retained_earnings': retained_earnings,
        'net_equity': total_assets - total_liabilities,
        'asset_breakdown': account_breakdown(transactions, 'asset'),
        'liability_breakdown': account_breakdown(transactions, 'liability'),
    }


def build_cash_flow_dataset(transactions):
    total_inflows = aggregate_total(transactions, 'income')
    total_outflows = aggregate_total(transactions, 'expense')
    monthly_rows = (
        transactions.annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(
            inflows=Sum('amount', filter=Q(type='income')),
            outflows=Sum('amount', filter=Q(type='expense')),
        )
        .order_by('-month')[:6]
    )

    monthly_breakdown = []
    for row in monthly_rows:
        inflows = row['inflows'] or Decimal('0')
        outflows = row['outflows'] or Decimal('0')
        monthly_breakdown.append(
            {
                'month': row['month'].strftime('%B %Y') if row['month'] else None,
                'inflows': inflows,
                'outflows': outflows,
                'net_flow': inflows - outflows,
            }
        )

    return {
        'transaction_count': transactions.count(),
        'total_inflows': total_inflows,
        'total_outflows': total_outflows,
        'net_cash_flow': total_inflows - total_outflows,
        'monthly_breakdown': monthly_breakdown,
        'recent_transactions': serialize_transactions(transactions[:10]),
    }


def build_type_summary_dataset(transactions, txn_type):
    filtered_transactions = transactions.filter(type=txn_type)
    total_amount = aggregate_total(filtered_transactions)

    return {
        'transaction_type': txn_type,
        'transaction_count': filtered_transactions.count(),
        'total_amount': total_amount,
        'breakdown_by_account': account_breakdown(filtered_transactions),
        'top_transactions': top_transactions(filtered_transactions),
        'recent_transactions': serialize_transactions(filtered_transactions[:10]),
    }


def build_general_overview_dataset(transactions):
    total_income = aggregate_total(transactions, 'income')
    total_expenses = aggregate_total(transactions, 'expense')
    total_assets = aggregate_total(transactions, 'asset')
    total_liabilities = aggregate_total(transactions, 'liability')

    return {
        'transaction_count': transactions.count(),
        'total_income': total_income,
        'total_expenses': total_expenses,
        'net_profit': total_income - total_expenses,
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'recent_transactions': serialize_transactions(transactions[:10]),
        'top_expenses': top_transactions(transactions, 'expense'),
        'top_income': top_transactions(transactions, 'income'),
        'monthly_cash_flow': build_cash_flow_dataset(transactions).get('monthly_breakdown', []),
    }


def aggregate_total(transactions, txn_type=None):
    queryset = transactions.filter(type=txn_type) if txn_type else transactions
    return queryset.aggregate(total=Sum('amount'))['total'] or Decimal('0')


def account_breakdown(transactions, txn_type=None):
    queryset = transactions.filter(type=txn_type) if txn_type else transactions
    rows = (
        queryset.values('account__name', 'account__category__name')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )

    return [
        {
            'account': row['account__name'] or 'Unassigned',
            'category': row['account__category__name'] or 'Uncategorized',
            'total': row['total'] or Decimal('0'),
        }
        for row in rows
    ]


def top_transactions(transactions, txn_type=None, limit=5):
    queryset = transactions.filter(type=txn_type) if txn_type else transactions
    return serialize_transactions(queryset.order_by('-amount', '-date', '-created_at')[:limit])


def serialize_transactions(transactions):
    return [
        {
            'date': txn.date.isoformat(),
            'description': txn.description,
            'amount': txn.amount,
            'type': txn.type,
            'account': txn.account.name if txn.account else 'Unassigned',
            'category': (
                txn.account.category.name
                if txn.account and txn.account.category
                else 'Uncategorized'
            ),
        }
        for txn in transactions
    ]

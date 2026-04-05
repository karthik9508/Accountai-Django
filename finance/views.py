from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Q
from django.db.models.functions import TruncMonth
from .ai_services import parse_transaction_with_gemini, generate_ai_report
from .models import Account, AccountCategory, Transaction, BusinessProfile, Invoice, InvoiceItem
from .report_orchestrator import orchestrate_ai_report
from .invoice_pdf_utils import generate_invoice_pdf


VALID_TRANSACTION_TYPES = {choice[0] for choice in Transaction.TRANSACTION_TYPES}


def get_recent_transactions(user, limit=10):
    return Transaction.objects.filter(user=user).order_by('-date', '-created_at')[:limit]


def get_all_transactions(user):
    return (
        Transaction.objects.filter(user=user)
        .select_related('account', 'account__category')
        .order_by('-date', '-created_at')
    )


def build_transaction_draft(parsed_data, fallback_description):
    amount = parsed_data.get('amount', '0.00')
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal('0.00')

    txn_type = str(parsed_data.get('type', 'expense')).lower()
    if txn_type not in VALID_TRANSACTION_TYPES:
        txn_type = 'expense'

    return {
        'description': parsed_data.get('description') or fallback_description,
        'type': txn_type,
        'category_name': (parsed_data.get('category_name') or 'Uncategorized').strip().title(),
        'account_name': (parsed_data.get('account_name') or 'General').strip().title(),
        'amount': f"{amount:.2f}",
        'date': timezone.localdate().isoformat(),
    }


def validate_transaction_draft(post_data):
    description = (post_data.get('description') or '').strip()
    txn_type = (post_data.get('type') or '').strip().lower()
    category_name = (post_data.get('category_name') or '').strip().title()
    account_name = (post_data.get('account_name') or '').strip().title()
    raw_amount = (post_data.get('amount') or '').strip()
    raw_date = (post_data.get('date') or '').strip()

    if not description:
        raise ValueError("Description is required.")
    if txn_type not in VALID_TRANSACTION_TYPES:
        raise ValueError("Select a valid transaction type.")
    if not category_name:
        raise ValueError("Category is required.")
    if not account_name:
        raise ValueError("Account name is required.")

    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid amount.")

    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    try:
        txn_date = date.fromisoformat(raw_date)
    except ValueError:
        raise ValueError("Enter a valid transaction date.")

    return {
        'description': description,
        'type': txn_type,
        'category_name': category_name,
        'account_name': account_name,
        'amount': amount,
        'date': txn_date,
    }


def build_draft_from_transaction(transaction):
    return {
        'description': transaction.description,
        'type': transaction.type,
        'category_name': (
            transaction.account.category.name
            if transaction.account and transaction.account.category
            else 'Uncategorized'
        ),
        'account_name': transaction.account.name if transaction.account else 'General',
        'amount': f"{transaction.amount:.2f}",
        'date': transaction.date.isoformat(),
    }


def save_transaction_from_draft(user, draft, instance=None):
    category, _ = AccountCategory.objects.get_or_create(name=draft['category_name'])
    account, _ = Account.objects.get_or_create(
        name=draft['account_name'],
        category=category,
    )

    transaction = instance or Transaction(user=user)
    transaction.user = user
    transaction.account = account
    transaction.amount = draft['amount']
    transaction.type = draft['type']
    transaction.description = draft['description']
    transaction.date = draft['date']
    transaction.save()
    return transaction

@login_required
def add_transaction_ai(request):
    """
    Parse a natural language transaction, let the user review it,
    and save only after confirmation.
    """
    context = {
        'transactions': get_recent_transactions(request.user),
        'draft': None,
        'original_description': '',
    }

    if request.method == 'POST':
        action = request.POST.get('action', 'parse')

        if action == 'save':
            try:
                draft = validate_transaction_draft(request.POST)
                transaction = save_transaction_from_draft(request.user, draft)
                profile, _ = BusinessProfile.objects.get_or_create(
                    user=request.user,
                    defaults={'company_name': request.user.username},
                )
                messages.success(
                    request,
                    f"Successfully recorded {transaction.type.title()}: {profile.currency}{transaction.amount} under {transaction.account.name}.",
                )
                return redirect('finance:add_transaction')
            except Exception as e:
                context['draft'] = {
                    'description': request.POST.get('description', ''),
                    'type': request.POST.get('type', 'expense'),
                    'category_name': request.POST.get('category_name', ''),
                    'account_name': request.POST.get('account_name', ''),
                    'amount': request.POST.get('amount', ''),
                    'date': request.POST.get('date', ''),
                }
                context['original_description'] = request.POST.get('original_description', '')
                messages.error(request, f"Unable to save transaction: {str(e)}")
                return render(request, 'finance/add_transaction.html', context)

        nlp_text = (request.POST.get('description') or '').strip()
        context['original_description'] = nlp_text

        if not nlp_text:
            messages.error(request, "Please provide a transaction description.")
            return render(request, 'finance/add_transaction.html', context)

        try:
            parsed_data = parse_transaction_with_gemini(nlp_text)
            if not parsed_data:
                messages.error(request, "Failed to parse the transaction using AI. Provide a clearer description.")
                return render(request, 'finance/add_transaction.html', context)

            context['draft'] = build_transaction_draft(parsed_data, nlp_text)
            messages.success(request, "Review the AI-detected transaction details below, edit anything you want, then save.")
        except Exception as e:
            messages.error(request, f"An error occurred: {str(e)}")

        return render(request, 'finance/add_transaction.html', context)

    return render(request, 'finance/add_transaction.html', context)


@login_required
def edit_transaction(request, transaction_id):
    transaction = get_object_or_404(
        Transaction.objects.select_related('account', 'account__category'),
        id=transaction_id,
        user=request.user,
    )

    if request.method == 'POST':
        try:
            draft = validate_transaction_draft(request.POST)
            transaction = save_transaction_from_draft(request.user, draft, instance=transaction)
            profile, _ = BusinessProfile.objects.get_or_create(
                user=request.user,
                defaults={'company_name': request.user.username},
            )
            messages.success(
                request,
                f"Updated {transaction.type.title()}: {profile.currency}{transaction.amount} under {transaction.account.name}.",
            )
            return redirect('finance:add_transaction')
        except Exception as e:
            messages.error(request, f"Unable to update transaction: {str(e)}")
            draft = {
                'description': request.POST.get('description', ''),
                'type': request.POST.get('type', 'expense'),
                'category_name': request.POST.get('category_name', ''),
                'account_name': request.POST.get('account_name', ''),
                'amount': request.POST.get('amount', ''),
                'date': request.POST.get('date', ''),
            }
    else:
        draft = build_draft_from_transaction(transaction)

    return render(
        request,
        'finance/edit_transaction.html',
        {
            'transaction': transaction,
            'draft': draft,
        },
    )


@login_required
def all_transactions(request):
    transactions = get_all_transactions(request.user)
    totals = {
        'transaction_count': transactions.count(),
        'total_income': transactions.filter(type='income').aggregate(total=Sum('amount'))['total'] or Decimal('0'),
        'total_expenses': transactions.filter(type='expense').aggregate(total=Sum('amount'))['total'] or Decimal('0'),
    }
    totals['net_balance'] = totals['total_income'] - totals['total_expenses']

    return render(
        request,
        'finance/all_transactions.html',
        {
            'transactions': transactions,
            'totals': totals,
        },
    )


@login_required
def profit_loss(request):
    """Profit & Loss report — Income vs Expenses."""
    user_txns = Transaction.objects.filter(user=request.user)

    # Aggregate totals
    total_income = user_txns.filter(type='income').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_expenses = user_txns.filter(type='expense').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    net_profit = total_income - total_expenses

    # Breakdown by account
    income_breakdown = (
        user_txns.filter(type='income')
        .values('account__name')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )
    expense_breakdown = (
        user_txns.filter(type='expense')
        .values('account__name')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )

    context = {
        'total_income': total_income,
        'total_expenses': total_expenses,
        'net_profit': net_profit,
        'income_breakdown': income_breakdown,
        'expense_breakdown': expense_breakdown,
    }
    return render(request, 'finance/profit_loss.html', context)


@login_required
def balance_sheet(request):
    """Balance Sheet — Assets vs Liabilities."""
    user_txns = Transaction.objects.filter(user=request.user)

    total_assets = user_txns.filter(type='asset').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_liabilities = user_txns.filter(type='liability').aggregate(total=Sum('amount'))['total'] or Decimal('0')

    total_income = user_txns.filter(type='income').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_expenses = user_txns.filter(type='expense').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    retained_earnings = total_income - total_expenses

    net_equity = total_assets - total_liabilities

    asset_breakdown = (
        user_txns.filter(type='asset')
        .values('account__name')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )
    liability_breakdown = (
        user_txns.filter(type='liability')
        .values('account__name')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )

    context = {
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'retained_earnings': retained_earnings,
        'net_equity': net_equity,
        'asset_breakdown': asset_breakdown,
        'liability_breakdown': liability_breakdown,
    }
    return render(request, 'finance/balance_sheet.html', context)


@login_required
def cash_flow(request):
    """Cash Flow — all transactions ordered by date."""
    user_txns = Transaction.objects.filter(user=request.user)

    total_inflows = user_txns.filter(type='income').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_outflows = user_txns.filter(type='expense').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    net_flow = total_inflows - total_outflows

    # Monthly breakdown
    monthly_data = (
        user_txns
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(
            inflows=Sum('amount', filter=Q(type='income')),
            outflows=Sum('amount', filter=Q(type='expense')),
        )
        .order_by('-month')
    )

    all_transactions = user_txns.order_by('-date', '-created_at')

    context = {
        'total_inflows': total_inflows,
        'total_outflows': total_outflows,
        'net_flow': net_flow,
        'monthly_data': monthly_data,
        'all_transactions': all_transactions,
    }
    return render(request, 'finance/cash_flow.html', context)

@login_required
def ai_report(request):
    """View bridging natural language queries to AI Report generation."""
    ai_response = None
    user_question = None
    orchestration = None

    if request.method == 'POST':
        user_question = request.POST.get('question')
        if user_question:
            try:
                orchestration = orchestrate_ai_report(user_question, request.user)
                ai_response = generate_ai_report(user_question, orchestration)
            except Exception as e:
                messages.error(request, f"Unable to generate AI report: {str(e)}")
        else:
            messages.error(request, "Please enter a question for the AI.")
            
    return render(request, 'finance/ai_report.html', {
        'ai_response': ai_response,
        'user_question': user_question,
        'orchestration': orchestration,
    })


# ── Auto-create Transaction from Invoice ───────────────────────────

def auto_create_transaction_for_invoice(invoice):
    """
    When an invoice is marked as 'paid', automatically create a linked
    income transaction — unless one already exists.
    """
    if invoice.status != 'paid':
        return None

    # Don't create a duplicate if already linked
    if invoice.transaction:
        return invoice.transaction

    # Get-or-create category & account for invoice income
    category, _ = AccountCategory.objects.get_or_create(name='Revenue')
    account, _ = Account.objects.get_or_create(
        name='Invoice Income',
        category=category,
    )

    transaction = Transaction.objects.create(
        user=invoice.user,
        account=account,
        amount=invoice.total,
        type='income',
        description=f'Invoice {invoice.invoice_number} — {invoice.client_name}',
        date=invoice.issue_date,
    )

    # Link invoice back to the transaction
    invoice.transaction = transaction
    invoice.save(update_fields=['transaction'])

    return transaction


# ── Business Profile ───────────────────────────────────────────────

@login_required
def business_profile(request):
    """Settings page for company details, tax config, invoice numbering, bank info."""
    profile, created = BusinessProfile.objects.get_or_create(
        user=request.user,
        defaults={'company_name': request.user.username},
    )

    if request.method == 'POST':
        profile.company_name = request.POST.get('company_name', '').strip() or request.user.username
        profile.company_address = request.POST.get('company_address', '').strip()
        profile.company_phone = request.POST.get('company_phone', '').strip()
        profile.company_email = request.POST.get('company_email', '').strip()
        profile.gstin = request.POST.get('gstin', '').strip()
        profile.currency = request.POST.get('currency', '₹')

        profile.tax_type = request.POST.get('tax_type', 'none')
        try:
            profile.tax_rate = Decimal(request.POST.get('tax_rate', '0') or '0')
        except (InvalidOperation, ValueError):
            profile.tax_rate = Decimal('0')
        try:
            profile.cgst_rate = Decimal(request.POST.get('cgst_rate', '0') or '0')
        except (InvalidOperation, ValueError):
            profile.cgst_rate = Decimal('0')
        try:
            profile.sgst_rate = Decimal(request.POST.get('sgst_rate', '0') or '0')
        except (InvalidOperation, ValueError):
            profile.sgst_rate = Decimal('0')

        profile.invoice_prefix = request.POST.get('invoice_prefix', 'INV').strip() or 'INV'
        try:
            profile.next_invoice_number = int(request.POST.get('next_invoice_number', '1') or '1')
        except ValueError:
            profile.next_invoice_number = 1

        profile.bank_name = request.POST.get('bank_name', '').strip()
        profile.bank_account = request.POST.get('bank_account', '').strip()
        profile.bank_ifsc = request.POST.get('bank_ifsc', '').strip()

        profile.save()
        messages.success(request, 'Business profile updated successfully.')
        return redirect('finance:business_profile')

    return render(request, 'finance/business_profile.html', {'profile': profile})


# ── Invoices ───────────────────────────────────────────────────────

@login_required
def invoice_list(request):
    """List all invoices with optional status filtering."""
    invoices = Invoice.objects.filter(user=request.user)
    status_filter = request.GET.get('status', 'all')
    if status_filter != 'all':
        invoices = invoices.filter(status=status_filter)

    totals = {
        'all': Invoice.objects.filter(user=request.user).count(),
        'draft': Invoice.objects.filter(user=request.user, status='draft').count(),
        'sent': Invoice.objects.filter(user=request.user, status='sent').count(),
        'paid': Invoice.objects.filter(user=request.user, status='paid').count(),
        'overdue': Invoice.objects.filter(user=request.user, status='overdue').count(),
    }

    profile, _ = BusinessProfile.objects.get_or_create(
        user=request.user,
        defaults={'company_name': request.user.username},
    )

    return render(request, 'finance/invoice_list.html', {
        'invoices': invoices,
        'status_filter': status_filter,
        'totals': totals,
        'profile': profile,
    })


@login_required
def invoice_create(request):
    """Create an invoice, optionally pre-filled from a sales transaction."""
    profile, _ = BusinessProfile.objects.get_or_create(
        user=request.user,
        defaults={'company_name': request.user.username},
    )

    transaction = None
    txn_id = request.GET.get('transaction_id')
    if txn_id:
        transaction = get_object_or_404(Transaction, id=txn_id, user=request.user)

    if request.method == 'POST':
        try:
            client_name = request.POST.get('client_name', '').strip()
            if not client_name:
                raise ValueError('Client name is required.')

            issue_date_str = request.POST.get('issue_date', '')
            due_date_str = request.POST.get('due_date', '')
            try:
                issue_dt = date.fromisoformat(issue_date_str)
            except ValueError:
                raise ValueError('Valid issue date is required.')
            try:
                due_dt = date.fromisoformat(due_date_str)
            except ValueError:
                raise ValueError('Valid due date is required.')

            # Collect line items
            descriptions = request.POST.getlist('item_description')
            quantities = request.POST.getlist('item_quantity')
            unit_prices = request.POST.getlist('item_unit_price')

            if not descriptions or not any(d.strip() for d in descriptions):
                raise ValueError('At least one line item is required.')

            line_items = []
            subtotal = Decimal('0')
            for desc, qty_str, price_str in zip(descriptions, quantities, unit_prices):
                desc = desc.strip()
                if not desc:
                    continue
                try:
                    qty = Decimal(qty_str or '1')
                    price = Decimal(price_str or '0')
                except (InvalidOperation, TypeError):
                    raise ValueError(f'Invalid quantity or price for "{desc}".')
                amount = qty * price
                subtotal += amount
                line_items.append({
                    'description': desc,
                    'quantity': qty,
                    'unit_price': price,
                    'amount': amount,
                })

            # Tax calculation
            tax_type = profile.tax_type
            tax_amount = Decimal('0')
            tax_rate = profile.tax_rate
            cgst_rate = profile.cgst_rate
            sgst_rate = profile.sgst_rate
            if tax_type == 'gst':
                tax_amount = subtotal * tax_rate / Decimal('100')
            elif tax_type == 'cgst_sgst':
                tax_amount = subtotal * (cgst_rate + sgst_rate) / Decimal('100')

            total = subtotal + tax_amount

            # Generate invoice number
            inv_number = profile.generate_invoice_number()

            # Link transaction if provided
            txn_id_hidden = request.POST.get('transaction_id')
            txn = None
            if txn_id_hidden:
                try:
                    txn = Transaction.objects.get(id=txn_id_hidden, user=request.user)
                except Transaction.DoesNotExist:
                    pass

            invoice = Invoice.objects.create(
                user=request.user,
                transaction=txn,
                invoice_number=inv_number,
                client_name=client_name,
                client_email=request.POST.get('client_email', '').strip(),
                client_address=request.POST.get('client_address', '').strip(),
                client_gstin=request.POST.get('client_gstin', '').strip(),
                issue_date=issue_dt,
                due_date=due_dt,
                status=request.POST.get('status', 'draft'),
                notes=request.POST.get('notes', '').strip(),
                subtotal=subtotal,
                tax_type=tax_type,
                tax_rate=tax_rate,
                cgst_rate=cgst_rate,
                sgst_rate=sgst_rate,
                tax_amount=tax_amount,
                total=total,
            )

            for item in line_items:
                InvoiceItem.objects.create(invoice=invoice, **item)

            # Auto-create transaction if created as paid
            txn = auto_create_transaction_for_invoice(invoice)
            if txn:
                messages.success(request, f'Invoice {inv_number} created and income of {profile.currency}{txn.amount} recorded.')
            else:
                messages.success(request, f'Invoice {inv_number} created successfully.')
            return redirect('finance:invoice_detail', invoice_id=invoice.id)

        except ValueError as e:
            messages.error(request, str(e))

    # Pre-fill defaults
    today = timezone.localdate()
    defaults = {
        'issue_date': today.isoformat(),
        'due_date': (today + timedelta(days=30)).isoformat(),
        'client_name': '',
        'client_email': '',
        'client_address': '',
        'client_gstin': '',
        'notes': '',
        'item_description': '',
        'item_amount': '',
    }

    if transaction:
        defaults['item_description'] = transaction.description
        defaults['item_amount'] = str(transaction.amount)
        defaults['issue_date'] = transaction.date.isoformat()
        defaults['due_date'] = (transaction.date + timedelta(days=30)).isoformat()

    return render(request, 'finance/invoice_form.html', {
        'profile': profile,
        'transaction': transaction,
        'defaults': defaults,
        'edit_mode': False,
    })


@login_required
def invoice_detail(request, invoice_id):
    """View a single invoice in a print-ready layout."""
    invoice = get_object_or_404(Invoice, id=invoice_id, user=request.user)
    profile, _ = BusinessProfile.objects.get_or_create(
        user=request.user,
        defaults={'company_name': request.user.username},
    )

    # Handle status change via POST
    if request.method == 'POST':
        new_status = request.POST.get('new_status')
        if new_status in dict(Invoice.STATUS_CHOICES):
            invoice.status = new_status
            invoice.save(update_fields=['status'])

            # Auto-create income transaction when marked as paid
            txn = auto_create_transaction_for_invoice(invoice)
            if txn:
                messages.success(
                    request,
                    f'Invoice marked as Paid. Income transaction of {profile.currency}{txn.amount} recorded automatically.',
                )
            else:
                messages.success(request, f'Invoice marked as {invoice.get_status_display()}.')

            return redirect('finance:invoice_detail', invoice_id=invoice.id)

    items = invoice.items.all()

    # Compute CGST/SGST amounts for display
    cgst_amount = Decimal('0')
    sgst_amount = Decimal('0')
    if invoice.tax_type == 'cgst_sgst':
        cgst_amount = invoice.subtotal * invoice.cgst_rate / Decimal('100')
        sgst_amount = invoice.subtotal * invoice.sgst_rate / Decimal('100')

    return render(request, 'finance/invoice_detail.html', {
        'invoice': invoice,
        'items': items,
        'profile': profile,
        'cgst_amount': cgst_amount,
        'sgst_amount': sgst_amount,
    })


@login_required
def invoice_edit(request, invoice_id):
    """Edit an existing invoice."""
    invoice = get_object_or_404(Invoice, id=invoice_id, user=request.user)
    profile, _ = BusinessProfile.objects.get_or_create(
        user=request.user,
        defaults={'company_name': request.user.username},
    )

    if request.method == 'POST':
        try:
            client_name = request.POST.get('client_name', '').strip()
            if not client_name:
                raise ValueError('Client name is required.')

            try:
                issue_dt = date.fromisoformat(request.POST.get('issue_date', ''))
            except ValueError:
                raise ValueError('Valid issue date is required.')
            try:
                due_dt = date.fromisoformat(request.POST.get('due_date', ''))
            except ValueError:
                raise ValueError('Valid due date is required.')

            descriptions = request.POST.getlist('item_description')
            quantities = request.POST.getlist('item_quantity')
            unit_prices = request.POST.getlist('item_unit_price')

            if not descriptions or not any(d.strip() for d in descriptions):
                raise ValueError('At least one line item is required.')

            line_items = []
            subtotal = Decimal('0')
            for desc, qty_str, price_str in zip(descriptions, quantities, unit_prices):
                desc = desc.strip()
                if not desc:
                    continue
                try:
                    qty = Decimal(qty_str or '1')
                    price = Decimal(price_str or '0')
                except (InvalidOperation, TypeError):
                    raise ValueError(f'Invalid quantity or price for "{desc}".')
                amount = qty * price
                subtotal += amount
                line_items.append({
                    'description': desc,
                    'quantity': qty,
                    'unit_price': price,
                    'amount': amount,
                })

            tax_type = profile.tax_type
            tax_amount = Decimal('0')
            if tax_type == 'gst':
                tax_amount = subtotal * profile.tax_rate / Decimal('100')
            elif tax_type == 'cgst_sgst':
                tax_amount = subtotal * (profile.cgst_rate + profile.sgst_rate) / Decimal('100')
            total = subtotal + tax_amount

            invoice.client_name = client_name
            invoice.client_email = request.POST.get('client_email', '').strip()
            invoice.client_address = request.POST.get('client_address', '').strip()
            invoice.client_gstin = request.POST.get('client_gstin', '').strip()
            invoice.issue_date = issue_dt
            invoice.due_date = due_dt
            invoice.status = request.POST.get('status', 'draft')
            invoice.notes = request.POST.get('notes', '').strip()
            invoice.subtotal = subtotal
            invoice.tax_type = tax_type
            invoice.tax_rate = profile.tax_rate
            invoice.cgst_rate = profile.cgst_rate
            invoice.sgst_rate = profile.sgst_rate
            invoice.tax_amount = tax_amount
            invoice.total = total
            invoice.save()

            # Replace line items
            invoice.items.all().delete()
            for item in line_items:
                InvoiceItem.objects.create(invoice=invoice, **item)

            # Auto-create transaction if edited to paid
            txn = auto_create_transaction_for_invoice(invoice)
            if txn:
                messages.success(request, f'Invoice {invoice.invoice_number} updated. Income of {profile.currency}{txn.amount} recorded.')
            else:
                messages.success(request, f'Invoice {invoice.invoice_number} updated.')
            return redirect('finance:invoice_detail', invoice_id=invoice.id)

        except ValueError as e:
            messages.error(request, str(e))

    # Pre-fill from existing invoice
    existing_items = list(invoice.items.values('description', 'quantity', 'unit_price', 'amount'))
    defaults = {
        'issue_date': invoice.issue_date.isoformat(),
        'due_date': invoice.due_date.isoformat(),
        'client_name': invoice.client_name,
        'client_email': invoice.client_email,
        'client_address': invoice.client_address,
        'client_gstin': invoice.client_gstin,
        'notes': invoice.notes,
        'status': invoice.status,
    }

    return render(request, 'finance/invoice_form.html', {
        'profile': profile,
        'invoice': invoice,
        'defaults': defaults,
        'existing_items': existing_items,
        'edit_mode': True,
    })


@login_required
def invoice_delete(request, invoice_id):
    """Delete an invoice."""
    invoice = get_object_or_404(Invoice, id=invoice_id, user=request.user)
    if request.method == 'POST':
        inv_num = invoice.invoice_number
        invoice.delete()
        messages.success(request, f'Invoice {inv_num} deleted.')
    return redirect('finance:invoice_list')


@login_required
def invoice_pdf(request, invoice_id):
    """Generate and stream a PDF download for the invoice."""
    invoice = get_object_or_404(Invoice, id=invoice_id, user=request.user)
    profile, _ = BusinessProfile.objects.get_or_create(
        user=request.user,
        defaults={'company_name': request.user.username},
    )

    buf = generate_invoice_pdf(invoice, profile)
    response = HttpResponse(buf.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{invoice.invoice_number}.pdf"'
    return response

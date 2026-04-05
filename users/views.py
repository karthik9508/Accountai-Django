from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum
from decimal import Decimal
from finance.models import Transaction

@login_required
def home(request):
    user_txns = Transaction.objects.filter(user=request.user)
    total_income = user_txns.filter(type='income').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_expenses = user_txns.filter(type='expense').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    net_balance = total_income - total_expenses
    txn_count = user_txns.count()

    context = {
        'total_income': total_income,
        'total_expenses': total_expenses,
        'net_balance': net_balance,
        'txn_count': txn_count,
    }
    return render(request, 'users/home.html', context)
def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Automatically log in the user after registering
            login(request, user)
            messages.success(request, f"Registration successful. Welcome, {user.username}!")
            return redirect('/')
    else:
        form = UserCreationForm()
    return render(request, 'users/register.html', {'form': form})

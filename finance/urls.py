from django.urls import path
from . import views

app_name = 'finance'

urlpatterns = [
    path('ai-transaction/', views.add_transaction_ai, name='add_transaction'),
    path('transactions/', views.all_transactions, name='all_transactions'),
    path('transactions/<int:transaction_id>/edit/', views.edit_transaction, name='edit_transaction'),
    path('reports/profit-loss/', views.profit_loss, name='profit_loss'),
    path('reports/balance-sheet/', views.balance_sheet, name='balance_sheet'),
    path('reports/cash-flow/', views.cash_flow, name='cash_flow'),
    path('ai-assistant/', views.ai_report, name='ai_report'),

    # Invoices
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoices/create/', views.invoice_create, name='invoice_create'),
    path('invoices/<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:invoice_id>/edit/', views.invoice_edit, name='invoice_edit'),
    path('invoices/<int:invoice_id>/delete/', views.invoice_delete, name='invoice_delete'),
    path('invoices/<int:invoice_id>/pdf/', views.invoice_pdf, name='invoice_pdf'),

    # Settings
    path('settings/business-profile/', views.business_profile, name='business_profile'),
]


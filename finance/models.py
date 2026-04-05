from django.db import models
from django.contrib.auth.models import User

class AccountCategory(models.Model):
    """
    Broad categorization (Assets, Liabilities, Equity, Revenue, Expenses)
    """
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

class Account(models.Model):
    """
    Specific ledger accounts under a category (e.g., 'Cash', 'Electricity', 'Sales')
    """
    name = models.CharField(max_length=100)
    category = models.ForeignKey(AccountCategory, on_delete=models.CASCADE, related_name='accounts')
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.category.name})"

class Transaction(models.Model):
    """
    Individual financial entries
    """
    TRANSACTION_TYPES = [
        ('income', 'Income'),
        ('expense', 'Expense'),
        ('asset', 'Asset'),
        ('liability', 'Liability'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='transactions')
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    description = models.CharField(max_length=255)
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.date} - {self.description} - ₹{self.amount}"


class BusinessProfile(models.Model):
    """
    Stores company details, tax configuration, invoice settings, and bank info.
    One profile per user.
    """
    CURRENCY_CHOICES = [
        ('₹', 'INR - Indian Rupee (₹)'),
        ('$', 'USD - US Dollar ($)'),
        ('€', 'EUR - Euro (€)'),
        ('£', 'GBP - British Pound (£)'),
        ('¥', 'JPY - Japanese Yen (¥)'),
        ('A$', 'AUD - Australian Dollar (A$)'),
        ('C$', 'CAD - Canadian Dollar (C$)'),
        ('CHF', 'CHF - Swiss Franc (CHF)'),
        ('¥', 'CNY - Chinese Yuan (¥)'),
        ('₩', 'KRW - South Korean Won (₩)'),
        ('R', 'ZAR - South African Rand (R)'),
        ('د.إ', 'AED - UAE Dirham (د.إ)'),
        ('﷼', 'SAR - Saudi Riyal (﷼)'),
        ('₿', 'BTC - Bitcoin (₿)'),
    ]

    TAX_TYPE_CHOICES = [
        ('none', 'No Tax'),
        ('gst', 'GST'),
        ('cgst_sgst', 'CGST + SGST'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='business_profile')
    company_name = models.CharField(max_length=200)
    company_address = models.TextField(blank=True, default='')
    company_phone = models.CharField(max_length=20, blank=True, default='')
    company_email = models.EmailField(blank=True, default='')
    gstin = models.CharField(max_length=20, blank=True, default='', verbose_name='GSTIN')
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹')

    tax_type = models.CharField(max_length=10, choices=TAX_TYPE_CHOICES, default='none')
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text='Single GST rate %')
    cgst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text='CGST rate %')
    sgst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text='SGST rate %')

    invoice_prefix = models.CharField(max_length=10, default='INV')
    next_invoice_number = models.PositiveIntegerField(default=1)

    bank_name = models.CharField(max_length=100, blank=True, default='')
    bank_account = models.CharField(max_length=50, blank=True, default='')
    bank_ifsc = models.CharField(max_length=20, blank=True, default='', verbose_name='IFSC Code')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.company_name} ({self.user.username})"

    def generate_invoice_number(self):
        """Generate next invoice number and increment the counter."""
        number = f"{self.invoice_prefix}-{self.next_invoice_number:04d}"
        self.next_invoice_number += 1
        self.save(update_fields=['next_invoice_number'])
        return number


class Invoice(models.Model):
    """
    Invoice header — linked to a user and optionally to a source sales transaction.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
    ]

    TAX_TYPE_CHOICES = BusinessProfile.TAX_TYPE_CHOICES

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invoices')
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices'
    )
    invoice_number = models.CharField(max_length=30, unique=True)
    client_name = models.CharField(max_length=200)
    client_email = models.EmailField(blank=True, default='')
    client_address = models.TextField(blank=True, default='')
    client_gstin = models.CharField(max_length=20, blank=True, default='', verbose_name='Client GSTIN')

    issue_date = models.DateField()
    due_date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')

    notes = models.TextField(blank=True, default='')

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_type = models.CharField(max_length=10, choices=TAX_TYPE_CHOICES, default='none')
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cgst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    sgst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.invoice_number} — {self.client_name} — ₹{self.total}"

    class Meta:
        ordering = ['-issue_date', '-created_at']


class InvoiceItem(models.Model):
    """
    Individual line items on an invoice.
    """
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.description} — {self.quantity} × ₹{self.unit_price}"

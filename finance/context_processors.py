"""
Context processor to inject the user's currency symbol into every template.
"""
from .models import BusinessProfile


def currency_symbol(request):
    """Add the user's selected currency symbol to the template context."""
    if request.user.is_authenticated:
        try:
            profile = request.user.business_profile
            return {'currency': profile.currency}
        except BusinessProfile.DoesNotExist:
            pass
    return {'currency': '₹'}

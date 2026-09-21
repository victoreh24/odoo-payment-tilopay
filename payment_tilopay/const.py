# Tilopay documents both app.tilopay.com and api.tilopay.com; the base URL is editable on the provider.
DEFAULT_API_BASE = 'https://app.tilopay.com/api/v1'
RETURN_ROUTE = '/payment/tilopay/return'
WEBHOOK_ROUTE = '/payment/tilopay/webhook'
PLATFORM = 'odoo-lapa-concierge'
TOKEN_LIFETIME_HOURS = 23

SUPPORTED_CURRENCIES = {'USD', 'CRC'}
DEFAULT_PAYMENT_METHOD_CODES = {'card', 'visa', 'mastercard', 'amex'}

# Tilopay's "code" on the return URL / consult payload: "1" means approved.
APPROVED_CODES = {'1'}
PENDING_STATUSES = {'pending', 'processing', 'in_process', 'in process', 'created'}
CANCELED_STATUSES = {'cancelled', 'canceled', 'void', 'voided', 'expired'}
DONE_STATUSES = {'1', 'approved', 'aprobado', 'aprobada', 'success', 'successful', 'paid', 'captured', 'processed', 'completed'}

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.urls import urljoin

from odoo.addons.payment_tilopay import const

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(selection_add=[('tilopay', 'Tilopay')], ondelete={'tilopay': 'set default'})
    tilopay_api_base = fields.Char(
        string='Tilopay API base URL', default=const.DEFAULT_API_BASE, required_if_provider='tilopay')
    tilopay_api_user = fields.Char(
        string='Tilopay API user', required_if_provider='tilopay', groups='base.group_system', copy=False)
    tilopay_api_password = fields.Char(
        string='Tilopay API password', required_if_provider='tilopay', groups='base.group_system', copy=False)
    tilopay_key = fields.Char(
        string='Tilopay merchant key', required_if_provider='tilopay', groups='base.group_system', copy=False)
    tilopay_access_token = fields.Char(groups='base.group_system', copy=False)
    tilopay_token_expiry = fields.Datetime(groups='base.group_system', copy=False)

    # === BUSINESS METHODS === #
    def _get_supported_currencies(self):
        supported = super()._get_supported_currencies()
        if self.code == 'tilopay':
            supported = supported.filtered(lambda c: c.name in const.SUPPORTED_CURRENCIES)
        return supported

    def _get_default_payment_method_codes(self):
        self.ensure_one()
        if self.code != 'tilopay':
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _get_reset_values(self):
        if self.code != 'tilopay':
            return super()._get_reset_values()
        return {
            'tilopay_api_user': None, 'tilopay_api_password': None, 'tilopay_key': None,
            'tilopay_access_token': None, 'tilopay_token_expiry': None,
        }

    # === REQUEST HELPERS === #
    def _build_request_url(self, endpoint, **kwargs):
        if self.code != 'tilopay':
            return super()._build_request_url(endpoint, **kwargs)
        return urljoin((self.tilopay_api_base or const.DEFAULT_API_BASE).rstrip('/') + '/', endpoint.lstrip('/'))

    def _build_request_headers(self, method, endpoint, payload, *, is_login=False, **kwargs):
        if self.code != 'tilopay':
            return super()._build_request_headers(method, endpoint, payload, **kwargs)
        headers = {'Accept': 'application/json'}
        if not is_login:
            headers['Authorization'] = f'Bearer {self._tilopay_get_token()}'
        return headers

    def _parse_response_error(self, response):
        if self.code != 'tilopay':
            return super()._parse_response_error(response)
        try:
            content = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(content, dict):
            return content.get('message') or content.get('description') or content.get('error') or str(content)[:200]
        return str(content)[:200]

    def _tilopay_get_token(self):
        """Return a cached bearer token, logging in again when expired."""
        self.ensure_one()
        if self.tilopay_access_token and self.tilopay_token_expiry and self.tilopay_token_expiry > fields.Datetime.now():
            return self.tilopay_access_token
        content = self._send_api_request(
            'POST', '/login', json={'apiuser': self.tilopay_api_user, 'password': self.tilopay_api_password},
            is_login=True)
        token = None
        if isinstance(content, dict):
            token = content.get('access_token') or content.get('token') or content.get('accessToken')
        if not token:
            raise ValidationError(_("Tilopay login did not return an access token."))
        self.sudo().write({
            'tilopay_access_token': token,
            'tilopay_token_expiry': fields.Datetime.now() + timedelta(hours=const.TOKEN_LIFETIME_HOURS),
        })
        return token

    def _tilopay_consult(self, order_number):
        """Ask Tilopay for the authoritative state of an order."""
        self.ensure_one()
        content = self._send_api_request(
            'POST', '/consult', json={'key': self.tilopay_key, 'orderNumber': order_number},
            reference=order_number)
        if isinstance(content, list):
            content = content[0] if content else {}
        if not isinstance(content, dict):
            raise ValidationError(_("Unexpected Tilopay consult response."))
        content.setdefault('orderNumber', order_number)
        return content

    def action_tilopay_test_connection(self):
        self.ensure_one()
        self.write({'tilopay_access_token': False, 'tilopay_token_expiry': False})
        self._tilopay_get_token()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success', 'message': _("Tilopay login succeeded."), 'sticky': False},
        }

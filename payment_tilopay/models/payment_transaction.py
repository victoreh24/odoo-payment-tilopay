import base64
import logging
from datetime import timedelta

from werkzeug.urls import url_decode, url_parse

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.urls import urljoin

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_tilopay import const

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    tilopay_checkout_url = fields.Char(copy=False, readonly=True)
    tilopay_last_consult = fields.Datetime(copy=False, readonly=True)

    # === REDIRECT FLOW === #
    def _get_specific_rendering_values(self, processing_values):
        if self.provider_code != 'tilopay':
            return super()._get_specific_rendering_values(processing_values)
        payload = self._tilopay_prepare_payload()
        try:
            content = self.provider_id._send_api_request(
                'POST', '/processPayment', json=payload, reference=self.reference)
        except ValidationError as error:
            self._set_error(str(error))
            return {}
        url = isinstance(content, dict) and content.get('url')
        if not url or str(content.get('type', '100')) != '100':
            message = (isinstance(content, dict) and (content.get('description') or content.get('message'))) \
                or _("Tilopay did not return a checkout URL.")
            self._set_error(message)
            return {}
        self.write({'tilopay_checkout_url': url})
        self._set_pending(state_message=_("Waiting for the customer to pay on Tilopay."))
        parsed = url_parse(url)
        return {'api_url': url, 'url_params': dict(url_decode(parsed.query))}

    def _tilopay_prepare_payload(self):
        self.ensure_one()
        provider = self.provider_id
        first_name, last_name = payment_utils.split_partner_name(self.partner_name or '')
        base_url = provider.get_base_url()
        return {
            'redirect': urljoin(base_url, const.RETURN_ROUTE),
            'key': provider.tilopay_key,
            'amount': f'{self.amount:.2f}',
            'currency': self.currency_id.name,
            'orderNumber': self.reference,
            'capture': '1',
            'billToFirstName': first_name or 'Guest',
            'billToLastName': last_name or '-',
            'billToAddress': self.partner_address or 'N/A',
            'billToAddress2': '',
            'billToCity': self.partner_city or 'Nosara',
            'billToState': self.partner_state_id.name or 'Guanacaste',
            'billToZipPostCode': self.partner_zip or '50206',
            'billToCountry': self.partner_country_id.code or 'CR',
            'billToTelephone': self.partner_phone or '00000000',
            'billToEmail': self.partner_email or '',
            'shipToFirstName': first_name or 'Guest',
            'shipToLastName': last_name or '-',
            'shipToAddress': self.partner_address or 'N/A',
            'shipToAddress2': '',
            'shipToCity': self.partner_city or 'Nosara',
            'shipToState': self.partner_state_id.name or 'Guanacaste',
            'shipToZipPostCode': self.partner_zip or '50206',
            'shipToCountry': self.partner_country_id.code or 'CR',
            'shipToTelephone': self.partner_phone or '00000000',
            'subscription': '0',
            'platform': const.PLATFORM,
            'returnData': base64.b64encode(self.reference.encode()).decode(),
            'hashVersion': 'V2',
        }

    # === PROCESSING === #
    @api.model
    def _extract_reference(self, provider_code, payment_data):
        if provider_code != 'tilopay':
            return super()._extract_reference(provider_code, payment_data)
        reference = payment_data.get('order') or payment_data.get('orderNumber') or payment_data.get('order_number')
        if not reference and payment_data.get('returnData'):
            try:
                reference = base64.b64decode(payment_data['returnData']).decode()
            except Exception:  # noqa: BLE001
                reference = None
        return reference

    def _extract_amount_data(self, payment_data):
        if self.provider_code != 'tilopay':
            return super()._extract_amount_data(payment_data)
        amount = payment_data.get('amount')
        currency = payment_data.get('currency')
        if amount in (None, '') or not currency:
            return None  # Tilopay's redirect does not carry the amount: opt out of the check.
        try:
            return {'amount': float(amount), 'currency_code': str(currency).upper()}
        except (TypeError, ValueError):
            return None

    @api.model
    def _tilopay_normalize_status(self, payment_data):
        code = str(payment_data.get('code', '')).strip()
        if code in const.APPROVED_CODES:
            return 'done'
        status = str(payment_data.get('status') or payment_data.get('state') or '').strip().lower()
        if status in const.DONE_STATUSES:
            return 'done'
        if status in const.PENDING_STATUSES:
            return 'pending'
        if status in const.CANCELED_STATUSES:
            return 'canceled'
        if code or status in const.DECLINED_STATUSES:
            # Only an explicit answer (a non-approval code or a decline status) is a rejection.
            return 'error'
        return 'unknown'

    def _apply_updates(self, payment_data):
        if self.provider_code != 'tilopay':
            return super()._apply_updates(payment_data)
        provider_ref = payment_data.get('tilopay-transaction') or payment_data.get('transactionId') \
            or payment_data.get('auth') or payment_data.get('tpt')
        if provider_ref:
            self.provider_reference = str(provider_ref)
        status = self._tilopay_normalize_status(payment_data)
        description = payment_data.get('description') or payment_data.get('message') or ''
        if status == 'done':
            self._set_done()
        elif status == 'pending':
            self._set_pending(state_message=description or None)
        elif status == 'canceled':
            self._set_canceled(state_message=description or None)
        elif status == 'unknown':
            # "No news" is not a decline: the guest may still be typing the card number. Marking
            # the transaction as failed here would tell the app the payment was rejected and
            # take it out of the reconcile cron while the money can still arrive.
            age_limit = fields.Datetime.now() - timedelta(hours=const.UNKNOWN_MAX_AGE_HOURS)
            if self.create_date and self.create_date < age_limit:
                self._set_error(_("Tilopay never confirmed this payment."))
            elif self.state == 'draft':
                self._set_pending()
        else:
            _logger.warning("Tilopay: transaction %s rejected: %s", self.reference, payment_data)
            self._set_error(_("Payment rejected by Tilopay. %s", description).strip())

    def _tilopay_reconcile(self, fallback_data=None):
        """Verify the order with Tilopay's consult endpoint and apply the authoritative result.

        The redirect parameters alone are never trusted for a success; they are only used to
        record a rejection when Tilopay cannot be reached.
        """
        for tx in self.filtered(lambda t: t.provider_code == 'tilopay'):
            if tx.state in ('done', 'cancel'):
                continue
            try:
                data = tx.provider_id._tilopay_consult(tx.reference)
                tx.tilopay_last_consult = fields.Datetime.now()
            except ValidationError as error:
                _logger.warning("Tilopay: consult failed for %s: %s", tx.reference, error)
                if fallback_data and tx._tilopay_normalize_status(fallback_data) != 'done':
                    tx._process('tilopay', dict(fallback_data, orderNumber=tx.reference))
                continue
            if tx._tilopay_normalize_status(data) == 'error' and fallback_data \
                    and tx._tilopay_normalize_status(fallback_data) == 'pending':
                tx._set_pending()
                continue
            tx._process('tilopay', data)
        return True

    @api.model
    def _cron_tilopay_reconcile(self):
        now = fields.Datetime.now()
        pending = self.search([
            ('provider_code', '=', 'tilopay'),
            ('state', 'in', ('draft', 'pending')),
            ('tilopay_checkout_url', '!=', False),
            ('create_date', '<', now - timedelta(minutes=2)),
            ('create_date', '>', now - timedelta(days=2)),
        ], limit=200)
        pending._tilopay_reconcile()
        return True

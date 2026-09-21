import logging

from odoo import http
from odoo.http import request

from odoo.addons.payment_tilopay import const

_logger = logging.getLogger(__name__)


class TilopayController(http.Controller):

    @http.route(const.RETURN_ROUTE, type='http', auth='public', methods=['GET', 'POST'], csrf=False, save_session=False)
    def tilopay_return(self, **data):
        """Customer comes back from the hosted payment page."""
        _logger.info("Tilopay: return with data %s", {k: v for k, v in data.items() if k != 'OrderHash'})
        tx = request.env['payment.transaction'].sudo()._search_by_reference('tilopay', data)
        if tx:
            tx._tilopay_reconcile(fallback_data=data)
        return request.redirect('/payment/status')

    @http.route(const.WEBHOOK_ROUTE, type='http', auth='public', methods=['POST'], csrf=False)
    def tilopay_webhook(self, **params):
        """Server-to-server notification (must be enabled by Tilopay support)."""
        data = dict(params)
        try:
            body = request.get_json_data()
            if isinstance(body, dict):
                data.update(body)
        except Exception:  # noqa: BLE001 - form-encoded webhook
            pass
        _logger.info("Tilopay: webhook %s", {k: v for k, v in data.items() if k != 'OrderHash'})
        tx = request.env['payment.transaction'].sudo()._search_by_reference('tilopay', data)
        if tx:
            tx._tilopay_reconcile(fallback_data=data)
        return request.make_json_response({'ok': True})

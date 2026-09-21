from unittest.mock import MagicMock, patch

from odoo.tests import HttpCase, tagged


def fake_tilopay(responses):
    """Return a requests.request replacement answering per endpoint suffix."""
    calls = []

    def _request(method, url, **kwargs):
        calls.append((method, url, kwargs.get('json')))
        for suffix, payload in responses.items():
            if url.endswith(suffix):
                response = MagicMock()
                response.status_code = 200
                response.ok = True
                response.raise_for_status.return_value = None
                response.json.return_value = payload
                response.text = str(payload)
                return response
        raise AssertionError(f"Unexpected Tilopay call {method} {url}")

    _request.calls = calls
    return _request


LOGIN = {'access_token': 'tok-123', 'token_type': 'bearer'}
CHECKOUT = {'type': '100', 'url': 'https://app.tilopay.com/hpp/abc?lang=es'}


@tagged('concierge', 'post_install', '-at_install')
class TestTilopay(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref('payment_tilopay.payment_provider_tilopay')
        cls.provider.write({
            'state': 'test', 'tilopay_api_user': 'user', 'tilopay_api_password': 'pass', 'tilopay_key': 'key-1',
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Ana Guest', 'email': 'ana@example.com'})
        cls.usd = cls.env.ref('base.USD')

    def _tx(self, amount=95.0, reference='TEST-001'):
        method = self.env['payment.method'].search([('code', '=', 'card')], limit=1)
        return self.env['payment.transaction'].create({
            'provider_id': self.provider.id,
            'payment_method_id': method.id,
            'reference': reference,
            'amount': amount,
            'currency_id': self.usd.id,
            'partner_id': self.partner.id,
            'operation': 'online_redirect',
        })

    def test_checkout_url_and_pending(self):
        tx = self._tx()
        fake = fake_tilopay({'/login': LOGIN, '/processPayment': CHECKOUT})
        with patch('odoo.addons.payment.models.payment_provider.requests.request', side_effect=fake):
            values = tx._get_specific_rendering_values({'reference': tx.reference})
        self.assertEqual(values['api_url'], CHECKOUT['url'])
        self.assertEqual(values['url_params'], {'lang': 'es'})
        self.assertEqual(tx.state, 'pending')
        self.assertEqual(tx.tilopay_checkout_url, CHECKOUT['url'])
        urls = [c[1] for c in fake.calls]
        self.assertEqual(len(fake.calls), 2, urls)
        login_call, pay_call = fake.calls
        self.assertTrue(login_call[1].endswith('/login'))
        self.assertEqual(pay_call[2]['orderNumber'], 'TEST-001')
        self.assertEqual(pay_call[2]['amount'], '95.00')
        self.assertEqual(pay_call[2]['currency'], 'USD')
        self.assertTrue(pay_call[2]['redirect'].endswith('/payment/tilopay/return'))
        self.assertEqual(self.provider.tilopay_access_token, 'tok-123')

    def test_checkout_error_sets_tx_error(self):
        tx = self._tx(reference='TEST-ERR')
        fake = fake_tilopay({'/login': LOGIN, '/processPayment': {'type': '400', 'description': 'Invalid key'}})
        with patch('odoo.addons.payment.models.payment_provider.requests.request', side_effect=fake):
            values = tx._get_specific_rendering_values({'reference': tx.reference})
        self.assertEqual(values, {})
        self.assertEqual(tx.state, 'error')
        self.assertIn('Invalid key', tx.state_message)

    def test_return_route_verifies_with_consult(self):
        tx = self._tx(reference='TEST-RET')
        tx._set_pending()
        fake = fake_tilopay({'/login': LOGIN, '/consult': {'code': '1', 'description': 'Approved', 'auth': 'A1',
                                                          'tilopay-transaction': 'T-999', 'amount': '95.00', 'currency': 'USD'}})
        with patch('odoo.addons.payment.models.payment_provider.requests.request', side_effect=fake):
            response = self.url_open('/payment/tilopay/return?code=1&order=TEST-RET&auth=A1&tpt=1', allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn('/payment/status', response.headers['Location'])
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.provider_reference, 'T-999')
        self.assertTrue(tx.tilopay_last_consult)

    def test_redirect_success_is_not_trusted_without_consult(self):
        tx = self._tx(reference='TEST-UNVERIFIED')
        tx._set_pending()
        fake = fake_tilopay({'/login': LOGIN})  # consult unreachable -> AssertionError -> request fails
        with patch('odoo.addons.payment.models.payment_provider.requests.request', side_effect=fake):
            with patch('odoo.addons.payment_tilopay.models.payment_provider.PaymentProvider._tilopay_consult',
                       side_effect=__import__('odoo').exceptions.ValidationError('down')):
                self.url_open('/payment/tilopay/return?code=1&order=TEST-UNVERIFIED', allow_redirects=False)
        self.assertEqual(tx.state, 'pending')

    def test_declined_redirect_recorded_when_consult_down(self):
        tx = self._tx(reference='TEST-DECL')
        tx._set_pending()
        with patch('odoo.addons.payment_tilopay.models.payment_provider.PaymentProvider._tilopay_consult',
                   side_effect=__import__('odoo').exceptions.ValidationError('down')):
            self.url_open('/payment/tilopay/return?code=0&order=TEST-DECL&description=Insufficient+funds',
                          allow_redirects=False)
        self.assertEqual(tx.state, 'error')
        self.assertIn('Insufficient funds', tx.state_message)

    def test_amount_mismatch_is_rejected(self):
        tx = self._tx(amount=95.0, reference='TEST-AMT')
        tx._set_pending()
        with patch('odoo.addons.payment_tilopay.models.payment_provider.PaymentProvider._tilopay_consult',
                   return_value={'code': '1', 'amount': '10.00', 'currency': 'USD', 'orderNumber': 'TEST-AMT'}):
            tx._tilopay_reconcile()
        self.assertEqual(tx.state, 'error')

    def test_cron_reconciles_pending(self):
        tx = self._tx(reference='TEST-CRON')
        tx.write({'tilopay_checkout_url': 'https://x'})
        tx._set_pending()
        self.env.cr.execute("UPDATE payment_transaction SET create_date = now() - interval '10 minutes' WHERE id = %s", (tx.id,))
        tx.invalidate_recordset(['create_date'])
        with patch('odoo.addons.payment_tilopay.models.payment_provider.PaymentProvider._tilopay_consult',
                   return_value={'code': '1', 'orderNumber': 'TEST-CRON'}):
            self.env['payment.transaction']._cron_tilopay_reconcile()
        self.assertEqual(tx.state, 'done')

    def test_webhook(self):
        tx = self._tx(reference='TEST-WH')
        tx._set_pending()
        with patch('odoo.addons.payment_tilopay.models.payment_provider.PaymentProvider._tilopay_consult',
                   return_value={'code': '1', 'orderNumber': 'TEST-WH'}):
            response = self.url_open('/payment/tilopay/webhook', data='{"order": "TEST-WH", "code": "1"}',
                                     headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, 'done')

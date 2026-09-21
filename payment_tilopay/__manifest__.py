{
    'name': 'Payment Provider: Tilopay',
    'version': '19.0.1.1.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': 'Costa Rican payment gateway (cards + SINPE Movil) through the Tilopay hosted payment page',
    'description': """
Tilopay hosted payment page integration for Odoo 19.

* Redirect flow: Odoo calls processPayment, the customer pays on Tilopay, Tilopay redirects back.
* Every outcome is re-verified against Tilopay's consult endpoint before a transaction is confirmed.
* Optional webhook endpoint and a 5-minute reconciliation cron for lost redirects.
""",
    'author': 'Momentum CR',
    'website': 'https://momentum.cr',
    'license': 'LGPL-3',
    'depends': ['payment'],
    'data': [
        'views/payment_tilopay_templates.xml',
        'views/payment_provider_views.xml',
        'data/payment_provider_data.xml',
        'data/ir_cron_data.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
}

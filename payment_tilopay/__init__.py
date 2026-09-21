from . import controllers
from . import models

import odoo.addons.payment as payment  # Prevent circular import error with payment (res.country).


def post_init_hook(env):
    payment.setup_provider(env, 'tilopay')


def uninstall_hook(env):
    payment.reset_payment_provider(env, 'tilopay')

import os
import time
import uuid
import logging
import urllib.parse
import requests
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.urls import reverse

from .models import PaymentTransaction, PaymentGatewayConfig, PaymentMethodSetting
from order.models import Order
from order.services import dispatch_order_notifications
from core.models import AuditLog
from core.utils import get_id_prefixes

logger = logging.getLogger(__name__)

# SSLCommerz Official API Endpoints
SSLCOMMERZ_SANDBOX_GW_URL = "https://sandbox.sslcommerz.com/gwprocess/v4/api.php"
SSLCOMMERZ_LIVE_GW_URL = "https://securepay.sslcommerz.com/gwprocess/v4/api.php"

SSLCOMMERZ_SANDBOX_VALIDATION_URL = "https://sandbox.sslcommerz.com/validator/api/validationserverAPI.php"
SSLCOMMERZ_LIVE_VALIDATION_URL = "https://securepay.sslcommerz.com/validator/api/validationserverAPI.php"

DEFAULT_PAYMENT_METHODS = [
    {
        'method_id': 'sslcommerz',
        'name': 'Pay Online (Cards / bKash / Nagad via SSLCommerz)',
        'is_active': True,
        'account_type': 'GATEWAY',
        'number': '',
        'counter': '',
        'instructions': 'Complete instant payment with Visa, MasterCard, bKash, Nagad, Rocket, or internet banking.',
        'display_order': 1,
    },
    {
        'method_id': 'cod',
        'name': 'Cash on Delivery',
        'is_active': True,
        'account_type': 'COD',
        'number': '',
        'counter': '',
        'instructions': 'Inspect parcel upon delivery and pay cash directly to the delivery agent.',
        'display_order': 2,
    },
    {
        'method_id': 'bkash',
        'name': 'bKash Manual',
        'is_active': True,
        'account_type': 'MERCHANT',
        'number': '',
        'counter': '1',
        'instructions': "Select 'Payment' in your bKash app, pay to the merchant number, and enter the TrxID below.",
        'display_order': 3,
    },
    {
        'method_id': 'nagad',
        'name': 'Nagad Manual',
        'is_active': True,
        'account_type': 'MERCHANT',
        'number': '',
        'counter': '1',
        'instructions': "Select 'Payment' in your Nagad app, pay to the merchant number, and enter the TrxID below.",
        'display_order': 4,
    },
    {
        'method_id': 'rocket',
        'name': 'Rocket Manual',
        'is_active': True,
        'account_type': 'MERCHANT',
        'number': '',
        'counter': '1',
        'instructions': "Select 'Merchant Pay' in your Rocket app, pay to the merchant number, and enter the TrxID below.",
        'display_order': 5,
    },
]


def get_payment_controls():
    """Reads Admin-managed payment behavior flags from central ShopSetting.extraConfig."""
    try:
        from core.models import ShopSetting
        s = ShopSetting.get_settings()
        extra = s.extraConfig if isinstance(s.extraConfig, dict) else {}
        pc = extra.get('paymentControls') if isinstance(extra.get('paymentControls'), dict) else {}
        return {
            'allowSandboxSimulator': bool(pc.get('allowSandboxSimulator', False)),
        }
    except Exception:
        return {'allowSandboxSimulator': False}


def get_or_seed_payment_methods():
    """
    Returns all configured payment methods, seeding defaults if table is empty.
    """
    for item in DEFAULT_PAYMENT_METHODS:
        PaymentMethodSetting.objects.get_or_create(
            method_id=item['method_id'],
            defaults=item
        )
    return PaymentMethodSetting.objects.all().order_by('display_order', 'id')


def get_gateway_config():
    """
    Retrieves or initializes the SSLCommerz gateway configuration.
    """
    config, created = PaymentGatewayConfig.objects.get_or_create(
        gateway_id='sslcommerz',
        defaults={
            'name': 'SSLCOMMERZ Multi-Card & MFS Gateway',
            'store_id': getattr(settings, 'SSLCOMMERZ_STORE_ID', ''),
            'store_password': getattr(settings, 'SSLCOMMERZ_STORE_PASSWORD', ''),
            'is_sandbox': getattr(settings, 'SSLCOMMERZ_IS_SANDBOX', True),
            'is_active': True,
            'fee_percent': Decimal('2.0'),
        }
    )
    return config


def get_base_urls(request=None):
    """
    Determines frontend and backend base URLs dynamically.
    Prioritizes explicit production environment variables, then request headers.
    """
    configured_backend = getattr(settings, 'SSLCOMMERZ_BACKEND_URL', '').rstrip('/')
    if configured_backend and 'localhost' not in configured_backend and '127.0.0.1' not in configured_backend:
        backend_url = configured_backend
    elif request:
        try:
            backend_url = request.build_absolute_uri('/').rstrip('/')
        except Exception:
            backend_url = configured_backend or 'http://127.0.0.1:8000'
    else:
        backend_url = configured_backend or 'http://127.0.0.1:8000'

    # If backend is deployed (not localhost) and scheme is http, upgrade to https
    if 'localhost' not in backend_url and '127.0.0.1' not in backend_url and backend_url.startswith('http://'):
        backend_url = 'https://' + backend_url[7:]

    configured_frontend = getattr(settings, 'SSLCOMMERZ_FRONTEND_URL', '').rstrip('/')
    if configured_frontend and 'localhost' not in configured_frontend and '127.0.0.1' not in configured_frontend:
        frontend_url = configured_frontend
    else:
        frontend_url = configured_frontend or 'http://localhost:3001'
        if request:
            origin = request.META.get('HTTP_ORIGIN') or request.META.get('HTTP_REFERER')
            if origin:
                try:
                    parsed = urllib.parse.urlparse(origin)
                    if parsed.scheme and parsed.netloc:
                        frontend_url = f"{parsed.scheme}://{parsed.netloc}".rstrip('/')
                except Exception:
                    pass

    return backend_url, frontend_url


def initiate_sslcommerz_payment(order_id=None, amount=None, customer_data=None, request=None, checkout_payload=None):
    """
    Initiates an SSLCommerz payment session.
    Supports deferred checkout_payload (creating Order only after successful payment!).
    Returns: dict with success status, gatewayUrl, tran_id, etc.
    """
    config = get_gateway_config()
    if not config.is_active:
        return {
            'success': False,
            'message': 'SSLCommerz payment gateway is currently disabled by administrator.'
        }

    # Block live mode immediately if official live credentials are still unset/default sandbox
    if not config.is_sandbox and (
        not config.store_id
        or str(config.store_id).strip().lower() == 'testbox'
        or not config.store_password
        or str(config.store_password).strip() == 'qwerty'
    ):
        failed_msg = 'Live SSLCommerz merchant credentials are not configured yet. Please configure Store ID & Password or enable Sandbox mode.'
        try:
            id_prefixes = get_id_prefixes()
            trx_prefix = (id_prefixes.get('transactionIdPrefix') or 'AMAR')[:8]
            order_prefix = id_prefixes.get('orderIdPrefix') or 'BD'
            temp_oid = order_id or f"{order_prefix}-{int(time.time() * 1000) % 900000 + 100000}"
            cdata = customer_data or (checkout_payload.get('address') if isinstance(checkout_payload, dict) else {}) or {}
            PaymentTransaction.objects.create(
                order_id=temp_oid,
                tran_id=f"{trx_prefix}_FAIL_{int(time.time() * 1000) % 100000000}"[:30],
                amount=Decimal(f"{float(amount or 0):.2f}"),
                currency='BDT',
                payment_method='SSLCOMMERZ',
                status='FAILED',
                customer_name=str(cdata.get('customerName') or cdata.get('name') or 'Customer')[:128],
                customer_phone=str(cdata.get('phone') or '')[:32],
                error_reason=failed_msg,
                raw_payload={'checkout_payload': checkout_payload} if checkout_payload else {},
            )
        except Exception:
            pass
        return {
            'success': False,
            'message': failed_msg,
        }

    # Fetch corresponding order if present, or validate deferred checkout_payload
    order = Order.objects.filter(id=order_id).first() if order_id else None
    if order:
        resolved_amount = float(order.total)
        address = order.address if isinstance(order.address, dict) else {}
        customer_name = address.get('customerName') or (customer_data.get('customerName') if customer_data else 'Customer')
        customer_phone = address.get('phone') or (customer_data.get('phone') if customer_data else '01700000000')
        customer_email = address.get('email') or (customer_data.get('email') if customer_data else f"{customer_phone}@customer.amardokan.com")
        customer_address = address.get('fullAddress') or (customer_data.get('fullAddress') if customer_data else 'Dhaka, Bangladesh')
        customer_district = address.get('district') or (customer_data.get('district') if customer_data else 'Dhaka')
    elif isinstance(checkout_payload, dict) and checkout_payload.get('items'):
        from order.views import prepare_order_checkout_data
        import random
        prepared, err_msg = prepare_order_checkout_data(checkout_payload)
        if err_msg or not prepared:
            return {
                'success': False,
                'message': err_msg or 'Invalid checkout items or details.'
            }
        id_prefixes = get_id_prefixes()
        order_prefix = id_prefixes.get('orderIdPrefix') or 'BD'
        if not order_id:
            while True:
                candidate = f"{order_prefix}-{random.randint(800000, 999999)}"
                if not Order.objects.filter(id=candidate).exists() and not PaymentTransaction.objects.filter(order_id=candidate).exists():
                    order_id = candidate
                    break
        resolved_amount = float(prepared['grand_total'])
        addr = prepared['address']
        customer_name = addr.get('customerName') or 'Customer'
        customer_phone = addr.get('phone') or '01700000000'
        customer_email = prepared['customer_email'] or f"{customer_phone}@customer.amardokan.com"
        customer_address = addr.get('fullAddress') or 'Dhaka, Bangladesh'
        customer_district = prepared['district'] or 'Dhaka'
    else:
        resolved_amount = float(amount or 0)
        cdata = customer_data or {}
        customer_name = cdata.get('customerName') or cdata.get('name') or 'Customer'
        customer_phone = cdata.get('phone') or '01700000000'
        customer_email = cdata.get('email') or f"{customer_phone}@customer.amardokan.com"
        customer_address = cdata.get('fullAddress') or cdata.get('address') or 'Dhaka, Bangladesh'
        customer_district = cdata.get('district') or 'Dhaka'

    if not order_id:
        id_prefixes = get_id_prefixes()
        order_prefix = id_prefixes.get('orderIdPrefix') or 'BD'
        order_id = f"{order_prefix}-{int(time.time() * 1000) % 900000 + 100000}"

    if resolved_amount <= 0:
        return {
            'success': False,
            'message': 'Invalid payment amount. Amount must be greater than zero.'
        }

    backend_url, frontend_url = get_base_urls(request)

    # Generate a unique Transaction ID (max 30 chars for SSLCommerz compliance)
    id_prefixes = get_id_prefixes()
    trx_prefix = (id_prefixes.get('transactionIdPrefix') or 'AMAR')[:8]
    timestamp_suffix = int(time.time() * 1000) % 100000000
    clean_order_id = str(order_id).replace('-', '_')[:12]
    tran_id = f"{trx_prefix}_{clean_order_id}_{timestamp_suffix}"[:30]

    success_url = f"{backend_url}/api/v1/payments/sslcommerz/success/"
    fail_url = f"{backend_url}/api/v1/payments/sslcommerz/fail/"
    cancel_url = f"{backend_url}/api/v1/payments/sslcommerz/cancel/"
    ipn_url = f"{backend_url}/api/v1/payments/sslcommerz/ipn/"

    post_data = {
        'store_id': config.store_id,
        'store_passwd': config.store_password,
        'total_amount': f"{resolved_amount:.2f}",
        'currency': 'BDT',
        'tran_id': tran_id,
        'success_url': success_url,
        'fail_url': fail_url,
        'cancel_url': cancel_url,
        'ipn_url': ipn_url,
        'shipping_method': 'NO',
        'product_name': f"AmarDokan Order #{order_id}",
        'product_category': 'Ecommerce',
        'product_profile': 'general',
        'cus_name': customer_name,
        'cus_email': customer_email or f"customer_{timestamp_suffix}@amardokan.com",
        'cus_add1': customer_address or 'Dhaka, Bangladesh',
        'cus_city': customer_district or 'Dhaka',
        'cus_state': customer_district or 'Dhaka',
        'cus_postcode': '1200',
        'cus_country': 'Bangladesh',
        'cus_phone': customer_phone,
    }

    # Record initial transaction in DB (deferred checkout stores payload here until payment succeeds)
    raw_init_payload = {
        'initiate_request': post_data,
        'frontend_url': frontend_url,
    }
    if isinstance(checkout_payload, dict) and checkout_payload.get('items'):
        raw_init_payload['checkout_payload'] = checkout_payload

    transaction = PaymentTransaction.objects.create(
        order_id=order_id,
        tran_id=tran_id,
        amount=Decimal(f"{resolved_amount:.2f}"),
        currency='BDT',
        payment_method='SSLCOMMERZ',
        status='INITIATED',
        customer_name=customer_name,
        customer_phone=customer_phone,
        raw_payload=raw_init_payload,
    )

    initiate_url = SSLCOMMERZ_SANDBOX_GW_URL if config.is_sandbox else SSLCOMMERZ_LIVE_GW_URL

    try:
        response = requests.post(initiate_url, data=post_data, timeout=12)
        response_json = {}
        try:
            response_json = response.json()
        except Exception:
            logger.warning(f"SSLCommerz returned non-JSON response: {response.text[:200]}")

        if response.status_code == 200 and response_json.get('status') == 'SUCCESS':
            gateway_url = response_json.get('GatewayPageURL') or response_json.get('redirectGatewayURL')
            session_key = response_json.get('sessionkey', '')

            transaction.gateway_url = gateway_url
            transaction.session_key = session_key
            raw = transaction.raw_payload or {}
            raw['initiate_response'] = response_json
            transaction.raw_payload = raw
            transaction.save(update_fields=['gateway_url', 'session_key', 'raw_payload'])

            return {
                'success': True,
                'gatewayUrl': gateway_url,
                'tran_id': tran_id,
                'session_key': session_key,
                'order_id': order_id,
                'amount': resolved_amount,
                'is_sandbox': config.is_sandbox,
                'mode': 'LIVE_SANDBOX_API' if config.is_sandbox else 'LIVE_PRODUCTION_API'
            }
        else:
            failed_reason = response_json.get('failedreason') or f"Status: {response_json.get('status') or response.status_code}"
            logger.warning(f"SSLCommerz initiate rejected: {failed_reason}")
            transaction.error_reason = failed_reason
            raw = transaction.raw_payload or {}
            raw['initiate_response'] = response_json
            transaction.raw_payload = raw

            # Fallback to local Sandbox Simulator ONLY if Admin explicitly enabled allowSandboxSimulator AND in sandbox mode
            allow_sim = get_payment_controls().get('allowSandboxSimulator', False)
            if config.is_sandbox and allow_sim:
                simulator_url = f"{backend_url}/api/v1/payments/sslcommerz/sandbox-simulator/?tran_id={tran_id}"
                transaction.gateway_url = simulator_url
                transaction.save(update_fields=['error_reason', 'raw_payload', 'gateway_url'])
                return {
                    'success': True,
                    'gatewayUrl': simulator_url,
                    'tran_id': tran_id,
                    'order_id': order_id,
                    'amount': resolved_amount,
                    'is_sandbox': True,
                    'mode': 'SANDBOX_SIMULATOR',
                    'note': 'SSLCommerz sandbox API returned error; routed to Admin-enabled local sandbox simulator.'
                }

            transaction.status = 'FAILED'
            transaction.save(update_fields=['status', 'error_reason', 'raw_payload'])
            return {
                'success': False,
                'message': f"SSLCommerz initiation failed: {failed_reason}",
                'error': failed_reason,
            }

    except Exception as e:
        logger.exception(f"SSLCommerz connection error: {str(e)}")
        transaction.error_reason = f"Connection error: {str(e)}"

        # Route to local simulator ONLY when Admin explicitly enabled allowSandboxSimulator in sandbox mode
        allow_sim = get_payment_controls().get('allowSandboxSimulator', False)
        if config.is_sandbox and allow_sim:
            simulator_url = f"{backend_url}/api/v1/payments/sslcommerz/sandbox-simulator/?tran_id={tran_id}"
            transaction.gateway_url = simulator_url
            transaction.save(update_fields=['error_reason', 'gateway_url'])
            return {
                'success': True,
                'gatewayUrl': simulator_url,
                'tran_id': tran_id,
                'order_id': order_id,
                'amount': resolved_amount,
                'is_sandbox': True,
                'mode': 'SANDBOX_SIMULATOR',
                'note': 'SSLCommerz external sandbox was unreachable; routed to Admin-enabled local sandbox simulator.'
            }

        transaction.status = 'FAILED'
        transaction.save(update_fields=['status', 'error_reason'])
        return {
            'success': False,
            'message': f"Payment gateway connection timeout: {str(e)}",
            'error': str(e)
        }


def validate_sslcommerz_transaction(val_id, tran_id=None):
    """
    Validates a transaction using SSLCommerz Validation Server API.
    """
    config = get_gateway_config()
    allow_sim = get_payment_controls().get('allowSandboxSimulator', False)

    if not val_id:
        if config.is_sandbox and allow_sim and tran_id:
            return {
                'status': 'VALID',
                'val_id': f"SANDBOX-VAL-{tran_id}",
                'tran_id': tran_id,
                'bank_tran_id': f"SANDBOX-BANK-{tran_id}",
                'card_type': 'SSLCOMMERZ-Sandbox',
                'card_issuer': 'Sandbox Gateway Test Issuer',
                'simulated': True,
            }
        return {'status': 'FAILED', 'error': 'Missing val_id'}

    # Reject explicitly invalid validation tokens
    if str(val_id).strip().upper().startswith(('INVALID', 'WRONG', 'FAIL')):
        return {'status': 'FAILED', 'error': 'Invalid validation ID (val_id) provided.'}

    # If it's a simulated sandbox transaction, validate ONLY when sandbox simulator is active
    if str(val_id).startswith('SIM-') or str(val_id).startswith('SANDBOX-'):
        if not (config.is_sandbox and allow_sim):
            return {'status': 'FAILED', 'error': 'Simulated validation rejected in strict Live/No-Simulator mode.'}
        return {
            'status': 'VALID',
            'val_id': val_id,
            'tran_id': tran_id,
            'bank_tran_id': f"BANK-SIM-{int(time.time())}",
            'card_type': 'BKASH-Direct' if 'BKASH' in str(val_id) else ('VISA-CityBank' if 'VISA' in str(val_id) else 'NAGAD-Wallet'),
            'card_issuer': 'Sandbox Gateway Test Issuer',
            'simulated': True,
        }

    val_url = SSLCOMMERZ_SANDBOX_VALIDATION_URL if config.is_sandbox else SSLCOMMERZ_LIVE_VALIDATION_URL
    params = {
        'val_id': str(val_id).strip(),
        'store_id': config.store_id,
        'store_passwd': config.store_password,
        'format': 'json',
        'v': '1'
    }

    try:
        resp = requests.get(val_url, params=params, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') in ['INVALID_TRANSACTION', 'FAILED', 'CANCELLED']:
                return {
                    'status': 'FAILED',
                    'error': data.get('error') or f"Gateway returned {data.get('status')}",
                    'raw': data,
                }
            if config.is_sandbox and allow_sim and data.get('status') not in ['VALID', 'VALIDATED']:
                logger.info(f"Sandbox SSLCommerz validation fallback for {val_id}")
                return {
                    'status': 'VALID',
                    'val_id': val_id,
                    'tran_id': tran_id,
                    'bank_tran_id': data.get('bank_tran_id') or f"SANDBOX-VAL-{val_id}",
                    'card_type': data.get('card_type') or 'SSLCOMMERZ-Sandbox',
                    'card_issuer': data.get('card_issuer') or 'Sandbox Issuer',
                }
            return data
        else:
            logger.warning(f"SSLCommerz validator HTTP error: {resp.status_code} - {resp.text[:150]}")
            if config.is_sandbox and allow_sim:
                return {
                    'status': 'VALID',
                    'val_id': val_id,
                    'tran_id': tran_id,
                    'bank_tran_id': f"SANDBOX-VAL-{val_id}",
                    'card_type': 'SSLCOMMERZ-Sandbox',
                    'card_issuer': 'Sandbox Issuer',
                }
            return {'status': 'FAILED', 'error': f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"SSLCommerz validation request failed: {str(e)}")
        if config.is_sandbox and allow_sim:
            return {
                'status': 'VALID',
                'val_id': val_id or f"SANDBOX-VAL-{tran_id}",
                'tran_id': tran_id,
                'bank_tran_id': f"SANDBOX-VAL-{val_id or tran_id}",
                'card_type': 'SSLCOMMERZ-Sandbox',
                'card_issuer': 'Sandbox Issuer',
                'fallback': True,
            }
        return {'status': 'FAILED', 'error': str(e)}


def process_payment_success(data):
    """
    Handles payment success callback from SSLCommerz.
    Updates transaction, marks order as CONFIRMED & PAID, dispatches notifications.
    """
    tran_id = data.get('tran_id')
    if isinstance(tran_id, list) and tran_id:
        tran_id = tran_id[0]
    val_id = data.get('val_id')
    if isinstance(val_id, list) and val_id:
        val_id = val_id[0]
    amount_str = data.get('amount')
    if isinstance(amount_str, list) and amount_str:
        amount_str = amount_str[0]
    card_type = data.get('card_type') or data.get('card_brand') or 'SSLCOMMERZ-Card'
    if isinstance(card_type, list) and card_type:
        card_type = card_type[0]
    card_issuer = data.get('card_issuer') or ''
    if isinstance(card_issuer, list) and card_issuer:
        card_issuer = card_issuer[0]
    bank_tran_id = data.get('bank_tran_id') or ''
    if isinstance(bank_tran_id, list) and bank_tran_id:
        bank_tran_id = bank_tran_id[0]

    transaction = PaymentTransaction.objects.filter(tran_id=tran_id).first() if tran_id else None
    if not transaction and val_id:
        transaction = PaymentTransaction.objects.filter(val_id=val_id).first()

    if not transaction:
        val_status = 'UNKNOWN_TRANSACTION'
        val_result = {'status': val_status, 'error': f'No payment session found for TrxID: {tran_id}'}
        is_valid = False
    else:
        # Validate with SSLCommerz validation API
        val_result = validate_sslcommerz_transaction(val_id, tran_id)
        val_status = val_result.get('status')
        is_valid = val_status in ['VALID', 'VALIDATED']

    order_id = transaction.order_id if transaction else data.get('value_a') or data.get('order_id')
    order = Order.objects.filter(id=order_id).first() if order_id else None

    now_str = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')

    if transaction:
        transaction.val_id = val_id or transaction.val_id
        transaction.bank_tran_id = bank_tran_id or val_result.get('bank_tran_id') or transaction.bank_tran_id
        transaction.card_type = card_type or val_result.get('card_type') or transaction.card_type
        transaction.card_issuer = card_issuer or val_result.get('card_issuer') or transaction.card_issuer
        transaction.status = 'VALIDATED' if is_valid else 'FAILED'
        raw = transaction.raw_payload or {}
        raw['success_callback'] = dict(data)
        raw['validation_result'] = val_result
        transaction.raw_payload = raw
        if not is_valid:
            transaction.error_reason = f"Validation status: {val_status}"
        transaction.save()

    # Materialize deferred Order atomically ONLY when SSLCommerz payment succeeds
    auto_verified_flag = bool(is_valid)
    created_from_deferred = False
    if not order and is_valid and transaction and isinstance(transaction.raw_payload, dict):
        checkout_payload = transaction.raw_payload.get('checkout_payload')
        if isinstance(checkout_payload, dict) and checkout_payload.get('items'):
            from order.views import create_order_from_payload
            order, _, _ = create_order_from_payload(
                checkout_payload,
                order_id_override=transaction.order_id,
                payment_method_override='SSLCOMMERZ',
                payment_status_override='PAID',
                initial_status='PENDING',
                origin_note_override="Payment verified automatically. Ready for confirmation.",
                extra_address={
                    'paymentTrxId': tran_id,
                    'valId': val_id,
                    'paymentMethod': 'SSLCOMMERZ',
                    'cardType': card_type,
                    'verificationStatus': 'VERIFIED' if auto_verified_flag else 'PENDING_VERIFICATION',
                    'paymentVerified': auto_verified_flag,
                    'autoVerified': auto_verified_flag,
                },
                create_payment_txn=False,
            )
            created_from_deferred = bool(order)
            if order and transaction.order_id != order.id:
                transaction.order_id = order.id
                transaction.save(update_fields=['order_id'])

    if order:
        if is_valid:
            # Ensure order stays in PENDING until Admin confirms (SSLCommerz is auto-validated on server callback)
            if order.paymentStatus != 'PAID':
                order.paymentStatus = 'PAID'
            order.paymentMethod = 'SSLCOMMERZ'

            address = order.address if isinstance(order.address, dict) else {}
            address['paymentTrxId'] = tran_id
            address['valId'] = val_id
            address['paymentMethod'] = 'SSLCOMMERZ'
            address['cardType'] = card_type
            address['verificationStatus'] = 'VERIFIED'
            address['paymentVerified'] = True
            address['autoVerified'] = True
            order.address = address

            history = list(order.statusHistory) if isinstance(order.statusHistory, list) else []
            if not created_from_deferred:
                history.append({
                    'status': order.status,
                    'timestamp': now_str,
                    'note': "Payment verified automatically. Ready for confirmation.",
                    'updatedBy': 'Payment Verification'
                })
            order.statusHistory = history

            int_notes = list(order.internalNotes) if isinstance(order.internalNotes, list) else []
            int_notes.append(
                f"SSLCommerz Payment Received: ৳{order.total} via {card_type} ({card_issuer or 'N/A'}). BankTrx: {bank_tran_id}, ValID: {val_id}"
            )
            order.internalNotes = int_notes

            order.save()

            AuditLog.log(
                actor='SSLCommerz Gateway',
                role='SYSTEM',
                action='PAYMENT_RECEIVED',
                target_type='ORDER',
                target_id=order.id,
                details=f"Payment validated for ৳{order.total} (Method: SSLCOMMERZ, Card: {card_type}, TrxID: {tran_id}, ValID: {val_id})",
                ip_address='127.0.0.1'
            )

            try:
                from core.models import MarketingEvent
                import uuid
                if not MarketingEvent.objects.filter(orderId=order.id, eventName='Purchase').exists():
                    order_items = order.items if isinstance(order.items, list) else []
                    for idx, item in enumerate(order_items or [{'name': f'Order #{order.id}', 'quantity': 1, 'price': float(order.total or 0)}]):
                        if not isinstance(item, dict):
                            continue
                        qty = int(item.get('quantity') or 1)
                        MarketingEvent.objects.create(
                            id=f"mkt-{uuid.uuid4().hex[:8]}",
                            eventId=f"ssl_{tran_id}_{idx}",
                            eventName='Purchase',
                            timestamp=timezone.localtime().isoformat(),
                            browserReported=True,
                            serverCapiReported=True,
                            source='sslcommerz_gateway',
                            value=float(item.get('price') or 0) * qty if item.get('price') else float(order.total or 0),
                            orderId=order.id,
                            productName=str(item.get('name') or f'Order #{order.id}')[:255],
                            productId=str(item.get('productId') or item.get('id') or '')[:100],
                            dedupMatched=True,
                        )
            except Exception as mkt_err:
                logger.warning(f"Failed recording SSLCommerz Purchase MarketingEvent for {order.id}: {mkt_err}")
        else:
            order.paymentStatus = 'FAILED'
            address = order.address if isinstance(order.address, dict) else {}
            address['verificationStatus'] = 'NOT_VERIFIED'
            address['paymentVerified'] = False
            order.address = address
            history = list(order.statusHistory) if isinstance(order.statusHistory, list) else []
            history.append({
                'status': order.status,
                'timestamp': now_str,
                'note': f"SSLCommerz payment validation failed (Status: {val_status})",
                'updatedBy': 'SSLCommerz Gateway'
            })
            order.statusHistory = history
            order.save()

    return is_valid, order, transaction


def process_payment_failure(data):
    """
    Handles payment failure callback from SSLCommerz.
    """
    tran_id = data.get('tran_id')
    if isinstance(tran_id, list):
        tran_id = tran_id[0] if tran_id else ''
    failed_reason = data.get('failedreason') or data.get('error') or 'Payment transaction failed or declined.'
    if isinstance(failed_reason, list):
        failed_reason = failed_reason[0] if failed_reason else ''

    transaction = PaymentTransaction.objects.filter(tran_id=tran_id).first()
    order = None
    now_str = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')

    if transaction:
        transaction.status = 'FAILED'
        transaction.error_reason = failed_reason
        raw = transaction.raw_payload or {}
        raw['fail_callback'] = dict(data)
        transaction.raw_payload = raw
        transaction.save()

        order = Order.objects.filter(id=transaction.order_id).first()

    if order:
        order.paymentStatus = 'FAILED'
        history = list(order.statusHistory) if isinstance(order.statusHistory, list) else []
        history.append({
            'status': order.status,
            'timestamp': now_str,
            'note': f"SSLCommerz payment failed: {failed_reason}",
            'updatedBy': 'SSLCommerz Gateway'
        })
        order.statusHistory = history
        order.save()

        AuditLog.log(
            actor='SSLCommerz Gateway',
            role='SYSTEM',
            action='PAYMENT_FAILED',
            target_type='ORDER',
            target_id=order.id,
            details=f"Payment failed for TrxID {tran_id}. Reason: {failed_reason}",
            ip_address='127.0.0.1'
        )

    return False, order, transaction, failed_reason


def process_payment_cancel(data):
    """
    Handles payment cancellation callback from SSLCommerz.
    """
    tran_id = data.get('tran_id')
    if isinstance(tran_id, list):
        tran_id = tran_id[0] if tran_id else ''
    transaction = PaymentTransaction.objects.filter(tran_id=tran_id).first()
    order = None
    now_str = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')

    if transaction:
        transaction.status = 'CANCELLED'
        raw = transaction.raw_payload or {}
        raw['cancel_callback'] = dict(data)
        transaction.raw_payload = raw
        transaction.save()

        order = Order.objects.filter(id=transaction.order_id).first()

    if order:
        order.paymentStatus = 'FAILED'
        history = list(order.statusHistory) if isinstance(order.statusHistory, list) else []
        history.append({
            'status': order.status,
            'timestamp': now_str,
            'note': "Customer cancelled the SSLCommerz payment window (Payment Cancelled)",
            'updatedBy': 'Customer'
        })
        order.statusHistory = history
        order.save()

        AuditLog.log(
            actor='Customer',
            role='SYSTEM',
            action='PAYMENT_CANCELLED',
            target_type='ORDER',
            target_id=order.id,
            details=f"Payment cancelled by user for TrxID {tran_id} (Order #{order.id})",
            ip_address='127.0.0.1'
        )

    return False, order, transaction

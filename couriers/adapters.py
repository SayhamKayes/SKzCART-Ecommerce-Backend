import os
import random
import logging
import requests
from django.utils import timezone
from core.utils import normalize_bangladesh_phone

logger = logging.getLogger(__name__)

class BaseCourierAdapter:
    def __init__(self, provider_id, name):
        self.provider_id = provider_id
        self.name = name

    def create_shipment(self, order, item_weight_kg=0.5, special_instruction=""):
        raise NotImplementedError

    def parse_webhook(self, payload):
        raise NotImplementedError


class PathaoAdapter(BaseCourierAdapter):
    BASE_URL = "https://api-hermes.pathao.com"
    SANDBOX_URL = "https://courier-api-sandbox.pathao.com"

    def __init__(self):
        super().__init__('pathao', 'Pathao Courier')

    def get_credentials(self):
        from .models import CourierConfig
        config = CourierConfig.objects.filter(id='pathao').first()
        extra = (config.extraCredentials if config and isinstance(config.extraCredentials, dict) else {})
        client_id = (config.apiKey if config and config.apiKey else '') or os.getenv('PATHAO_CLIENT_ID', '')
        client_secret = (config.secret if config and config.secret else '') or os.getenv('PATHAO_CLIENT_SECRET', '')
        username = extra.get('username') or os.getenv('PATHAO_USERNAME', '')
        password = extra.get('password') or os.getenv('PATHAO_PASSWORD', '')
        store_id = str(extra.get('store_id') or os.getenv('PATHAO_STORE_ID', '1')).strip() or '1'
        return client_id.strip(), client_secret.strip(), str(username).strip(), str(password).strip(), store_id

    def get_access_token(self):
        client_id, client_secret, username, password, _ = self.get_credentials()

        if not (client_id and client_secret and username and password):
            return None, "Pathao requires Client ID, Client Secret, Account Username/Email, and Password."

        try:
            resp = requests.post(
                f"{self.BASE_URL}/aladdin/api/v1/issue-token",
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "username": username,
                    "password": password,
                    "grant_type": "password"
                },
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=12
            )
            data = resp.json() if resp.text else {}
            if resp.status_code == 200 and data.get('access_token'):
                return data.get('access_token'), None
            err = data.get('message') or data.get('error_description') or f"HTTP {resp.status_code}"
            return None, f"Pathao auth failed: {err}"
        except Exception as e:
            logger.error(f"Pathao issue-token failed: {e}")
            return None, f"Pathao connection error: {str(e)}"

    def create_shipment(self, order, item_weight_kg=0.5, special_instruction=""):
        client_id, client_secret, _, _, store_id = self.get_credentials()
        district = order.address.get('district', '') if isinstance(order.address, dict) else ''
        is_dhaka = str(district).strip().lower() == 'dhaka'
        delivery_fee = 60 if is_dhaka else 130
        cod_amount = order.total if order.paymentMethod == 'COD' else 0

        address_dict = order.address if isinstance(order.address, dict) else {}
        recipient_name = address_dict.get('customerName') or 'Valued Customer'
        raw_phone = address_dict.get('phone') or ''
        recipient_phone = normalize_bangladesh_phone(raw_phone)
        full_address = address_dict.get('fullAddress') or f"{address_dict.get('thana', '')}, {district}"
        note = special_instruction or address_dict.get('orderNote') or 'Please call before delivery.'

        is_mock_key = client_id in ['', 'pk_live_pathao_merchant_92813'] or client_secret in ['', 'ps_secret_key_847192738']

        # If live credentials exist, attempt real Pathao API call (fail loudly instead of faking if auth/booking fails)
        if not is_mock_key:
            token, auth_err = self.get_access_token()
            if not token:
                raise ValueError(auth_err or "Pathao authentication failed. Check Pathao credentials in Courier Hub.")
            if True:
                payload = {
                    "store_id": int(store_id) if str(store_id).isdigit() else 1,
                    "merchant_order_id": str(order.id),
                    "recipient_name": recipient_name,
                    "recipient_phone": recipient_phone,
                    "recipient_address": full_address,
                    "recipient_city": 1,
                    "recipient_zone": 1,
                    "delivery_type": 48,
                    "item_type": 2,
                    "special_instruction": note,
                    "item_quantity": 1,
                    "item_weight": float(item_weight_kg),
                    "amount_to_collect": int(cod_amount)
                }
                try:
                    resp = requests.post(
                        f"{self.BASE_URL}/aladdin/api/v1/orders",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json"
                        },
                        timeout=15
                    )
                    data = resp.json() if resp.text else {}
                    if resp.status_code in [200, 201] and (data.get('type') == 'success' or data.get('data')):
                        c_data = data.get('data', {})
                        consignment_id = str(c_data.get('consignment_id') or c_data.get('order_id') or '')
                        tracking_id = consignment_id
                        return {
                            'provider': 'pathao',
                            'trackingId': tracking_id,
                            'consignmentId': consignment_id,
                            'courierStatus': 'Order Placed at Pathao Hub',
                            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
                            'deliveryFee': delivery_fee,
                            'codAmount': cod_amount,
                            'trackingUrl': f"https://pathao.com/courier/tracking?consignment={consignment_id}",
                            'hubLocation': 'Tejgaon Central Sorting Hub' if is_dhaka else 'Dhaka Transit Departure Hub',
                            'assignedRiderName': 'Pathao Fleet Agent',
                            'assignedRiderPhone': '01700123456',
                            'liveApi': True,
                        }
                    else:
                        err_msg = data.get('message') or resp.text or 'Pathao order booking failed'
                        logger.error(f"Pathao create order error: {err_msg}")
                        raise ValueError(f"Pathao Error: {err_msg}")
                except requests.RequestException as e:
                    logger.error(f"Pathao network error: {e}")
                    raise ConnectionError(f"Pathao connection failed: {str(e)}")

        # Sandbox / Demonstration Fallback
        prefix = (district[:3].upper() if len(district) >= 3 else 'DHK')
        tracking_id = f"PTH-{prefix}-{random.randint(100000, 999999)}"
        consignment_id = f"PTH-CNS-{random.randint(1000000, 9999999)}"
        rider_suffix = random.randint(100000, 999999)

        return {
            'provider': 'pathao',
            'trackingId': tracking_id,
            'consignmentId': consignment_id,
            'courierStatus': 'Order Placed at Pathao Hub (Sandbox/Demo)',
            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
            'deliveryFee': delivery_fee,
            'codAmount': cod_amount,
            'trackingUrl': f"https://pathao.com/courier/tracking?consignment={consignment_id}",
            'hubLocation': 'Tejgaon Central Sorting Hub' if is_dhaka else 'Dhaka Transit Departure Hub',
            'assignedRiderName': 'Pathao Fleet Agent',
            'assignedRiderPhone': f"01700{rider_suffix}",
            'liveApi': False,
        }

    def get_balance(self):
        client_id, client_secret, _, _, _ = self.get_credentials()
        if not client_id or client_id == 'pk_live_pathao_merchant_92813' or client_secret in ['', 'ps_secret_key_847192738']:
            return {
                'success': True,
                'isDemo': True,
                'current_balance': 12650,
                'message': 'Demo Pathao Remittance Balance (Configure live Client ID, Secret, Email & Password in settings)'
            }
        token, auth_err = self.get_access_token()
        if not token:
            return {
                'success': False,
                'isDemo': False,
                'current_balance': 0,
                'message': auth_err or 'Pathao live authentication failed'
            }
        return {
            'success': True,
            'isDemo': False,
            'current_balance': 0,
            'balanceUnsupported': True,
            'message': 'Pathao OAuth2 live token authenticated successfully (Pathao API does not expose wallet balance endpoint).'
        }

    def check_status(self, tracking_code):
        return {'status': 200, 'tracking_id': tracking_code, 'provider': 'pathao'}

    def parse_webhook(self, payload):
        event = str(payload.get('event') or payload.get('order_status') or payload.get('status') or '').lower().strip()
        mapping = {
            'order.delivered': ('DELIVERED', 'Parcel successfully delivered to recipient (Pathao)'),
            'delivered': ('DELIVERED', 'Parcel successfully delivered to recipient (Pathao)'),
            'order.in_transit': ('IN_TRANSIT', 'Parcel in transit to destination hub (Pathao)'),
            'in_transit': ('IN_TRANSIT', 'Parcel in transit to destination hub (Pathao)'),
            'order.picked_up': ('IN_TRANSIT', 'Rider picked up parcel from merchant (Pathao)'),
            'picked_up': ('IN_TRANSIT', 'Rider picked up parcel from merchant (Pathao)'),
            'order.out_for_delivery': ('OUT_FOR_DELIVERY', 'Rider out for delivery to recipient (Pathao)'),
            'out_for_delivery': ('OUT_FOR_DELIVERY', 'Rider out for delivery to recipient (Pathao)'),
            'order.returned': ('RETURNED', 'Parcel returned to merchant (Pathao)'),
            'returned': ('RETURNED', 'Parcel returned to merchant (Pathao)'),
            'order.cancelled': ('CANCELLED', 'Parcel booking cancelled (Pathao)'),
            'cancelled': ('CANCELLED', 'Parcel booking cancelled (Pathao)'),
        }
        order_status, notes = mapping.get(event, ('IN_TRANSIT', f"Pathao status updated: {event}"))
        return {
            'orderStatus': order_status,
            'collectedAmount': payload.get('collected_amount') or payload.get('cod_amount') or 0,
            'notes': notes,
        }


class SteadfastAdapter(BaseCourierAdapter):
    BASE_URL = "https://portal.steadfast.com.bd/api/v1"

    def __init__(self):
        super().__init__('steadfast', 'Steadfast Courier')

    def get_credentials(self):
        from .models import CourierConfig
        config = CourierConfig.objects.filter(id='steadfast').first()
        api_key = (config.apiKey if config and config.apiKey else '') or os.getenv('STEADFAST_API_KEY', '')
        secret_key = (config.secret if config and config.secret else '') or os.getenv('STEADFAST_SECRET_KEY', '')
        return api_key.strip(), secret_key.strip()

    def get_headers(self):
        api_key, secret_key = self.get_credentials()
        return {
            'Api-Key': api_key,
            'Secret-Key': secret_key,
            'Content-Type': 'application/json'
        }

    def create_shipment(self, order, item_weight_kg=0.5, special_instruction=""):
        api_key, secret_key = self.get_credentials()
        district = order.address.get('district', '') if isinstance(order.address, dict) else ''
        is_dhaka = str(district).strip().lower() == 'dhaka'
        delivery_fee = 60 if is_dhaka else 130
        cod_amount = order.total if order.paymentMethod == 'COD' else 0

        address_dict = order.address if isinstance(order.address, dict) else {}
        recipient_name = address_dict.get('customerName') or 'Valued Customer'
        raw_phone = address_dict.get('phone') or ''
        recipient_phone = normalize_bangladesh_phone(raw_phone)
        full_address = address_dict.get('fullAddress') or f"{address_dict.get('thana', '')}, {district}"
        note = special_instruction or address_dict.get('orderNote') or 'Please call before delivery.'

        is_mock_key = api_key in ['', 'stf_live_api_88492010'] or secret_key in ['', 'stf_sec_019283849']

        # If a real merchant key is provided, execute real HTTP POST request to Steadfast
        if not is_mock_key:
            payload = {
                'invoice': str(order.id),
                'recipient_name': recipient_name,
                'recipient_phone': recipient_phone,
                'recipient_address': full_address,
                'cod_amount': int(cod_amount),
                'note': note
            }
            try:
                resp = requests.post(
                    f"{self.BASE_URL}/create_order",
                    json=payload,
                    headers=self.get_headers(),
                    timeout=15
                )
                data = resp.json() if resp.text else {}
                if resp.status_code == 200 and data.get('status') == 200:
                    consignment = data.get('consignment', {})
                    tracking_code = str(consignment.get('tracking_code') or '')
                    consignment_id = str(consignment.get('consignment_id') or '')
                    return {
                        'provider': 'steadfast',
                        'trackingId': tracking_code,
                        'consignmentId': consignment_id,
                        'courierStatus': 'Consignment Created at Steadfast',
                        'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
                        'deliveryFee': delivery_fee,
                        'codAmount': cod_amount,
                        'trackingUrl': f"https://steadfast.com.bd/tracking/{tracking_code}",
                        'hubLocation': 'Motijheel Central Distribution Hub' if is_dhaka else 'District Delivery Station',
                        'assignedRiderName': 'Steadfast Delivery Executive',
                        'assignedRiderPhone': '01811000000',
                        'liveApi': True,
                    }
                else:
                    err_msg = data.get('message') or resp.text or f"HTTP status {resp.status_code}"
                    logger.error(f"Steadfast create_order failed: {err_msg}")
                    raise ValueError(f"Steadfast Error: {err_msg}")
            except requests.RequestException as e:
                logger.error(f"Steadfast network error: {e}")
                raise ConnectionError(f"Steadfast connection failed: {str(e)}")

        # Sandbox / Demonstration Fallback
        tracking_id = f"STDF-{random.randint(20000000, 99999999)}"
        consignment_id = f"CID-{random.randint(100000, 999999)}"
        rider_suffix = random.randint(100000, 999999)

        return {
            'provider': 'steadfast',
            'trackingId': tracking_id,
            'consignmentId': consignment_id,
            'courierStatus': 'Consignment Created at Steadfast (Sandbox/Demo)',
            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
            'deliveryFee': delivery_fee,
            'codAmount': cod_amount,
            'trackingUrl': f"https://steadfast.com.bd/tracking/{tracking_id}",
            'hubLocation': 'Motijheel Distribution Hub' if is_dhaka else 'District Delivery Station',
            'assignedRiderName': 'Steadfast Express Executive',
            'assignedRiderPhone': f"01811{rider_suffix}",
            'liveApi': False,
        }

    def get_balance(self):
        api_key, secret_key = self.get_credentials()
        if not api_key or api_key == 'stf_live_api_88492010':
            return {
                'success': True,
                'isDemo': True,
                'current_balance': 14850,
                'message': 'Demo Steadfast Balance (Configure your real API key in settings)'
            }
        try:
            resp = requests.get(
                f"{self.BASE_URL}/get_balance",
                headers=self.get_headers(),
                timeout=10
            )
            data = resp.json() if resp.text else {}
            if resp.status_code == 200 and data.get('status') == 200:
                return {
                    'success': True,
                    'isDemo': False,
                    'current_balance': data.get('current_balance', 0),
                    'message': 'Live balance fetched successfully'
                }
            return {
                'success': False,
                'message': data.get('message') or f"Status {resp.status_code}"
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def check_status(self, tracking_code):
        try:
            resp = requests.get(
                f"{self.BASE_URL}/status_by_trackingcode/{tracking_code}",
                headers=self.get_headers(),
                timeout=10
            )
            data = resp.json() if resp.text else {}
            return data
        except Exception as e:
            return {'status': 500, 'message': str(e)}

    def parse_webhook(self, payload):
        raw_status = str(payload.get('status') or payload.get('event') or '').lower().strip()
        mapping = {
            'delivered': ('DELIVERED', 'Parcel successfully delivered to recipient (Steadfast)'),
            'partial_delivered': ('DELIVERED', 'Parcel partially delivered (Steadfast)'),
            'delivered_approval_pending': ('DELIVERED', 'Delivered, pending settlement approval (Steadfast)'),
            'cancelled': ('CANCELLED', 'Parcel booking cancelled (Steadfast)'),
            'hold': ('ON_HOLD', 'Parcel temporarily held at Steadfast hub'),
            'in_review': ('COURIER_CREATED', 'Consignment created and under review (Steadfast)'),
            'pending': ('COURIER_CREATED', 'Awaiting rider pickup (Steadfast)'),
            'picked_up': ('IN_TRANSIT', 'Rider picked up parcel from merchant hub (Steadfast)'),
            'in_transit': ('IN_TRANSIT', 'Parcel in transit to destination hub (Steadfast)'),
            'out_for_delivery': ('OUT_FOR_DELIVERY', 'Delivery rider is out for delivery (Steadfast)'),
            'return': ('RETURNED', 'Parcel returned as recipient was unavailable'),
            'returned': ('RETURNED', 'Parcel returned to merchant (Steadfast)'),
        }
        order_status, notes = mapping.get(raw_status, ('IN_TRANSIT', f"Steadfast status updated: {raw_status}"))
        return {
            'orderStatus': order_status,
            'collectedAmount': payload.get('cod_amount') or payload.get('collected_amount') or 0,
            'notes': notes,
        }


class RedxAdapter(BaseCourierAdapter):
    BASE_URL = "https://openapi.redx.com.bd/v1.0.0-beta"

    def __init__(self):
        super().__init__('redx', 'RedX Logistics')

    def get_credentials(self):
        from .models import CourierConfig
        config = CourierConfig.objects.filter(id='redx').first()
        extra = (config.extraCredentials if config and isinstance(config.extraCredentials, dict) else {})
        token = (config.apiKey if config and config.apiKey else '') or os.getenv('REDX_API_TOKEN', '')
        area_id = int(extra.get('delivery_area_id') or 1) if str(extra.get('delivery_area_id') or '').isdigit() else 1
        return token.strip(), area_id

    def create_shipment(self, order, item_weight_kg=0.5, special_instruction=""):
        token, delivery_area_id = self.get_credentials()
        district = order.address.get('district', '') if isinstance(order.address, dict) else ''
        is_dhaka = str(district).strip().lower() == 'dhaka'
        delivery_fee = 60 if is_dhaka else 130
        cod_amount = order.total if order.paymentMethod == 'COD' else 0

        address_dict = order.address if isinstance(order.address, dict) else {}
        recipient_name = address_dict.get('customerName') or 'Valued Customer'
        raw_phone = address_dict.get('phone') or ''
        recipient_phone = normalize_bangladesh_phone(raw_phone)
        full_address = address_dict.get('fullAddress') or f"{address_dict.get('thana', '')}, {district}"
        note = special_instruction or address_dict.get('orderNote') or 'Please call before delivery.'

        is_mock_key = token in ['', 'redx_token_live_738192']

        # If live token configured, execute real RedX API request
        if not is_mock_key:
            payload = {
                "customer_name": recipient_name,
                "customer_phone": recipient_phone,
                "delivery_area": district or "Dhaka",
                "delivery_area_id": delivery_area_id,
                "customer_address": full_address,
                "merchant_invoice_id": str(order.id),
                "cash_collection_amount": int(cod_amount),
                "parcel_weight": int(float(item_weight_kg) * 1000),
                "instruction": note,
                "value": int(order.total)
            }
            try:
                resp = requests.post(
                    f"{self.BASE_URL}/parcels",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"
                    },
                    timeout=15
                )
                data = resp.json() if resp.text else {}
                if resp.status_code in [200, 201] and data.get('tracking_id'):
                    tracking_id = str(data.get('tracking_id'))
                    return {
                        'provider': 'redx',
                        'trackingId': tracking_id,
                        'consignmentId': tracking_id,
                        'courierStatus': 'Consignment Booked in RedX Network',
                        'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
                        'deliveryFee': delivery_fee,
                        'codAmount': cod_amount,
                        'trackingUrl': f"https://redx.com.bd/track/{tracking_id}",
                        'hubLocation': 'Gazipur Central Fulfilment' if is_dhaka else 'Regional Logistics Node',
                        'assignedRiderName': 'RedX Delivery Executive',
                        'assignedRiderPhone': '01912000000',
                        'liveApi': True,
                    }
                else:
                    err_msg = data.get('message') or resp.text or 'RedX parcel booking failed'
                    logger.error(f"RedX create order error: {err_msg}")
                    raise ValueError(f"RedX Error: {err_msg}")
            except requests.RequestException as e:
                logger.error(f"RedX network error: {e}")
                raise ConnectionError(f"RedX connection failed: {str(e)}")

        # Sandbox / Demonstration Fallback
        tracking_id = f"REDX-{random.randint(100000000, 999999999)}"
        consignment_id = f"RC-{random.randint(100000, 999999)}"
        rider_suffix = random.randint(100000, 999999)

        return {
            'provider': 'redx',
            'trackingId': tracking_id,
            'consignmentId': consignment_id,
            'courierStatus': 'Consignment Booked in RedX Network (Sandbox/Demo)',
            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
            'deliveryFee': delivery_fee,
            'codAmount': cod_amount,
            'trackingUrl': f"https://redx.com.bd/track/{tracking_id}",
            'hubLocation': 'Gazipur Central Fulfilment' if is_dhaka else 'Regional Logistics Node',
            'assignedRiderName': 'RedX Delivery Rider',
            'assignedRiderPhone': f"01912{rider_suffix}",
            'liveApi': False,
        }

    def get_balance(self):
        token, _ = self.get_credentials()
        if not token or token == 'redx_token_live_738192':
            return {
                'success': True,
                'isDemo': True,
                'current_balance': 6820,
                'message': 'Demo RedX Wallet Balance (Configure API Token in settings)'
            }
        try:
            resp = requests.get(
                f"{self.BASE_URL}/pickup/stores",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if resp.status_code == 200:
                return {
                    'success': True,
                    'isDemo': False,
                    'current_balance': 0,
                    'balanceUnsupported': True,
                    'message': 'RedX live API token verified against pickup stores endpoint.'
                }
            return {
                'success': False,
                'isDemo': False,
                'message': f"RedX authentication failed (HTTP {resp.status_code})"
            }
        except Exception as e:
            return {'success': False, 'isDemo': False, 'message': str(e)}

    def check_status(self, tracking_code):
        token, _ = self.get_credentials()
        if not token or token == 'redx_token_live_738192':
            return {'status': 200, 'tracking_id': tracking_code, 'provider': 'redx'}
        try:
            resp = requests.get(
                f"{self.BASE_URL}/parcels/{tracking_code}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            return resp.json() if resp.text else {'status': resp.status_code}
        except Exception as e:
            return {'status': 500, 'message': str(e)}

    def parse_webhook(self, payload):
        raw_status = str(payload.get('status') or payload.get('parcel_status') or payload.get('event') or '').lower().strip()
        mapping = {
            'delivered': ('DELIVERED', 'Parcel successfully delivered to recipient (RedX)'),
            'in_transit': ('IN_TRANSIT', 'Parcel in RedX transit hub'),
            'pickup_completed': ('IN_TRANSIT', 'RedX rider picked up parcel from merchant'),
            'out_for_delivery': ('OUT_FOR_DELIVERY', 'RedX rider out for delivery to recipient'),
            'returned_to_merchant': ('RETURNED', 'Parcel returned to merchant (RedX)'),
            'returned': ('RETURNED', 'Parcel returned to merchant (RedX)'),
            'cancelled': ('CANCELLED', 'Parcel booking cancelled (RedX)'),
        }
        order_status, notes = mapping.get(raw_status, ('IN_TRANSIT', f"RedX status updated: {raw_status}"))
        return {
            'orderStatus': order_status,
            'collectedAmount': payload.get('amount_collected') or payload.get('collected_amount') or payload.get('cod_amount') or 0,
            'notes': notes,
        }


class ECourierAdapter(BaseCourierAdapter):
    BASE_URL = "https://backoffice.ecourier.com.bd/api"

    def __init__(self):
        super().__init__('ecourier', 'eCourier')

    def get_credentials(self):
        from .models import CourierConfig
        config = CourierConfig.objects.filter(id='ecourier').first()
        extra = (config.extraCredentials if config and isinstance(config.extraCredentials, dict) else {})
        api_key = (config.apiKey if config and config.apiKey else '') or os.getenv('ECOURIER_API_KEY', '')
        secret = (config.secret if config and config.secret else '') or os.getenv('ECOURIER_API_SECRET', '')
        user_id = str(extra.get('user_id') or os.getenv('ECOURIER_USER_ID', '')).strip()
        package_code = str(extra.get('package_code') or os.getenv('ECOURIER_PACKAGE_CODE', 'PKG-01')).strip() or 'PKG-01'
        return api_key.strip(), secret.strip(), user_id, package_code

    def create_shipment(self, order, item_weight_kg=0.5, special_instruction=""):
        api_key, secret, user_id, package_code = self.get_credentials()
        district = order.address.get('district', '') if isinstance(order.address, dict) else ''
        thana = order.address.get('thana', '') if isinstance(order.address, dict) else ''
        is_dhaka = str(district).strip().lower() == 'dhaka'
        delivery_fee = 60 if is_dhaka else 130
        cod_amount = order.total if order.paymentMethod == 'COD' else 0

        address_dict = order.address if isinstance(order.address, dict) else {}
        recipient_name = address_dict.get('customerName') or 'Valued Customer'
        raw_phone = address_dict.get('phone') or ''
        recipient_phone = normalize_bangladesh_phone(raw_phone)
        full_address = address_dict.get('fullAddress') or f"{thana}, {district}"
        note = special_instruction or address_dict.get('orderNote') or 'Please call before delivery.'

        is_mock_key = api_key in ['', 'ec_usr_849201'] or secret in ['', 'ec_key_192837']

        # If live credentials exist, execute real eCourier API call
        if not is_mock_key:
            if not user_id:
                raise ValueError("eCourier requires User ID alongside API Key & API Secret in Courier Hub.")
            payload = {
                "recipient_name": recipient_name,
                "recipient_mobile": recipient_phone,
                "recipient_address": full_address,
                "recipient_district": district or "Dhaka",
                "recipient_thana": thana or "Mirpur",
                "package_code": package_code,
                "product_price": int(order.total),
                "cash_collection_amount": int(cod_amount),
                "invoice": str(order.id),
                "parcel_detail": note
            }
            try:
                resp = requests.post(
                    f"{self.BASE_URL}/order-place",
                    json=payload,
                    headers={
                        "API-KEY": api_key,
                        "API-SECRET": secret,
                        "USER-ID": user_id,
                        "Content-Type": "application/json"
                    },
                    timeout=15
                )
                data = resp.json() if resp.text else {}
                if resp.status_code in [200, 201] and (data.get('success') or data.get('tracking_id') or data.get('ID')):
                    tracking_id = str(data.get('tracking_id') or data.get('ID') or data.get('id') or '')
                    return {
                        'provider': 'ecourier',
                        'trackingId': tracking_id,
                        'consignmentId': tracking_id,
                        'courierStatus': 'Manifested with eCourier',
                        'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
                        'deliveryFee': delivery_fee,
                        'codAmount': cod_amount,
                        'trackingUrl': f"https://ecourier.com.bd/track/{tracking_id}",
                        'hubLocation': 'Kakrail Delivery Office',
                        'assignedRiderName': 'eCourier Express Agent',
                        'assignedRiderPhone': '01620000000',
                        'liveApi': True,
                    }
                else:
                    err_msg = data.get('message') or resp.text or 'eCourier booking failed'
                    logger.error(f"eCourier error: {err_msg}")
                    raise ValueError(f"eCourier Error: {err_msg}")
            except requests.RequestException as e:
                logger.error(f"eCourier network error: {e}")
                raise ConnectionError(f"eCourier connection failed: {str(e)}")

        # Sandbox / Demonstration Fallback
        tracking_id = f"EC-{random.randint(1000000, 9999999)}"
        consignment_id = f"EC-CNG-{random.randint(10000, 99999)}"
        rider_suffix = random.randint(100000, 999999)

        return {
            'provider': 'ecourier',
            'trackingId': tracking_id,
            'consignmentId': consignment_id,
            'courierStatus': 'Manifested with eCourier (Sandbox/Demo)',
            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
            'deliveryFee': delivery_fee,
            'codAmount': cod_amount,
            'trackingUrl': f"https://ecourier.com.bd/track/{tracking_id}",
            'hubLocation': 'Kakrail Delivery Office',
            'assignedRiderName': 'eCourier Express Agent',
            'assignedRiderPhone': f"01620{rider_suffix}",
            'liveApi': False,
        }

    def get_balance(self):
        api_key, secret, user_id, _ = self.get_credentials()
        if not api_key or api_key == 'ec_usr_849201' or secret in ['', 'ec_key_192837']:
            return {
                'success': True,
                'isDemo': True,
                'current_balance': 4150,
                'message': 'Demo eCourier Balance (Configure API Key, Secret & User ID in settings)'
            }
        if not user_id:
            return {
                'success': False,
                'isDemo': False,
                'message': 'eCourier User ID is missing.'
            }
        try:
            resp = requests.post(
                f"{self.BASE_URL}/packages",
                headers={
                    "API-KEY": api_key,
                    "API-SECRET": secret,
                    "USER-ID": user_id,
                    "Content-Type": "application/json"
                },
                timeout=10
            )
            if resp.status_code == 200:
                return {
                    'success': True,
                    'isDemo': False,
                    'current_balance': 0,
                    'balanceUnsupported': True,
                    'message': 'eCourier live credentials verified against packages endpoint.'
                }
            return {
                'success': False,
                'isDemo': False,
                'message': f"eCourier auth failed (HTTP {resp.status_code})"
            }
        except Exception as e:
            return {'success': False, 'isDemo': False, 'message': str(e)}

    def check_status(self, tracking_code):
        return {'status': 200, 'tracking_id': tracking_code, 'provider': 'ecourier'}

    def parse_webhook(self, payload):
        raw_status = str(payload.get('status') or payload.get('parcel_status') or payload.get('event') or '').lower().strip()
        mapping = {
            'delivered': ('DELIVERED', 'Parcel successfully delivered via eCourier'),
            'in-transit': ('IN_TRANSIT', 'Parcel in destination transit hub (eCourier)'),
            'in_transit': ('IN_TRANSIT', 'Parcel in destination transit hub (eCourier)'),
            'out for delivery': ('OUT_FOR_DELIVERY', 'eCourier rider out for delivery'),
            'out_for_delivery': ('OUT_FOR_DELIVERY', 'eCourier rider out for delivery'),
            'returned': ('RETURNED', 'Parcel returned to merchant (eCourier)'),
            'return': ('RETURNED', 'Parcel returned to merchant (eCourier)'),
            'cancelled': ('CANCELLED', 'Parcel booking cancelled (eCourier)'),
        }
        order_status, notes = mapping.get(raw_status, ('IN_TRANSIT', f"eCourier status updated: {raw_status}"))
        return {
            'orderStatus': order_status,
            'collectedAmount': payload.get('collected_amount') or payload.get('payment_amount') or payload.get('cod_amount') or 0,
            'notes': notes,
        }


courier_adapters = {
    'pathao': PathaoAdapter(),
    'steadfast': SteadfastAdapter(),
    'redx': RedxAdapter(),
    'ecourier': ECourierAdapter(),
}

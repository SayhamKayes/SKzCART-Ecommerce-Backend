import os
import random
import logging
import requests
from django.utils import timezone
from core.utils import normalize_bangladesh_phone

logger = logging.getLogger(__name__)

class BaseCourierAdapter:
    LEGACY_PLACEHOLDER_KEYS = {
        '',
        'pk_live_pathao_merchant_92813',
        'ps_secret_key_847192738',
        'stf_live_api_88492010',
        'stf_sec_019283849',
        'redx_token_live_738192',
        'redx_hash_918237',
        'ec_usr_849201',
        'ec_key_192837',
    }

    def __init__(self, provider_id, name):
        self.provider_id = provider_id
        self.name = name

    def get_provider_config(self):
        from .models import CourierConfig
        return CourierConfig.objects.filter(id=self.provider_id).first()

    def has_live_credential(self, value):
        cleaned = str(value or '').strip()
        return bool(cleaned and cleaned not in self.LEGACY_PLACEHOLDER_KEYS)

    def is_sandbox_mode(self, config=None):
        cfg = config or self.get_provider_config()
        extra = cfg.extraCredentials if (cfg and isinstance(cfg.extraCredentials, dict)) else {}
        return bool(extra.get('sandboxMode', False))

    def get_dynamic_fee(self, config, is_dhaka):
        if config:
            return int(config.insideDhakaRate or 0) if is_dhaka else int(config.outsideDhakaRate or 0)
        return 0

    def create_shipment(self, order, item_weight_kg=0.5, special_instruction=""):
        raise NotImplementedError

    def parse_webhook(self, payload):
        raise NotImplementedError

    def extract_rider_info(self, payload):
        """Extract live rider name, phone, and hub location from courier API/webhook payloads if present."""
        if not isinstance(payload, dict):
            return {}
        nested_data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
        delivery_man = (
            payload.get('delivery_man')
            or payload.get('rider')
            or nested_data.get('delivery_man')
            or nested_data.get('rider')
            or {}
        )
        dm_dict = delivery_man if isinstance(delivery_man, dict) else {}

        rider_name = str(
            payload.get('rider_name')
            or payload.get('delivery_man_name')
            or payload.get('driver_name')
            or payload.get('courier_man_name')
            or nested_data.get('rider_name')
            or nested_data.get('delivery_man_name')
            or dm_dict.get('name')
            or (delivery_man if isinstance(delivery_man, str) else '')
            or ''
        ).strip()

        rider_phone = str(
            payload.get('rider_phone')
            or payload.get('delivery_man_phone')
            or payload.get('delivery_man_mobile')
            or payload.get('driver_phone')
            or payload.get('courier_man_phone')
            or nested_data.get('rider_phone')
            or nested_data.get('delivery_man_phone')
            or dm_dict.get('phone')
            or dm_dict.get('mobile')
            or ''
        ).strip()

        hub_location = str(
            payload.get('hub_name')
            or payload.get('sorting_hub')
            or payload.get('branch_name')
            or nested_data.get('hub_name')
            or nested_data.get('branch_name')
            or ''
        ).strip()

        return {
            'assignedRiderName': rider_name,
            'assignedRiderPhone': normalize_bangladesh_phone(rider_phone) if rider_phone else '',
            'hubLocation': hub_location,
        }


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
        store_id = str(extra.get('store_id') or os.getenv('PATHAO_STORE_ID', '')).strip()
        use_official_sandbox = bool(extra.get('useOfficialSandbox', False))
        base_url = self.SANDBOX_URL if use_official_sandbox else self.BASE_URL
        return client_id.strip(), client_secret.strip(), str(username).strip(), str(password).strip(), store_id, base_url

    def get_access_token(self):
        client_id, client_secret, username, password, _, base_url = self.get_credentials()

        if not (client_id and client_secret and username and password):
            return None, "Pathao requires Client ID, Client Secret, Account Username/Email, and Password."

        try:
            resp = requests.post(
                f"{base_url}/aladdin/api/v1/issue-token",
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

    def resolve_default_store_id(self, token, base_url, configured_store_id):
        if configured_store_id and str(configured_store_id).isdigit():
            return int(configured_store_id)
        try:
            resp = requests.get(
                f"{base_url}/aladdin/api/v1/stores",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=8,
            )
            if resp.status_code == 200:
                stores = resp.json().get('data', {}).get('data', [])
                if isinstance(stores, list) and len(stores) > 0:
                    return int(stores[0].get('store_id') or 1)
        except Exception as e:
            logger.warning(f"Pathao store list lookup failed: {e}")
        return 1

    def create_shipment(self, order, item_weight_kg=0.5, special_instruction=""):
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        client_id, client_secret, _, _, store_id, base_url = self.get_credentials()
        district = order.address.get('district', '') if isinstance(order.address, dict) else ''
        is_dhaka = str(district).strip().lower() == 'dhaka'
        delivery_fee = self.get_dynamic_fee(config, is_dhaka)
        cod_amount = order.total if order.paymentMethod == 'COD' else 0

        address_dict = order.address if isinstance(order.address, dict) else {}
        recipient_name = address_dict.get('customerName') or 'Valued Customer'
        raw_phone = address_dict.get('phone') or ''
        recipient_phone = normalize_bangladesh_phone(raw_phone)
        full_address = address_dict.get('fullAddress') or f"{address_dict.get('thana', '')}, {district}".strip(', ')
        note = special_instruction or address_dict.get('orderNote') or 'Please call before delivery.'

        has_live_creds = self.has_live_credential(client_id) and self.has_live_credential(client_secret)

        # Strict Live Production mode when sandbox_mode is False
        if not sandbox_mode:
            if not has_live_creds:
                raise ValueError("Pathao live API credentials are missing. Add real credentials or enable Sandbox Mode in Admin → Courier Hub.")
            token, auth_err = self.get_access_token()
            if not token:
                raise ValueError(auth_err or "Pathao authentication failed. Check Pathao credentials in Courier Hub.")
            resolved_store_id = self.resolve_default_store_id(token, base_url, store_id)
            city_id, zone_id = self.resolve_city_zone(token, district, address_dict.get('thana', ''), base_url=base_url)
            payload = {
                "store_id": resolved_store_id,
                "merchant_order_id": str(order.id),
                "recipient_name": recipient_name,
                "recipient_phone": recipient_phone,
                "recipient_address": full_address,
                "recipient_city": city_id,
                "recipient_zone": zone_id,
                "delivery_type": 48,
                "item_type": 2,
                "special_instruction": note,
                "item_quantity": 1,
                "item_weight": float(item_weight_kg),
                "amount_to_collect": int(cod_amount)
            }
            if True:
                try:
                    resp = requests.post(
                        f"{base_url}/aladdin/api/v1/orders",
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
                        tracking_id = str(c_data.get('tracking_code') or c_data.get('consignment_id') or consignment_id)
                        rider_info = self.extract_rider_info(data)
                        return {
                            'provider': 'pathao',
                            'trackingId': tracking_id,
                            'consignmentId': consignment_id,
                            'courierStatus': 'Order Placed at Pathao Hub',
                            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
                            'deliveryFee': delivery_fee,
                            'codAmount': cod_amount,
                            'trackingUrl': f"https://pathao.com/courier/tracking?consignment={consignment_id}",
                            'hubLocation': rider_info.get('hubLocation') or ('Tejgaon Central Sorting Hub' if is_dhaka else 'Dhaka Transit Departure Hub'),
                            'assignedRiderName': rider_info.get('assignedRiderName', ''),
                            'assignedRiderPhone': rider_info.get('assignedRiderPhone', ''),
                            'liveApi': True,
                        }
                    else:
                        err_msg = data.get('message') or resp.text or 'Pathao order booking failed'
                        logger.error(f"Pathao create order error: {err_msg}")
                        raise ValueError(f"Pathao Error: {err_msg}")
                except requests.RequestException as e:
                    logger.error(f"Pathao network error: {e}")
                    raise ConnectionError(f"Pathao connection failed: {str(e)}")

        # Sandbox Testing Fallback
        prefix = (district[:3].upper() if len(district) >= 3 else 'DHK')
        tracking_id = f"PTH-{prefix}-{random.randint(100000, 999999)}"
        consignment_id = f"PTH-CNS-{random.randint(1000000, 9999999)}"

        return {
            'provider': 'pathao',
            'trackingId': tracking_id,
            'consignmentId': consignment_id,
            'courierStatus': 'Order Placed at Pathao Hub (Sandbox Testing)',
            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
            'deliveryFee': delivery_fee,
            'codAmount': cod_amount,
            'trackingUrl': f"https://pathao.com/courier/tracking?consignment={consignment_id}",
            'hubLocation': 'Tejgaon Central Sorting Hub' if is_dhaka else 'Regional Sorting Hub',
            'assignedRiderName': 'Pathao Fleet Agent (Sandbox)',
            'assignedRiderPhone': '',
            'liveApi': False,
        }

    def get_balance(self):
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        client_id, client_secret, _, _, _, _ = self.get_credentials()
        has_live = self.has_live_credential(client_id) and self.has_live_credential(client_secret)
        if sandbox_mode:
            return {
                'success': True,
                'isDemo': True,
                'sandboxMode': True,
                'current_balance': 0,
                'message': 'Sandbox Testing Mode Active — No live Pathao API call made.'
            }
        if not has_live:
            return {
                'success': False,
                'isDemo': False,
                'current_balance': 0,
                'message': 'Pathao API credentials missing. Configure Client ID, Secret, Email & Password (or enable Sandbox Mode).'
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

    def resolve_city_zone(self, token, district, thana="", base_url=None):
        """Dynamically resolve Pathao city_id & zone_id from district/thana."""
        api_base = base_url or self.BASE_URL
        city_id, zone_id = 1, 1
        d_clean = str(district or '').strip().lower()
        t_clean = str(thana or '').strip().lower()
        if not d_clean:
            return city_id, zone_id
        try:
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            c_resp = requests.get(f"{api_base}/aladdin/api/v1/city-list", headers=headers, timeout=8)
            c_data = c_resp.json().get('data', {}).get('data', []) if c_resp.status_code == 200 else []
            for c in c_data:
                if d_clean in str(c.get('city_name', '')).lower():
                    city_id = int(c.get('city_id', 1))
                    break
            if t_clean:
                z_resp = requests.get(f"{api_base}/aladdin/api/v1/cities/{city_id}/zone-list", headers=headers, timeout=8)
                z_data = z_resp.json().get('data', {}).get('data', []) if z_resp.status_code == 200 else []
                for z in z_data:
                    if t_clean in str(z.get('zone_name', '')).lower():
                        zone_id = int(z.get('zone_id', 1))
                        break
                if zone_id == 1 and z_data:
                    zone_id = int(z_data[0].get('zone_id', 1))
        except Exception as e:
            logger.warning(f"Pathao city/zone lookup fallback for {district}/{thana}: {e}")
        return city_id, zone_id

    def check_status(self, tracking_code):
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        client_id, client_secret, _, _, _, base_url = self.get_credentials()
        if sandbox_mode:
            return {'status': 200, 'tracking_id': tracking_code, 'provider': 'pathao', 'isDemo': True}
        if not (self.has_live_credential(client_id) and self.has_live_credential(client_secret)):
            return {'status': 400, 'isDemo': False, 'message': 'Pathao live API credentials not configured.'}
        token, auth_err = self.get_access_token()
        if not token:
            return {'status': 401, 'message': auth_err or 'Pathao authentication failed'}
        try:
            resp = requests.get(
                f"{base_url}/aladdin/api/v1/orders/{tracking_code}/info",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=10
            )
            data = resp.json() if resp.text else {}
            info = data.get('data') if isinstance(data.get('data'), dict) else data
            return {
                'status': resp.status_code,
                'provider': 'pathao',
                'tracking_id': tracking_code,
                'order_status': info.get('order_status') or info.get('order_status_slug') or '',
                'raw': data,
            }
        except Exception as e:
            return {'status': 500, 'message': str(e)}

    def parse_webhook(self, payload):
        nested = payload.get('data') if isinstance(payload.get('data'), dict) else {}
        event = str(
            payload.get('event')
            or payload.get('order_status')
            or payload.get('order_status_slug')
            or payload.get('status')
            or nested.get('order_status')
            or nested.get('order_status_slug')
            or ''
        ).lower().strip()
        mapping = {
            # Pathao Webhook official dot-hyphen events + REST API statuses
            'order.created': ('COURIER_CREATED', 'Order created in Pathao portal'),
            'order.updated': ('COURIER_CREATED', 'Order updated in Pathao portal'),
            'order.pickup-requested': ('COURIER_CREATED', 'Pickup requested at Pathao hub'),
            'order.assigned-for-pickup': ('COURIER_CREATED', 'Pathao rider assigned for pickup'),
            'pickup_requested': ('COURIER_CREATED', 'Pickup requested at Pathao hub'),
            'pickup requested': ('COURIER_CREATED', 'Pickup requested at Pathao hub'),
            'assigned for pickup': ('COURIER_CREATED', 'Pathao rider assigned for pickup'),
            'order.picked': ('PICKED_UP', 'Rider picked up parcel from merchant (Pathao)'),
            'order.picked-up': ('PICKED_UP', 'Rider picked up parcel from merchant (Pathao)'),
            'order.picked_up': ('PICKED_UP', 'Rider picked up parcel from merchant (Pathao)'),
            'picked': ('PICKED_UP', 'Rider picked up parcel from merchant (Pathao)'),
            'picked_up': ('PICKED_UP', 'Rider picked up parcel from merchant (Pathao)'),
            'picked up': ('PICKED_UP', 'Rider picked up parcel from merchant (Pathao)'),
            'order.at-the-sorting-hub': ('IN_TRANSIT', 'Parcel arrived at Pathao sorting hub'),
            'order.in-transit': ('IN_TRANSIT', 'Parcel in transit to destination hub (Pathao)'),
            'order.in_transit': ('IN_TRANSIT', 'Parcel in transit to destination hub (Pathao)'),
            'order.received-at-last-mile-hub': ('IN_TRANSIT', 'Parcel received at destination hub (Pathao)'),
            'order.on-hold': ('IN_TRANSIT', 'Parcel temporarily on hold (Pathao)'),
            'at the sorting hub': ('IN_TRANSIT', 'Parcel arrived at Pathao sorting hub'),
            'received at last mile hub': ('IN_TRANSIT', 'Parcel received at destination hub (Pathao)'),
            'in_transit': ('IN_TRANSIT', 'Parcel in transit to destination hub (Pathao)'),
            'in transit': ('IN_TRANSIT', 'Parcel in transit to destination hub (Pathao)'),
            'on_hold': ('IN_TRANSIT', 'Parcel temporarily on hold (Pathao)'),
            'on hold': ('IN_TRANSIT', 'Parcel temporarily on hold (Pathao)'),
            'hold': ('IN_TRANSIT', 'Parcel temporarily on hold (Pathao)'),
            'order.assigned-for-delivery': ('OUT_FOR_DELIVERY', 'Rider assigned and out for delivery (Pathao)'),
            'order.out-for-delivery': ('OUT_FOR_DELIVERY', 'Rider out for delivery to recipient (Pathao)'),
            'order.out_for_delivery': ('OUT_FOR_DELIVERY', 'Rider out for delivery to recipient (Pathao)'),
            'assigned for delivery': ('OUT_FOR_DELIVERY', 'Rider assigned and out for delivery (Pathao)'),
            'out_for_delivery': ('OUT_FOR_DELIVERY', 'Rider out for delivery to recipient (Pathao)'),
            'out for delivery': ('OUT_FOR_DELIVERY', 'Rider out for delivery to recipient (Pathao)'),
            'order.delivered': ('DELIVERED', 'Parcel successfully delivered to recipient (Pathao)'),
            'order.partial-delivery': ('DELIVERED', 'Parcel partially delivered (Pathao)'),
            'delivered': ('DELIVERED', 'Parcel successfully delivered to recipient (Pathao)'),
            'partial delivery': ('DELIVERED', 'Parcel partially delivered (Pathao)'),
            'partial_delivered': ('DELIVERED', 'Parcel partially delivered (Pathao)'),
            'order.return': ('RETURNED', 'Parcel returned to merchant (Pathao)'),
            'order.returned': ('RETURNED', 'Parcel returned to merchant (Pathao)'),
            'order.delivery-failed': ('RETURNED', 'Delivery failed, returning parcel (Pathao)'),
            'return': ('RETURNED', 'Parcel returned to merchant (Pathao)'),
            'returned': ('RETURNED', 'Parcel returned to merchant (Pathao)'),
            'delivery failed': ('RETURNED', 'Delivery failed, returning parcel (Pathao)'),
            'order.pickup-cancelled': ('CANCELLED', 'Pickup cancelled (Pathao)'),
            'order.cancelled': ('CANCELLED', 'Parcel booking cancelled (Pathao)'),
            'pickup cancelled': ('CANCELLED', 'Pickup cancelled (Pathao)'),
            'cancelled': ('CANCELLED', 'Parcel booking cancelled (Pathao)'),
        }
        order_status, notes = mapping.get(event, ('IN_TRANSIT', f"Pathao status updated: {event.replace('_', ' ').replace('-', ' ').strip()}"))
        return {
            'orderStatus': order_status,
            'collectedAmount': payload.get('collected_amount') or payload.get('cod_amount') or nested.get('collected_amount') or 0,
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
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        api_key, secret_key = self.get_credentials()
        district = order.address.get('district', '') if isinstance(order.address, dict) else ''
        is_dhaka = str(district).strip().lower() == 'dhaka'
        delivery_fee = self.get_dynamic_fee(config, is_dhaka)
        cod_amount = order.total if order.paymentMethod == 'COD' else 0

        address_dict = order.address if isinstance(order.address, dict) else {}
        recipient_name = address_dict.get('customerName') or 'Valued Customer'
        raw_phone = address_dict.get('phone') or ''
        recipient_phone = normalize_bangladesh_phone(raw_phone)
        full_address = address_dict.get('fullAddress') or f"{address_dict.get('thana', '')}, {district}"
        note = special_instruction or address_dict.get('orderNote') or 'Please call before delivery.'

        has_live = self.has_live_credential(api_key) and self.has_live_credential(secret_key)

        if not sandbox_mode:
            if not has_live:
                raise ValueError("Steadfast live API credentials are missing. Configure API Key & Secret or enable Sandbox Mode in Admin → Courier Hub.")
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
                    tracking_code = str(consignment.get('tracking_code') or consignment.get('consignment_id') or '')
                    consignment_id = str(consignment.get('consignment_id') or tracking_code or '')
                    rider_info = self.extract_rider_info(consignment if isinstance(consignment, dict) else data)
                    return {
                        'provider': 'steadfast',
                        'trackingId': tracking_code,
                        'consignmentId': consignment_id,
                        'courierStatus': 'Consignment Created at Steadfast',
                        'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
                        'deliveryFee': delivery_fee,
                        'codAmount': cod_amount,
                        'trackingUrl': f"https://steadfast.com.bd/tracking/{tracking_code}",
                        'hubLocation': rider_info.get('hubLocation') or ('Motijheel Central Distribution Hub' if is_dhaka else 'District Delivery Station'),
                        'assignedRiderName': rider_info.get('assignedRiderName', ''),
                        'assignedRiderPhone': rider_info.get('assignedRiderPhone', ''),
                        'liveApi': True,
                    }
                else:
                    err_msg = data.get('message') or resp.text or f"HTTP status {resp.status_code}"
                    logger.error(f"Steadfast create_order failed: {err_msg}")
                    raise ValueError(f"Steadfast Error: {err_msg}")
            except requests.RequestException as e:
                logger.error(f"Steadfast network error: {e}")
                raise ConnectionError(f"Steadfast connection failed: {str(e)}")

        # Sandbox Testing Fallback
        tracking_id = f"STDF-{random.randint(20000000, 99999999)}"
        consignment_id = f"CID-{random.randint(100000, 999999)}"

        return {
            'provider': 'steadfast',
            'trackingId': tracking_id,
            'consignmentId': consignment_id,
            'courierStatus': 'Consignment Created at Steadfast (Sandbox Testing)',
            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
            'deliveryFee': delivery_fee,
            'codAmount': cod_amount,
            'trackingUrl': f"https://steadfast.com.bd/tracking/{tracking_id}",
            'hubLocation': 'Motijheel Distribution Hub' if is_dhaka else 'District Delivery Station',
            'assignedRiderName': 'Steadfast Express Executive (Sandbox)',
            'assignedRiderPhone': '',
            'liveApi': False,
        }

    def get_balance(self):
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        api_key, secret_key = self.get_credentials()
        if sandbox_mode:
            return {
                'success': True,
                'isDemo': True,
                'sandboxMode': True,
                'current_balance': 0,
                'message': 'Sandbox Testing Mode Active — No live Steadfast API call made.'
            }
        if not (self.has_live_credential(api_key) and self.has_live_credential(secret_key)):
            return {
                'success': False,
                'isDemo': False,
                'current_balance': 0,
                'message': 'Steadfast API credentials missing. Configure API Key & Secret (or enable Sandbox Mode).'
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
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        api_key, secret_key = self.get_credentials()
        if sandbox_mode:
            return {'status': 200, 'tracking_id': tracking_code, 'provider': 'steadfast', 'isDemo': True}
        if not (self.has_live_credential(api_key) and self.has_live_credential(secret_key)):
            return {'status': 400, 'isDemo': False, 'message': 'Steadfast live API credentials not configured.'}
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
        raw_status = str(
            payload.get('status')
            or payload.get('delivery_status')
            or payload.get('event')
            or ''
        ).lower().strip()
        tracking_msg = str(payload.get('tracking_message') or '').strip()
        mapping = {
            'delivered': ('DELIVERED', 'Parcel successfully delivered to recipient (Steadfast)'),
            'partial_delivered': ('DELIVERED', 'Parcel partially delivered (Steadfast)'),
            'delivered_approval_pending': ('DELIVERED', 'Delivered, pending settlement approval (Steadfast)'),
            'partial_delivered_approval_pending': ('DELIVERED', 'Partially delivered, pending settlement approval (Steadfast)'),
            'cancelled': ('CANCELLED', 'Parcel booking cancelled (Steadfast)'),
            'cancelled_approval_pending': ('CANCELLED', 'Cancellation pending approval (Steadfast)'),
            'hold': ('IN_TRANSIT', 'Parcel temporarily held at Steadfast hub'),
            'in_review': ('COURIER_CREATED', 'Consignment created and under review (Steadfast)'),
            'pending': ('COURIER_CREATED', 'Awaiting rider pickup (Steadfast)'),
            'awaiting_pickup': ('COURIER_CREATED', 'Awaiting rider pickup (Steadfast)'),
            'picked_up': ('PICKED_UP', 'Rider picked up parcel from merchant hub (Steadfast)'),
            'picked up': ('PICKED_UP', 'Rider picked up parcel from merchant hub (Steadfast)'),
            'in_transit': ('IN_TRANSIT', 'Parcel in transit to destination hub (Steadfast)'),
            'in transit': ('IN_TRANSIT', 'Parcel in transit to destination hub (Steadfast)'),
            'out_for_delivery': ('OUT_FOR_DELIVERY', 'Delivery rider is out for delivery (Steadfast)'),
            'out for delivery': ('OUT_FOR_DELIVERY', 'Delivery rider is out for delivery (Steadfast)'),
            'return': ('RETURNED', 'Parcel returned to merchant (Steadfast)'),
            'returned': ('RETURNED', 'Parcel returned to merchant (Steadfast)'),
            'unknown': ('IN_TRANSIT', tracking_msg or 'Awaiting courier status update (Steadfast)'),
            'unknown_approval_pending': ('IN_TRANSIT', tracking_msg or 'Status update pending review (Steadfast)'),
        }
        order_status, notes = mapping.get(raw_status, ('IN_TRANSIT', tracking_msg or f"Steadfast status updated: {raw_status.replace('_', ' ').replace('-', ' ').strip()}"))
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
        raw_area = str(extra.get('delivery_area_id') or '').strip()
        area_id = int(raw_area) if raw_area.isdigit() else None
        return token.strip(), area_id

    def resolve_area(self, token, district, thana="", configured_area_id=None):
        """Dynamically resolve RedX delivery_area_id from /areas endpoint using thana/district."""
        if configured_area_id:
            return configured_area_id, (thana or district or "Bangladesh")
        t_clean = str(thana or '').strip().lower()
        d_clean = str(district or '').strip().lower()
        try:
            url = f"{self.BASE_URL}/areas"
            params = {'district_name': district} if district else {}
            resp = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=8)
            if resp.status_code != 200 and params:
                resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=8)
            areas = resp.json().get('areas', []) if resp.status_code == 200 and resp.text else []
            if isinstance(areas, list):
                if t_clean:
                    for a in areas:
                        aname = str(a.get('name') or a.get('area_name') or '').lower()
                        if t_clean in aname or aname in t_clean:
                            return int(a.get('id') or 1), str(a.get('name') or thana or district)
                if d_clean:
                    for a in areas:
                        dist_str = str(a.get('district_name') or a.get('zone_name') or a.get('name') or '').lower()
                        if d_clean in dist_str:
                            return int(a.get('id') or 1), str(a.get('name') or district)
                if areas:
                    return int(areas[0].get('id') or 1), str(areas[0].get('name') or district or "Bangladesh")
        except Exception as e:
            logger.warning(f"RedX area lookup fallback for {district}/{thana}: {e}")
        return 1, (thana or district or "Bangladesh")

    def create_shipment(self, order, item_weight_kg=0.5, special_instruction=""):
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        token, configured_area_id = self.get_credentials()
        district = order.address.get('district', '') if isinstance(order.address, dict) else ''
        thana = order.address.get('thana', '') if isinstance(order.address, dict) else ''
        is_dhaka = str(district).strip().lower() == 'dhaka'
        delivery_fee = self.get_dynamic_fee(config, is_dhaka)
        cod_amount = order.total if order.paymentMethod == 'COD' else 0

        address_dict = order.address if isinstance(order.address, dict) else {}
        recipient_name = address_dict.get('customerName') or 'Valued Customer'
        raw_phone = address_dict.get('phone') or ''
        recipient_phone = normalize_bangladesh_phone(raw_phone)
        full_address = address_dict.get('fullAddress') or f"{thana}, {district}".strip(', ')
        note = special_instruction or address_dict.get('orderNote') or 'Please call before delivery.'

        has_live = self.has_live_credential(token)

        if not sandbox_mode:
            if not has_live:
                raise ValueError("RedX live API token is missing. Configure API Token or enable Sandbox Mode in Admin → Courier Hub.")
            delivery_area_id, area_label = self.resolve_area(token, district, thana, configured_area_id)
            payload = {
                "customer_name": recipient_name,
                "customer_phone": recipient_phone,
                "delivery_area": area_label,
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
                    rider_info = self.extract_rider_info(data)
                    return {
                        'provider': 'redx',
                        'trackingId': tracking_id,
                        'consignmentId': str(data.get('consignment_id') or tracking_id),
                        'courierStatus': 'Consignment Booked in RedX Network',
                        'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
                        'deliveryFee': delivery_fee,
                        'codAmount': cod_amount,
                        'trackingUrl': f"https://redx.com.bd/track/{tracking_id}",
                        'hubLocation': rider_info.get('hubLocation') or ('Gazipur Central Fulfilment' if is_dhaka else 'Regional Logistics Node'),
                        'assignedRiderName': rider_info.get('assignedRiderName', ''),
                        'assignedRiderPhone': rider_info.get('assignedRiderPhone', ''),
                        'liveApi': True,
                    }
                else:
                    err_msg = data.get('message') or resp.text or 'RedX parcel booking failed'
                    logger.error(f"RedX create order error: {err_msg}")
                    raise ValueError(f"RedX Error: {err_msg}")
            except requests.RequestException as e:
                logger.error(f"RedX network error: {e}")
                raise ConnectionError(f"RedX connection failed: {str(e)}")

        # Sandbox Testing Fallback
        tracking_id = f"REDX-{random.randint(100000000, 999999999)}"
        consignment_id = f"RC-{random.randint(100000, 999999)}"

        return {
            'provider': 'redx',
            'trackingId': tracking_id,
            'consignmentId': consignment_id,
            'courierStatus': 'Consignment Booked in RedX Network (Sandbox Testing)',
            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
            'deliveryFee': delivery_fee,
            'codAmount': cod_amount,
            'trackingUrl': f"https://redx.com.bd/track/{tracking_id}",
            'hubLocation': 'Gazipur Central Fulfilment' if is_dhaka else 'Regional Logistics Node',
            'assignedRiderName': 'RedX Delivery Rider (Sandbox)',
            'assignedRiderPhone': '',
            'liveApi': False,
        }

    def get_balance(self):
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        token, _ = self.get_credentials()
        if sandbox_mode:
            return {
                'success': True,
                'isDemo': True,
                'sandboxMode': True,
                'current_balance': 0,
                'message': 'Sandbox Testing Mode Active — No live RedX API call made.'
            }
        if not self.has_live_credential(token):
            return {
                'success': False,
                'isDemo': False,
                'current_balance': 0,
                'message': 'RedX API token missing. Configure API Token (or enable Sandbox Mode).'
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
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        token, _ = self.get_credentials()
        if sandbox_mode:
            return {'status': 200, 'tracking_id': tracking_code, 'provider': 'redx', 'isDemo': True}
        if not self.has_live_credential(token):
            return {'status': 400, 'isDemo': False, 'message': 'RedX live API token not configured.'}
        try:
            resp = requests.get(
                f"{self.BASE_URL}/parcel/info/{tracking_code}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if resp.status_code == 404:
                resp = requests.get(
                    f"{self.BASE_URL}/parcels/{tracking_code}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10
                )
            data = resp.json() if resp.text else {'status': resp.status_code}
            parcel = data.get('parcel') if isinstance(data.get('parcel'), dict) else {}
            return {
                'status': resp.status_code,
                'provider': 'redx',
                'tracking_id': tracking_code,
                'parcel_status': parcel.get('status') or data.get('status') or '',
                'raw': data,
            }
        except Exception as e:
            return {'status': 500, 'message': str(e)}

    def parse_webhook(self, payload):
        parcel = payload.get('parcel') if isinstance(payload.get('parcel'), dict) else {}
        raw_status = str(
            payload.get('status')
            or payload.get('parcel_status')
            or payload.get('event')
            or parcel.get('status')
            or ''
        ).lower().strip()
        mapping = {
            'pickup-pending': ('COURIER_CREATED', 'Pickup pending at RedX hub'),
            'pickup_pending': ('COURIER_CREATED', 'Pickup pending at RedX hub'),
            'pickup-in-progress': ('COURIER_CREATED', 'RedX rider en route for pickup'),
            'pickup requested': ('COURIER_CREATED', 'Pickup requested in RedX network'),
            'picked-up': ('PICKED_UP', 'RedX rider picked up parcel from merchant'),
            'picked_up': ('PICKED_UP', 'RedX rider picked up parcel from merchant'),
            'picked up': ('PICKED_UP', 'RedX rider picked up parcel from merchant'),
            'pickup-completed': ('PICKED_UP', 'RedX rider picked up parcel from merchant'),
            'pickup_completed': ('PICKED_UP', 'RedX rider picked up parcel from merchant'),
            'received-at-hub': ('IN_TRANSIT', 'Parcel received at RedX hub'),
            'agent-hold': ('IN_TRANSIT', 'Parcel temporarily held by RedX hub'),
            'agent-area-change': ('IN_TRANSIT', 'Parcel re-routed in RedX delivery zone'),
            'ready-for-delivery': ('IN_TRANSIT', 'Parcel ready for last mile delivery (RedX)'),
            'in-transit': ('IN_TRANSIT', 'Parcel in RedX transit hub'),
            'in_transit': ('IN_TRANSIT', 'Parcel in RedX transit hub'),
            'in transit': ('IN_TRANSIT', 'Parcel in RedX transit hub'),
            'delivery-in-progress': ('OUT_FOR_DELIVERY', 'RedX rider out for delivery to recipient'),
            'out-for-delivery': ('OUT_FOR_DELIVERY', 'RedX rider out for delivery to recipient'),
            'out_for_delivery': ('OUT_FOR_DELIVERY', 'RedX rider out for delivery to recipient'),
            'out for delivery': ('OUT_FOR_DELIVERY', 'RedX rider out for delivery to recipient'),
            'delivered': ('DELIVERED', 'Parcel successfully delivered to recipient (RedX)'),
            'cash-collected': ('DELIVERED', 'Parcel delivered and cash collected (RedX)'),
            'agent-returning': ('RETURNED', 'Parcel returning to merchant (RedX)'),
            'returned-to-merchant': ('RETURNED', 'Parcel returned to merchant (RedX)'),
            'returned_to_merchant': ('RETURNED', 'Parcel returned to merchant (RedX)'),
            'returned': ('RETURNED', 'Parcel returned to merchant (RedX)'),
            'return': ('RETURNED', 'Parcel returned to merchant (RedX)'),
            'cancelled': ('CANCELLED', 'Parcel booking cancelled (RedX)'),
            'pickup-cancelled': ('CANCELLED', 'Parcel pickup cancelled (RedX)'),
        }
        order_status, notes = mapping.get(raw_status, ('IN_TRANSIT', f"RedX status updated: {raw_status.replace('_', ' ').replace('-', ' ').strip()}"))
        return {
            'orderStatus': order_status,
            'collectedAmount': payload.get('amount_collected') or payload.get('collected_amount') or payload.get('cod_amount') or parcel.get('cash_collection_amount') or 0,
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
        package_code = str(extra.get('package_code') or os.getenv('ECOURIER_PACKAGE_CODE', '')).strip()
        return api_key.strip(), secret.strip(), user_id, package_code

    def resolve_package_code(self, api_key, secret, user_id, configured_code):
        if configured_code:
            return configured_code
        try:
            resp = requests.post(
                f"{self.BASE_URL}/packages",
                headers={
                    "API-KEY": api_key,
                    "API-SECRET": secret,
                    "USER-ID": user_id,
                    "Content-Type": "application/json",
                },
                timeout=8,
            )
            if resp.status_code == 200 and resp.text:
                data = resp.json()
                pkgs = data if isinstance(data, list) else (data.get('data') or data.get('packages') or [])
                if isinstance(pkgs, list) and len(pkgs) > 0:
                    first = pkgs[0]
                    if isinstance(first, dict):
                        return str(first.get('package_code') or first.get('code') or first.get('id') or '').strip()
        except Exception as e:
            logger.warning(f"eCourier package lookup failed: {e}")
        return ''

    def create_shipment(self, order, item_weight_kg=0.5, special_instruction=""):
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        api_key, secret, user_id, package_code = self.get_credentials()
        district = order.address.get('district', '') if isinstance(order.address, dict) else ''
        thana = order.address.get('thana', '') if isinstance(order.address, dict) else ''
        is_dhaka = str(district).strip().lower() == 'dhaka'
        delivery_fee = self.get_dynamic_fee(config, is_dhaka)
        cod_amount = order.total if order.paymentMethod == 'COD' else 0

        address_dict = order.address if isinstance(order.address, dict) else {}
        recipient_name = address_dict.get('customerName') or 'Valued Customer'
        raw_phone = address_dict.get('phone') or ''
        recipient_phone = normalize_bangladesh_phone(raw_phone)
        full_address = address_dict.get('fullAddress') or f"{thana}, {district}".strip(', ')
        note = special_instruction or address_dict.get('orderNote') or 'Please call before delivery.'

        has_live = self.has_live_credential(api_key) and self.has_live_credential(secret)

        if not sandbox_mode:
            if not has_live:
                raise ValueError("eCourier live API credentials are missing. Configure API Key, Secret & User ID or enable Sandbox Mode in Admin → Courier Hub.")
            if not user_id:
                raise ValueError("eCourier requires User ID alongside API Key & API Secret in Courier Hub.")
            resolved_pkg = self.resolve_package_code(api_key, secret, user_id, package_code)
            if not resolved_pkg:
                raise ValueError("eCourier Package Code is missing and could not be auto-fetched. Please set Package Code in Admin → Courier Hub.")
            payload = {
                "recipient_name": recipient_name,
                "recipient_mobile": recipient_phone,
                "recipient_address": full_address,
                "recipient_district": district,
                "recipient_thana": thana or address_dict.get('area') or district,
                "package_code": resolved_pkg,
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
                    rider_info = self.extract_rider_info(data)
                    return {
                        'provider': 'ecourier',
                        'trackingId': tracking_id,
                        'consignmentId': str(data.get('consignment_id') or tracking_id),
                        'courierStatus': 'Manifested with eCourier',
                        'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
                        'deliveryFee': delivery_fee,
                        'codAmount': cod_amount,
                        'trackingUrl': f"https://ecourier.com.bd/track/{tracking_id}",
                        'hubLocation': rider_info.get('hubLocation') or 'Kakrail Delivery Office',
                        'assignedRiderName': rider_info.get('assignedRiderName', ''),
                        'assignedRiderPhone': rider_info.get('assignedRiderPhone', ''),
                        'liveApi': True,
                    }
                else:
                    err_msg = data.get('message') or resp.text or 'eCourier booking failed'
                    logger.error(f"eCourier error: {err_msg}")
                    raise ValueError(f"eCourier Error: {err_msg}")
            except requests.RequestException as e:
                logger.error(f"eCourier network error: {e}")
                raise ConnectionError(f"eCourier connection failed: {str(e)}")

        # Sandbox Testing Fallback
        tracking_id = f"EC-{random.randint(1000000, 9999999)}"
        consignment_id = f"EC-CNG-{random.randint(10000, 99999)}"

        return {
            'provider': 'ecourier',
            'trackingId': tracking_id,
            'consignmentId': consignment_id,
            'courierStatus': 'Manifested with eCourier (Sandbox Testing)',
            'createdDate': timezone.localtime().strftime('%Y-%m-%d %I:%M %p'),
            'deliveryFee': delivery_fee,
            'codAmount': cod_amount,
            'trackingUrl': f"https://ecourier.com.bd/track/{tracking_id}",
            'hubLocation': 'Kakrail Delivery Office',
            'assignedRiderName': 'eCourier Express Agent (Sandbox)',
            'assignedRiderPhone': '',
            'liveApi': False,
        }

    def get_balance(self):
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        api_key, secret, user_id, _ = self.get_credentials()
        if sandbox_mode:
            return {
                'success': True,
                'isDemo': True,
                'sandboxMode': True,
                'current_balance': 0,
                'message': 'Sandbox Testing Mode Active — No live eCourier API call made.'
            }
        if not (self.has_live_credential(api_key) and self.has_live_credential(secret)):
            return {
                'success': False,
                'isDemo': False,
                'current_balance': 0,
                'message': 'eCourier API credentials missing. Configure API Key, Secret & User ID (or enable Sandbox Mode).'
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
        config = self.get_provider_config()
        sandbox_mode = self.is_sandbox_mode(config)
        api_key, secret, user_id, _ = self.get_credentials()
        if sandbox_mode:
            return {'status': 200, 'tracking_id': tracking_code, 'provider': 'ecourier', 'isDemo': True}
        if not (self.has_live_credential(api_key) and self.has_live_credential(secret)):
            return {'status': 400, 'isDemo': False, 'message': 'eCourier live API credentials not configured.'}
        try:
            resp = requests.post(
                f"{self.BASE_URL}/parcel-tracking",
                json={"tracking": tracking_code},
                headers={
                    "API-KEY": api_key,
                    "API-SECRET": secret,
                    "USER-ID": user_id,
                    "Content-Type": "application/json"
                },
                timeout=10
            )
            data = resp.json() if resp.text else {}
            q_data = data.get('query_data') if isinstance(data.get('query_data'), list) else []
            latest_q = q_data[0] if (q_data and isinstance(q_data[0], dict)) else {}
            return {
                'status': resp.status_code,
                'provider': 'ecourier',
                'tracking_id': tracking_code,
                'parcel_status': data.get('status') or data.get('parcel_status') or latest_q.get('status') or '',
                'raw': data,
            }
        except Exception as e:
            return {'status': 500, 'message': str(e)}

    def parse_webhook(self, payload):
        q_data = payload.get('query_data') if isinstance(payload.get('query_data'), list) else []
        latest_q = q_data[0] if (q_data and isinstance(q_data[0], dict)) else {}
        raw_status = str(
            payload.get('status')
            or payload.get('parcel_status')
            or payload.get('event')
            or latest_q.get('status')
            or ''
        ).lower().strip()
        mapping = {
            'order placed': ('COURIER_CREATED', 'Order placed at eCourier'),
            'order_placed': ('COURIER_CREATED', 'Order placed at eCourier'),
            'pending': ('COURIER_CREATED', 'Pending pickup at eCourier'),
            'pickup requested': ('COURIER_CREATED', 'Pickup requested in eCourier portal'),
            'delivered': ('DELIVERED', 'Parcel successfully delivered via eCourier'),
            'delivered_successfully': ('DELIVERED', 'Parcel successfully delivered via eCourier'),
            'picked_up': ('PICKED_UP', 'Rider picked up parcel from merchant (eCourier)'),
            'picked-up': ('PICKED_UP', 'Rider picked up parcel from merchant (eCourier)'),
            'picked up': ('PICKED_UP', 'Rider picked up parcel from merchant (eCourier)'),
            'parcel picked': ('PICKED_UP', 'Rider picked up parcel from merchant (eCourier)'),
            'in-transit': ('IN_TRANSIT', 'Parcel in destination transit hub (eCourier)'),
            'in_transit': ('IN_TRANSIT', 'Parcel in destination transit hub (eCourier)'),
            'in transit': ('IN_TRANSIT', 'Parcel in destination transit hub (eCourier)'),
            'arrived at hub': ('IN_TRANSIT', 'Parcel arrived at eCourier hub'),
            'forwarded': ('IN_TRANSIT', 'Parcel forwarded to destination hub (eCourier)'),
            'hold': ('IN_TRANSIT', 'Parcel held at eCourier hub'),
            'out for delivery': ('OUT_FOR_DELIVERY', 'eCourier rider out for delivery'),
            'out-for-delivery': ('OUT_FOR_DELIVERY', 'eCourier rider out for delivery'),
            'out_for_delivery': ('OUT_FOR_DELIVERY', 'eCourier rider out for delivery'),
            'assigned to rider': ('OUT_FOR_DELIVERY', 'Assigned to eCourier delivery rider'),
            'returned': ('RETURNED', 'Parcel returned to merchant (eCourier)'),
            'return': ('RETURNED', 'Parcel returned to merchant (eCourier)'),
            'return to merchant': ('RETURNED', 'Parcel returned to merchant (eCourier)'),
            'cancelled': ('CANCELLED', 'Parcel booking cancelled (eCourier)'),
            'canceled': ('CANCELLED', 'Parcel booking cancelled (eCourier)'),
        }
        order_status, notes = mapping.get(raw_status, ('IN_TRANSIT', f"eCourier status updated: {raw_status.replace('_', ' ').replace('-', ' ').strip()}"))
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

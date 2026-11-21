import re

BANGLADESH_DISTRICTS = [
    'Dhaka', 'Gazipur', 'Narayanganj', 'Tangail', 'Narsingdi', 'Manikganj', 'Munshiganj',
    'Faridpur', 'Chittagong', "Cox's Bazar", 'Comilla', 'Feni', 'Noakhali', 'Brahmanbaria',
    'Chandpur', 'Sylhet', 'Moulvibazar', 'Habiganj', 'Sunamganj', 'Rajshahi', 'Bogura',
    'Pabna', 'Sirajganj', 'Naogaon', 'Natore', 'Khulna', 'Jessore', 'Kushtia', 'Satkhira',
    'Bagerhat', 'Barishal', 'Patuakhali', 'Bhola', 'Rangpur', 'Dinajpur', 'Kurigram',
    'Mymensingh', 'Jamalpur', 'Netrokona'
]

DHAKA_SUBURBS = [
    'savar', 'keraniganj', 'dhamrai', 'ashulia', 'tongi', 'gazipur', 'narayanganj'
]

def normalize_bangladesh_phone(phone: str) -> str:
    if not phone:
        return ''
    cleaned = re.sub(r'\D', '', str(phone))
    if cleaned.startswith('880'):
        cleaned = cleaned[2:]
    if cleaned.startswith('80') and len(cleaned) == 12:
        cleaned = cleaned[1:]
    return cleaned

def get_id_prefixes():
    """
    Resolve dynamic Order ID & Transaction ID prefixes from central ShopSetting.extraConfig.idPrefixes.
    Always falls back cleanly to system defaults when unset or empty.
    """
    from core.models import ShopSetting
    raw = {}
    try:
        s = ShopSetting.get_settings()
        extra = s.extraConfig if isinstance(s.extraConfig, dict) else {}
        if isinstance(extra.get('idPrefixes'), dict):
            raw = extra.get('idPrefixes')
    except Exception:
        raw = {}

    def _clean_code(val, default_val, max_len=12):
        cleaned = re.sub(r'[^A-Za-z0-9_-]', '', str(val or '').strip()).upper()
        return cleaned[:max_len] if cleaned else default_val

    order_symbol = str(raw.get('orderSymbol') if raw.get('orderSymbol') is not None else '#').strip()
    if not order_symbol and raw.get('orderSymbol') is None:
        order_symbol = '#'
    order_symbol = order_symbol[:4]

    return {
        'orderSymbol': order_symbol,
        'orderIdPrefix': _clean_code(raw.get('orderIdPrefix'), 'BD', 12),
        'transactionIdPrefix': _clean_code(raw.get('transactionIdPrefix'), 'AMAR', 10),
        'reconciliationOrderPrefix': _clean_code(raw.get('reconciliationOrderPrefix'), '', 10),
        'reconciliationTrxPrefix': _clean_code(raw.get('reconciliationTrxPrefix'), '', 10),
    }


def is_valid_bangladesh_phone(phone: str) -> bool:
    normalized = normalize_bangladesh_phone(phone)
    # Must be 11 digits and start with 013-019
    return bool(re.match(r'^01[3-9]\d{8}$', normalized))

def calculate_delivery_fee(district: str, area: str = '', subtotal: float = 0) -> dict:
    """
    Dynamically resolves delivery zone & cost from ShopSetting.extraConfig.productPage.deliveryZones
    (configured in Admin -> Product Editorial -> Delivery Cost), supporting WooCommerce-style
    district zones, free-shipping minimum order thresholds, and Rest-of-Bangladesh fallback.
    """
    norm_district = (district or '').strip().lower()
    norm_area = (area or '').strip().lower()
    order_subtotal = float(subtotal or 0)

    configured_zones = None
    try:
        from core.models import ShopSetting
        s = ShopSetting.get_settings()
        extra = s.extraConfig if isinstance(s.extraConfig, dict) else {}
        product_page = extra.get('productPage') if isinstance(extra.get('productPage'), dict) else {}
        if 'deliveryZones' in product_page and isinstance(product_page.get('deliveryZones'), list):
            configured_zones = product_page.get('deliveryZones')
    except Exception:
        configured_zones = None

    if configured_zones is not None:
        enabled_zones = [z for z in configured_zones if isinstance(z, dict) and z.get('enabled', True) is not False]
        matched_zone = None

        # Pass 1: Specific district match (non-fallback zones)
        for z in enabled_zones:
            if z.get('isDefaultFallback'):
                continue
            districts_list = [str(d).strip().lower() for d in (z.get('districts') or []) if str(d).strip()]
            if norm_district and norm_district in districts_list:
                matched_zone = z
                break

        # Pass 2: Fallback / Rest of Bangladesh zone
        if not matched_zone:
            for z in enabled_zones:
                districts_list = [str(d).strip().lower() for d in (z.get('districts') or []) if str(d).strip()]
                if z.get('isDefaultFallback') or len(districts_list) == 0:
                    matched_zone = z
                    break

        if not matched_zone:
            return {'deliveryZone': 'free', 'deliveryFee': 0}

        try:
            base_cost = max(0, int(round(float(matched_zone.get('cost', 0) or 0))))
        except (TypeError, ValueError):
            base_cost = 0
        try:
            free_min = max(0.0, float(matched_zone.get('freeShippingMinOrder', 0) or 0))
        except (TypeError, ValueError):
            free_min = 0.0

        effective_fee = 0 if (free_min > 0 and order_subtotal >= free_min) else base_cost
        zone_key = str(matched_zone.get('zoneKey') or matched_zone.get('name') or 'custom_zone').strip()
        return {'deliveryZone': zone_key, 'deliveryFee': effective_fee}

    # Initial fallback only when ShopSetting.extraConfig.productPage.deliveryZones has never been initialized
    if norm_district == 'dhaka':
        is_suburb = any(sub in norm_area for sub in DHAKA_SUBURBS)
        if is_suburb:
            return {'deliveryZone': 'sub_dhaka', 'deliveryFee': 100}
        return {'deliveryZone': 'inside_dhaka', 'deliveryFee': 70}

    if norm_district in ['gazipur', 'narayanganj', 'munshiganj', 'manikganj', 'narsingdi']:
        return {'deliveryZone': 'sub_dhaka', 'deliveryFee': 100}

    return {'deliveryZone': 'outside_dhaka', 'deliveryFee': 130}

def assess_order_risk(phone: str, address: str, total: float, payment_method: str = 'COD') -> dict:
    score = 10
    factors = []

    normalized = normalize_bangladesh_phone(phone)

    if not is_valid_bangladesh_phone(phone):
        score += 45
        factors.append('Invalid Bangladesh phone number format')

    if normalized.startswith('014') or normalized.startswith('019'):
        score += 5
        factors.append('Banglalink/Teletalk virtual SIM flag')

    if len(str(address or '').strip()) < 15:
        score += 25
        factors.append('Short or ambiguous delivery address')

    if payment_method == 'COD' and total > 5000:
        score += 30
        factors.append('High-value Cash on Delivery order threshold exceeded (>৳5,000)')

    if payment_method in ['BKASH', 'NAGAD', 'SSLCOMMERZ']:
        score = max(5, score - 20)

    score = min(100, max(0, score))
    return {
        'riskScore': score,
        'riskFactors': factors
    }

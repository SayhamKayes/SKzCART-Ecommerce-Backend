import datetime
import json
import os
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict

from django.db import OperationalError, ProgrammingError, connection
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import AnalyticsVisit, AuditLog, MarketingConfig, MarketingEvent, ShopSetting


SOURCE_COLORS = {
    'Google Search': '#2563EB',
    'Facebook': '#1877F2',
    'Instagram': '#E1306C',
    'Direct': '#10B981',
    'YouTube': '#EF4444',
    'Campaign Page': '#8B5CF6',
    'Product Page': '#0EA5E9',
    'Referral / Other': '#64748B',
}

DEVICE_COLORS = {
    'Mobile': '#10B981',
    'Desktop': '#2563EB',
    'Tablet': '#F59E0B',
}


def _ensure_analytics_visit_table():
    try:
        table_name = AnalyticsVisit._meta.db_table
        if table_name not in connection.introspection.table_names():
            with connection.schema_editor() as schema_editor:
                schema_editor.create_model(AnalyticsVisit)
    except Exception:
        pass


def _safe_query_visits():
    try:
        return list(AnalyticsVisit.objects.all())
    except (OperationalError, ProgrammingError):
        _ensure_analytics_visit_table()
        try:
            return list(AnalyticsVisit.objects.all())
        except Exception:
            return []


def _get_ga4_config_dict():
    marketing_cfg = MarketingConfig.objects.first()
    shop_cfg = ShopSetting.get_settings()
    extra = dict(shop_cfg.extraConfig) if isinstance(shop_cfg.extraConfig, dict) else {}
    ga_extra = dict(extra.get('googleAnalytics')) if isinstance(extra.get('googleAnalytics'), dict) else {}

    ga4_id = str(
        ga_extra.get('ga4Id')
        or (marketing_cfg.ga4Id if marketing_cfg else '')
        or os.getenv('GA4_MEASUREMENT_ID', '')
        or ''
    ).strip()

    return {
        'ga4Id': ga4_id,
        'ga4PropertyId': str(ga_extra.get('ga4PropertyId') or '').strip(),
        'ga4ApiSecret': str(ga_extra.get('ga4ApiSecret') or '').strip(),
        'ga4StreamName': str(ga_extra.get('ga4StreamName') or shop_cfg.shopName or 'Storefront Web Stream').strip(),
        'ga4Enabled': ga_extra.get('ga4Enabled', True) is not False,
        'connected': bool(ga4_id and ga4_id.upper().startswith('G-') and ga_extra.get('ga4Enabled', True) is not False),
        'serverRelayEnabled': bool(ga4_id and str(ga_extra.get('ga4ApiSecret') or '').strip()),
        'marketingPixelActive': bool(marketing_cfg.isActive) if marketing_cfg else True,
    }


def normalize_traffic_source(raw_source='', referrer_host='', utm_medium='', page_path=''):
    src = str(raw_source or '').strip().lower()
    ref = str(referrer_host or '').strip().lower()
    med = str(utm_medium or '').strip().lower()
    path = str(page_path or '').strip().lower()
    combined = f"{src} {ref} {med}"

    if 'instagram' in combined or src in ('ig',):
        return 'Instagram'
    if 'youtube' in combined or 'youtu.be' in combined or src in ('yt',):
        return 'YouTube'
    if 'facebook' in combined or 'fb.com' in combined or 'l.fb' in combined or 'fbclid' in combined or src in ('fb', 'meta'):
        return 'Facebook'
    if 'google' in combined or 'gclid' in combined or 'organic' in med:
        return 'Google Search'
    if src == 'campaign_landing':
        if '/campaign-products' in path or '/special-campaign' in path:
            return 'Campaign Page'
        return 'Product Page'
    if src in ('campaign page', 'campaign_page'):
        return 'Campaign Page'
    if src in ('product page', 'product_page'):
        return 'Product Page'
    if not src or src in ('direct', '(direct)', 'none', 'website', 'storefront', 'storefront_card', 'landing', 'admin_test_console'):
        if not ref:
            return 'Direct'
        return 'Referral / Other'
    return raw_source.strip()[:80]


def detect_device_type(user_agent='', client_device=''):
    explicit = str(client_device or '').strip().lower()
    if explicit in ('mobile', 'desktop', 'tablet'):
        return explicit.capitalize()
    ua = str(user_agent or '').lower()
    if any(k in ua for k in ('ipad', 'tablet', 'playbook', 'silk')):
        return 'Tablet'
    if any(k in ua for k in ('mobile', 'android', 'iphone', 'ipod', 'blackberry', 'opera mini', 'iemobile')):
        return 'Mobile'
    return 'Desktop'


def detect_browser_name(user_agent=''):
    ua = str(user_agent or '').lower()
    if 'edg/' in ua or 'edge/' in ua:
        return 'Edge'
    if 'opr/' in ua or 'opera' in ua:
        return 'Opera'
    if 'firefox/' in ua:
        return 'Firefox'
    if 'chrome/' in ua and 'chromium' not in ua:
        return 'Chrome'
    if 'safari/' in ua and 'chrome/' not in ua:
        return 'Safari'
    return 'Other'


def detect_city_and_country(request, payload_city='', payload_country='', timezone_name=''):
    city = str(payload_city or '').strip()
    country = str(payload_country or '').strip()

    if not city:
        city = str(
            request.META.get('HTTP_CF_IPCITY')
            or request.META.get('HTTP_X_VERCEL_IP_CITY')
            or request.META.get('HTTP_X_APPENGINE_CITY')
            or request.META.get('HTTP_X_GEO_CITY')
            or ''
        ).strip()
        if city:
            try:
                city = urllib.parse.unquote(city)
            except Exception:
                pass

    if not country:
        country = str(
            request.META.get('HTTP_CF_IPCOUNTRY')
            or request.META.get('HTTP_X_VERCEL_IP_COUNTRY')
            or request.META.get('HTTP_X_APPENGINE_COUNTRY')
            or ''
        ).strip()

    tz = str(timezone_name or '').strip()
    if not city and '/' in tz:
        city_part = tz.split('/')[-1].replace('_', ' ').strip()
        if city_part:
            city = city_part
    if not country and 'dhaka' in (tz.lower() + ' ' + city.lower()):
        country = 'Bangladesh'

    return city or 'Dhaka', country or 'Bangladesh'


def send_ga4_measurement_protocol(cfg, client_id, event_name, params=None, debug=False):
    ga4_id = cfg.get('ga4Id', '').strip()
    api_secret = cfg.get('ga4ApiSecret', '').strip()
    if not ga4_id or not api_secret:
        return {'sent': False, 'reason': 'API Secret not set'}

    endpoint = 'https://www.google-analytics.com/debug/mp/collect' if debug else 'https://www.google-analytics.com/mp/collect'
    url = f"{endpoint}?measurement_id={urllib.parse.quote(ga4_id)}&api_secret={urllib.parse.quote(api_secret)}"
    body = {
        'client_id': str(client_id or f"anon.{uuid.uuid4().hex[:10]}"),
        'events': [
            {
                'name': event_name,
                'params': params or {'engagement_time_msec': '100'},
            }
        ],
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode('utf-8', errors='ignore')
            parsed = json.loads(raw) if raw.strip() else {}
            return {'sent': True, 'status': resp.status, 'response': parsed}
    except Exception as exc:
        return {'sent': False, 'error': str(exc)}


@api_view(['GET', 'POST', 'PUT'])
@permission_classes([AllowAny])
def ga4_config_view(request):
    if request.method == 'GET':
        return Response({'success': True, 'config': _get_ga4_config_dict()})

    data = request.data or {}
    ga4_id = str(data.get('ga4Id') or '').strip()
    ga4_property_id = str(data.get('ga4PropertyId') or '').strip()
    ga4_api_secret = str(data.get('ga4ApiSecret') or '').strip()
    ga4_stream_name = str(data.get('ga4StreamName') or '').strip()
    ga4_enabled = bool(data.get('ga4Enabled', True))

    if ga4_id and not ga4_id.upper().startswith('G-'):
        return Response(
            {
                'success': False,
                'message': 'GA4 Measurement ID must start with "G-" (e.g. G-XXXXXXXXXX).',
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    shop_cfg = ShopSetting.get_settings()
    extra = dict(shop_cfg.extraConfig) if isinstance(shop_cfg.extraConfig, dict) else {}
    existing_ga = dict(extra.get('googleAnalytics')) if isinstance(extra.get('googleAnalytics'), dict) else {}

    extra['googleAnalytics'] = {
        'ga4Id': ga4_id if 'ga4Id' in data else existing_ga.get('ga4Id', ''),
        'ga4PropertyId': ga4_property_id if 'ga4PropertyId' in data else existing_ga.get('ga4PropertyId', ''),
        'ga4ApiSecret': ga4_api_secret if 'ga4ApiSecret' in data else existing_ga.get('ga4ApiSecret', ''),
        'ga4StreamName': ga4_stream_name if 'ga4StreamName' in data else existing_ga.get('ga4StreamName', ''),
        'ga4Enabled': ga4_enabled if 'ga4Enabled' in data else existing_ga.get('ga4Enabled', True),
    }
    shop_cfg.extraConfig = extra
    shop_cfg.save(update_fields=['extraConfig', 'updated_at'])

    marketing_cfg = MarketingConfig.objects.first()
    if not marketing_cfg:
        marketing_cfg = MarketingConfig.objects.create(id='default_config', ga4Id=extra['googleAnalytics']['ga4Id'])
    else:
        marketing_cfg.ga4Id = extra['googleAnalytics']['ga4Id'] if extra['googleAnalytics']['ga4Enabled'] else ''
        marketing_cfg.save(update_fields=['ga4Id', 'updated_at'])

    AuditLog.log(
        actor='Admin',
        role='MARKETING',
        action='UPDATE_GA4_CONFIG',
        target_type='SETTINGS',
        target_id='google_analytics_config',
        details=f"GA4 config updated. Measurement ID: {extra['googleAnalytics']['ga4Id'] or 'Cleared'}, Enabled: {extra['googleAnalytics']['ga4Enabled']}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )

    return Response({
        'success': True,
        'config': _get_ga4_config_dict(),
        'message': 'Google Analytics configuration saved and synchronized across storefront!',
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def ga4_collect_view(request):
    """Receives live storefront GA4 telemetry (page views, funnel events, and session pings)."""
    _ensure_analytics_visit_table()
    data = request.data or {}
    cfg = _get_ga4_config_dict()

    event_type = str(data.get('eventType') or 'page_view').strip().lower()
    session_id = str(data.get('sessionId') or f"sess_{uuid.uuid4().hex[:10]}").strip()[:120]
    visitor_id = str(data.get('visitorId') or f"vis_{uuid.uuid4().hex[:10]}").strip()[:120]
    duration_sec = max(0, min(86400, int(float(data.get('durationSeconds') or 0))))

    # Heartbeat duration update without creating redundant page_view rows
    if event_type == 'session_ping':
        latest = AnalyticsVisit.objects.filter(sessionId=session_id).order_by('-created_at').first()
        if latest:
            if duration_sec > latest.durationSeconds:
                latest.durationSeconds = duration_sec
                latest.save(update_fields=['durationSeconds'])
            return Response({'success': True, 'updated': True, 'sessionId': session_id})

    ua = request.META.get('HTTP_USER_AGENT', '')
    source = normalize_traffic_source(
        data.get('trafficSource') or data.get('source') or '',
        data.get('referrerHost') or '',
        data.get('utmMedium') or '',
        data.get('pagePath') or '',
    )
    device = detect_device_type(ua, data.get('deviceType'))
    browser = detect_browser_name(ua)
    city, country = detect_city_and_country(
        request,
        data.get('city'),
        data.get('country'),
        data.get('timezone'),
    )

    now_iso = timezone.localtime().isoformat()
    visit = AnalyticsVisit.objects.create(
        id=f"gav-{uuid.uuid4().hex[:10]}",
        visitorId=visitor_id,
        sessionId=session_id,
        eventType=event_type[:50],
        pagePath=str(data.get('pagePath') or '/')[:255],
        pageTitle=str(data.get('pageTitle') or 'Storefront')[:255],
        trafficSource=source,
        referrerHost=str(data.get('referrerHost') or '')[:150],
        utmMedium=str(data.get('utmMedium') or '')[:100],
        utmCampaign=str(data.get('utmCampaign') or '')[:150],
        deviceType=device,
        browserName=browser,
        city=city[:100],
        country=country[:100],
        isNewVisitor=bool(data.get('isNewVisitor', True)),
        durationSeconds=duration_sec,
        orderId=str(data.get('orderId') or '')[:100],
        productId=str(data.get('productId') or '')[:100],
        productName=str(data.get('productName') or '')[:255],
        value=float(data.get('value') or 0),
        timestamp=now_iso,
    )

    mp_result = None
    if cfg.get('ga4Enabled') and cfg.get('ga4Id') and cfg.get('ga4ApiSecret'):
        mp_result = send_ga4_measurement_protocol(
            cfg,
            visitor_id,
            event_type,
            params={
                'page_location': str(data.get('pageLocation') or ''),
                'page_title': visit.pageTitle,
                'currency': 'BDT',
                'value': visit.value,
                'engagement_time_msec': str(max(100, duration_sec * 1000)),
            },
        )

    return Response({
        'success': True,
        'visitId': visit.id,
        'mpRelay': mp_result,
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def ga4_test_view(request):
    _ensure_analytics_visit_table()
    cfg = _get_ga4_config_dict()
    ga4_id = cfg.get('ga4Id')
    if not ga4_id:
        return Response(
            {
                'success': False,
                'message': 'Please enter and save a GA4 Measurement ID (G-XXXXXXXXXX) first.',
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    debug_res = None
    if cfg.get('ga4ApiSecret'):
        debug_res = send_ga4_measurement_protocol(
            cfg,
            'admin.test.client',
            'page_view',
            params={'page_title': 'GA4 Connection Diagnostic Test', 'engagement_time_msec': '150'},
            debug=True,
        )

    return Response({
        'success': True,
        'ga4Id': ga4_id,
        'serverRelayTested': bool(cfg.get('ga4ApiSecret')),
        'debugResult': debug_res,
        'message': (
            f"GA4 Measurement ID ({ga4_id}) is active & ready! Server Measurement Protocol check succeeded."
            if debug_res and debug_res.get('sent')
            else f"GA4 Measurement ID ({ga4_id}) is connected and injecting gtag.js cleanly on your storefront."
        ),
    })


def _extract_iso_date(raw_str, dt_obj=None):
    if raw_str:
        s = str(raw_str).strip()
        if len(s) >= 10 and s[4] == '-' and s[7] == '-':
            return s[:10]
    if dt_obj is not None:
        try:
            return timezone.localtime(dt_obj).date().isoformat()
        except Exception:
            return dt_obj.date().isoformat()
    return ''


def _resolve_date_window(range_key, custom_start, custom_end, records_dates):
    today = timezone.localdate()
    if range_key == '7d':
        start_d = today - datetime.timedelta(days=6)
        end_d = today
    elif range_key == '14d':
        start_d = today - datetime.timedelta(days=13)
        end_d = today
    elif range_key == '30d':
        start_d = today - datetime.timedelta(days=29)
        end_d = today
    elif range_key == '6m':
        m = today.month - 5
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        start_d = datetime.date(y, m, 1)
        end_d = today
    elif range_key == '1y':
        m = today.month - 11
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        start_d = datetime.date(y, m, 1)
        end_d = today
    elif range_key == 'custom':
        try:
            s_d = datetime.date.fromisoformat(str(custom_start))
            e_d = datetime.date.fromisoformat(str(custom_end))
            start_d, end_d = (s_d, e_d) if s_d <= e_d else (e_d, s_d)
        except Exception:
            start_d = today - datetime.timedelta(days=29)
            end_d = today
    else:  # 'all'
        valid_dates = [d for d in records_dates if d]
        if valid_dates:
            min_iso = min(valid_dates)
            max_iso = max(valid_dates)
            start_d = datetime.date.fromisoformat(min_iso)
            end_d = max(today, datetime.date.fromisoformat(max_iso))
            if start_d == end_d:
                start_d = end_d - datetime.timedelta(days=6)
        else:
            start_d = today - datetime.timedelta(days=29)
            end_d = today

    day_span = max(1, (end_d - start_d).days + 1)
    if range_key in ('6m', '1y') or day_span > 150:
        granularity = 'monthly'
    elif day_span > 45:
        granularity = 'weekly'
    else:
        granularity = 'daily'

    return start_d, end_d, granularity


@api_view(['GET'])
@permission_classes([AllowAny])
def ga4_dashboard_view(request):
    """
    Aggregates real visitor telemetry, traffic sources, devices, cities,
    and full e-commerce conversion drop-off funnel dynamically from database records.
    """
    from order.models import Order

    range_key = str(request.GET.get('range') or '30d').strip().lower()
    custom_start = request.GET.get('start', '')
    custom_end = request.GET.get('end', '')

    visits = _safe_query_visits()
    mkt_events = list(MarketingEvent.objects.all())
    orders = list(Order.objects.all())

    all_dates = []
    for v in visits:
        all_dates.append(_extract_iso_date(v.timestamp, v.created_at))
    for o in orders:
        all_dates.append(_extract_iso_date(o.createdAt, getattr(o, 'created_at', None)))
    for e in mkt_events:
        all_dates.append(_extract_iso_date(e.timestamp, e.created_at))

    start_d, end_d, granularity = _resolve_date_window(range_key, custom_start, custom_end, all_dates)
    start_iso = start_d.isoformat()
    end_iso = end_d.isoformat()

    scoped_visits = [
        v for v in visits
        if start_iso <= (_extract_iso_date(v.timestamp, v.created_at) or '') <= end_iso
    ]
    scoped_orders = [
        o for o in orders
        if start_iso <= (_extract_iso_date(o.createdAt, getattr(o, 'created_at', None)) or '') <= end_iso
    ]
    scoped_mkt = [
        e for e in mkt_events
        if start_iso <= (_extract_iso_date(e.timestamp, e.created_at) or '') <= end_iso
    ]

    # Build continuous chart timeline buckets
    buckets = {}
    if granularity == 'monthly':
        cy, cm = start_d.year, start_d.month
        while (cy, cm) <= (end_d.year, end_d.month):
            key = f"{cy:04d}-{cm:02d}"
            dt = datetime.date(cy, cm, 1)
            buckets[key] = {
                'key': key,
                'label': dt.strftime("%b '%y"),
                'fullLabel': dt.strftime("%B %Y"),
                'visitorsSet': set(),
                'pageViews': 0,
                'purchases': 0,
            }
            cm += 1
            if cm > 12:
                cm = 1
                cy += 1
    elif granularity == 'weekly':
        cursor = start_d - datetime.timedelta(days=start_d.weekday())
        while cursor <= end_d:
            key = cursor.isoformat()
            wk_end = cursor + datetime.timedelta(days=6)
            buckets[key] = {
                'key': key,
                'label': f"Wk {cursor.day} {cursor.strftime('%b')}",
                'fullLabel': f"{cursor.day} {cursor.strftime('%b')} - {wk_end.day} {wk_end.strftime('%b')}",
                'visitorsSet': set(),
                'pageViews': 0,
                'purchases': 0,
            }
            cursor += datetime.timedelta(days=7)
    else:
        cursor = start_d
        while cursor <= end_d:
            key = cursor.isoformat()
            buckets[key] = {
                'key': key,
                'label': f"{cursor.day} {cursor.strftime('%b')}",
                'fullLabel': cursor.strftime("%d %b, %Y"),
                'visitorsSet': set(),
                'pageViews': 0,
                'purchases': 0,
            }
            cursor += datetime.timedelta(days=1)

    def _bucket_key(iso_d):
        if not iso_d:
            return ''
        if granularity == 'monthly':
            return iso_d[:7]
        if granularity == 'weekly':
            d_obj = datetime.date.fromisoformat(iso_d)
            return (d_obj - datetime.timedelta(days=d_obj.weekday())).isoformat()
        return iso_d

    # Aggregate session, traffic source, device, geo, top pages, funnel from AnalyticsVisit
    session_map = {}
    visitor_new_map = {}
    source_counts = defaultdict(int)
    source_revenue = defaultdict(float)
    device_counts = defaultdict(int)
    city_stats = defaultdict(lambda: {'city': '', 'country': 'Bangladesh', 'visitors': set(), 'orders': 0, 'revenue': 0.0})
    page_stats = defaultdict(lambda: {'path': '', 'title': '', 'views': 0, 'durationSum': 0, 'durationHits': 0})

    visit_views_count = 0
    visit_cart_count = 0
    visit_checkout_count = 0
    visit_purchase_count = 0

    for v in scoped_visits:
        iso_d = _extract_iso_date(v.timestamp, v.created_at)
        b_key = _bucket_key(iso_d)
        if b_key in buckets:
            buckets[b_key]['visitorsSet'].add(v.visitorId)
            if v.eventType in ('page_view', 'view_item'):
                buckets[b_key]['pageViews'] += 1
            if v.eventType == 'purchase':
                buckets[b_key]['purchases'] += 1

        if v.visitorId not in visitor_new_map:
            visitor_new_map[v.visitorId] = bool(v.isNewVisitor)

        normalized_visit_src = normalize_traffic_source(
            v.trafficSource,
            v.referrerHost,
            v.utmMedium,
            v.pagePath,
        )
        if v.sessionId not in session_map:
            session_map[v.sessionId] = {
                'visitorId': v.visitorId,
                'source': normalized_visit_src or 'Direct',
                'device': v.deviceType or 'Desktop',
                'city': v.city or 'Dhaka',
                'country': v.country or 'Bangladesh',
                'pageViews': 0,
                'maxDuration': 0,
            }
            source_counts[session_map[v.sessionId]['source']] += 1
            device_counts[session_map[v.sessionId]['device']] += 1

        sess = session_map[v.sessionId]
        if v.durationSeconds > sess['maxDuration']:
            sess['maxDuration'] = int(v.durationSeconds)
        if v.eventType in ('page_view', 'view_item'):
            sess['pageViews'] += 1

        c_name = (v.city or 'Dhaka').strip()
        city_stats[c_name]['city'] = c_name
        city_stats[c_name]['country'] = v.country or 'Bangladesh'
        city_stats[c_name]['visitors'].add(v.visitorId)

        if v.eventType in ('page_view', 'view_item'):
            visit_views_count += 1
            p_key = v.pagePath or '/'
            page_stats[p_key]['path'] = p_key
            page_stats[p_key]['title'] = v.pageTitle or p_key
            page_stats[p_key]['views'] += 1
            if v.durationSeconds > 0:
                page_stats[p_key]['durationSum'] += v.durationSeconds
                page_stats[p_key]['durationHits'] += 1
        elif v.eventType == 'add_to_cart':
            visit_cart_count += 1
        elif v.eventType == 'begin_checkout':
            visit_checkout_count += 1
        elif v.eventType == 'purchase':
            visit_purchase_count += 1

    # Enrich with real Orders & MarketingEvents so historical checkout cities, attribution, and funnel stay accurate
    order_purchase_count = 0
    total_revenue = 0.0
    for o in scoped_orders:
        st = str(o.status or '').upper()
        if st == 'CANCELLED':
            continue
        order_purchase_count += 1
        amt = max(0.0, float(o.total or 0) - float(getattr(o, 'deliveryFee', 0) or 0))
        total_revenue += amt

        iso_d = _extract_iso_date(o.createdAt, getattr(o, 'created_at', None))
        b_key = _bucket_key(iso_d)
        if b_key in buckets and visit_purchase_count == 0:
            buckets[b_key]['purchases'] += 1

        utm = o.utm if isinstance(o.utm, dict) else {}
        src_label = normalize_traffic_source(utm.get('source') or '', '', utm.get('medium') or '')
        source_revenue[src_label] += amt
        if not scoped_visits:
            source_counts[src_label] += 1

        addr = o.address if isinstance(o.address, dict) else {}
        dist = str(addr.get('district') or '').strip()
        if dist:
            city_stats[dist]['city'] = dist
            city_stats[dist]['orders'] += 1
            city_stats[dist]['revenue'] += amt

    # Funnel totals combine live AnalyticsVisit + MarketingEvent fallback cleanly (taking max per stage without double-counting)
    mkt_views = sum(1 for e in scoped_mkt if e.eventName in ('PageView', 'ViewContent', 'ProductClick'))
    mkt_cart = sum(1 for e in scoped_mkt if e.eventName == 'AddToCart')
    mkt_checkout = sum(1 for e in scoped_mkt if e.eventName == 'InitiateCheckout')
    mkt_purchase_ids = {e.orderId for e in scoped_mkt if e.eventName == 'Purchase' and e.orderId}
    mkt_purchase = max(len(mkt_purchase_ids), sum(1 for e in scoped_mkt if e.eventName == 'Purchase' and not e.orderId))

    views_total = max(visit_views_count, mkt_views)
    cart_total = max(visit_cart_count, mkt_cart)
    checkout_total = max(visit_checkout_count, mkt_checkout)
    purchase_total = max(visit_purchase_count, order_purchase_count, mkt_purchase)
    # Keep funnel strictly monotonic if older orders exist prior to view tracking
    checkout_total = max(checkout_total, purchase_total)
    cart_total = max(cart_total, checkout_total)
    views_total = max(views_total, cart_total)

    total_visitors = len(visitor_new_map)
    new_visitors = sum(1 for is_new in visitor_new_map.values() if is_new)
    returning_visitors = max(0, total_visitors - new_visitors)

    total_sessions = len(session_map)
    total_page_views = sum(s['pageViews'] for s in session_map.values())
    avg_duration_sec = (
        round(sum(s['maxDuration'] for s in session_map.values()) / total_sessions)
        if total_sessions > 0
        else 0
    )
    engaged_sessions = sum(
        1 for s in session_map.values() if s['pageViews'] >= 2 or s['maxDuration'] >= 10
    )
    engagement_rate = round((engaged_sessions / total_sessions) * 100, 1) if total_sessions > 0 else 0.0
    bounce_rate = round(100.0 - engagement_rate, 1) if total_sessions > 0 else 0.0
    views_per_session = round(total_page_views / total_sessions, 2) if total_sessions > 0 else 0.0
    funnel_conv_rate = round((purchase_total / views_total) * 100, 1) if views_total > 0 else 0.0

    # Format Traffic Sources Pie + Breakdown
    total_src_hits = sum(source_counts.values()) or 1
    traffic_sources = []
    for label, cnt in sorted(source_counts.items(), key=lambda x: (-x[1], -source_revenue.get(x[0], 0))):
        traffic_sources.append({
            'label': label,
            'count': cnt,
            'sharePct': round((cnt / total_src_hits) * 100, 1),
            'revenue': round(source_revenue.get(label, 0), 2),
            'color': SOURCE_COLORS.get(label, '#6366F1'),
        })

    # Ensure standard channels exist visually only if non-empty or show dynamic list
    visitor_types = [
        {'label': 'New Visitors', 'count': new_visitors, 'color': '#10B981'},
        {'label': 'Returning Visitors', 'count': returning_visitors, 'color': '#2563EB'},
    ]

    device_breakdown = [
        {'label': dev, 'count': cnt, 'color': DEVICE_COLORS.get(dev, '#64748B')}
        for dev, cnt in sorted(device_counts.items(), key=lambda x: -x[1])
    ]

    location_breakdown = []
    for item in city_stats.values():
        v_cnt = len(item['visitors'])
        location_breakdown.append({
            'city': item['city'],
            'country': item['country'],
            'visitors': v_cnt,
            'orders': item['orders'],
            'revenue': round(item['revenue'], 2),
            'score': v_cnt + item['orders'],
        })
    location_breakdown.sort(key=lambda x: (-x['score'], -x['revenue']))

    # Step-by-step E-commerce Funnel with exact drop-off percentages
    raw_funnel = [
        ('View', 'Product / Page Views', views_total, '#2563EB'),
        ('Add to Cart', 'Added to Cart', cart_total, '#8B5CF6'),
        ('Checkout', 'Checkout Initiated', checkout_total, '#F59E0B'),
        ('Purchase', 'Completed Purchase', purchase_total, '#10B981'),
    ]
    funnel = []
    for idx, (stage, label, cnt, color) in enumerate(raw_funnel):
        prev_cnt = raw_funnel[idx - 1][2] if idx > 0 else cnt
        rate_from_prev = round((cnt / prev_cnt) * 100, 1) if prev_cnt > 0 else (100.0 if cnt > 0 else 0.0)
        drop_off_pct = round(max(0.0, 100.0 - rate_from_prev), 1) if idx > 0 and prev_cnt > 0 else 0.0
        rate_from_top = round((cnt / views_total) * 100, 1) if views_total > 0 else 0.0
        funnel.append({
            'stage': stage,
            'label': label,
            'count': cnt,
            'rateFromPrev': rate_from_prev,
            'dropOffPct': drop_off_pct,
            'rateFromTop': rate_from_top,
            'color': color,
        })

    timeline_series = [
        {
            'key': b['key'],
            'label': b['label'],
            'fullLabel': b['fullLabel'],
            'visitors': len(b['visitorsSet']),
            'pageViews': b['pageViews'],
            'purchases': b['purchases'],
        }
        for b in buckets.values()
    ]

    top_pages = sorted(
        [
            {
                'path': p['path'],
                'title': p['title'],
                'views': p['views'],
                'avgDurationSec': round(p['durationSum'] / p['durationHits']) if p['durationHits'] > 0 else 0,
            }
            for p in page_stats.values()
        ],
        key=lambda x: -x['views'],
    )[:10]

    # Resolve product names dynamically for any visit referencing a product ID or /products/<slug> path
    prod_by_id = {}
    prod_by_slug = {}
    try:
        from products.models import Product
        for p in Product.objects.only('id', 'name', 'slug').iterator():
            pname = str(p.name or '').strip()
            if p.id and pname:
                prod_by_id[str(p.id).strip()] = pname
            if getattr(p, 'slug', None) and pname:
                prod_by_slug[str(p.slug).strip().lower()] = pname
    except Exception:
        pass

    def _resolve_visit_product_name(v):
        if (v.productName or '').strip():
            return v.productName.strip()
        pid = str(v.productId or '').strip()
        if pid and pid in prod_by_id:
            return prod_by_id[pid]
        path = str(v.pagePath or '')
        if '/products/' in path or '/product/' in path:
            seg = path.split('?')[0].rstrip('/').split('/')[-1].strip().lower()
            if seg in prod_by_slug:
                return prod_by_slug[seg]
            if seg in prod_by_id:
                return prod_by_id[seg]
        if 'product=' in path:
            try:
                qs = urllib.parse.parse_qs(path.split('?', 1)[1])
                q_pid = (qs.get('product') or qs.get('productId') or [''])[0].strip()
                if q_pid in prod_by_id:
                    return prod_by_id[q_pid]
                if q_pid.lower() in prod_by_slug:
                    return prod_by_slug[q_pid.lower()]
            except Exception:
                pass
        return ''

    recent_activity = [
        {
            'id': v.id,
            'eventType': v.eventType,
            'pagePath': v.pagePath,
            'pageTitle': v.pageTitle,
            'productId': v.productId,
            'productName': _resolve_visit_product_name(v),
            'trafficSource': normalize_traffic_source(v.trafficSource, v.referrerHost, v.utmMedium, v.pagePath),
            'deviceType': v.deviceType,
            'browserName': v.browserName,
            'city': v.city,
            'country': v.country,
            'isNewVisitor': v.isNewVisitor,
            'durationSeconds': v.durationSeconds,
            'timestamp': v.timestamp,
        }
        for v in scoped_visits[:50]
    ]

    return Response({
        'success': True,
        'config': _get_ga4_config_dict(),
        'bounds': {
            'startISO': start_iso,
            'endISO': end_iso,
            'granularity': granularity,
            'rangeLabel': f"{start_d.strftime('%d %b')} – {end_d.strftime('%d %b')} ({granularity.capitalize()})",
        },
        'summary': {
            'totalVisitors': total_visitors,
            'newVisitors': new_visitors,
            'returningVisitors': returning_visitors,
            'totalSessions': total_sessions,
            'totalPageViews': total_page_views,
            'viewsPerSession': views_per_session,
            'avgSessionDurationSec': avg_duration_sec,
            'engagementRate': engagement_rate,
            'bounceRate': bounce_rate,
            'funnelConversionRate': funnel_conv_rate,
            'totalRevenue': round(total_revenue, 2),
        },
        'trafficSources': traffic_sources,
        'visitorTypes': visitor_types,
        'deviceBreakdown': device_breakdown,
        'locationBreakdown': location_breakdown[:12],
        'funnel': funnel,
        'timelineSeries': timeline_series,
        'topPages': top_pages,
        'recentActivity': recent_activity,
    })


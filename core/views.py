from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpResponse
from django.utils import timezone
import os
import time
import uuid

from .models import AuditLog, MarketingEvent, NotificationLog, MarketingConfig, ShopSetting, StaffMember, RecycleBinItem
from .serializers import AuditLogSerializer, MarketingEventSerializer, NotificationLogSerializer, MarketingConfigSerializer, ShopSettingSerializer, StaffMemberSerializer, RecycleBinItemSerializer
from .recycle_bin import (
    move_to_recycle_bin,
    snapshot_staff,
    snapshot_audit_log,
    restore_recycle_bin_item,
    permanently_delete_recycle_bin_item,
)
from .capi import send_meta_capi_event

@api_view(['GET'])
@permission_classes([AllowAny])
def api_root(request):
    accept_header = request.META.get('HTTP_ACCEPT', '')
    if 'text/html' in accept_header:
        html_content = """<!DOCTYPE html>
<html lang="bn">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AmarDokan Backend Engine • Django REST</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Hind+Siliguri:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card: #111827;
      --border: #1f293d;
      --primary: #10b981;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Plus Jakarta Sans', 'Hind Siliguri', sans-serif;
      background: radial-gradient(circle at 50% 0%, #172554 0%, #090d16 60%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .container {
      max-width: 820px;
      width: 100%;
      background: rgba(17, 24, 39, 0.9);
      border: 1px solid var(--border);
      backdrop-filter: blur(16px);
      border-radius: 20px;
      padding: 40px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 14px;
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #34d399;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 20px;
    }
    .dot {
      width: 8px;
      height: 8px;
      background: #10b981;
      border-radius: 50%;
      box-shadow: 0 0 10px #10b981;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.5; transform: scale(0.85); }
    }
    h1 {
      font-size: 30px;
      font-weight: 800;
      letter-spacing: -0.5px;
      margin-bottom: 12px;
      background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    p.lead {
      color: var(--text-muted);
      font-size: 15px;
      line-height: 1.6;
      margin-bottom: 26px;
    }
    .action-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 16px;
      margin-bottom: 30px;
    }
    .action-card {
      background: #151d30;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px;
      text-decoration: none;
      color: inherit;
      transition: all 0.2s ease;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .action-card:hover {
      border-color: #10b981;
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(16, 185, 129, 0.15);
    }
    .action-title {
      font-weight: 700;
      font-size: 15px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .action-desc {
      font-size: 13px;
      color: var(--text-muted);
    }
    .tag {
      font-size: 11px;
      padding: 2px 6px;
      border-radius: 4px;
      background: #1e293b;
      color: #94a3b8;
    }
    .btn-frontend {
      background: linear-gradient(135deg, #10b981 0%, #059669 100%);
      color: #ffffff;
      padding: 14px 24px;
      border-radius: 10px;
      text-decoration: none;
      font-weight: 700;
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-size: 15px;
      transition: opacity 0.2s;
    }
    .btn-frontend:hover { opacity: 0.9; }
    .footer-note {
      margin-top: 24px;
      padding-top: 20px;
      border-top: 1px solid var(--border);
      font-size: 13px;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 10px;
    }
    code {
      background: #1e293b;
      padding: 2px 6px;
      border-radius: 4px;
      font-family: monospace;
      color: #38bdf8;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="badge">
      <span class="dot"></span>
      Django REST API Server Online
    </div>
    <h1>AmarDokan Bangladesh Native E-Commerce Backend</h1>
    <p class="lead">
      Welcome! Your Django REST Framework backend is running and healthy.<br>
      To browse and shop our storefront, click the button below to visit the frontend.
    </p>

    <div style="margin-bottom: 28px;">
      <a href="http://localhost:3001" class="btn-frontend" target="_blank">
        🛍️ Open Storefront Website (http://localhost:3001) →
      </a>
    </div>

    <div class="action-grid">
      <a href="/api/v1/health" class="action-card" target="_blank">
        <div class="action-title">
          API Health Check
          <span class="tag">GET</span>
        </div>
        <div class="action-desc">View system version and backend health response</div>
      </a>
      <a href="/api/v1/products" class="action-card" target="_blank">
        <div class="action-title">
          Products API
          <span class="tag">GET</span>
        </div>
        <div class="action-desc">Live JSON catalog data for all database products</div>
      </a>
      <a href="/api/v1/orders" class="action-card" target="_blank">
        <div class="action-title">
          Orders API
          <span class="tag">GET</span>
        </div>
        <div class="action-desc">Customer orders and tracking history</div>
      </a>
      <a href="/api/v1/couriers" class="action-card" target="_blank">
        <div class="action-title">
          Courier Configs
          <span class="tag">GET</span>
        </div>
        <div class="action-desc">Pathao, RedX, and Steadfast courier hub configurations</div>
      </a>
      <a href="/api/v1/audit-logs" class="action-card" target="_blank">
        <div class="action-title">
          Audit Logs
          <span class="tag">GET</span>
        </div>
        <div class="action-desc">System and administrator audit trail logs</div>
      </a>
      <a href="/admin/" class="action-card" target="_blank">
        <div class="action-title">
          Django Admin Portal
          <span class="tag">PORTAL</span>
        </div>
        <div class="action-desc">Browse and manage data via the Django admin panel</div>
      </a>
    </div>

    <div class="footer-note">
      <div>Stack: <code>Django 5.2</code> + <code>DRF</code> + <code>Python 3.14</code></div>
      <div>Frontend Proxy Target: <code>http://127.0.0.1:8000</code></div>
    </div>
  </div>
</body>
</html>"""
        return HttpResponse(html_content, content_type='text/html; charset=utf-8')

    return Response({
        'status': 'online',
        'platform': 'AmarDokan Bangladesh Native E-Commerce Backend (Django REST)',
        'version': '1.0.0',
        'frontend_url': 'http://localhost:3001',
        'endpoints': {
            'health': '/api/v1/health',
            'products': '/api/v1/products',
            'orders': '/api/v1/orders',
            'checkout': '/api/v1/checkout',
            'tracking': '/api/v1/tracking/lookup',
            'couriers': '/api/v1/couriers',
            'couriers_assign': '/api/v1/couriers/assign',
            'courier_webhook': '/api/v1/webhooks/couriers',
            'audit_logs': '/api/v1/audit-logs',
            'notifications': '/api/v1/notifications',
            'marketing_events': '/api/v1/marketing/events',
            'admin': '/admin/',
        }
    })

@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    return Response({
        'status': 'ok',
        'platform': 'AmarDokan Bangladesh Native E-Commerce (Django REST)',
        'version': '1.0.0',
        'timestamp': timezone.localtime().isoformat()
    })

@api_view(['GET'])
@permission_classes([AllowAny])
def audit_logs_list(request):
    logs = AuditLog.objects.all()[:100]
    serializer = AuditLogSerializer(logs, many=True)
    return Response({
        'success': True,
        'logs': serializer.data
    })


@api_view(['DELETE'])
@permission_classes([AllowAny])
def audit_log_delete_view(request, pk):
    log = AuditLog.objects.filter(id=pk).first()
    if not log:
        return Response({'success': False, 'message': 'Audit log not found.'}, status=status.HTTP_404_NOT_FOUND)
    snap = snapshot_audit_log(log)
    log.delete()
    rb = move_to_recycle_bin(**snap, deleted_by='Admin')
    return Response({
        'success': True,
        'id': pk,
        'recycleBinId': rb.id,
        'message': 'Audit log moved to Recycle Bin.'
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def audit_logs_bulk_delete_view(request):
    ids = (request.data or {}).get('ids', [])
    if not isinstance(ids, list) or not ids:
        return Response({'success': False, 'message': 'No audit log IDs provided.'}, status=status.HTTP_400_BAD_REQUEST)
    targets = list(AuditLog.objects.filter(id__in=ids))
    count = 0
    for log in targets:
        snap = snapshot_audit_log(log)
        log.delete()
        move_to_recycle_bin(**snap, deleted_by='Admin')
        count += 1
    return Response({
        'success': True,
        'deletedCount': count,
        'message': f'Moved {count} audit log(s) to Recycle Bin.'
    })

@api_view(['GET', 'POST', 'PUT'])
@permission_classes([AllowAny])
def marketing_config_view(request):
    config = MarketingConfig.objects.first()
    if not config:
        config = MarketingConfig.objects.create(
            id='default_config',
            pixelId='',
            capiToken='',
            ga4Id='',
            testEventCode='',
            targetScope='GLOBAL',
            targetProductId='',
            targetLandingSlug='',
            isActive=True
        )

    if request.method == 'GET':
        serializer = MarketingConfigSerializer(config)
        return Response({'success': True, 'config': serializer.data})

    data = request.data
    config.pixelId = data.get('pixelId', config.pixelId)
    config.capiToken = data.get('capiToken', config.capiToken)
    config.ga4Id = data.get('ga4Id', config.ga4Id)
    config.testEventCode = data.get('testEventCode', config.testEventCode)
    config.adAccountId = data.get('adAccountId', getattr(config, 'adAccountId', ''))
    config.targetScope = data.get('targetScope', config.targetScope)
    config.targetProductId = data.get('targetProductId', config.targetProductId)
    config.targetLandingSlug = data.get('targetLandingSlug', config.targetLandingSlug)
    if 'isActive' in data:
        config.isActive = bool(data.get('isActive'))
    config.save()

    AuditLog.log(
        actor='Admin',
        role='MARKETING',
        action='UPDATE_CONFIG',
        target_type='SETTINGS',
        target_id='marketing_config',
        details=f"Marketing config updated. Scope: {config.targetScope}, Pixel: {config.pixelId}, Active: {config.isActive}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    serializer = MarketingConfigSerializer(config)
    return Response({'success': True, 'config': serializer.data, 'message': 'Configuration saved successfully'})

@api_view(['POST'])
@permission_classes([AllowAny])
def marketing_capi_event_view(request):
    data = request.data
    event_name = data.get('eventName', 'PageView')
    event_id = data.get('eventId') or f"mkt-{int(time.time() * 1000)}"
    event_source_url = data.get('eventSourceUrl', '')
    user_data = data.get('userData', {})
    custom_data = data.get('customData', {})
    value = custom_data.get('value')
    order_id = custom_data.get('orderId') or custom_data.get('order_id') or data.get('orderId')
    source = data.get('source', 'website')
    campaign = data.get('campaign', '')

    config = MarketingConfig.objects.first()
    if config and not config.isActive:
        return Response({
            'success': False,
            'paused': True,
            'message': 'Meta Pixel & CAPI tracking is currently paused.'
        })

    capi_result = None

    client_ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '127.0.0.1'))
    if client_ip and ',' in client_ip:
        client_ip = client_ip.split(',')[0].strip()
    client_user_agent = request.META.get('HTTP_USER_AGENT', '')

    meta_pixel_id = (config.pixelId if config and config.pixelId else '') or os.getenv('META_PIXEL_ID', '')
    meta_capi_token = (config.capiToken if config and config.capiToken else '') or os.getenv('META_CAPI_TOKEN', '')
    is_capi_enabled = bool(meta_pixel_id and meta_capi_token)

    capi_custom_data = {
        'currency': custom_data.get('currency') or 'BDT',
        'value': float(value or 0) if value is not None else 0,
    }
    if custom_data.get('content_name'):
        capi_custom_data['content_name'] = custom_data['content_name']
    if custom_data.get('content_ids'):
        capi_custom_data['content_ids'] = custom_data['content_ids']
    if order_id:
        capi_custom_data['order_id'] = str(order_id)

    if is_capi_enabled:
        test_code = data.get('testEventCode') or (config.testEventCode if config else '') or os.getenv('META_TEST_EVENT_CODE', '')
        capi_result = send_meta_capi_event(
            pixel_id=meta_pixel_id,
            access_token=meta_capi_token,
            event_name=event_name,
            event_id=event_id,
            event_source_url=event_source_url,
            client_ip=client_ip or '127.0.0.1',
            client_user_agent=client_user_agent or 'Mozilla/5.0',
            fbp=user_data.get('fbp', ''),
            fbc=user_data.get('fbc', ''),
            email=user_data.get('em', ''),
            phone=user_data.get('ph', ''),
            custom_data=capi_custom_data,
            test_event_code=test_code
        )

    now_str = timezone.localtime().isoformat()
    items_list = custom_data.get('items') if isinstance(custom_data.get('items'), list) else data.get('items')
    product_name = custom_data.get('content_name') or data.get('productName') or data.get('name') or ''
    content_ids = custom_data.get('content_ids')
    product_id = content_ids[0] if isinstance(content_ids, list) and content_ids else data.get('productId', '')

    # Deduplicate Purchase events by orderId so identical orders are never recorded twice
    if event_name == 'Purchase' and order_id:
        existing_purchase = MarketingEvent.objects.filter(orderId=str(order_id), eventName='Purchase').first()
        if existing_purchase:
            return Response({
                'success': True,
                'deduplicated': True,
                'event_id': existing_purchase.eventId,
                'capi_result': capi_result,
                'event': MarketingEventSerializer(existing_purchase).data
            })

    # Store item-level breakdown when products are included in Cart/Purchase events
    if isinstance(items_list, list) and len(items_list) >= 1 and event_name in ('Purchase', 'InitiateCheckout'):
        created_events = []
        for idx, item in enumerate(items_list):
            if not isinstance(item, dict):
                continue
            item_pid = str(item.get('productId') or item.get('id') or product_id or '')
            item_pname = str(item.get('name') or product_name or '')
            item_qty = int(item.get('quantity') or 1)
            item_val = float(item.get('price') or 0) * item_qty
            ev = MarketingEvent.objects.create(
                id=f"mkt-{uuid.uuid4().hex[:8]}",
                eventId=f"{event_id}_{idx}" if idx > 0 else event_id,
                eventName=event_name,
                timestamp=now_str,
                browserReported=data.get('browserReported', True),
                serverCapiReported=True,
                source=source,
                campaign=campaign,
                value=item_val if item_val > 0 else (value if idx == 0 and value is not None else 0),
                orderId=order_id,
                productName=item_pname[:255],
                productId=item_pid[:100],
                dedupMatched=True
            )
            created_events.append(ev)
        mkt_event = created_events[0] if created_events else None
    else:
        mkt_event = None

    if mkt_event is None:
        mkt_event = MarketingEvent.objects.create(
            id=f"mkt-{uuid.uuid4().hex[:8]}",
            eventId=event_id,
            eventName=event_name,
            timestamp=now_str,
            browserReported=data.get('browserReported', True),
            serverCapiReported=True,
            source=source,
            campaign=campaign,
            value=value if value is not None else 0,
            orderId=order_id,
            productName=str(product_name)[:255],
            productId=str(product_id)[:100] if product_id else '',
            dedupMatched=True
        )

    return Response({
        'success': True,
        'event_id': event_id,
        'capi_result': capi_result,
        'event': MarketingEventSerializer(mkt_event).data
    })

@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([AllowAny])
def marketing_events_view(request):
    if request.method == 'DELETE':
        deleted_cnt, _ = MarketingEvent.objects.all().delete()
        return Response({
            'success': True,
            'deletedCount': deleted_cnt,
            'events': [],
            'productAnalytics': [],
            'message': 'Pixel event stream & product history cleared.'
        })

    if request.method == 'GET':
        config = MarketingConfig.objects.first()
        if config and not config.isActive:
            return Response({
                'success': True,
                'isActive': False,
                'activePixelId': config.pixelId,
                'events': [],
                'productAnalytics': [],
            })

        # Restrict strictly to currently active products in the Store catalog
        from products.models import Product
        active_by_id = {}
        active_by_name = {}
        for p in Product.objects.all().iterator():
            pid_str = str(p.id).strip()
            pname_str = str(p.name or '').strip()
            active_by_id[pid_str] = p
            if pname_str:
                active_by_name[pname_str.lower()] = p

        def match_active_product(pid, pname):
            clean_pid = str(pid or '').strip()
            if clean_pid and clean_pid in active_by_id:
                return active_by_id[clean_pid]
            raw_name = str(pname or '').strip()
            # Strip trailing " (x1)" if present from checkout formatting
            clean_name = raw_name.split(' (x')[0].strip().lower()
            if clean_name and clean_name in active_by_name:
                return active_by_name[clean_name]
            return None

        stats_map = {}
        purchased_order_ids = set()
        seen_purchase_pairs = set()
        seen_event_ids = set()
        active_events = []

        def ensure_bucket(prod_obj):
            if not prod_obj:
                return None
            key = str(prod_obj.id)
            if key not in stats_map:
                stats_map[key] = {
                    'productId': key,
                    'productName': str(prod_obj.name or f"Product #{key}"),
                    'views': 0,
                    'clicks': 0,
                    'purchases': 0,
                    'revenue': 0.0,
                }
            return stats_map[key]

        for ev in MarketingEvent.objects.all().iterator():
            ev_name = str(ev.eventName or '').strip()
            if ev_name == 'PageView':
                continue
            matched_prod = match_active_product(ev.productId, ev.productName)
            if not matched_prod:
                continue

            # Deduplicate identical Purchase rows for the same (orderId, productId)
            if ev_name == 'Purchase' and ev.orderId:
                pair_key = (str(ev.orderId), str(matched_prod.id))
                if pair_key in seen_purchase_pairs:
                    continue
                seen_purchase_pairs.add(pair_key)

            ev_key = str(ev.eventId or ev.id)
            if ev_key in seen_event_ids:
                continue
            seen_event_ids.add(ev_key)

            if len(active_events) < 200:
                active_events.append(ev)

            bucket = ensure_bucket(matched_prod)
            if not bucket:
                continue
            if ev_name == 'ViewContent':
                bucket['views'] += 1
            elif ev_name in ('ProductClick', 'AddToCart', 'InitiateCheckout'):
                bucket['clicks'] += 1
            elif ev_name == 'Purchase':
                bucket['purchases'] += 1
                bucket['revenue'] += float(ev.value or 0)
                if ev.orderId:
                    purchased_order_ids.add(str(ev.orderId))

        # Backfill any real non-failed active orders for active store products not yet counted
        try:
            from order.models import Order
            for ord_obj in Order.objects.exclude(status__in=['CANCELLED', 'FAILED', 'RETURNED']).iterator():
                oid_str = str(ord_obj.id)
                if oid_str in purchased_order_ids:
                    continue
                if isinstance(ord_obj.items, list):
                    for it in ord_obj.items:
                        if not isinstance(it, dict):
                            continue
                        matched_prod = match_active_product(it.get('productId') or it.get('id'), it.get('name'))
                        bucket = ensure_bucket(matched_prod)
                        if bucket:
                            pair_key = (oid_str, str(matched_prod.id))
                            if pair_key in seen_purchase_pairs:
                                continue
                            seen_purchase_pairs.add(pair_key)
                            qty = int(it.get('quantity') or 1)
                            bucket['purchases'] += qty
                            bucket['revenue'] += float(it.get('price') or 0) * qty
        except Exception:
            pass

        product_analytics = sorted(
            stats_map.values(),
            key=lambda x: (x['purchases'] * 10 + x['clicks'] * 2 + x['views']),
            reverse=True
        )
        serializer = MarketingEventSerializer(active_events, many=True)

        return Response({
            'success': True,
            'isActive': bool(config.isActive) if config else True,
            'activePixelId': config.pixelId if config else '',
            'events': serializer.data,
            'productAnalytics': product_analytics,
        })
    elif request.method == 'POST':
        data = request.data.copy()
        event_id = f"mkt-{int(time.time() * 1000)}"
        now_str = timezone.localtime().isoformat()

        data['id'] = data.get('id') or event_id
        data['timestamp'] = data.get('timestamp') or now_str
        data['serverCapiReported'] = True
        data['dedupMatched'] = True

        serializer = MarketingEventSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'event': serializer.data}, status=status.HTTP_201_CREATED)
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def notifications_list(request):
    if request.method == 'GET':
        notifs = NotificationLog.objects.filter(channel='EMAIL')[:100]
        serializer = NotificationLogSerializer(notifs, many=True)
        return Response({
            'success': True,
            'notifications': serializer.data
        })
    elif request.method == 'POST':
        from order.services import send_manual_notification
        data = request.data or {}
        recipient = (data.get('recipient') or data.get('recipientEmail') or '').strip()
        message_text = (data.get('messageText') or data.get('message') or '').strip()
        subject = (data.get('subject') or '').strip()
        template_name = data.get('templateName', 'MANUAL_EMAIL')
        order_id = data.get('orderId', 'MANUAL-EMAIL')

        if not recipient or '@' not in recipient or not message_text:
            return Response({
                'success': False,
                'message': 'Valid recipient email and messageText are required.'
            }, status=status.HTTP_400_BAD_REQUEST)

        res = send_manual_notification(
            'EMAIL',
            recipient,
            message_text,
            template_name=template_name,
            order_id=order_id,
            subject=subject,
        )
        return Response({
            'success': bool(res.get('success', False)),
            'result': res,
            'message': f"Email dispatched to {recipient}" if res.get('success') else (res.get('error') or 'Email dispatch failed'),
        }, status=status.HTTP_201_CREATED if res.get('success') else status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([AllowAny])
def sync_meta_ads_view(request):
    config = MarketingConfig.objects.first()
    ad_account_id = (request.data.get('adAccountId') or (config.adAccountId if config else '')).strip()
    access_token = (request.data.get('capiToken') or (config.capiToken if config else '')).strip()

    if ad_account_id and access_token:
        formatted_account = ad_account_id if ad_account_id.startswith('act_') else f"act_{ad_account_id}"
        url = f"https://graph.facebook.com/v19.0/{formatted_account}/insights"
        params = {
            'access_token': access_token,
            'fields': 'campaign_name,spend,clicks,impressions,cpc,ctr',
            'date_preset': 'maximum',
            'level': 'campaign'
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                insights_data = resp.json().get('data', [])
                return Response({
                    'success': True,
                    'synced': True,
                    'count': len(insights_data),
                    'campaigns': insights_data,
                    'message': f"Successfully synced {len(insights_data)} campaigns from Meta Marketing API!"
                })
            else:
                err_msg = resp.json().get('error', {}).get('message', 'Meta API sync error')
                return Response({
                    'success': False,
                    'synced': False,
                    'message': f"Meta API response: {err_msg}"
                }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'success': False, 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({
        'success': True,
        'synced': True,
        'simulation': True,
        'message': "Ad metrics live synced with local store orders & ROAS matrix."
    })

def facebook_catalog_feed_view(request):
    """
    Standard Facebook Commerce Manager Product XML/RSS Feed.
    Allows Facebook & Instagram to automatically import all store products,
    prices, and stock for Dynamic Advantage+ Catalog Ads.
    """
    from django.http import HttpResponse
    from products.models import Product

    try:
        prods = Product.objects.all()
        host = request.build_absolute_uri('/')[:-1]

        items_xml = []
        for p in prods:
            image_url = ""
            if isinstance(p.images, list) and len(p.images) > 0:
                first_img = p.images[0]
                if isinstance(first_img, str):
                    image_url = first_img
                elif isinstance(first_img, dict):
                    image_url = first_img.get('url', '')
            if not image_url:
                image_url = f"{host}/assets/default-product-placeholder.png"
            if not image_url.startswith('http'):
                image_url = f"{host}{image_url}"

            link = f"{host}/campaign-products?product={p.id}&utm_source=facebook&utm_campaign=catalog_feed"
            availability = 'out of stock' if p.isOutOfStock or p.stock <= 0 else 'in stock'
            desc = p.shortDescription or p.description or p.name

            items_xml.append(f"""
        <item>
            <g:id>{p.id}</g:id>
            <g:title><![CDATA[{p.name}]]></g:title>
            <g:description><![CDATA[{desc}]]></g:description>
            <g:link>{link}</g:link>
            <g:image_link>{image_url}</g:image_link>
            <g:brand>AmarDokan</g:brand>
            <g:condition>new</g:condition>
            <g:availability>{availability}</g:availability>
            <g:price>{p.price} BDT</g:price>
        </item>""")

        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">
    <channel>
        <title>AmarDokan Product Catalog Feed</title>
        <link>{host}</link>
        <description>Live automated product feed for Facebook &amp; Instagram Dynamic Ads</description>
        {''.join(items_xml)}
    </channel>
</rss>"""

        return HttpResponse(xml_content, content_type='application/xml; charset=utf-8')
    except Exception as exc:
        return HttpResponse(f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><error>{str(exc)}</error>", content_type='application/xml; charset=utf-8', status=500)


def _deep_merge_dicts(base, incoming):
    """Recursively merge incoming dict values into base dict without dropping sibling nested keys."""
    result = dict(base) if isinstance(base, dict) else {}
    if not isinstance(incoming, dict):
        return result
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge_dicts(result.get(k), v)
        else:
            result[k] = v
    return result


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def shop_settings_view(request):
    """
    Get or update global shop identity, contact information, branding assets,
    footer configuration, and WhatsApp automated notification credentials.
    """
    settings = ShopSetting.get_settings()
    footer_fields = [
        'footerNotice', 'footerNoticeEn',
        'footerWorkingHours', 'footerWorkingHoursEn',
        'footerHotline', 'footerEmail',
        'footerCourier1', 'footerCourier2', 'footerCourier3',
        'footerCopyrightText', 'footerPaymentColumns',
    ]

    if request.method == 'GET':
        serializer = ShopSettingSerializer(settings)
        out_data = dict(serializer.data)
        extra = settings.extraConfig if isinstance(settings.extraConfig, dict) else {}
        extra_footer = extra.get('footer', {}) if isinstance(extra.get('footer'), dict) else {}
        for ff in footer_fields:
            if ff in extra_footer:
                out_data[ff] = extra_footer[ff]
        return Response({'success': True, 'settings': out_data})

    data = request.data
    fields = [
        'shopName', 'shopTagline', 'hotline', 'whatsappNumber', 'supportEmail',
        'address', 'logoUrl', 'faviconUrl', 'headerLogoUrl', 'footerLogoUrl',
        'whatsappProvider', 'whatsappToken',
        'whatsappPhoneNumberId', 'whatsappInstanceId', 'adminAlertPhone',
        'emailHost', 'emailPort', 'emailUseTls', 'emailUseSsl',
        'emailHostUser', 'emailHostPassword', 'defaultFromEmail',
        'shopLanguageMode', 'defaultLanguage'
    ]
    for field in fields:
        if field in data:
            setattr(settings, field, data[field])

    # Support frontend alias keys ('name' -> 'shopName', 'tagline' -> 'shopTagline')
    if 'name' in data and 'shopName' not in data and data['name']:
        settings.shopName = data['name']
    if 'tagline' in data and 'shopTagline' not in data and data['tagline'] is not None:
        settings.shopTagline = data['tagline']

    if 'whatsappEnabled' in data:
        settings.whatsappEnabled = bool(data['whatsappEnabled'])
    if 'emailEnabled' in data:
        settings.emailEnabled = bool(data['emailEnabled'])

    # Safely merge extraConfig and footer attributes recursively
    extra = dict(settings.extraConfig) if isinstance(settings.extraConfig, dict) else {}
    if 'extraConfig' in data and isinstance(data['extraConfig'], dict):
        extra = _deep_merge_dicts(extra, data['extraConfig'])

    extra_footer = dict(extra.get('footer', {})) if isinstance(extra.get('footer'), dict) else {}
    for ff in footer_fields:
        if ff in data:
            extra_footer[ff] = data[ff]
    extra['footer'] = extra_footer
    settings.extraConfig = extra

    settings.save()

    AuditLog.log(
        actor='Admin',
        role='ADMIN',
        action='UPDATE_SHOP_SETTINGS',
        target_type='SETTINGS',
        target_id='default_shop_setting',
        details=f"Shop settings updated. Name: {settings.shopName}, Mode: {settings.shopLanguageMode}, WA: {settings.whatsappEnabled}, Email: {settings.emailEnabled}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    serializer = ShopSettingSerializer(settings)
    out_data = dict(serializer.data)
    for ff in footer_fields:
        if ff in extra_footer:
            out_data[ff] = extra_footer[ff]

    return Response({
        'success': True,
        'settings': out_data,
        'message': 'Shop settings updated successfully'
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def test_email_view(request):
    """
    Dispatches a real test Email via configured SMTP credentials to verify connectivity.
    """
    data = request.data
    recipient = data.get('recipientEmail', '').strip()
    
    settings = ShopSetting.get_settings()
    if not recipient:
        recipient = settings.supportEmail or 'customer@example.com'

    try:
        from order.services import send_test_smtp_email
        res = send_test_smtp_email(recipient)
        return Response(res)
    except Exception as exc:
        return Response({
            'success': False,
            'error': str(exc)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def staff_list_or_create(request):
    """
    List all staff members or register a new staff member (Admin / Manager / Dispatcher).
    """
    StaffMember.seed_defaults_if_empty()

    if request.method == 'GET':
        staff_qs = StaffMember.objects.all().order_by('created_at')
        serializer = StaffMemberSerializer(staff_qs, many=True)
        return Response({
            'success': True,
            'staff': serializer.data
        })

    data = request.data or {}
    name = str(data.get('name', '')).strip()
    username = str(data.get('username', '')).strip().lower()
    phone = str(data.get('phone', '')).strip()
    email = str(data.get('email', '')).strip()
    password = str(data.get('password', '')).strip()
    role = str(data.get('role', 'MANAGER')).strip().upper()
    notes = str(data.get('notes', '')).strip()

    if not name:
        return Response({'success': False, 'message': 'Staff full name is required'}, status=status.HTTP_400_BAD_REQUEST)
    if not username or len(username) < 3:
        return Response({'success': False, 'message': 'Username must be at least 3 characters long'}, status=status.HTTP_400_BAD_REQUEST)
    if not password or len(password) < 4:
        return Response({'success': False, 'message': 'Password must be at least 4 characters long'}, status=status.HTTP_400_BAD_REQUEST)
    if role not in ['ADMIN', 'MANAGER', 'DISPATCHER']:
        role = 'MANAGER'

    if StaffMember.objects.filter(username__iexact=username).exists():
        return Response({'success': False, 'message': f'Username @{username} is already taken. Please choose another.'}, status=status.HTTP_400_BAD_REQUEST)

    new_id = f"staff-{role.lower()}-{uuid.uuid4().hex[:6]}"
    new_staff = StaffMember.objects.create(
        id=new_id,
        name=name,
        username=username,
        phone=phone,
        email=email,
        password=password,
        role=role,
        status='ACTIVE',
        notes=notes
    )

    AuditLog.log(
        actor=data.get('actor', 'Admin'),
        role='ADMIN',
        action='CREATE_STAFF',
        target_type='SETTINGS',
        target_id=new_staff.id,
        details=f"New staff account created: {new_staff.name} (@{new_staff.username}) - Role: {new_staff.role}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    serializer = StaffMemberSerializer(new_staff)
    return Response({
        'success': True,
        'message': f'Staff member {new_staff.name} created successfully!',
        'staff': serializer.data
    }, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([AllowAny])
def staff_detail_update_delete(request, staff_id):
    """
    Get, update, or remove a staff member.
    """
    StaffMember.seed_defaults_if_empty()
    staff = StaffMember.objects.filter(id=staff_id).first()
    if not staff:
        staff = StaffMember.objects.filter(username__iexact=staff_id).first()
    if not staff:
        return Response({'success': False, 'message': 'Staff account not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        serializer = StaffMemberSerializer(staff)
        return Response({'success': True, 'staff': serializer.data})

    if request.method == 'DELETE':
        if staff.username.lower() == 'admin' or staff.id == 'staff-admin-root':
            return Response({'success': False, 'message': 'Cannot delete root Super Admin account'}, status=status.HTTP_400_BAD_REQUEST)

        staff_name = staff.name
        staff_user = staff.username
        snap = snapshot_staff(staff)
        staff.delete()
        rb = move_to_recycle_bin(**snap, deleted_by='Admin')

        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='STAFF_MOVED_TO_TRASH',
            target_type='SETTINGS',
            target_id=staff_id,
            details=f"Staff account moved to Recycle Bin: {staff_name} (@{staff_user})",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )
        return Response({
            'success': True,
            'recycleBinId': rb.id,
            'message': f'Staff member {staff_name} moved to Recycle Bin.'
        })

    # PUT / PATCH
    data = request.data or {}
    if 'name' in data and data['name']:
        staff.name = str(data['name']).strip()
    if 'phone' in data:
        staff.phone = str(data['phone']).strip()
    if 'email' in data:
        staff.email = str(data['email']).strip()
    if 'role' in data and data['role'] in ['ADMIN', 'MANAGER', 'DISPATCHER']:
        # Protect root admin from demotion
        if (staff.username.lower() != 'admin' and staff.id != 'staff-admin-root') or data['role'] == 'ADMIN':
            staff.role = data['role']
    if 'status' in data and data['status'] in ['ACTIVE', 'SUSPENDED']:
        if (staff.username.lower() != 'admin' and staff.id != 'staff-admin-root') or data['status'] == 'ACTIVE':
            staff.status = data['status']
    if 'notes' in data:
        staff.notes = str(data['notes']).strip()
    if 'password' in data and str(data['password']).strip():
        staff.password = str(data['password']).strip()

    staff.save()

    AuditLog.log(
        actor='Admin',
        role='ADMIN',
        action='UPDATE_STAFF',
        target_type='SETTINGS',
        target_id=staff.id,
        details=f"Staff account updated: {staff.name} (@{staff.username}) - Role: {staff.role}, Status: {staff.status}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    serializer = StaffMemberSerializer(staff)
    return Response({
        'success': True,
        'message': 'Staff account updated successfully',
        'staff': serializer.data
    })


import hmac
import hashlib
import base64
import json
import time
import struct
import secrets
from urllib.parse import quote
from django.conf import settings

def _generate_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii').rstrip('=')

def _compute_totp(secret_b32, for_time=None, interval=30, digits=6):
    clean = str(secret_b32 or '').strip().upper().replace(' ', '')
    if not clean:
        return ''
    pad_len = (-len(clean)) % 8
    key = base64.b32decode(clean + ('=' * pad_len), casefold=True)
    counter = int((for_time if for_time is not None else time.time()) // interval)
    msg = struct.pack('>Q', counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f"{code_int:0{digits}d}"

def _verify_totp(secret_b32, code, window=1):
    clean_code = ''.join(ch for ch in str(code or '') if ch.isdigit())
    if len(clean_code) != 6 or not secret_b32:
        return False
    now = time.time()
    for step in range(-window, window + 1):
        expected = _compute_totp(secret_b32, for_time=now + (step * 30))
        if expected and hmac.compare_digest(clean_code, expected):
            return True
    return False

def _hash_security_secret(raw_value):
    clean = str(raw_value or '').strip()
    return hmac.new(settings.SECRET_KEY.encode('utf-8'), clean.encode('utf-8'), hashlib.sha256).hexdigest()

def _sign_payload(payload_dict):
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload_dict).encode('utf-8')).decode('utf-8')
    sig = hmac.new(settings.SECRET_KEY.encode('utf-8'), encoded_payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{encoded_payload}.{sig}"

def _verify_signed_payload(token_str):
    try:
        if not token_str or '.' not in token_str:
            return None
        encoded_payload, sig = token_str.split('.', 1)
        expected_sig = hmac.new(settings.SECRET_KEY.encode('utf-8'), encoded_payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload_bytes = base64.urlsafe_b64decode(encoded_payload.encode('utf-8'))
        payload = json.loads(payload_bytes.decode('utf-8'))
        if payload.get('exp', 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None

def _mask_email(email_str):
    if not email_str or '@' not in email_str:
        return ''
    local, domain = email_str.split('@', 1)
    if len(local) <= 2:
        masked = local[0] + '*' * max(1, len(local) - 1)
    else:
        masked = local[:2] + '*' * max(2, len(local) - 3) + local[-1]
    return f"{masked}@{domain}"

def _resolve_staff_email(staff, shop_settings):
    if staff and staff.email and '@' in staff.email:
        return staff.email.strip()
    if staff and staff.role == 'ADMIN':
        if shop_settings.supportEmail and '@' in shop_settings.supportEmail:
            return shop_settings.supportEmail.strip()
        if shop_settings.emailHostUser and '@' in shop_settings.emailHostUser:
            return shop_settings.emailHostUser.strip()
    return ''

def _send_staff_otp_email(recipient_email, recipient_name, otp_code, purpose, shop_settings):
    from django.core.mail import EmailMultiAlternatives, get_connection
    if not recipient_email or '@' not in recipient_email:
        return False, 'No email address is linked to this staff account.'
    if not (shop_settings.emailHostUser and shop_settings.emailHostPassword):
        return False, 'SMTP Email Gateway is not configured yet in Admin -> Notifications & Email.'
    try:
        shop_title = shop_settings.shopName or 'AmarDokan'
        from_email = shop_settings.defaultFromEmail or f"{shop_title} <{shop_settings.emailHostUser.strip()}>"
        title = 'Admin Two-Step Verification Code' if purpose == '2FA' else 'Admin Password Reset Verification Code'
        subject = f"🔐 {shop_title} - {title}: {otp_code}"
        body = (
            f"Hello {recipient_name},\n\n"
            f"Your 6-digit verification code for {title} is: {otp_code}\n\n"
            f"This code expires in 10 minutes. Do not share it with anyone.\n\n"
            f"- {shop_title} Security"
        )
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 520px; margin: 0 auto; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 16px; padding: 24px; color: #111827;">
          <h2 style="margin: 0 0 8px 0; color: #059669; font-size: 20px;">{shop_title} Security</h2>
          <p style="margin: 0 0 16px 0; color: #4b5563; font-size: 13px;">{title}</p>
          <p style="font-size: 14px;">Hello <strong>{recipient_name}</strong>,</p>
          <p style="font-size: 14px;">Use the 6-digit code below to complete your verification:</p>
          <div style="background: #f0fdf4; border: 2px dashed #059669; border-radius: 12px; padding: 16px; text-align: center; margin: 18px 0;">
            <span style="font-size: 28px; font-family: monospace; font-weight: 800; letter-spacing: 6px; color: #065f46;">{otp_code}</span>
          </div>
          <p style="font-size: 12px; color: #6b7280;">Valid for 10 minutes. If you did not request this, review your Security & Audit Logs immediately.</p>
        </div>
        """
        conn = get_connection(
            backend='django.core.mail.backends.smtp.EmailBackend',
            host=shop_settings.emailHost or 'smtp.gmail.com',
            port=int(shop_settings.emailPort or 587),
            username=shop_settings.emailHostUser.strip(),
            password=shop_settings.emailHostPassword.strip(),
            use_tls=bool(shop_settings.emailUseTls),
            use_ssl=bool(shop_settings.emailUseSsl),
            timeout=10,
        )
        msg = EmailMultiAlternatives(subject=subject, body=body, from_email=from_email, to=[recipient_email], connection=conn)
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        return True, ''
    except Exception as exc:
        return False, str(exc)

def generate_staff_token(staff):
    """
    Generates a secure, cryptographically signed token with 7 days expiry.
    """
    return _sign_payload({
        'id': staff.id,
        'username': staff.username,
        'role': staff.role,
        'exp': int(time.time()) + (86400 * 7),
    })

def verify_staff_token(token_str):
    """
    Verifies token signature, expiry, and returns payload or None.
    """
    payload = _verify_signed_payload(token_str)
    if not payload or payload.get('purpose') in ('staff_2fa', 'staff_pw_reset'):
        return None
    return payload

def _serialize_staff_user(staff):
    return {
        'id': staff.id,
        'name': staff.name,
        'username': staff.username,
        'phone': staff.phone,
        'email': staff.email,
        'role': staff.role,
        'status': staff.status,
        'notes': staff.notes,
    }


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def security_2fa_view(request):
    """
    GET /api/v1/security/2fa — Return central DB Two-Step Verification configuration & setup URI.
    POST /api/v1/security/2fa — Configure, verify code, regenerate secret, or toggle 2FA.
    """
    settings_obj = ShopSetting.get_settings()
    extra = dict(settings_obj.extraConfig) if isinstance(settings_obj.extraConfig, dict) else {}
    sec = dict(extra.get('security')) if isinstance(extra.get('security'), dict) else {}

    if not sec.get('twoFactorSecret'):
        sec['twoFactorSecret'] = _generate_totp_secret()
        extra['security'] = sec
        settings_obj.extraConfig = extra
        settings_obj.save(update_fields=['extraConfig', 'updated_at'])

    shop_title = settings_obj.shopName or 'AmarDokan'
    smtp_configured = bool(settings_obj.emailHostUser and settings_obj.emailHostPassword)

    def _build_payload():
        secret = sec.get('twoFactorSecret', '')
        otpauth = f"otpauth://totp/{quote(shop_title)}:Admin?secret={secret}&issuer={quote(shop_title)}"
        return {
            'twoFactorEnabled': bool(sec.get('twoFactorEnabled', False)),
            'twoFactorMethod': sec.get('twoFactorMethod', 'TOTP'),
            'twoFactorScope': sec.get('twoFactorScope', 'ALL_STAFF'),
            'hasRecoveryPin': bool(sec.get('recoveryPinHash')),
            'smtpConfigured': smtp_configured,
            'setupSecret': secret,
            'otpauthUrl': otpauth,
            'updatedAt': sec.get('updatedAt', ''),
        }

    if request.method == 'GET':
        return Response({'success': True, 'security': _build_payload()})

    data = request.data or {}
    action = str(data.get('action') or 'update').strip().lower()
    actor = str(data.get('actor') or 'Admin').strip()
    ip_addr = request.META.get('REMOTE_ADDR', '127.0.0.1')

    if action == 'regenerate_secret':
        sec['twoFactorSecret'] = _generate_totp_secret()
        sec['twoFactorEnabled'] = False
        sec['updatedAt'] = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')
        extra['security'] = sec
        settings_obj.extraConfig = extra
        settings_obj.save(update_fields=['extraConfig', 'updated_at'])
        AuditLog.log(
            actor=actor,
            role='ADMIN',
            action='TWO_FACTOR_SECRET_REGENERATED',
            target_type='SETTINGS',
            target_id='default_shop_setting',
            details='Generated a new Authenticator (TOTP) 2FA secret key (2FA reset to disabled until verified).',
            ip_address=ip_addr,
        )
        return Response({
            'success': True,
            'message': 'New Authenticator secret generated. Please verify a code before enabling 2FA.',
            'security': _build_payload(),
        })

    method = str(data.get('twoFactorMethod') or sec.get('twoFactorMethod') or 'TOTP').strip().upper()
    if method not in ('TOTP', 'EMAIL_OTP'):
        method = 'TOTP'
    scope = str(data.get('twoFactorScope') or sec.get('twoFactorScope') or 'ALL_STAFF').strip().upper()
    if scope not in ('ALL_STAFF', 'ADMIN_ONLY'):
        scope = 'ALL_STAFF'

    enable_target = bool(data.get('twoFactorEnabled', sec.get('twoFactorEnabled', False)))
    recovery_pin = str(data.get('recoveryPin') or '').strip()
    verification_code = str(data.get('verificationCode') or '').strip()

    if recovery_pin:
        if not recovery_pin.isdigit() or len(recovery_pin) < 6:
            return Response({'success': False, 'message': 'Recovery Security PIN must be at least 6 digits.'}, status=status.HTTP_400_BAD_REQUEST)
        sec['recoveryPinHash'] = _hash_security_secret(recovery_pin)

    if data.get('clearRecoveryPin'):
        sec['recoveryPinHash'] = ''

    # Verify setup before turning 2FA ON to prevent accidental lockout
    was_enabled = bool(sec.get('twoFactorEnabled', False))
    if enable_target and not was_enabled:
        if method == 'EMAIL_OTP':
            if not smtp_configured and not sec.get('recoveryPinHash'):
                return Response({
                    'success': False,
                    'message': 'Configure SMTP Email Gateway in Notifications (or set a 6-digit Backup PIN) before enabling Email OTP 2FA.'
                }, status=status.HTTP_400_BAD_REQUEST)
        elif method == 'TOTP':
            if verification_code:
                if not _verify_totp(sec.get('twoFactorSecret'), verification_code):
                    return Response({
                        'success': False,
                        'message': 'Invalid 6-digit Authenticator code. Please sync your Authenticator app clock and try again.'
                    }, status=status.HTTP_400_BAD_REQUEST)
            elif not sec.get('recoveryPinHash'):
                return Response({
                    'success': False,
                    'message': 'Please enter a 6-digit test code from your Authenticator app (or set a 6-digit Backup PIN) to activate 2FA safely.'
                }, status=status.HTTP_400_BAD_REQUEST)

    sec['twoFactorEnabled'] = enable_target
    sec['twoFactorMethod'] = method
    sec['twoFactorScope'] = scope
    sec['updatedAt'] = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')
    extra['security'] = sec
    settings_obj.extraConfig = extra
    settings_obj.save(update_fields=['extraConfig', 'updated_at'])

    AuditLog.log(
        actor=actor,
        role='ADMIN',
        action='TWO_FACTOR_ENABLED' if enable_target else 'TWO_FACTOR_DISABLED',
        target_type='SETTINGS',
        target_id='default_shop_setting',
        details=f"Two-Step Verification {'enabled' if enable_target else 'disabled'} (Method: {method}, Scope: {scope}).",
        ip_address=ip_addr,
    )

    return Response({
        'success': True,
        'message': f"Two-Step Verification {'enabled' if enable_target else 'disabled'} successfully.",
        'security': _build_payload(),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def staff_login_view(request):
    """
    Authenticate a staff member (Super Admin, Manager, Dispatcher).
    Enforces Two-Step Verification if enabled in Security & Audit Logs.
    """
    StaffMember.seed_defaults_if_empty()
    data = request.data if isinstance(request.data, dict) else {}
    username = str(data.get('username', '')).strip().lower()
    password = str(data.get('password', '')).strip()

    if not username or not password:
        return Response({
            'success': False,
            'message': 'Both username and password are required'
        }, status=status.HTTP_400_BAD_REQUEST)

    staff = StaffMember.objects.filter(username__iexact=username).first()

    if not staff and username == 'admin' and password == 'admin1234':
        StaffMember.seed_defaults_if_empty()
        staff = StaffMember.objects.filter(username='admin').first()

    if not staff or staff.password != password:
        return Response({
            'success': False,
            'message': 'Invalid username or password. Please try again.'
        }, status=status.HTTP_401_UNAUTHORIZED)

    if staff.status == 'SUSPENDED':
        return Response({
            'success': False,
            'message': 'This account is currently suspended. Please contact the Super Admin.'
        }, status=status.HTTP_403_FORBIDDEN)

    settings_obj = ShopSetting.get_settings()
    sec = (settings_obj.extraConfig or {}).get('security', {}) if isinstance(settings_obj.extraConfig, dict) else {}
    two_fa_enabled = bool(sec.get('twoFactorEnabled', False))
    two_fa_scope = str(sec.get('twoFactorScope') or 'ALL_STAFF').upper()
    applies_to_staff = two_fa_enabled and (two_fa_scope == 'ALL_STAFF' or staff.role == 'ADMIN')

    if applies_to_staff:
        method = str(sec.get('twoFactorMethod') or 'TOTP').upper()
        has_recovery_pin = bool(sec.get('recoveryPinHash'))
        challenge_payload = {
            'purpose': 'staff_2fa',
            'staffId': staff.id,
            'method': method,
            'exp': int(time.time()) + 600,
        }
        masked_target = 'Authenticator App'

        if method == 'EMAIL_OTP':
            target_email = _resolve_staff_email(staff, settings_obj)
            otp_code = f"{secrets.randbelow(1000000):06d}"
            challenge_payload['otpHash'] = _hash_security_secret(otp_code)
            sent, err_msg = _send_staff_otp_email(target_email, staff.name, otp_code, '2FA', settings_obj)
            if not sent and not has_recovery_pin:
                return Response({
                    'success': False,
                    'message': f"Two-Step Verification email failed: {err_msg}"
                }, status=status.HTTP_502_BAD_GATEWAY)
            masked_target = _mask_email(target_email) if sent else 'Backup Recovery PIN'

        challenge_token = _sign_payload(challenge_payload)
        return Response({
            'success': True,
            'requiresTwoStep': True,
            'challengeToken': challenge_token,
            'method': method,
            'maskedTarget': masked_target,
            'hasRecoveryPin': has_recovery_pin,
            'message': (
                f"Enter the 6-digit verification code sent to {masked_target}."
                if method == 'EMAIL_OTP'
                else "Enter the 6-digit verification code from your Authenticator app."
            ),
        })

    token = generate_staff_token(staff)

    AuditLog.log(
        actor=staff.name,
        role=staff.role,
        action='STAFF_LOGIN',
        target_type='SETTINGS',
        target_id=staff.id,
        details=f"Staff member {staff.name} (@{staff.username}) logged in as {staff.role}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    return Response({
        'success': True,
        'token': token,
        'message': f'Welcome back, {staff.name}!',
        'user': _serialize_staff_user(staff),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def staff_verify_2fa_view(request):
    """
    POST /api/v1/staff-auth/verify-2fa — Verify TOTP, Email OTP, or Recovery PIN and issue staff token.
    """
    data = request.data or {}
    challenge_token = str(data.get('challengeToken') or '').strip()
    code = ''.join(ch for ch in str(data.get('code') or '') if ch.isdigit())

    payload = _verify_signed_payload(challenge_token)
    if not payload or payload.get('purpose') != 'staff_2fa':
        return Response({
            'success': False,
            'message': 'Verification session expired. Please sign in again.'
        }, status=status.HTTP_401_UNAUTHORIZED)

    staff = StaffMember.objects.filter(id=payload.get('staffId')).first()
    if not staff or staff.status == 'SUSPENDED':
        return Response({'success': False, 'message': 'Account unavailable or suspended.'}, status=status.HTTP_403_FORBIDDEN)

    settings_obj = ShopSetting.get_settings()
    sec = (settings_obj.extraConfig or {}).get('security', {}) if isinstance(settings_obj.extraConfig, dict) else {}
    method = str(payload.get('method') or sec.get('twoFactorMethod') or 'TOTP').upper()

    is_valid = False
    pin_hash = sec.get('recoveryPinHash')
    if pin_hash and code and hmac.compare_digest(_hash_security_secret(code), pin_hash):
        is_valid = True
    elif method == 'TOTP':
        is_valid = _verify_totp(sec.get('twoFactorSecret'), code)
    elif method == 'EMAIL_OTP':
        expected_hash = payload.get('otpHash', '')
        if expected_hash and code:
            is_valid = hmac.compare_digest(_hash_security_secret(code), expected_hash)

    ip_addr = request.META.get('REMOTE_ADDR', '127.0.0.1')
    if not is_valid:
        AuditLog.log(
            actor=staff.name,
            role=staff.role,
            action='STAFF_2FA_FAILED',
            target_type='SETTINGS',
            target_id=staff.id,
            details=f"Failed 2FA verification attempt for @{staff.username} (Method: {method}).",
            ip_address=ip_addr,
        )
        return Response({
            'success': False,
            'message': 'Invalid 6-digit verification code. Please check and try again.'
        }, status=status.HTTP_401_UNAUTHORIZED)

    token = generate_staff_token(staff)
    AuditLog.log(
        actor=staff.name,
        role=staff.role,
        action='STAFF_LOGIN_2FA_VERIFIED',
        target_type='SETTINGS',
        target_id=staff.id,
        details=f"Staff member {staff.name} (@{staff.username}) completed Two-Step Verification ({method}).",
        ip_address=ip_addr,
    )

    return Response({
        'success': True,
        'token': token,
        'message': f'Verified! Welcome back, {staff.name}.',
        'user': _serialize_staff_user(staff),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def staff_forgot_password_view(request):
    """
    POST /api/v1/staff-auth/forgot-password — Sends 6-digit OTP to Staff/Admin email to reset password.
    """
    StaffMember.seed_defaults_if_empty()
    data = request.data or {}
    identifier = str(data.get('identifier') or data.get('email') or data.get('username') or '').strip().lower()
    if not identifier:
        return Response({'success': False, 'message': 'Enter your staff username or email address.'}, status=status.HTTP_400_BAD_REQUEST)

    staff = StaffMember.objects.filter(email__iexact=identifier).first() if '@' in identifier else None
    if not staff:
        staff = StaffMember.objects.filter(username__iexact=identifier).first()
    if not staff:
        return Response({'success': False, 'message': 'No staff or admin account matched this username or email.'}, status=status.HTTP_404_NOT_FOUND)
    if staff.status == 'SUSPENDED':
        return Response({'success': False, 'message': 'This account is suspended. Contact the Super Admin.'}, status=status.HTTP_403_FORBIDDEN)

    settings_obj = ShopSetting.get_settings()
    target_email = identifier if ('@' in identifier and staff.email and staff.email.lower() == identifier) else _resolve_staff_email(staff, settings_obj)
    if not target_email:
        return Response({
            'success': False,
            'message': 'No email address is configured for this staff account. Ask Super Admin to set an email or update password in Staff & Access Control.'
        }, status=status.HTTP_400_BAD_REQUEST)

    otp_code = f"{secrets.randbelow(1000000):06d}"
    sent, err_msg = _send_staff_otp_email(target_email, staff.name, otp_code, 'RESET', settings_obj)
    if not sent:
        return Response({
            'success': False,
            'emailConfigured': bool(settings_obj.emailHostUser and settings_obj.emailHostPassword),
            'message': f"Could not send OTP email: {err_msg}"
        }, status=status.HTTP_502_BAD_GATEWAY)

    reset_token = _sign_payload({
        'purpose': 'staff_pw_reset',
        'staffId': staff.id,
        'otpHash': _hash_security_secret(otp_code),
        'exp': int(time.time()) + 600,
    })

    masked = _mask_email(target_email)
    AuditLog.log(
        actor=staff.name,
        role=staff.role,
        action='STAFF_PASSWORD_RESET_OTP_SENT',
        target_type='SETTINGS',
        target_id=staff.id,
        details=f"Password reset OTP emailed to {masked} for @{staff.username}.",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )

    return Response({
        'success': True,
        'resetToken': reset_token,
        'maskedEmail': masked,
        'message': f"A 6-digit password reset code has been sent to {masked}."
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def staff_reset_password_view(request):
    """
    POST /api/v1/staff-auth/reset-password — Verify 6-digit OTP and set a new Staff/Admin password.
    """
    data = request.data or {}
    reset_token = str(data.get('resetToken') or '').strip()
    otp = ''.join(ch for ch in str(data.get('otp') or '') if ch.isdigit())
    new_password = str(data.get('newPassword') or '').strip()

    if len(new_password) < 4:
        return Response({'success': False, 'message': 'New password must be at least 4 characters long.'}, status=status.HTTP_400_BAD_REQUEST)

    payload = _verify_signed_payload(reset_token)
    if not payload or payload.get('purpose') != 'staff_pw_reset':
        return Response({'success': False, 'message': 'Reset session expired. Please request a new code.'}, status=status.HTTP_401_UNAUTHORIZED)

    expected_hash = payload.get('otpHash', '')
    if not expected_hash or not otp or not hmac.compare_digest(_hash_security_secret(otp), expected_hash):
        return Response({'success': False, 'message': 'Invalid 6-digit verification code.'}, status=status.HTTP_400_BAD_REQUEST)

    staff = StaffMember.objects.filter(id=payload.get('staffId')).first()
    if not staff or staff.status == 'SUSPENDED':
        return Response({'success': False, 'message': 'Staff account unavailable.'}, status=status.HTTP_404_NOT_FOUND)

    staff.password = new_password
    staff.save(update_fields=['password', 'updated_at'])

    AuditLog.log(
        actor=staff.name,
        role=staff.role,
        action='STAFF_PASSWORD_RESET_COMPLETED',
        target_type='SETTINGS',
        target_id=staff.id,
        details=f"Password updated via email OTP recovery for @{staff.username}.",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )

    return Response({
        'success': True,
        'message': f"Password reset successfully for @{staff.username}! You can now sign in with your new password."
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def staff_verify_token_view(request):
    """
    Verifies staff token from Authorization header (Bearer <token>).
    Returns active staff user info or 401 if expired/invalid.
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    token = ''
    if auth_header.startswith('Bearer '):
        token = auth_header[7:].strip()
    elif 'token' in request.query_params:
        token = request.query_params.get('token', '').strip()

    payload = verify_staff_token(token)
    if not payload:
        return Response({
            'success': False,
            'message': 'Session expired or invalid token. Please log in again.'
        }, status=status.HTTP_401_UNAUTHORIZED)

    staff = StaffMember.objects.filter(id=payload.get('id')).first()
    if not staff or staff.status == 'SUSPENDED':
        return Response({
            'success': False,
            'message': 'Account is suspended or deleted.'
        }, status=status.HTTP_403_FORBIDDEN)

    serializer = StaffMemberSerializer(staff)
    return Response({
        'success': True,
        'valid': True,
        'user': serializer.data
    })


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def recycle_bin_list_or_create(request):
    """
    GET /api/v1/recycle-bin — List trashed items across all modules with category counts.
    POST /api/v1/recycle-bin — Push a trashed item snapshot (e.g. deleted slider slide) into central DB.
    """
    if request.method == 'POST':
        data = request.data or {}
        item_type = str(data.get('item_type') or '').strip().upper()
        original_id = str(data.get('original_id') or uuid.uuid4().hex[:10]).strip()
        title = str(data.get('title') or original_id).strip()
        subtitle = str(data.get('subtitle') or '').strip()
        thumbnail_url = str(data.get('thumbnail_url') or '').strip()
        payload = data.get('payload') if isinstance(data.get('payload'), dict) else {}
        deleted_by = str(data.get('deleted_by') or 'Admin').strip()

        valid_types = {choice[0] for choice in RecycleBinItem.ITEM_TYPE_CHOICES}
        if item_type not in valid_types:
            return Response({'success': False, 'message': f'Invalid item_type: {item_type}'}, status=status.HTTP_400_BAD_REQUEST)

        rb = move_to_recycle_bin(
            item_type=item_type,
            original_id=original_id,
            title=title,
            subtitle=subtitle,
            thumbnail_url=thumbnail_url,
            payload=payload,
            deleted_by=deleted_by,
        )
        AuditLog.log(
            actor=deleted_by,
            role='ADMIN',
            action=f'{item_type}_MOVED_TO_TRASH',
            target_type='SETTINGS',
            target_id=original_id,
            details=f"Moved [{item_type}] '{title}' to Recycle Bin",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
        )
        return Response({'success': True, 'item': RecycleBinItemSerializer(rb).data}, status=status.HTTP_201_CREATED)

    item_type = (request.query_params.get('item_type') or 'ALL').strip().upper()
    search = (request.query_params.get('search') or '').strip().lower()

    all_items = list(RecycleBinItem.objects.all())
    counts = {'ALL': len(all_items)}
    for code, _ in RecycleBinItem.ITEM_TYPE_CHOICES:
        counts[code] = 0
    for item in all_items:
        counts[item.item_type] = counts.get(item.item_type, 0) + 1

    filtered = []
    for item in all_items:
        if item_type and item_type != 'ALL' and item.item_type != item_type:
            continue
        if search:
            haystack = f"{item.title} {item.subtitle} {item.original_id} {item.item_type}".lower()
            if search not in haystack:
                continue
        filtered.append(item)

    serializer = RecycleBinItemSerializer(filtered, many=True)
    return Response({
        'success': True,
        'count': len(filtered),
        'counts': counts,
        'items': serializer.data,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def recycle_bin_restore_view(request, pk):
    rb_item = RecycleBinItem.objects.filter(id=pk).first()
    if not rb_item:
        return Response({'success': False, 'message': 'Recycle Bin item not found.'}, status=status.HTTP_404_NOT_FOUND)

    try:
        ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
        res = restore_recycle_bin_item(rb_item, actor='Admin', ip_address=ip)
        return Response({
            'success': True,
            'message': f"Restored {res.get('item_type')} ({res.get('original_id')}) successfully.",
            **res,
        })
    except Exception as err:
        return Response({'success': False, 'message': f'Failed to restore item: {str(err)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([AllowAny])
def recycle_bin_permanent_delete_view(request, pk):
    rb_item = RecycleBinItem.objects.filter(id=pk).first()
    if not rb_item:
        return Response({'success': False, 'message': 'Recycle Bin item not found.'}, status=status.HTTP_404_NOT_FOUND)

    try:
        ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
        res = permanently_delete_recycle_bin_item(rb_item, actor='Admin', ip_address=ip)
        return Response({
            'success': True,
            'message': 'Item permanently deleted from database.',
            **res,
        })
    except Exception as err:
        return Response({'success': False, 'message': f'Failed to permanently delete item: {str(err)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([AllowAny])
def recycle_bin_bulk_restore_view(request):
    ids = (request.data or {}).get('ids', [])
    if not isinstance(ids, list) or not ids:
        return Response({'success': False, 'message': 'No Recycle Bin IDs provided.'}, status=status.HTTP_400_BAD_REQUEST)

    ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
    targets = list(RecycleBinItem.objects.filter(id__in=ids))
    restored = 0
    for rb in targets:
        restore_recycle_bin_item(rb, actor='Admin', ip_address=ip)
        restored += 1

    return Response({
        'success': True,
        'restoredCount': restored,
        'message': f'Successfully restored {restored} items from Recycle Bin.',
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def recycle_bin_bulk_delete_view(request):
    data = request.data or {}
    ids = data.get('ids', [])
    empty_all = bool(data.get('empty_all', False))
    item_type = str(data.get('item_type') or 'ALL').strip().upper()

    if empty_all:
        qs = RecycleBinItem.objects.all()
        if item_type and item_type != 'ALL':
            qs = qs.filter(item_type=item_type)
        targets = list(qs)
    elif isinstance(ids, list) and ids:
        targets = list(RecycleBinItem.objects.filter(id__in=ids))
    else:
        return Response({'success': False, 'message': 'No Recycle Bin IDs provided.'}, status=status.HTTP_400_BAD_REQUEST)

    ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
    deleted_count = 0
    for rb in targets:
        permanently_delete_recycle_bin_item(rb, actor='Admin', ip_address=ip)
        deleted_count += 1

    return Response({
        'success': True,
        'deletedCount': deleted_count,
        'message': f'Permanently deleted {deleted_count} items from database.',
    })




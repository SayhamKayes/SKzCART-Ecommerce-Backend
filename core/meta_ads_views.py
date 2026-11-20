import datetime
import os
import re
import uuid

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from order.models import Order

from .meta_ads_service import (
    build_oauth_login_url,
    check_account_billing_health,
    discover_meta_assets,
    estimate_audience_reach,
    evaluate_billing_guard,
    exchange_oauth_code,
    fetch_ad_account_pixels,
    fetch_page_published_posts,
    fetch_remote_campaigns_tree,
    from_meta_minor_units,
    launch_meta_campaign_flow,
    normalize_ad_account_id,
    search_targeting_options,
    update_meta_adset,
    update_meta_object_status,
)
from .meta_security import (
    create_oauth_state,
    decrypt_token,
    encrypt_token,
    mask_token,
    resolve_tenant_context,
    sanitize_tenant_id,
    verify_oauth_state,
)
from .models import (
    AuditLog,
    MarketingConfig,
    MetaTenantConnection,
    SaaSAdCampaign,
    SaaSAdCreativeAd,
    SaaSAdSet,
    ShopSetting,
    StoreTenant,
)
from .serializers import (
    MetaTenantConnectionSerializer,
    SaaSAdCampaignSerializer,
    SaaSAdSetSerializer,
    StoreTenantSerializer,
)


def _slugify_utm(name: str) -> str:
    clean = re.sub(r'[^a-z0-9]+', '_', str(name or '').lower()).strip('_')
    return clean or 'campaign'


def _compute_order_attribution(camp: SaaSAdCampaign, orders_list: list):
    utm_key = (camp.utmCampaign or '').strip().lower()
    camp_slug = _slugify_utm(camp.name)
    meta_cid = (camp.metaCampaignId or '').strip().lower()
    prod_id = (camp.productId or '').strip()

    matched_orders = []
    for ord_obj in orders_list:
        if ord_obj.status in ('CANCELLED', 'FAILED', 'RETURNED'):
            continue
        utm = ord_obj.utm if isinstance(ord_obj.utm, dict) else {}
        ord_camp = str(utm.get('campaign') or '').strip().lower()
        ord_src = str(utm.get('source') or '').strip().lower()

        camp_match = bool(
            ord_camp
            and (
                (utm_key and ord_camp == utm_key)
                or (camp_slug and (ord_camp == camp_slug or camp_slug in ord_camp))
                or (meta_cid and meta_cid in ord_camp)
            )
        )
        prod_match = False
        if prod_id and prod_id != 'all' and ord_src in ('facebook', 'instagram', 'fb', 'ig', 'meta'):
            items = ord_obj.items if isinstance(ord_obj.items, list) else []
            prod_match = any(str(it.get('productId') or it.get('id') or '') == prod_id for it in items if isinstance(it, dict))

        if camp_match or prod_match:
            matched_orders.append(ord_obj)

    orders_count = len(matched_orders)
    revenue_sum = float(sum(float(getattr(o, 'total', 0) or 0) for o in matched_orders))
    spend_val = float(camp.spend or 0)
    roas_val = round(revenue_sum / spend_val, 2) if spend_val > 0 else 0.0
    return orders_count, revenue_sum, roas_val


def _get_or_init_connection(tenant_id: str) -> MetaTenantConnection:
    conn = MetaTenantConnection.objects.filter(tenantId=tenant_id).first()
    if not conn:
        conn = MetaTenantConnection.objects.create(
            id=f"metac-{uuid.uuid4().hex[:10]}",
            tenantId=tenant_id,
        )
    return conn


def _apply_discovered_assets(conn: MetaTenantConnection, raw_token: str, token_type: str, assets: dict, app_id: str = '', expires_in: int = 0):
    conn.accessTokenEncrypted = encrypt_token(raw_token)
    conn.tokenMasked = mask_token(raw_token)
    conn.tokenType = token_type
    if expires_in and expires_in > 0:
        conn.tokenExpiresAt = timezone.now() + datetime.timedelta(seconds=expires_in)
    if app_id:
        conn.metaAppId = app_id

    user_info = assets.get('user') or {}
    conn.metaUserId = user_info.get('id', '')
    conn.metaUserName = user_info.get('name', '')
    conn.grantedScopes = assets.get('grantedScopes') or []

    ad_accounts = assets.get('adAccounts') or []
    pages = assets.get('pages') or []
    pixels = assets.get('pixels') or []

    # Strip raw pageAccessTokens before persisting public asset metadata list, while caching encrypted primary token
    sanitized_pages = []
    selected_page = None
    for pg in pages:
        if conn.pageId and pg.get('id') == conn.pageId:
            selected_page = pg
        sanitized_pages.append({
            'id': pg.get('id', ''),
            'name': pg.get('name', ''),
            'category': pg.get('category', ''),
            'instagramActorId': pg.get('instagramActorId', ''),
            'instagramUsername': pg.get('instagramUsername', ''),
            'hasPageToken': bool(pg.get('pageAccessToken')),
        })

    if not selected_page and pages:
        selected_page = pages[0]

    selected_acc = None
    for acc in ad_accounts:
        if conn.adAccountId and acc.get('id') == normalize_ad_account_id(conn.adAccountId):
            selected_acc = acc
            break
    if not selected_acc and ad_accounts:
        selected_acc = ad_accounts[0]

    conn.availableAdAccounts = ad_accounts
    conn.availablePages = sanitized_pages
    conn.availablePixels = pixels

    if selected_acc:
        conn.adAccountId = selected_acc.get('id', '')
        conn.adAccountName = selected_acc.get('name', '')
        conn.adAccountCurrency = selected_acc.get('currency', 'BDT')
        conn.adAccountStatus = selected_acc.get('statusLabel', 'UNKNOWN')
        conn.fundingSourceSummary = selected_acc.get('fundingSummary', '')
        conn.spendCap = float(selected_acc.get('spendCap') or 0)
        conn.amountSpent = float(selected_acc.get('amountSpent') or 0)
        conn.accountBalance = float(selected_acc.get('balance') or 0)
        billing_status, billing_msg = evaluate_billing_guard(selected_acc, conn.grantedScopes)
        conn.billingStatus = billing_status
        conn.billingAlertMessage = billing_msg
    else:
        conn.billingStatus = 'UNVERIFIED'
        conn.billingAlertMessage = 'No Meta Ad Account found on this Facebook user. Ensure an Ad Account is assigned.'

    if selected_page:
        conn.pageId = selected_page.get('id', '')
        conn.pageName = selected_page.get('name', '')
        conn.pageCategory = selected_page.get('category', '')
        conn.instagramActorId = selected_page.get('instagramActorId', '')
        if selected_page.get('pageAccessToken'):
            conn.pageAccessTokenEncrypted = encrypt_token(selected_page['pageAccessToken'])

    if not conn.pixelId and pixels:
        conn.pixelId = pixels[0].get('id', '')

    conn.isConnected = True
    conn.lastError = ''
    conn.lastSyncedAt = timezone.now()
    conn.save()

    # Keep default store MarketingConfig pixelId & adAccountId synchronized
    if conn.tenantId == 'default':
        mkt_cfg = MarketingConfig.objects.first()
        if mkt_cfg:
            updated = False
            if conn.adAccountId and not mkt_cfg.adAccountId:
                mkt_cfg.adAccountId = conn.adAccountId
                updated = True
            if conn.pixelId and not mkt_cfg.pixelId:
                mkt_cfg.pixelId = conn.pixelId
                updated = True
            if updated:
                mkt_cfg.save()


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def marketing_tenants_view(request):
    """SaaS Multi-Tenant Store registry & switching."""
    _, actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    if request.method == 'GET':
        tenants = StoreTenant.objects.all().order_by('created_at')
        conns = {c.tenantId: c for c in MetaTenantConnection.objects.all()}
        enriched = []
        for t in tenants:
            c = conns.get(t.tenantId)
            row = StoreTenantSerializer(t).data
            row['metaConnected'] = bool(c and c.isConnected)
            row['adAccountId'] = c.adAccountId if c else ''
            row['pageName'] = c.pageName if c else ''
            row['billingStatus'] = c.billingStatus if c else 'UNVERIFIED'
            enriched.append(row)
        return Response({'success': True, 'tenants': enriched})

    data = request.data or {}
    store_name = str(data.get('storeName') or '').strip()
    if not store_name:
        return Response({'success': False, 'message': 'Store Name is required.'}, status=status.HTTP_400_BAD_REQUEST)

    raw_slug = data.get('tenantId') or re.sub(r'[^a-zA-Z0-9_-]+', '-', store_name.lower()).strip('-')
    tenant_slug = sanitize_tenant_id(raw_slug)
    if tenant_slug == 'default' or StoreTenant.objects.filter(tenantId=tenant_slug).exists():
        tenant_slug = f"{tenant_slug[:40]}-{uuid.uuid4().hex[:5]}"

    tenant = StoreTenant.objects.create(
        id=f"tenant-{uuid.uuid4().hex[:8]}",
        tenantId=tenant_slug,
        storeName=store_name,
        domainUrl=str(data.get('domainUrl') or '').strip(),
        ownerName=str(data.get('ownerName') or '').strip(),
        ownerEmail=str(data.get('ownerEmail') or '').strip(),
        plan=str(data.get('plan') or 'PRO').upper() if str(data.get('plan') or 'PRO').upper() in ('STARTER', 'PRO', 'ENTERPRISE') else 'PRO',
        status='ACTIVE',
    )
    _get_or_init_connection(tenant.tenantId)
    AuditLog.log(
        actor=actor,
        role='ADMIN',
        action='CREATE_SAAS_TENANT',
        target_type='TENANT',
        target_id=tenant.tenantId,
        details=f"Registered SaaS Store Tenant: {tenant.storeName} ({tenant.tenantId})",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )
    return Response({'success': True, 'tenant': StoreTenantSerializer(tenant).data}, status=status.HTTP_201_CREATED)


@api_view(['GET', 'DELETE'])
@permission_classes([AllowAny])
def meta_connection_view(request):
    tenant, actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    conn = _get_or_init_connection(tenant.tenantId)
    if request.method == 'DELETE':
        conn.accessTokenEncrypted = ''
        conn.pageAccessTokenEncrypted = ''
        conn.tokenMasked = ''
        conn.metaUserId = ''
        conn.metaUserName = ''
        conn.grantedScopes = []
        conn.adAccountId = ''
        conn.adAccountName = ''
        conn.pageId = ''
        conn.pageName = ''
        conn.instagramActorId = ''
        conn.availableAdAccounts = []
        conn.availablePages = []
        conn.availablePixels = []
        conn.isConnected = False
        conn.billingStatus = 'UNVERIFIED'
        conn.billingAlertMessage = ''
        conn.lastError = ''
        conn.save()
        AuditLog.log(
            actor=actor,
            role='MARKETING',
            action='DISCONNECT_META_ADS',
            target_type='TENANT',
            target_id=tenant.tenantId,
            details=f"Disconnected Meta account for store {tenant.storeName}",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
        )
        return Response({'success': True, 'connection': MetaTenantConnectionSerializer(conn).data})

    return Response({
        'success': True,
        'tenant': StoreTenantSerializer(tenant).data,
        'connection': MetaTenantConnectionSerializer(conn).data,
        'hasServerOAuthApp': bool(os.getenv('META_APP_ID', '').strip() and os.getenv('META_APP_SECRET', '').strip()),
    })


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def meta_oauth_url_view(request):
    tenant, _actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    conn = _get_or_init_connection(tenant.tenantId)
    data = request.data if request.method == 'POST' else request.query_params
    app_id = str(data.get('appId') or conn.metaAppId or os.getenv('META_APP_ID', '')).strip()
    redirect_uri = str(data.get('redirectUri') or '').strip()

    if not app_id:
        return Response({
            'success': False,
            'needsAppConfig': True,
            'message': 'Meta App ID is required for Facebook OAuth Login (set META_APP_ID on backend or enter App ID), OR connect directly with a System User Token.',
        }, status=status.HTTP_400_BAD_REQUEST)

    if not redirect_uri:
        return Response({'success': False, 'message': 'redirectUri is required.'}, status=status.HTTP_400_BAD_REQUEST)

    state = create_oauth_state(tenant.tenantId, redirect_uri)
    auth_url = build_oauth_login_url(app_id, redirect_uri, state)
    return Response({'success': True, 'authUrl': auth_url, 'state': state, 'tenantId': tenant.tenantId})


@api_view(['POST'])
@permission_classes([AllowAny])
def meta_oauth_exchange_view(request):
    tenant, actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    data = request.data or {}
    code = str(data.get('code') or '').strip()
    state = str(data.get('state') or '').strip()
    redirect_uri = str(data.get('redirectUri') or '').strip()

    if not code or not redirect_uri:
        return Response({'success': False, 'message': 'OAuth authorization code and redirectUri are required.'}, status=status.HTTP_400_BAD_REQUEST)

    if state:
        state_data = verify_oauth_state(state)
        if not state_data:
            return Response({'success': False, 'message': 'Invalid or expired OAuth state parameter.'}, status=status.HTTP_400_BAD_REQUEST)
        state_tenant_id = sanitize_tenant_id(state_data.get('tenantId'))
        if state_tenant_id != tenant.tenantId:
            resolved = StoreTenant.objects.filter(tenantId=state_tenant_id, status='ACTIVE').first()
            if resolved:
                tenant = resolved

    conn = _get_or_init_connection(tenant.tenantId)
    app_id = str(data.get('appId') or conn.metaAppId or os.getenv('META_APP_ID', '')).strip()
    app_secret = str(data.get('appSecret') or os.getenv('META_APP_SECRET', '')).strip()
    if not app_id or not app_secret:
        return Response({
            'success': False,
            'message': 'Server META_APP_ID & META_APP_SECRET (or App ID + App Secret) required to exchange Facebook OAuth code securely.',
        }, status=status.HTTP_400_BAD_REQUEST)

    ok_tok, tok_res = exchange_oauth_code(app_id, app_secret, redirect_uri, code)
    if not ok_tok:
        conn.lastError = tok_res.get('message', 'OAuth exchange failed')
        conn.save()
        return Response({'success': False, 'message': conn.lastError}, status=status.HTTP_400_BAD_REQUEST)

    raw_token = tok_res['access_token']
    ok_assets, assets = discover_meta_assets(raw_token)
    if not ok_assets:
        conn.lastError = assets.get('message', 'Failed to load Meta assets')
        conn.save()
        return Response({'success': False, 'message': conn.lastError}, status=status.HTTP_400_BAD_REQUEST)

    _apply_discovered_assets(conn, raw_token, 'OAUTH', assets, app_id=app_id, expires_in=int(tok_res.get('expires_in') or 0))
    AuditLog.log(
        actor=actor,
        role='MARKETING',
        action='CONNECT_META_OAUTH',
        target_type='TENANT',
        target_id=tenant.tenantId,
        details=f"Connected Meta OAuth user {conn.metaUserName} for store {tenant.storeName}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )
    return Response({'success': True, 'connection': MetaTenantConnectionSerializer(conn).data})


@api_view(['POST'])
@permission_classes([AllowAny])
def meta_connect_token_view(request):
    """Connects a seller store tenant via Business System User Token or Meta User Access Token."""
    tenant, actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    data = request.data or {}
    raw_token = str(data.get('accessToken') or '').strip()
    app_id = str(data.get('appId') or os.getenv('META_APP_ID', '')).strip()
    token_type = 'SYSTEM_USER' if str(data.get('tokenType') or 'SYSTEM_USER').upper() == 'SYSTEM_USER' else 'OAUTH'

    if not raw_token:
        return Response({'success': False, 'message': 'Meta Access Token is required.'}, status=status.HTTP_400_BAD_REQUEST)

    conn = _get_or_init_connection(tenant.tenantId)
    ok_assets, assets = discover_meta_assets(raw_token)
    if not ok_assets:
        conn.lastError = assets.get('message', 'Meta rejected the access token.')
        conn.save()
        return Response({'success': False, 'message': conn.lastError, 'metaError': assets}, status=status.HTTP_400_BAD_REQUEST)

    if data.get('adAccountId'):
        conn.adAccountId = normalize_ad_account_id(data['adAccountId'])
    if data.get('pageId'):
        conn.pageId = str(data['pageId']).strip()
    if data.get('pixelId'):
        conn.pixelId = str(data['pixelId']).strip()
    if 'isSandboxMode' in data:
        conn.isSandboxMode = bool(data['isSandboxMode'])

    _apply_discovered_assets(conn, raw_token, token_type, assets, app_id=app_id)
    AuditLog.log(
        actor=actor,
        role='MARKETING',
        action='CONNECT_META_TOKEN',
        target_type='TENANT',
        target_id=tenant.tenantId,
        details=f"Connected Meta {token_type} token ({conn.tokenMasked}) for store {tenant.storeName}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )
    return Response({'success': True, 'connection': MetaTenantConnectionSerializer(conn).data})


@api_view(['POST'])
@permission_classes([AllowAny])
def meta_select_assets_view(request):
    """Binds a specific Ad Account, Facebook Page, Pixel, and Sandbox Mode to the SaaS tenant."""
    tenant, actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    conn = _get_or_init_connection(tenant.tenantId)
    raw_token = decrypt_token(conn.accessTokenEncrypted)
    if not raw_token:
        return Response({'success': False, 'message': 'Connect Facebook OAuth or a Meta Access Token first.'}, status=status.HTTP_400_BAD_REQUEST)

    data = request.data or {}
    if 'adAccountId' in data and data.get('adAccountId'):
        conn.adAccountId = normalize_ad_account_id(data['adAccountId'])
    if 'pageId' in data and data.get('pageId'):
        conn.pageId = str(data['pageId']).strip()
    if 'pixelId' in data:
        conn.pixelId = str(data.get('pixelId') or '').strip()
    if 'isSandboxMode' in data:
        conn.isSandboxMode = bool(data.get('isSandboxMode'))

    # Refresh Ad Account billing guard & Page token for the chosen assets
    ok_assets, assets = discover_meta_assets(raw_token)
    if ok_assets:
        _apply_discovered_assets(conn, raw_token, conn.tokenType, assets, app_id=conn.metaAppId)
    else:
        if conn.adAccountId:
            ok_hlth, hlth = check_account_billing_health(raw_token, conn.adAccountId, conn.grantedScopes)
            if ok_hlth:
                conn.adAccountName = hlth.get('name', conn.adAccountName)
                conn.adAccountCurrency = hlth.get('currency', conn.adAccountCurrency)
                conn.adAccountStatus = hlth.get('statusLabel', conn.adAccountStatus)
                conn.billingStatus = hlth.get('billingStatus', conn.billingStatus)
                conn.billingAlertMessage = hlth.get('billingAlertMessage', '')
                conn.fundingSourceSummary = hlth.get('fundingSummary', '')
                conn.spendCap = float(hlth.get('spendCap') or 0)
                conn.amountSpent = float(hlth.get('amountSpent') or 0)
                conn.accountBalance = float(hlth.get('balance') or 0)
                conn.availablePixels = fetch_ad_account_pixels(raw_token, conn.adAccountId)
        conn.save()

    AuditLog.log(
        actor=actor,
        role='MARKETING',
        action='BIND_META_ASSETS',
        target_type='TENANT',
        target_id=tenant.tenantId,
        details=f"Bound AdAccount={conn.adAccountId}, Page={conn.pageId}, Pixel={conn.pixelId}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )
    return Response({'success': True, 'connection': MetaTenantConnectionSerializer(conn).data})


@api_view(['GET'])
@permission_classes([AllowAny])
def meta_account_health_view(request):
    tenant, _actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    conn = _get_or_init_connection(tenant.tenantId)
    raw_token = decrypt_token(conn.accessTokenEncrypted)
    if not raw_token or not conn.adAccountId:
        return Response({
            'success': True,
            'connected': False,
            'billingStatus': conn.billingStatus,
            'billingAlertMessage': conn.billingAlertMessage or 'Connect Meta account & select an Ad Account to check Billing Guard.',
            'connection': MetaTenantConnectionSerializer(conn).data,
        })

    ok, health = check_account_billing_health(raw_token, conn.adAccountId, conn.grantedScopes)
    if not ok:
        conn.lastError = health.get('message', 'Health check failed')
        if health.get('isBillingGuard'):
            conn.billingStatus = 'BLOCKED'
            conn.billingAlertMessage = conn.lastError
        conn.save()
        return Response({'success': False, 'message': conn.lastError, 'connection': MetaTenantConnectionSerializer(conn).data}, status=status.HTTP_400_BAD_REQUEST)

    conn.adAccountName = health.get('name', conn.adAccountName)
    conn.adAccountCurrency = health.get('currency', conn.adAccountCurrency)
    conn.adAccountStatus = health.get('statusLabel', conn.adAccountStatus)
    conn.billingStatus = health.get('billingStatus', 'HEALTHY')
    conn.billingAlertMessage = health.get('billingAlertMessage', '')
    conn.fundingSourceSummary = health.get('fundingSummary', '')
    conn.spendCap = float(health.get('spendCap') or 0)
    conn.amountSpent = float(health.get('amountSpent') or 0)
    conn.accountBalance = float(health.get('balance') or 0)
    conn.lastSyncedAt = timezone.now()
    conn.lastError = ''
    conn.save()

    return Response({'success': True, 'connected': True, 'health': health, 'connection': MetaTenantConnectionSerializer(conn).data})


@api_view(['GET'])
@permission_classes([AllowAny])
def meta_page_posts_view(request):
    tenant, _actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    conn = _get_or_init_connection(tenant.tenantId)
    page_token = decrypt_token(conn.pageAccessTokenEncrypted) or decrypt_token(conn.accessTokenEncrypted)
    if not conn.pageId or not page_token:
        return Response({
            'success': False,
            'posts': [],
            'message': 'Connect a Facebook Page with pages_read_engagement permission to load Page posts for boosting.',
        }, status=status.HTTP_400_BAD_REQUEST)

    ok, posts_or_err = fetch_page_published_posts(page_token, conn.pageId, limit=int(request.query_params.get('limit') or 15))
    if not ok:
        return Response({'success': False, 'posts': [], 'message': posts_or_err.get('message', 'Failed to fetch Page posts.')}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'success': True, 'posts': posts_or_err})


@api_view(['GET'])
@permission_classes([AllowAny])
def meta_targeting_search_view(request):
    tenant, _actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    conn = _get_or_init_connection(tenant.tenantId)
    raw_token = decrypt_token(conn.accessTokenEncrypted)
    if not raw_token:
        return Response({'success': False, 'results': [], 'message': 'Connect Meta account to query live Meta targeting suggestions.'}, status=status.HTTP_400_BAD_REQUEST)

    q = str(request.query_params.get('q') or '').strip()
    search_type = 'adgeolocation' if request.query_params.get('type') == 'adgeolocation' else 'adinterest'
    ok, res_or_err = search_targeting_options(raw_token, q, search_type=search_type)
    if not ok:
        return Response({'success': False, 'results': [], 'message': res_or_err.get('message', 'Targeting search failed.')}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'success': True, 'results': res_or_err})


@api_view(['POST'])
@permission_classes([AllowAny])
def meta_reach_estimate_view(request):
    tenant, _actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    conn = _get_or_init_connection(tenant.tenantId)
    raw_token = decrypt_token(conn.accessTokenEncrypted)
    if not raw_token or not conn.adAccountId:
        return Response({'success': False, 'message': 'Connected Meta Ad Account required for reach estimation.'}, status=status.HTTP_400_BAD_REQUEST)

    ok, est = estimate_audience_reach(raw_token, conn.adAccountId, request.data.get('targeting') or {})
    if not ok:
        return Response({'success': False, 'message': est.get('message', 'Reach estimate failed.')}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'success': True, 'estimate': est})


@api_view(['GET'])
@permission_classes([AllowAny])
def meta_campaigns_list_view(request):
    tenant, _actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    campaigns = list(SaaSAdCampaign.objects.filter(tenantId=tenant.tenantId).prefetch_related('adsets__ads'))
    orders_list = list(Order.objects.all())

    for camp in campaigns:
        ord_cnt, rev_sum, roas_val = _compute_order_attribution(camp, orders_list)
        if camp.storeOrders != ord_cnt or float(camp.storeRevenue or 0) != rev_sum or float(camp.roas or 0) != roas_val:
            camp.storeOrders = ord_cnt
            camp.storeRevenue = rev_sum
            camp.roas = roas_val
            camp.save(update_fields=['storeOrders', 'storeRevenue', 'roas'])

    serializer = SaaSAdCampaignSerializer(campaigns, many=True)
    return Response({'success': True, 'campaigns': serializer.data})


@api_view(['POST'])
@permission_classes([AllowAny])
def meta_campaign_launch_view(request):
    """
    Launches a live Meta Campaign -> AdSet -> Image/Post Creative -> Ad
    OR Boosts a Product / Facebook Page Post directly from AmarDokan.
    """
    tenant, actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    conn = _get_or_init_connection(tenant.tenantId)
    raw_token = decrypt_token(conn.accessTokenEncrypted)
    if not conn.isConnected or not raw_token:
        return Response({
            'success': False,
            'message': 'Please connect your Facebook Account (OAuth or System User Token) before launching live ads.',
        }, status=status.HTTP_400_BAD_REQUEST)

    if conn.billingStatus in ('BLOCKED', 'PAYMENT_DUE') and not conn.isSandboxMode:
        return Response({
            'success': False,
            'billingGuardBlocked': True,
            'message': f"Billing Guard Blocked Launch: {conn.billingAlertMessage}",
        }, status=status.HTTP_402_PAYMENT_REQUIRED)

    data = request.data or {}
    camp_name = str(data.get('name') or '').strip()
    if not camp_name:
        return Response({'success': False, 'message': 'Campaign name is required.'}, status=status.HTTP_400_BAD_REQUEST)

    public_origin = str(data.get('publicOrigin') or request.build_absolute_uri('/')[:-1]).rstrip('/')
    utm_slug = _slugify_utm(data.get('utmCampaign') or camp_name)
    data = dict(data)
    data['utmCampaign'] = utm_slug

    ok, launch_res = launch_meta_campaign_flow(conn, raw_token, data, public_origin=public_origin)
    if not ok:
        if launch_res.get('isBillingGuard'):
            conn.billingStatus = 'WARNING'
            conn.billingAlertMessage = launch_res.get('message', '')
            conn.save(update_fields=['billingStatus', 'billingAlertMessage'])
        return Response({
            'success': False,
            'stage': launch_res.get('stage', 'META_API'),
            'isBillingGuard': bool(launch_res.get('isBillingGuard')),
            'message': launch_res.get('message', 'Meta campaign launch failed.'),
        }, status=status.HTTP_400_BAD_REQUEST)

    camp = SaaSAdCampaign.objects.create(
        id=f"scamp-{uuid.uuid4().hex[:10]}",
        tenantId=tenant.tenantId,
        metaCampaignId=launch_res['metaCampaignId'],
        adAccountId=conn.adAccountId,
        source=str(data.get('source') or 'facebook'),
        campaignType=str(data.get('campaignType') or 'PRODUCT_SALES').upper(),
        name=camp_name,
        objective=str(data.get('objective') or 'OUTCOME_SALES').upper(),
        status=launch_res['status'],
        effectiveStatus=launch_res['status'],
        productId=str(data.get('productId') or ''),
        productName=str(data.get('productName') or ''),
        postId=str(data.get('postId') or ''),
        landingUrl=str(data.get('landingUrl') or ''),
        utmCampaign=utm_slug,
        dailyBudget=float(data.get('dailyBudget') or 0),
        lifetimeBudget=float(data.get('lifetimeBudget') or 0),
        currency=conn.adAccountCurrency or 'BDT',
        isRemoteSynced=True,
        lastSyncedAt=timezone.now(),
    )

    adset = SaaSAdSet.objects.create(
        id=f"sadset-{uuid.uuid4().hex[:10]}",
        tenantId=tenant.tenantId,
        campaign=camp,
        metaAdSetId=launch_res['metaAdSetId'],
        name=f"{camp_name} - AdSet",
        status=launch_res['status'],
        effectiveStatus=launch_res['status'],
        optimizationGoal=launch_res['optimizationGoal'],
        dailyBudget=float(data.get('dailyBudget') or 0),
        lifetimeBudget=float(data.get('lifetimeBudget') or 0),
        startTime=parse_datetime(str(launch_res.get('startTime') or '')),
        endTime=parse_datetime(str(launch_res.get('endTime') or '')) if launch_res.get('endTime') else None,
        targeting=launch_res['targetingSpec'],
    )

    SaaSAdCreativeAd.objects.create(
        id=f"sad-{uuid.uuid4().hex[:10]}",
        tenantId=tenant.tenantId,
        adset=adset,
        metaCreativeId=launch_res['metaCreativeId'],
        metaAdId=launch_res['metaAdId'],
        name=f"{camp_name} - Ad",
        status=launch_res['status'],
        effectiveStatus=launch_res['status'],
        primaryText=str(data.get('primaryText') or ''),
        headline=str(data.get('headline') or ''),
        description=str(data.get('description') or ''),
        callToAction=str(data.get('callToAction') or 'SHOP_NOW'),
        imageUrl=str(data.get('imageUrl') or ''),
        imageHash=launch_res.get('imageHash', ''),
        linkUrl=str(data.get('landingUrl') or ''),
        objectStoryId=f"{conn.pageId}_{data['postId']}" if data.get('postId') and '_' not in str(data.get('postId')) else str(data.get('postId') or ''),
    )

    AuditLog.log(
        actor=actor,
        role='MARKETING',
        action='LAUNCH_META_CAMPAIGN',
        target_type='CAMPAIGN',
        target_id=camp.id,
        details=f"[{tenant.tenantId}] Launched {camp.campaignType}: {camp.name} (Meta ID: {camp.metaCampaignId})",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )
    return Response({'success': True, 'campaign': SaaSAdCampaignSerializer(camp).data}, status=status.HTTP_201_CREATED)


@api_view(['PATCH'])
@permission_classes([AllowAny])
def meta_campaign_status_view(request, pk):
    tenant, actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    camp = SaaSAdCampaign.objects.filter(id=pk, tenantId=tenant.tenantId).first()
    if not camp:
        return Response({'success': False, 'message': 'Campaign not found for this tenant.'}, status=status.HTTP_404_NOT_FOUND)

    next_status = 'ACTIVE' if str(request.data.get('status') or '').upper() == 'ACTIVE' else 'PAUSED'
    conn = _get_or_init_connection(tenant.tenantId)
    raw_token = decrypt_token(conn.accessTokenEncrypted)

    if camp.metaCampaignId:
        if not raw_token:
            return Response({'success': False, 'message': 'Meta Access Token missing; cannot sync status with Facebook.'}, status=status.HTTP_400_BAD_REQUEST)
        ok, res = update_meta_object_status(raw_token, camp.metaCampaignId, next_status)
        if not ok:
            return Response({'success': False, 'message': res.get('message', 'Failed to update campaign status on Meta.')}, status=status.HTTP_400_BAD_REQUEST)

    camp.status = next_status
    camp.effectiveStatus = next_status
    camp.lastSyncedAt = timezone.now()
    camp.save(update_fields=['status', 'effectiveStatus', 'lastSyncedAt'])

    AuditLog.log(
        actor=actor,
        role='MARKETING',
        action='TOGGLE_META_CAMPAIGN',
        target_type='CAMPAIGN',
        target_id=camp.id,
        details=f"[{tenant.tenantId}] Updated campaign {camp.name} status -> {next_status}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )
    return Response({'success': True, 'campaign': SaaSAdCampaignSerializer(camp).data})


@api_view(['DELETE'])
@permission_classes([AllowAny])
def meta_campaign_delete_view(request, pk):
    tenant, actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    camp = SaaSAdCampaign.objects.filter(id=pk, tenantId=tenant.tenantId).first()
    if not camp:
        return Response({'success': False, 'message': 'Campaign not found for this tenant.'}, status=status.HTTP_404_NOT_FOUND)

    conn = _get_or_init_connection(tenant.tenantId)
    raw_token = decrypt_token(conn.accessTokenEncrypted)
    if camp.metaCampaignId and raw_token:
        ok, res = update_meta_object_status(raw_token, camp.metaCampaignId, 'DELETED')
        if not ok:
            return Response({'success': False, 'message': res.get('message', 'Failed to delete campaign on Meta.')}, status=status.HTTP_400_BAD_REQUEST)

    camp_name = camp.name
    camp.delete()
    AuditLog.log(
        actor=actor,
        role='MARKETING',
        action='DELETE_META_CAMPAIGN',
        target_type='CAMPAIGN',
        target_id=pk,
        details=f"[{tenant.tenantId}] Deleted Meta campaign {camp_name}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )
    return Response({'success': True, 'deletedId': pk})


@api_view(['PATCH'])
@permission_classes([AllowAny])
def meta_adset_update_view(request, pk):
    tenant, actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    adset = SaaSAdSet.objects.select_related('campaign').filter(id=pk, tenantId=tenant.tenantId).first()
    if not adset:
        return Response({'success': False, 'message': 'AdSet not found for this tenant.'}, status=status.HTTP_404_NOT_FOUND)

    data = request.data or {}
    daily_budget = data.get('dailyBudget')
    lifetime_budget = data.get('lifetimeBudget')
    end_time = data.get('endTime')
    new_status = data.get('status')
    targeting = data.get('targeting')

    conn = _get_or_init_connection(tenant.tenantId)
    raw_token = decrypt_token(conn.accessTokenEncrypted)

    if adset.metaAdSetId:
        if not raw_token:
            return Response({'success': False, 'message': 'Meta Access Token required to edit AdSet remotely.'}, status=status.HTTP_400_BAD_REQUEST)
        ok, res = update_meta_adset(
            raw_token,
            adset.metaAdSetId,
            currency=adset.campaign.currency or conn.adAccountCurrency or 'BDT',
            daily_budget=daily_budget,
            lifetime_budget=lifetime_budget,
            end_time=end_time,
            new_status=new_status,
            targeting_input=targeting,
        )
        if not ok:
            return Response({'success': False, 'message': res.get('message', 'Failed to update AdSet on Meta.')}, status=status.HTTP_400_BAD_REQUEST)

    if daily_budget is not None and float(daily_budget) > 0:
        adset.dailyBudget = float(daily_budget)
        adset.campaign.dailyBudget = float(daily_budget)
        adset.campaign.save(update_fields=['dailyBudget'])
    if lifetime_budget is not None and float(lifetime_budget) > 0:
        adset.lifetimeBudget = float(lifetime_budget)
    if end_time:
        parsed_end = parse_datetime(str(end_time))
        if parsed_end:
            adset.endTime = parsed_end
    if new_status:
        clean_st = 'ACTIVE' if str(new_status).upper() == 'ACTIVE' else 'PAUSED'
        adset.status = clean_st
        adset.effectiveStatus = clean_st
    if isinstance(targeting, dict):
        adset.targeting = targeting
    adset.save()

    AuditLog.log(
        actor=actor,
        role='MARKETING',
        action='UPDATE_META_ADSET',
        target_type='ADSET',
        target_id=adset.id,
        details=f"[{tenant.tenantId}] Updated AdSet {adset.name} budget={adset.dailyBudget} status={adset.status}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )
    return Response({'success': True, 'adset': SaaSAdSetSerializer(adset).data})


def _extract_insights_row(obj: dict) -> dict:
    ins_list = (obj.get('insights') or {}).get('data', []) if isinstance(obj.get('insights'), dict) else []
    row = ins_list[0] if ins_list else {}
    actions = row.get('actions') or []
    purchases = 0
    for act in actions:
        if act.get('action_type') in ('purchase', 'offsite_conversion.fb_pixel_purchase', 'omni_purchase'):
            purchases += int(float(act.get('value') or 0))
    return {
        'spend': float(row.get('spend') or 0),
        'impressions': int(float(row.get('impressions') or 0)),
        'reach': int(float(row.get('reach') or 0)),
        'clicks': int(float(row.get('clicks') or 0)),
        'ctr': float(row.get('ctr') or 0),
        'cpc': float(row.get('cpc') or 0),
        'cpm': float(row.get('cpm') or 0),
        'metaPurchases': purchases,
    }


@api_view(['POST'])
@permission_classes([AllowAny])
def meta_sync_two_way_view(request):
    """
    Full Two-Way Meta Sync:
      1. Refreshes Ad Account Billing Guard health
      2. Pulls remote Campaigns -> AdSets -> Ads + Review Feedback + Insights
      3. Upserts into PostgreSQL/SQLite SaaS models for this tenant
      4. Recomputes real Store Orders, Revenue & ROAS from AmarDokan orders
    """
    tenant, _actor, err_resp = resolve_tenant_context(request)
    if err_resp:
        return err_resp

    conn = _get_or_init_connection(tenant.tenantId)
    raw_token = decrypt_token(conn.accessTokenEncrypted)
    orders_list = list(Order.objects.all())

    if conn.isConnected and raw_token and conn.adAccountId:
        ok_hlth, hlth = check_account_billing_health(raw_token, conn.adAccountId, conn.grantedScopes)
        if ok_hlth:
            conn.adAccountName = hlth.get('name', conn.adAccountName)
            conn.adAccountCurrency = hlth.get('currency', conn.adAccountCurrency)
            conn.adAccountStatus = hlth.get('statusLabel', conn.adAccountStatus)
            conn.billingStatus = hlth.get('billingStatus', 'HEALTHY')
            conn.billingAlertMessage = hlth.get('billingAlertMessage', '')
            conn.fundingSourceSummary = hlth.get('fundingSummary', '')
            conn.spendCap = float(hlth.get('spendCap') or 0)
            conn.amountSpent = float(hlth.get('amountSpent') or 0)
            conn.accountBalance = float(hlth.get('balance') or 0)

        ok_tree, remote_list = fetch_remote_campaigns_tree(raw_token, conn.adAccountId)
        if not ok_tree:
            conn.lastError = remote_list.get('message', 'Failed to sync remote Meta campaigns.')
            conn.save()
            return Response({'success': False, 'message': conn.lastError, 'connection': MetaTenantConnectionSerializer(conn).data}, status=status.HTTP_400_BAD_REQUEST)

        curr = conn.adAccountCurrency or 'BDT'
        for rc in remote_list:
            mcid = str(rc.get('id') or '')
            if not mcid:
                continue
            ins = _extract_insights_row(rc)
            issues = rc.get('issues_info') or []
            issue_msg = '; '.join([i.get('error_summary') or i.get('error_message') or '' for i in issues if isinstance(i, dict) and (i.get('error_summary') or i.get('error_message'))])

            camp = SaaSAdCampaign.objects.filter(tenantId=tenant.tenantId, metaCampaignId=mcid).first()
            if not camp:
                camp = SaaSAdCampaign.objects.create(
                    id=f"scamp-{uuid.uuid4().hex[:10]}",
                    tenantId=tenant.tenantId,
                    metaCampaignId=mcid,
                    adAccountId=conn.adAccountId,
                    source='facebook',
                    campaignType='PRODUCT_SALES',
                    name=rc.get('name') or f'Meta Campaign {mcid}',
                    objective=rc.get('objective') or 'OUTCOME_SALES',
                    utmCampaign=_slugify_utm(rc.get('name') or mcid),
                    currency=curr,
                )

            camp.name = rc.get('name') or camp.name
            camp.objective = rc.get('objective') or camp.objective
            camp.status = rc.get('status') or camp.status
            camp.effectiveStatus = rc.get('effective_status') or camp.effectiveStatus
            camp.billingGuardAlert = issue_msg
            camp.spend = ins['spend']
            camp.impressions = ins['impressions']
            camp.reach = ins['reach']
            camp.clicks = ins['clicks']
            camp.ctr = ins['ctr']
            camp.cpc = ins['cpc']
            camp.cpm = ins['cpm']
            camp.metaPurchases = ins['metaPurchases']
            camp.isRemoteSynced = True
            camp.lastSyncedAt = timezone.now()

            adsets_map = {}
            remote_adsets = (rc.get('adsets') or {}).get('data', []) if isinstance(rc.get('adsets'), dict) else []
            first_daily = 0.0
            for ras in remote_adsets:
                mas_id = str(ras.get('id') or '')
                if not mas_id:
                    continue
                as_ins = _extract_insights_row(ras)
                daily_val = from_meta_minor_units(ras.get('daily_budget'), curr)
                life_val = from_meta_minor_units(ras.get('lifetime_budget'), curr)
                if daily_val > 0 and first_daily == 0:
                    first_daily = daily_val

                adset_obj, _ = SaaSAdSet.objects.update_or_create(
                    metaAdSetId=mas_id,
                    tenantId=tenant.tenantId,
                    defaults={
                        'id': f"sadset-{uuid.uuid4().hex[:10]}",
                        'campaign': camp,
                        'name': ras.get('name') or f'AdSet {mas_id}',
                        'status': ras.get('status') or 'PAUSED',
                        'effectiveStatus': ras.get('effective_status') or 'PAUSED',
                        'optimizationGoal': ras.get('optimization_goal') or 'LINK_CLICKS',
                        'billingEvent': ras.get('billing_event') or 'IMPRESSIONS',
                        'dailyBudget': daily_val,
                        'lifetimeBudget': life_val,
                        'targeting': ras.get('targeting') or {},
                        'spend': as_ins['spend'],
                        'impressions': as_ins['impressions'],
                        'reach': as_ins['reach'],
                        'clicks': as_ins['clicks'],
                        'ctr': as_ins['ctr'],
                        'cpc': as_ins['cpc'],
                    },
                )
                adsets_map[mas_id] = adset_obj

            if first_daily > 0:
                camp.dailyBudget = first_daily
            camp.save()

            remote_ads = (rc.get('ads') or {}).get('data', []) if isinstance(rc.get('ads'), dict) else []
            for rad in remote_ads:
                mad_id = str(rad.get('id') or '')
                parent_adset = adsets_map.get(str(rad.get('adset_id') or '')) or camp.adsets.first()
                if not mad_id or not parent_adset:
                    continue
                ad_ins = _extract_insights_row(rad)
                cr = rad.get('creative') if isinstance(rad.get('creative'), dict) else {}
                SaaSAdCreativeAd.objects.update_or_create(
                    metaAdId=mad_id,
                    tenantId=tenant.tenantId,
                    defaults={
                        'id': f"sad-{uuid.uuid4().hex[:10]}",
                        'adset': parent_adset,
                        'metaCreativeId': str(cr.get('id') or ''),
                        'name': rad.get('name') or f'Ad {mad_id}',
                        'status': rad.get('status') or 'PAUSED',
                        'effectiveStatus': rad.get('effective_status') or 'PAUSED',
                        'primaryText': cr.get('body') or '',
                        'headline': cr.get('title') or '',
                        'imageUrl': cr.get('image_url') or '',
                        'objectStoryId': cr.get('effective_object_story_id') or '',
                        'reviewFeedback': rad.get('ad_review_feedback') or {},
                        'spend': ad_ins['spend'],
                        'impressions': ad_ins['impressions'],
                        'reach': ad_ins['reach'],
                        'clicks': ad_ins['clicks'],
                    },
                )

        conn.lastSyncedAt = timezone.now()
        conn.lastError = ''
        conn.save()

    # Always recompute live store order ROAS across this tenant's campaigns
    campaigns = list(SaaSAdCampaign.objects.filter(tenantId=tenant.tenantId).prefetch_related('adsets__ads'))
    for camp in campaigns:
        ord_cnt, rev_sum, roas_val = _compute_order_attribution(camp, orders_list)
        camp.storeOrders = ord_cnt
        camp.storeRevenue = rev_sum
        camp.roas = roas_val
        camp.save(update_fields=['storeOrders', 'storeRevenue', 'roas'])

    return Response({
        'success': True,
        'synced': conn.isConnected,
        'connection': MetaTenantConnectionSerializer(conn).data,
        'campaigns': SaaSAdCampaignSerializer(campaigns, many=True).data,
        'message': (
            f"Two-way synced {len(campaigns)} Meta campaigns & live store ROAS attribution."
            if conn.isConnected
            else "Live store order attribution refreshed (connect Facebook OAuth/Token to sync remote Meta Insights)."
        ),
    })


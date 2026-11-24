import io
import os
import sys
import platform
import django
from django.conf import settings
from django.db import connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.core.management import call_command


@api_view(['GET'])
@permission_classes([AllowAny])
def api_status(request):
    from core.views import resolve_backend_runtime_context
    ctx = resolve_backend_runtime_context(request)
    base = ctx['backend_base_url']
    return Response({
        'status': 'healthy',
        'store_name': ctx['store_name'],
        'platform': f"{ctx['platform_title']} Core",
        'version': '1.0.0',
        'backend_url': base,
        'frontend_url': ctx['frontend_url'],
        'endpoints': {
            'products': f"{base}/api/v1/products",
            'orders': f"{base}/api/v1/orders",
            'checkout': f"{base}/api/v1/checkout",
            'couriers': f"{base}/api/v1/couriers",
            'payments': f"{base}/api/v1/payments/methods",
            'tracking': f"{base}/api/v1/tracking/lookup",
            'users': f"{base}/api/v1/users",
            'system_blueprint': f"{base}/api/v1/system/blueprint",
            'system_migrate': f"{base}/api/v1/system/migrate",
        }
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def system_blueprint(request):
    """
    Live System Architecture, Integrations, Database Inventory & Deployment Readiness Diagnostics.
    Returns real-time configuration state without exposing raw secrets, passwords, or private keys.
    """
    from products.models import Product, Coupon, Review, MediaAsset
    from order.models import Order
    from core.models import ShopSetting, StaffMember, RecycleBinItem, AuditLog, NotificationLog
    from couriers.models import CourierConfig
    from payments.models import PaymentMethodSetting, PaymentGatewayConfig, PaymentTransaction

    shop_settings = ShopSetting.get_settings()
    extra_cfg = shop_settings.extraConfig if isinstance(shop_settings.extraConfig, dict) else {}
    sec_cfg = extra_cfg.get('security', {}) if isinstance(extra_cfg.get('security'), dict) else {}
    portal_cfg = extra_cfg.get('portalAccess', {}) if isinstance(extra_cfg.get('portalAccess'), dict) else {}

    couriers_qs = CourierConfig.objects.all().order_by('id')
    couriers_data = []
    for c in couriers_qs:
        c_extra = c.extraCredentials if isinstance(c.extraCredentials, dict) else {}
        webhook_path = f"/api/v1/webhooks/couriers/{c.id}"
        couriers_data.append({
            'id': c.id,
            'name': c.name,
            'isActive': bool(c.isActive),
            'sandboxMode': bool(c_extra.get('sandboxMode', False)),
            'hasCredentials': bool((c.apiKey or '').strip() or (c.secret or '').strip()),
            'insideDhakaRate': float(c.insideDhakaRate or 0),
            'outsideDhakaRate': float(c.outsideDhakaRate or 0),
            'webhookPath': webhook_path,
            'webhookUrl': request.build_absolute_uri(webhook_path),
        })

    ssl_gw = PaymentGatewayConfig.objects.filter(gateway_id='sslcommerz').first()
    ssl_sandbox_sim = bool((extra_cfg.get('payment') or {}).get('allowSandboxSimulator', False))
    payments_qs = PaymentMethodSetting.objects.all().order_by('display_order', 'id')
    payments_data = []
    for p in payments_qs:
        is_ssl = p.method_id.lower() == 'sslcommerz'
        has_cfg = (
            True if p.method_id.lower() == 'cod'
            else (bool(ssl_gw and (ssl_gw.store_id or '').strip() and (ssl_gw.store_password or '').strip()) if is_ssl else bool((p.number or '').strip()))
        )
        payments_data.append({
            'id': p.method_id,
            'name': p.name,
            'type': p.account_type,
            'isActive': bool(p.is_active),
            'allowSandboxSimulator': ssl_sandbox_sim if is_ssl else False,
            'isSandbox': bool(ssl_gw.is_sandbox) if (is_ssl and ssl_gw) else False,
            'hasMerchantConfig': has_cfg,
        })

    staff_count = StaffMember.objects.count()
    riders_count = StaffMember.objects.filter(role='RIDER').count()
    counts = {
        'products': Product.objects.count(),
        'orders': Order.objects.count(),
        'coupons': Coupon.objects.count(),
        'reviews': Review.objects.count(),
        'staff': staff_count,
        'riders': riders_count,
        'media': MediaAsset.objects.count(),
        'transactions': PaymentTransaction.objects.count(),
        'notifications': NotificationLog.objects.count(),
        'auditLogs': AuditLog.objects.count(),
        'recycleBin': RecycleBinItem.objects.count(),
    }

    auto_seed_on_boot = os.getenv('AUTO_SEED_ON_BOOT', 'False').lower() in ('true', '1', 't')
    auto_migrate_on_boot = os.getenv('AUTO_MIGRATE_ON_BOOT', 'True').lower() in ('true', '1', 't')
    email_configured = bool(os.getenv('EMAIL_HOST_USER', '').strip() or (shop_settings.emailHostUser or '').strip())
    secret_env_configured = bool(os.getenv('DJANGO_SECRET_KEY', '').strip())

    env_checks = [
        {
            'key': 'DJANGO_SECRET_KEY',
            'label': 'Django Secret Key (ENV)',
            'configured': secret_env_configured,
            'status': 'ok' if secret_env_configured else 'warning',
            'detail': 'Configured via DJANGO_SECRET_KEY environment variable' if secret_env_configured else 'Using persisted runtime-generated instance key (set DJANGO_SECRET_KEY in production .env)',
        },
        {
            'key': 'DEBUG',
            'label': 'Production Debug Mode',
            'configured': not bool(settings.DEBUG),
            'status': 'ok' if not settings.DEBUG else 'warning',
            'detail': 'DEBUG=False (Hardened production mode)' if not settings.DEBUG else 'DEBUG=True (Development diagnostic mode active)',
        },
        {
            'key': 'AUTO_SEED_ON_BOOT',
            'label': 'WSGI Auto Demo Seeding',
            'configured': not auto_seed_on_boot,
            'status': 'ok' if not auto_seed_on_boot else 'warning',
            'detail': 'Disabled on boot (live catalog will not auto-reseed demo items)' if not auto_seed_on_boot else 'Enabled on boot (empty DB auto-seeds demo items)',
        },
        {
            'key': 'INITIAL_ADMIN',
            'label': 'Super Admin Account Provisioned',
            'configured': staff_count > 0,
            'status': 'ok' if staff_count > 0 else 'warning',
            'detail': f'{staff_count} staff account(s) active in central DB' if staff_count > 0 else 'No staff accounts yet (initial Super Admin setup required)',
        },
        {
            'key': 'SMTP_EMAIL',
            'label': 'SMTP Transactional Email',
            'configured': email_configured,
            'status': 'ok' if email_configured else 'info',
            'detail': f'Backend: {settings.EMAIL_BACKEND.rsplit(".", 1)[-1]}' if email_configured else 'No SMTP credentials set (using SafeConsoleEmailBackend)',
        },
        {
            'key': 'TWO_FACTOR_AUTH',
            'label': 'Admin Two-Step Verification',
            'configured': bool(sec_cfg.get('twoFactorEnabled', False)),
            'status': 'ok' if sec_cfg.get('twoFactorEnabled') else 'info',
            'detail': f"Enabled ({sec_cfg.get('twoFactorMethod', 'TOTP')})" if sec_cfg.get('twoFactorEnabled') else 'Disabled in Security & Audit settings',
        },
    ]

    ssl_ipn_path = '/api/v1/payments/sslcommerz/ipn/'
    webhook_courier_path = '/api/v1/webhooks/couriers'

    return Response({
        'success': True,
        'blueprint': {
            'shopName': shop_settings.shopName or 'Store',
            'runtime': {
                'pythonVersion': platform.python_version(),
                'djangoVersion': django.get_version(),
                'debug': bool(settings.DEBUG),
                'dbEngine': connection.vendor,
                'emailBackend': settings.EMAIL_BACKEND.rsplit('.', 1)[-1],
                'timeZone': getattr(settings, 'TIME_ZONE', 'UTC'),
                'autoMigrateOnBoot': auto_migrate_on_boot,
                'autoSeedOnBoot': auto_seed_on_boot,
                'allowedHostsCount': len(getattr(settings, 'ALLOWED_HOSTS', [])),
                'secretKeySource': 'ENV' if secret_env_configured else 'RUNTIME_GENERATED',
            },
            'counts': counts,
            'couriers': couriers_data,
            'payments': payments_data,
            'security': {
                'twoFactorEnabled': bool(sec_cfg.get('twoFactorEnabled', False)),
                'twoFactorMethod': sec_cfg.get('twoFactorMethod', 'TOTP'),
                'demoCustomerEnabled': bool(portal_cfg.get('demoCustomerEnabled', False)),
                'frontPanelAllowedRoles': portal_cfg.get('frontPanelAllowedRoles', ['ADMIN']),
                'riderPortalAllowedRoles': portal_cfg.get('riderPortalAllowedRoles', ['RIDER', 'DISPATCHER', 'ADMIN']),
                'staffNeedsBootstrap': staff_count == 0,
            },
            'callbacks': {
                'unifiedCourierWebhook': request.build_absolute_uri(webhook_courier_path),
                'sslcommerzIpn': request.build_absolute_uri(ssl_ipn_path),
            },
            'envChecks': env_checks,
            'endpoints': {
                'products': '/api/v1/products',
                'orders': '/api/v1/orders',
                'checkout': '/api/v1/checkout',
                'couriers': '/api/v1/couriers',
                'courierControls': '/api/v1/couriers/controls',
                'payments': '/api/v1/payments/methods',
                'staff': '/api/v1/staff',
                'shopSettings': '/api/v1/settings/shop',
                'portalAccess': '/api/v1/security/portal-access',
                'tracking': '/api/v1/tracking/lookup',
                'systemBlueprint': '/api/v1/system/blueprint',
            },
        }
    })


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def trigger_migration(request):
    """
    On-demand database schema migration endpoint.
    Initial demo seeding only runs if explicitly requested (?seed=true / {"seed": true}).
    """
    out_migrate = io.StringIO()
    out_seed = io.StringIO()
    seed_param = str(
        request.query_params.get('seed')
        or (request.data.get('seed') if isinstance(request.data, dict) else '')
        or ''
    ).lower() in ('true', '1', 'yes')
    try:
        call_command('migrate', interactive=False, stdout=out_migrate)
        migrate_log = out_migrate.getvalue()

        seed_log = ''
        if seed_param:
            call_command('seed_data', interactive=False, stdout=out_seed)
            seed_log = out_seed.getvalue()

        return Response({
            'success': True,
            'message': 'Database migration applied successfully!' if not seed_param else 'Database migration and initial data applied successfully!',
            'migrate_log': migrate_log,
            'seed_log': seed_log,
        })
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Migration failed: {str(e)}',
            'error': str(e),
            'partial_log': out_migrate.getvalue(),
        }, status=500)

import base64
import hashlib
import hmac
import json
import os
import re
import time

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from rest_framework import status
from rest_framework.response import Response

from .models import ShopSetting, StaffMember, StoreTenant

_TENANT_RE = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


def _get_fernet() -> Fernet:
    """
    Returns a Fernet instance keyed deterministically from META_TOKEN_ENCRYPTION_KEY
    or Django's SECRET_KEY so encrypted Meta tokens remain unreadable at rest in the DB.
    """
    raw_secret = (os.getenv('META_TOKEN_ENCRYPTION_KEY') or settings.SECRET_KEY or 'amardokan-meta-key').encode('utf-8')
    digest = hashlib.sha256(raw_secret).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_token(raw_token: str) -> str:
    clean = str(raw_token or '').strip()
    if not clean:
        return ''
    return _get_fernet().encrypt(clean.encode('utf-8')).decode('utf-8')


def decrypt_token(encrypted_token: str) -> str:
    clean = str(encrypted_token or '').strip()
    if not clean:
        return ''
    try:
        return _get_fernet().decrypt(clean.encode('utf-8')).decode('utf-8')
    except (InvalidToken, Exception):
        return ''


def mask_token(raw_token: str) -> str:
    clean = str(raw_token or '').strip()
    if not clean:
        return ''
    if len(clean) <= 10:
        return clean[:2] + '••••' + clean[-2:]
    return f"{clean[:4]}••••••{clean[-4:]}"


def create_oauth_state(tenant_id: str, return_origin: str = '') -> str:
    signer = TimestampSigner(salt='meta-oauth-state')
    payload = base64.urlsafe_b64encode(
        json.dumps({'tenantId': tenant_id, 'origin': return_origin, 'nonce': int(time.time())}).encode('utf-8')
    ).decode('utf-8')
    return signer.sign(payload)


def verify_oauth_state(state_str: str, max_age_seconds: int = 900):
    if not state_str:
        return None
    signer = TimestampSigner(salt='meta-oauth-state')
    try:
        raw_payload = signer.unsign(state_str, max_age=max_age_seconds)
        decoded = base64.urlsafe_b64decode(raw_payload.encode('utf-8')).decode('utf-8')
        data = json.loads(decoded)
        return data if isinstance(data, dict) else None
    except (BadSignature, SignatureExpired, Exception):
        return None


def verify_bearer_staff_token(token_str: str):
    """Verifies staff HMAC-SHA256 bearer token compatible with views._sign_payload."""
    try:
        if not token_str or '.' not in token_str:
            return None
        encoded_payload, sig = token_str.split('.', 1)
        expected_sig = hmac.new(
            settings.SECRET_KEY.encode('utf-8'),
            encoded_payload.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload_bytes = base64.urlsafe_b64decode(encoded_payload.encode('utf-8'))
        payload = json.loads(payload_bytes.decode('utf-8'))
        if payload.get('exp', 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def sanitize_tenant_id(raw_id: str) -> str:
    candidate = str(raw_id or 'default').strip()
    if not candidate or not _TENANT_RE.match(candidate):
        return 'default'
    return candidate


def resolve_tenant_context(request, allowed_roles=('ADMIN', 'MANAGER')):
    """
    Enforces multi-tenant isolation & staff/admin access control.
    Returns (tenant_obj, actor_label, error_response).
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    bearer_token = auth_header[7:].strip() if auth_header.startswith('Bearer ') else ''
    require_strict_auth = os.getenv('REQUIRE_STAFF_AUTH', '').lower() == 'true'

    actor_label = 'Admin'
    staff_member = None

    if bearer_token or require_strict_auth:
        if not bearer_token:
            return None, '', Response(
                {'success': False, 'message': 'Authentication required for SaaS Meta Ads endpoints.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        payload = verify_bearer_staff_token(bearer_token)
        if not payload:
            return None, '', Response(
                {'success': False, 'message': 'Session expired or invalid staff token.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        staff_member = StaffMember.objects.filter(id=payload.get('id')).first()
        if not staff_member or staff_member.status != 'ACTIVE':
            return None, '', Response(
                {'success': False, 'message': 'Staff account is inactive or suspended.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if allowed_roles and staff_member.role not in allowed_roles:
            return None, '', Response(
                {'success': False, 'message': f'Role {staff_member.role} does not have Meta Ads permission.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        actor_label = f"{staff_member.name} (@{staff_member.username})"

    raw_tenant = (
        request.META.get('HTTP_X_TENANT_ID')
        or request.query_params.get('tenant_id')
        or (request.data.get('tenantId') if isinstance(getattr(request, 'data', None), dict) else '')
        or 'default'
    )
    tenant_id = sanitize_tenant_id(raw_tenant)

    if tenant_id == 'default':
        shop = ShopSetting.get_settings()
        tenant_obj, _ = StoreTenant.objects.get_or_create(
            tenantId='default',
            defaults={
                'id': 'tenant-default',
                'storeName': shop.shopName or 'AmarDokan',
                'ownerEmail': shop.supportEmail or 'admin@amardokan.com',
                'plan': 'PRO',
                'status': 'ACTIVE',
            },
        )
    else:
        tenant_obj = StoreTenant.objects.filter(tenantId=tenant_id).first()
        if not tenant_obj:
            return None, '', Response(
                {'success': False, 'message': f'Unknown SaaS store tenant: {tenant_id}'},
                status=status.HTTP_404_NOT_FOUND,
            )

    if tenant_obj.status != 'ACTIVE':
        return None, '', Response(
            {'success': False, 'message': f'Store tenant "{tenant_obj.storeName}" is currently suspended.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    return tenant_obj, actor_label, None


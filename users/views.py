import os
import re
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password

from .models import CustomerProfile
from order.models import Order
from order.serializers import OrderSerializer


def normalize_phone(phone_str):
    if not phone_str:
        return ''
    digits = re.sub(r'\D', '', str(phone_str))
    if digits.startswith('880'):
        digits = digits[2:]
    elif not digits.startswith('0') and len(digits) >= 10:
        digits = '0' + digits
    return digits


@api_view(['POST'])
@permission_classes([AllowAny])
def user_login_or_register(request):
    data = request.data or {}
    action = data.get('action', '').strip().lower()
    raw_identifier = str(data.get('phone') or data.get('identifier') or '').strip()
    clean_phone = normalize_phone(raw_identifier)
    password = str(data.get('password') or '').strip()
    name = str(data.get('name') or 'Valued Customer').strip()
    email = data.get('email', '').strip() or None
    full_address = str(data.get('fullAddress') or '').strip()
    district = str(data.get('district') or 'Dhaka').strip()
    thana = str(data.get('thana') or 'Mirpur').strip()

    # Special handling for Demo User login
    demo_phone = os.getenv('DEMO_USER_PHONE', '01712345678')
    demo_pass = os.getenv('DEMO_USER_PASS', '1234')
    if action == 'demo' or (clean_phone == demo_phone and action != 'register'):
        demo_profile = CustomerProfile.objects.filter(phone=demo_phone).first()
        if not demo_profile:
            demo_profile = CustomerProfile.objects.create(
                id=f'usr-demo-{demo_phone}',
                name='Demo Customer',
                phone=demo_phone,
                email='demo@customer.com',
                district='Dhaka',
                thana='Dhanmondi',
                fullAddress='House 12, Road 4/A, Dhanmondi, Dhaka',
                password=make_password(demo_pass),
            )
        return Response({
            'success': True,
            'message': 'Successfully logged in as Demo Customer!',
            'user': {
                'id': demo_profile.id,
                'name': demo_profile.name,
                'phone': demo_profile.phone,
                'email': demo_profile.email,
                'district': demo_profile.district,
                'thana': demo_profile.thana,
                'fullAddress': demo_profile.fullAddress,
            }
        })

    # Registration Flow
    if action == 'register':
        if not clean_phone or len(clean_phone) < 11:
            return Response(
                {'success': False, 'message': 'Please enter a valid 11-digit phone number (e.g. 017XXXXXXXX)'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not name or name == 'Valued Customer':
            return Response(
                {'success': False, 'message': 'Please enter your full name'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not password or len(password) < 4:
            return Response(
                {'success': False, 'message': 'Password must be at least 4 characters'},
                status=status.HTTP_400_BAD_REQUEST
            )

        existing = CustomerProfile.objects.filter(phone=clean_phone).first()
        if not existing and len(clean_phone) >= 10:
            existing = CustomerProfile.objects.filter(phone__endswith=clean_phone[-10:]).first()

        if existing:
            if not existing.password:
                # User profile was auto-created during guest order, activate with password
                existing.name = name
                if email:
                    existing.email = email
                if district:
                    existing.district = district
                if thana:
                    existing.thana = thana
                if full_address:
                    existing.fullAddress = full_address
                existing.password = make_password(password)
                existing.save()
                return Response({
                    'success': True,
                    'message': 'Your account has been successfully registered and activated!',
                    'user': {
                        'id': existing.id,
                        'name': existing.name,
                        'phone': existing.phone,
                        'email': existing.email,
                        'district': existing.district,
                        'thana': existing.thana,
                        'fullAddress': existing.fullAddress,
                    }
                }, status=status.HTTP_200_OK)
            else:
                return Response(
                    {'success': False, 'message': 'An account with this phone number already exists. Please log in.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        profile = CustomerProfile.objects.create(
            id=f"usr-{clean_phone}-{int(timezone.now().timestamp())}",
            name=name,
            phone=clean_phone,
            email=email or f"{clean_phone}@customer.amardokan.com",
            district=district,
            thana=thana,
            fullAddress=full_address,
            password=make_password(password),
        )

        return Response({
            'success': True,
            'message': 'Your account has been registered successfully!',
            'user': {
                'id': profile.id,
                'name': profile.name,
                'phone': profile.phone,
                'email': profile.email,
                'district': profile.district,
                'thana': profile.thana,
                'fullAddress': profile.fullAddress,
            }
        }, status=status.HTTP_201_CREATED)

    # Login Flow
    profile = None
    if '@' in raw_identifier:
        profile = CustomerProfile.objects.filter(email__iexact=raw_identifier).first()

    if not profile and clean_phone:
        profile = CustomerProfile.objects.filter(phone=clean_phone).first()
        if not profile and len(clean_phone) >= 10:
            profile = CustomerProfile.objects.filter(phone__endswith=clean_phone[-10:]).first()

    if not profile:
        return Response(
            {'success': False, 'message': 'No account found with this phone number or email. Please check your credentials or register.'},
            status=status.HTTP_404_NOT_FOUND
        )

    # Validate password if user set a password
    if profile.password:
        if not password or not check_password(password, profile.password):
            return Response(
                {'success': False, 'message': 'Incorrect password. Please enter the correct password.'},
                status=status.HTTP_400_BAD_REQUEST
            )
    else:
        # Profile exists from guest order without password set yet, set it now
        if password and len(password) >= 4:
            profile.password = make_password(password)
            profile.save()

    return Response({
        'success': True,
        'message': 'Successfully logged in!',
        'user': {
            'id': profile.id,
            'name': profile.name,
            'phone': profile.phone,
            'email': profile.email or f"{profile.phone}@customer.amardokan.com",
            'district': profile.district or 'Dhaka',
            'thana': profile.thana or 'Mirpur',
            'fullAddress': profile.fullAddress or '',
        }
    })


@api_view(['GET', 'PUT', 'PATCH'])
@permission_classes([AllowAny])
def user_profile(request, phone):
    clean = normalize_phone(phone)
    profile = CustomerProfile.objects.filter(phone=clean).first()
    if not profile and len(clean) >= 10:
        profile = CustomerProfile.objects.filter(phone__endswith=clean[-10:]).first()

    if not profile:
        return Response({'success': False, 'message': 'User profile not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        data = request.data or {}
        if 'name' in data and data['name']:
            profile.name = str(data['name']).strip()
        if 'email' in data:
            profile.email = str(data['email']).strip() or None
        if 'district' in data and data['district']:
            profile.district = str(data['district']).strip()
        if 'thana' in data and data['thana']:
            profile.thana = str(data['thana']).strip()
        if 'fullAddress' in data:
            profile.fullAddress = str(data['fullAddress']).strip()
        if 'newPassword' in data and str(data['newPassword']).strip():
            new_pass = str(data['newPassword']).strip()
            current_pass = str(data.get('currentPassword') or '').strip()
            if len(new_pass) < 4:
                return Response({'success': False, 'message': 'New password must be at least 4 characters.'}, status=status.HTTP_400_BAD_REQUEST)
            if profile.password and not check_password(current_pass, profile.password):
                return Response({'success': False, 'message': 'Current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
            profile.password = make_password(new_pass)
        profile.save()

    return Response({
        'success': True,
        'user': {
            'id': profile.id,
            'name': profile.name,
            'phone': profile.phone,
            'email': profile.email,
            'district': profile.district,
            'thana': profile.thana,
            'fullAddress': profile.fullAddress,
        }
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def customer_orders(request, phone):
    clean = normalize_phone(phone)
    if not clean or len(clean) < 6:
        return Response({'success': False, 'orders': [], 'message': 'Invalid phone'}, status=status.HTTP_400_BAD_REQUEST)

    matched_orders = []
    for order in Order.objects.all():
        if isinstance(order.address, dict):
            ord_phone = normalize_phone(order.address.get('phone', ''))
            if ord_phone and (ord_phone.endswith(clean[-10:]) or clean.endswith(ord_phone[-10:])):
                matched_orders.append(order)

    serializer = OrderSerializer(matched_orders, many=True)
    return Response({
        'success': True,
        'orders': serializer.data,
        'total': len(matched_orders)
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def forgot_password_view(request):
    """
    Accepts: { "email": "user@example.com" }
    Looks up CustomerProfile or StaffMember by email,
    generates a secure temporary password, updates the DB,
    and sends an email with the reset password to the user.
    """
    import random
    import string
    from core.models import StaffMember, ShopSetting, AuditLog
    from django.core.mail import EmailMultiAlternatives, get_connection

    data = request.data or {}
    email = str(data.get('email') or '').strip().lower()

    if not email or '@' not in email:
        return Response({
            'success': False,
            'message': 'Please provide a valid email address.'
        }, status=status.HTTP_400_BAD_REQUEST)

    # Search CustomerProfile and StaffMember
    customer = CustomerProfile.objects.filter(email__iexact=email).first()
    staff = StaffMember.objects.filter(email__iexact=email).first()

    if not customer and not staff:
        return Response({
            'success': False,
            'message': 'No account found with this email address. Please check your email or create an account.'
        }, status=status.HTTP_404_NOT_FOUND)

    # Generate 8-character temporary password
    chars = string.ascii_uppercase + string.digits
    temp_pass = f"AD-{''.join(random.choice(chars) for _ in range(6))}"

    recipient_name = customer.name if customer else (staff.name if staff else 'User')
    account_type = 'Customer' if customer else f'Staff ({staff.role})'

    s = ShopSetting.get_settings()
    if not (s.emailHostUser and s.emailHostPassword):
        return Response({
            'success': False,
            'emailSent': False,
            'message': 'Email service is not configured yet. Please contact store support to reset your password.'
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    try:
        shop_title = s.shopName or 'AmarDokan'
        from_email = s.defaultFromEmail or f"{shop_title} <{s.emailHostUser.strip()}>"

        subject = f"🔒 {shop_title} - Password Reset"
        body = f"""Dear {recipient_name},

A password reset was requested for your {shop_title} account.

Your temporary password: {temp_pass}

Please use this temporary password to log in and update your password from your account profile/settings.

Best regards,
{shop_title} Security Team"""

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 550px; margin: 0 auto; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 16px; padding: 24px; color: #1f2937;">
            <div style="text-align: center; margin-bottom: 20px;">
                <h2 style="color: #059669; margin: 0; font-size: 24px; font-weight: 800;">{shop_title}</h2>
                <p style="color: #6b7280; font-size: 13px; margin: 4px 0 0 0;">Account Security & Password Recovery</p>
            </div>
            
            <p style="font-size: 14px; line-height: 1.6;">Dear <strong>{recipient_name}</strong>,</p>
            <p style="font-size: 14px; line-height: 1.6;">A temporary password has been generated for your <strong>{account_type}</strong> account:</p>
            
            <div style="background: #f0fdf4; border: 2px dashed #059669; border-radius: 12px; padding: 16px; text-align: center; margin: 20px 0;">
                <span style="font-size: 12px; color: #047857; text-transform: uppercase; font-weight: bold; letter-spacing: 1px; display: block; margin-bottom: 6px;">New Temporary Password</span>
                <span style="font-size: 24px; font-family: monospace; font-weight: bold; color: #065f46; letter-spacing: 2px;">{temp_pass}</span>
            </div>

            <p style="font-size: 13px; color: #4b5563; line-height: 1.6;">You can log in directly with this password. For your security, please change your password after logging in.</p>
            
            <hr style="border: none; border-top: 1px solid #f3f4f6; margin: 20px 0;" />
            <p style="font-size: 11px; color: #9ca3af; text-align: center; margin: 0;">If you did not request this password reset, please contact customer support immediately.</p>
        </div>
        """

        conn = get_connection(
            backend='django.core.mail.backends.smtp.EmailBackend',
            host=s.emailHost or 'smtp.gmail.com',
            port=int(s.emailPort or 587),
            username=s.emailHostUser.strip(),
            password=s.emailHostPassword.strip(),
            use_tls=bool(s.emailUseTls),
            use_ssl=bool(s.emailUseSsl),
            timeout=10,
        )

        msg = EmailMultiAlternatives(subject=subject, body=body, from_email=from_email, to=[email], connection=conn)
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
    except Exception as email_err:
        import logging
        logging.getLogger(__name__).warning(f"Forgot password email dispatch failed: {email_err}")
        return Response({
            'success': False,
            'emailSent': False,
            'message': f"Unable to send reset email right now ({str(email_err)}). Your existing password was not changed."
        }, status=status.HTTP_502_BAD_GATEWAY)

    if customer:
        customer.password = make_password(temp_pass)
        customer.save(update_fields=['password'])

    if staff:
        staff.password = temp_pass
        staff.save(update_fields=['password'])

    AuditLog.log(
        actor=recipient_name,
        role='CUSTOMER' if customer else 'STAFF',
        action='PASSWORD_RESET_DISPATCHED',
        target_type='USER',
        target_id=email,
        details=f"Password reset email dispatched successfully to {email} ({account_type}).",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    return Response({
        'success': True,
        'emailSent': True,
        'message': f"A temporary password has been sent to {email}. Please check your inbox or spam folder."
    })


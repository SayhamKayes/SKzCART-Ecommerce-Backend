import os
import time
from django.core.mail import EmailMultiAlternatives, get_connection
from django.conf import settings
from core.models import NotificationLog


def get_courier_tracking_urls(courier_provider: str, tracking_id: str, order_id: str) -> dict:
    """
    Returns live tracking URLs for Bangladesh courier services.
    """
    prov_lower = (courier_provider or '').lower()
    carrier_url = ""

    if 'steadfast' in prov_lower:
        carrier_url = f"https://steadfast.com.bd/t/{tracking_id}"
    elif 'pathao' in prov_lower:
        carrier_url = f"https://merchant.pathao.com/tracking?consignment_id={tracking_id}"
    elif 'redx' in prov_lower:
        carrier_url = f"https://redx.com.bd/track?trackingId={tracking_id}"
    elif 'paperfly' in prov_lower:
        carrier_url = f"https://paperfly.com.bd/tracking?id={tracking_id}"
    elif 'ecourier' in prov_lower:
        carrier_url = f"https://ecourier.com.bd/tracking?ecr={tracking_id}"

    frontend_base = (
        os.environ.get('FRONTEND_URL')
        or os.environ.get('APP_URL')
        or getattr(settings, 'SSLCOMMERZ_FRONTEND_URL', '')
    ).rstrip('/')
    native_url = f"{frontend_base}/track-order?id={order_id}" if frontend_base else f"/track-order?id={order_id}"

    return {
        'carrier_url': carrier_url,
        'native_url': native_url
    }

def send_order_email(order, event: str) -> dict:
    """
    Automated Order Email Dispatcher with responsive HTML template and plain text fallback.
    Sends confirmation, invoice breakdown, and live tracking links with anti-spam protections.
    """
    address = order.address if isinstance(order.address, dict) else {}
    customer_name = address.get('customerName', 'Valued Customer')
    customer_email = (getattr(order, 'customerEmail', None) or address.get('email') or '').strip()
    
    if not customer_email or '@' not in customer_email:
        return {
            'success': False,
            'status': 'SKIPPED',
            'message': 'No valid customer email address provided'
        }

    now_str = time.strftime('%Y-%m-%d %H:%M:%S')

    # Shop Info (Strictly read from Admin ShopSetting)
    shop_name = "AmarDokan"
    hotline = ""
    support_email = ""
    try:
        from core.models import ShopSetting
        s = ShopSetting.get_settings()
        if s.shopName:
            shop_name = s.shopName
        if s.hotline is not None:
            hotline = str(s.hotline).strip()
        if getattr(s, 'supportEmail', None) is not None:
            support_email = str(s.supportEmail).strip()
    except Exception:
        pass

    # Courier Tracking
    courier_info = order.courier if isinstance(order.courier, dict) else {}
    prov_name = courier_info.get('provider', 'Courier').upper()
    trkid = courier_info.get('trackingId', 'N/A')

    frontend_url = os.environ.get('FRONTEND_URL') or os.environ.get('APP_URL') or getattr(settings, 'SSLCOMMERZ_FRONTEND_URL', 'http://localhost:3001')
    live_tracking_url = f"{frontend_url.rstrip('/')}/track-order?id={order.id}"

    # Event specific headers & text across all order lifecycle statuses
    ev = (event or 'ORDER_CREATED').upper()
    if ev in ('ORDER_CREATED', 'PENDING'):
        subject = f"Order Confirmation: #{order.id} - {shop_name}"
        headline = "Your order has been placed successfully!"
        status_badge_text = "Order Received"
        status_badge_bg = "#10B981"
        sub_message = f"Dear {customer_name}, thank you for shopping with {shop_name}! Your order #{order.id} has been received successfully."
    elif ev in ('ORDER_CONFIRMED', 'CONFIRMED'):
        subject = f"Order Confirmed: #{order.id} - {shop_name}"
        headline = "Your order has been confirmed!"
        status_badge_text = "Confirmed"
        status_badge_bg = "#3B82F6"
        sub_message = f"Dear {customer_name}, your order #{order.id} is confirmed and packaging is underway."
    elif ev in ('SHIPMENT_CREATED', 'COURIER_CREATED'):
        subject = f"Courier Booked: #{order.id} - {shop_name}"
        headline = "Your order has been booked with courier!"
        status_badge_text = f"Courier Booked ({prov_name})"
        status_badge_bg = "#6366F1"
        sub_message = f"Dear {customer_name}, your parcel for order #{order.id} has been handed over to {prov_name} Courier (Tracking ID: {trkid})."
    elif ev == 'PICKED_UP':
        subject = f"Parcel Picked Up: #{order.id} - {shop_name}"
        headline = "Courier has picked up your parcel!"
        status_badge_text = "Picked Up"
        status_badge_bg = "#7C3AED"
        sub_message = f"Dear {customer_name}, our courier partner has collected your parcel #{order.id} from our hub."
    elif ev == 'IN_TRANSIT':
        subject = f"Parcel In Transit: #{order.id} - {shop_name}"
        headline = "Your parcel is on the way!"
        status_badge_text = "In Transit"
        status_badge_bg = "#8B5CF6"
        sub_message = f"Dear {customer_name}, your order #{order.id} is currently in transit to your destination hub."
    elif ev == 'OUT_FOR_DELIVERY':
        subject = f"Parcel Out for Delivery Today: #{order.id} - {shop_name}"
        headline = "Your parcel will arrive today!"
        status_badge_text = "Out for Delivery"
        status_badge_bg = "#F59E0B"
        sub_message = f"Dear {customer_name}, our courier delivery rider is out for delivery with your package today."
    elif ev == 'DELIVERED':
        subject = f"Parcel Delivered Successfully: #{order.id} - {shop_name}"
        headline = "Your parcel has been delivered!"
        status_badge_text = "Delivered"
        status_badge_bg = "#059669"
        sub_message = f"Dear {customer_name}, your order #{order.id} has been delivered successfully. Thank you for choosing {shop_name}."
    elif ev == 'ON_HOLD':
        subject = f"Order On Hold: #{order.id} - {shop_name}"
        headline = "Your order is temporarily on hold"
        status_badge_text = "On Hold"
        status_badge_bg = "#D97706"
        sub_message = f"Dear {customer_name}, your order #{order.id} is currently on hold. Our team will contact you shortly or please reach out via {support_email}."
    elif ev == 'RETURN_REQUESTED':
        subject = f"Return Requested: #{order.id} - {shop_name}"
        headline = "Your return request has been recorded"
        status_badge_text = "Return Requested"
        status_badge_bg = "#EA580C"
        sub_message = f"Dear {customer_name}, we have received the return request for order #{order.id}."
    elif ev == 'RETURNED':
        subject = f"Order Returned: #{order.id} - {shop_name}"
        headline = "Your parcel has been returned"
        status_badge_text = "Returned"
        status_badge_bg = "#DC2626"
        sub_message = f"Dear {customer_name}, your order #{order.id} has been marked as returned."
    elif ev == 'DELIVERY_FAILED':
        subject = f"Delivery Attempt Failed: #{order.id} - {shop_name}"
        headline = "Delivery attempt was unsuccessful"
        status_badge_text = "Delivery Failed"
        status_badge_bg = "#DC2626"
        sub_message = f"Dear {customer_name}, the delivery attempt for order #{order.id} could not be completed. Please contact us at {hotline} or {support_email}."
    elif ev == 'CANCELLED':
        subject = f"Order Cancelled: #{order.id} - {shop_name}"
        headline = "Your order has been cancelled"
        status_badge_text = "Cancelled"
        status_badge_bg = "#E11D48"
        sub_message = f"Dear {customer_name}, your order #{order.id} has been cancelled. Contact {support_email} if you have any questions."
    elif ev == 'REFUNDED':
        subject = f"Order Refunded: #{order.id} - {shop_name}"
        headline = "Your refund has been processed"
        status_badge_text = "Refunded"
        status_badge_bg = "#0284C7"
        sub_message = f"Dear {customer_name}, the payment refund for order #{order.id} has been processed."
    else:
        readable = ev.replace('_', ' ').title()
        subject = f"Order Update ({readable}): #{order.id} - {shop_name}"
        headline = f"Order Status Update: {readable}"
        status_badge_text = readable
        status_badge_bg = "#475569"
        sub_message = f"Dear {customer_name}, your order #{order.id} status has been updated to {readable}."

    # Generate item rows
    items = order.items if isinstance(order.items, list) else []
    items_html = ""
    items_text_list = []

    for itm in items:
        name = itm.get('name', 'Product')
        qty = itm.get('quantity', 1)
        price = itm.get('price', 0)
        img = itm.get('image', '')
        variant = itm.get('variant') or itm.get('selectedColor') or itm.get('selectedSize') or ''
        variant_str = f" ({variant})" if variant else ""
        row_total = price * qty
        items_text_list.append(f"- {name}{variant_str} x{qty}: ৳{row_total}")

        img_td = ""
        if img:
            img_td = f'<td style="padding: 12px 8px; width: 50px; vertical-align: middle;"><img src="{img}" alt="{name}" style="width: 48px; height: 48px; object-fit: cover; border-radius: 6px; border: 1px solid #e2e8f0;" /></td>'

        items_html += f"""
        <tr style="border-bottom: 1px solid #f1f5f9;">
            {img_td}
            <td style="padding: 12px 8px; vertical-align: middle;">
                <div style="font-weight: 600; color: #1e293b; font-size: 14px;">{name}</div>
                {f'<div style="font-size: 12px; color: #64748b;">Variant: {variant}</div>' if variant else ''}
                <div style="font-size: 12px; color: #94a3b8;">৳{price} x {qty}</div>
            </td>
            <td style="padding: 12px 8px; text-align: right; vertical-align: middle; font-weight: 600; color: #0f172a; font-size: 14px;">
                ৳{row_total}
            </td>
        </tr>
        """

    items_text_plain = "\n".join(items_text_list)

    # Address block
    full_addr = address.get('fullAddress', '')
    thana = address.get('thana', '')
    district = address.get('district', '')
    phone = address.get('phone', '')
    addr_line = f"{full_addr}, {thana}, {district}".strip(', ')

    discount_row_html = ""
    discount_row_text = ""
    if order.discount and order.discount > 0:
        discount_row_html = f"""
        <tr>
            <td style="padding: 6px 0; color: #059669; font-size: 14px;">Coupon Discount:</td>
            <td style="padding: 6px 0; text-align: right; color: #059669; font-weight: 600; font-size: 14px;">-৳{order.discount}</td>
        </tr>
        """
        discount_row_text = f"Coupon Discount: -৳{order.discount}\n"

    # Plain Text representation (Anti-spam best practice)
    plain_text = f"""======================================================
{shop_name} - {subject}
======================================================
{sub_message}

Order Number: #{order.id}
Date: {now_str}
Payment Method: {order.paymentMethod} ({order.paymentStatus})

Purchased Items:
{items_text_plain}

Order Summary:
Subtotal: ৳{order.subtotal}
Delivery Fee: ৳{order.deliveryFee}
{discount_row_text}Total Payable: ৳{order.total}

Delivery Address:
Recipient: {customer_name}
Mobile: {phone}
Address: {addr_line}

Live Parcel Tracking:
{live_tracking_url}

Customer Support: {hotline} | {support_email}
======================================================
"""

    # Dynamic CTA button based on event (Review request for DELIVERED, Tracking for others)
    if event == 'DELIVERED':
        cta_button_html = f"""
    <!-- Delivered & Review CTA -->
    <div style="padding: 22px 24px; text-align: center; background-color: #ecfdf5; border-bottom: 1px solid #d1fae5;">
        <div style="font-size: 15px; font-weight: 700; color: #065f46; margin-bottom: 8px;">Your parcel has been delivered successfully 🎉</div>
        <p style="margin: 0 0 14px 0; font-size: 13px; color: #047857;">How was your experience? Your feedback helps fellow shoppers make better choices.</p>
        <a href="{live_tracking_url}#reviews" target="_blank" style="display: inline-block; background-color: #059669; color: #ffffff; text-decoration: none; padding: 13px 28px; border-radius: 8px; font-weight: 700; font-size: 14px; box-shadow: 0 4px 6px -1px rgba(5, 150, 105, 0.25);">
            ⭐ Rate & Review Products
        </a>
        <div style="font-size: 12px; color: #059669; margin-top: 10px;">Order ID: <strong>#{order.id}</strong></div>
    </div>
        """
    else:
        cta_button_html = f"""
    <!-- Live Tracking CTA -->
    <div style="padding: 20px 24px; text-align: center; background-color: #f0fdf4; border-bottom: 1px solid #dcfce7;">
        <a href="{live_tracking_url}" target="_blank" style="display: inline-block; background-color: #10b981; color: #ffffff; text-decoration: none; padding: 13px 28px; border-radius: 8px; font-weight: 700; font-size: 15px; box-shadow: 0 4px 6px -1px rgba(16, 185, 129, 0.25);">
            🚚 Track Live Parcel
        </a>
        <div style="font-size: 12px; color: #059669; margin-top: 8px;">Order ID: <strong>#{order.id}</strong></div>
    </div>
        """

    # Responsive HTML representation
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{subject}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; color: #334155; line-height: 1.5;">
<div style="max-width: 600px; margin: 20px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    
    <!-- Top Brand Bar -->
    <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 24px; text-align: center; color: #ffffff;">
        <h1 style="margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.5px; color: #ffffff;">{shop_name}</h1>
        <p style="margin: 4px 0 0 0; font-size: 13px; color: #94a3b8;">Smart & Reliable Online Shopping</p>
    </div>

    <!-- Status Banner -->
    <div style="padding: 24px 24px 16px 24px; text-align: center; border-bottom: 1px solid #f1f5f9;">
        <span style="display: inline-block; background-color: {status_badge_bg}; color: #ffffff; font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 9999px; margin-bottom: 10px; text-transform: uppercase;">
            {status_badge_text}
        </span>
        <h2 style="margin: 0 0 8px 0; font-size: 20px; color: #0f172a; font-weight: 700;">{headline}</h2>
        <p style="margin: 0; font-size: 14px; color: #64748b;">{sub_message}</p>
    </div>

    {cta_button_html}

    <!-- Order Items -->
    <div style="padding: 20px 24px;">
        <h3 style="margin: 0 0 12px 0; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px; color: #475569; font-weight: 700;">Order Items ({len(items)})</h3>
        <table style="width: 100%; border-collapse: collapse;">
            <tbody>
                {items_html}
            </tbody>
        </table>
    </div>

    <!-- Invoice Summary -->
    <div style="padding: 16px 24px; background-color: #f8fafc; border-top: 1px solid #f1f5f9; border-bottom: 1px solid #f1f5f9;">
        <table style="width: 100%; border-collapse: collapse;">
            <tr>
                <td style="padding: 6px 0; color: #64748b; font-size: 14px;">Subtotal:</td>
                <td style="padding: 6px 0; text-align: right; color: #1e293b; font-weight: 600; font-size: 14px;">৳{order.subtotal}</td>
            </tr>
            <tr>
                <td style="padding: 6px 0; color: #64748b; font-size: 14px;">Delivery Fee:</td>
                <td style="padding: 6px 0; text-align: right; color: #1e293b; font-weight: 600; font-size: 14px;">৳{order.deliveryFee}</td>
            </tr>
            {discount_row_html}
            <tr style="border-top: 1px dashed #cbd5e1;">
                <td style="padding: 12px 0 6px 0; color: #0f172a; font-weight: 700; font-size: 16px;">Total Payable (Grand Total):</td>
                <td style="padding: 12px 0 6px 0; text-align: right; color: #10b981; font-weight: 800; font-size: 18px;">৳{order.total}</td>
            </tr>
            <tr>
                <td style="padding: 2px 0; color: #64748b; font-size: 12px;">Payment Method:</td>
                <td style="padding: 2px 0; text-align: right; color: #475569; font-weight: 600; font-size: 12px;">{order.paymentMethod} ({order.paymentStatus})</td>
            </tr>
        </table>
    </div>

    <!-- Delivery Address Details -->
    <div style="padding: 20px 24px; border-bottom: 1px solid #f1f5f9;">
        <h3 style="margin: 0 0 10px 0; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px; color: #475569; font-weight: 700;">Delivery Address</h3>
        <div style="font-size: 14px; color: #1e293b; line-height: 1.6;">
            <strong>{customer_name}</strong><br>
            📞 {phone}<br>
            📍 {addr_line}
        </div>
    </div>

    <!-- Footer & Safeguards -->
    <div style="padding: 24px; text-align: center; background-color: #ffffff; color: #94a3b8; font-size: 12px; line-height: 1.6;">
        <p style="margin: 0 0 6px 0;">For any support, call: <strong style="color: #475569;">{hotline}</strong> or email: <a href="mailto:{support_email}" style="color: #10b981; text-decoration: none;">{support_email}</a></p>
        <p style="margin: 0 0 12px 0;">This email was sent to confirm your order at {shop_name}.</p>
        <div style="border-top: 1px solid #f1f5f9; padding-top: 12px; color: #cbd5e1;">
            © 2026 {shop_name}. All rights reserved.
        </div>
    </div>

</div>
</body>
</html>
"""

    # Resolve store owner SMTP credentials from ShopSetting
    smtp_conn, from_email, provider_name, email_enabled, _shop_name = _resolve_smtp_settings(shop_name)
    if not email_enabled:
        return {
            'success': False,
            'status': 'DISABLED',
            'message': 'Automated email dispatch is disabled in Admin settings'
        }
    conn = smtp_conn

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_text,
            from_email=from_email,
            to=[customer_email],
            connection=conn
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)

        # Log to NotificationLog
        email_log = NotificationLog.objects.create(
            id=f"notif-email-{int(time.time() * 1000)}",
            orderId=order.id,
            channel='EMAIL',
            recipient=customer_email,
            templateName=f"{event}_EMAIL",
            messageText=f"{subject}\n{plain_text[:200]}...",
            status='SENT',
            timestamp=now_str,
            provider=provider_name
        )

        return {
            'success': True,
            'status': 'SENT',
            'provider': provider_name,
            'logId': email_log.id,
            'log': email_log
        }
    except Exception as exc:
        print(f"[Email Notification Error] Failed to send email for #{order.id}: {exc}")
        try:
            fail_log = NotificationLog.objects.create(
                id=f"notif-email-{int(time.time() * 1000)}",
                orderId=order.id,
                channel='EMAIL',
                recipient=customer_email,
                templateName=f"{event}_EMAIL",
                messageText=f"{subject} [FAILED: {exc}]",
                status='FAILED',
                timestamp=now_str,
                provider='Django_SMTP_Email'
            )
            return {
                'success': False,
                'status': 'FAILED',
                'error': str(exc),
                'logId': fail_log.id,
                'log': fail_log
            }
        except Exception:
            return {
                'success': False,
                'status': 'FAILED',
                'error': str(exc)
            }


def _resolve_smtp_settings(fallback_shop_name="Store"):
    """
    Resolve live SMTP connection, From email address, provider label, and enabled flag
    strictly from the central ShopSetting record.
    """
    shop_name = fallback_shop_name
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or getattr(settings, 'EMAIL_HOST_USER', '')
    provider_name = 'Django_Default_Email'
    email_enabled = True
    conn = None

    try:
        from core.models import ShopSetting
        s = ShopSetting.get_settings()
        shop_name = s.shopName or fallback_shop_name
        email_enabled = bool(s.emailEnabled)
        smtp_user = (s.emailHostUser or '').strip()
        smtp_pass = (s.emailHostPassword or '').strip()
        custom_from = (s.defaultFromEmail or '').strip()

        if custom_from and not (smtp_user and custom_from == 'AmarDokan <orders@amardokan.com>'):
            from_email = custom_from
        elif smtp_user and '@' in smtp_user:
            from_email = f"{shop_name} <{smtp_user}>"
        elif custom_from:
            from_email = custom_from

        if smtp_user and smtp_pass:
            host = (s.emailHost or 'smtp.gmail.com').strip()
            port = int(s.emailPort or 587)
            use_ssl = bool(s.emailUseSsl)
            use_tls = False if use_ssl else bool(s.emailUseTls)
            provider_name = f"SMTP ({host})"
            conn = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=host,
                port=port,
                username=smtp_user,
                password=smtp_pass,
                use_tls=use_tls,
                use_ssl=use_ssl,
                timeout=15,
            )
    except Exception as exc:
        print("[SMTP Config Lookup Notice]", exc)

    return conn, from_email, provider_name, email_enabled, shop_name


def dispatch_order_notifications(order, event: str):
    """
    Dispatches automated Email notification via store owner's SMTP configuration
    for order creation and every order status lifecycle update.
    """
    logs = []
    email_res = send_order_email(order, event)
    if email_res.get('log'):
        logs.append(email_res['log'])
    return logs


def send_manual_notification(
    channel: str,
    recipient: str,
    message_text: str,
    template_name: str = 'MANUAL_EMAIL',
    order_id: str = 'ADMIN-DISPATCH',
    subject: str = None,
) -> dict:
    """
    Sends a manual Email notification from Admin Notifications hub or Lead Follow-Up
    using live ShopSetting SMTP credentials and records the result in NotificationLog.
    """
    import html as html_lib

    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    conn, from_email, prov, email_enabled, shop_name = _resolve_smtp_settings("AmarDokan")
    email_subject = (subject or '').strip() or f"Order Notification #{order_id} - {shop_name}"
    has_env_smtp = bool(
        (getattr(settings, 'EMAIL_HOST_USER', '') or '').strip()
        and (getattr(settings, 'EMAIL_HOST_PASSWORD', '') or '').strip()
    )

    if not email_enabled:
        res = {
            'success': False,
            'status': 'FAILED',
            'error': 'Email notifications are disabled in Admin -> Notifications & Email.',
            'provider': prov,
        }
    elif conn is None and not has_env_smtp:
        res = {
            'success': False,
            'status': 'FAILED',
            'error': 'SMTP Email Gateway is not connected yet. Configure SMTP settings in Admin -> Notifications & Email first.',
            'provider': prov,
        }
    else:
        try:
            msg = EmailMultiAlternatives(
                subject=email_subject,
                body=message_text,
                from_email=from_email or 'noreply@amardokan.com',
                to=[recipient],
                connection=conn,
            )
            safe_html_msg = html_lib.escape(str(message_text))
            safe_shop_name = html_lib.escape(str(shop_name))
            safe_order_id = html_lib.escape(str(order_id))
            html_body = f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px; background: #ffffff;">
            <div style="border-bottom: 1px solid #f1f5f9; padding-bottom: 12px; margin-bottom: 16px;">
                <h2 style="color: #0f172a; margin: 0; font-size: 18px;">{safe_shop_name}</h2>
                <span style="font-size: 12px; color: #64748b;">Reference: #{safe_order_id}</span>
            </div>
            <div style="font-size: 14px; color: #334155; line-height: 1.65; white-space: pre-wrap;">{safe_html_msg}</div>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;" />
            <p style="font-size: 11px; color: #94a3b8; margin-bottom: 0;">Sent via {safe_shop_name} Notification Hub</p>
        </div>"""
            msg.attach_alternative(html_body, "text/html")
            msg.send(fail_silently=False)
            res = {'success': True, 'status': 'SENT', 'provider': prov}
        except Exception as e:
            res = {'success': False, 'status': 'FAILED', 'error': str(e), 'provider': prov}

    log_entry = NotificationLog.objects.create(
        id=f"notif-email-{int(time.time() * 1000)}",
        orderId=order_id,
        channel='EMAIL',
        recipient=recipient,
        templateName=template_name,
        messageText=f"[{email_subject}] {message_text}",
        status=res.get('status', 'FAILED'),
        timestamp=now_str,
        provider=prov,
    )

    return {
        'success': bool(res.get('success', False)),
        'status': res.get('status', 'FAILED'),
        'provider': prov,
        'error': res.get('error'),
        'logId': log_entry.id,
        'timestamp': now_str,
    }


def send_test_smtp_email(recipient_email: str):
    """
    Sends a test verification email using live ShopSetting SMTP credentials.
    """
    from core.models import ShopSetting
    s = ShopSetting.get_settings()
    conn, from_email, provider_name, _, shop_name = _resolve_smtp_settings(s.shopName or "AmarDokan")
    subject = f"{shop_name} - SMTP Email Gateway Connection Test"
    body = (
        f"Hello,\n\n"
        f"This is an automated test email from {shop_name}.\n"
        f"Your SMTP email server configuration has been connected successfully!\n\n"
        f"Order confirmations and every order status update will now be dispatched automatically to your customers."
    )
    html_content = f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 24px; max-width: 520px; margin: 0 auto; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; color: #1e293b;">
        <div style="background: #10b981; color: white; padding: 12px 20px; border-radius: 8px; font-weight: bold; font-size: 16px; margin-bottom: 16px;">
            SMTP Email Gateway Connected Successfully
        </div>
        <h3 style="margin: 0 0 8px 0; color: #0f172a;">{shop_name}</h3>
        <p style="font-size: 14px; color: #475569; line-height: 1.6;">
            Congratulations! Your SMTP email server is connected. Order confirmations and all order status updates will be sent automatically to your customers.
        </p>
        <div style="background: #f8fafc; border: 1px solid #f1f5f9; padding: 12px; border-radius: 8px; font-size: 12px; color: #64748b; margin-top: 16px;">
            Host: {s.emailHost or 'smtp.gmail.com'} | Port: {s.emailPort} | TLS: {str(s.emailUseTls)} | SSL: {str(s.emailUseSsl)} | User: {s.emailHostUser or 'Not specified'}
        </div>
    </div>"""

    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    msg = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=from_email,
        to=[recipient_email],
        connection=conn,
    )
    msg.attach_alternative(html_content, "text/html")
    msg.send(fail_silently=False)

    NotificationLog.objects.create(
        id=f"notif-email-test-{int(time.time() * 1000)}",
        orderId='SMTP-TEST',
        channel='EMAIL',
        recipient=recipient_email,
        templateName='SMTP_CONNECTION_TEST',
        messageText=f"[{subject}] Test email dispatched successfully via {provider_name}.",
        status='SENT',
        timestamp=now_str,
        provider=provider_name,
    )

    return {
        'success': True,
        'message': f'Test email sent to {recipient_email} successfully via {provider_name}!',
        'provider': provider_name,
    }

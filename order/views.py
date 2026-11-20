import random
import re
import time
from datetime import datetime, date, timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpResponse
from django.utils import timezone

from .models import Order
from .serializers import OrderSerializer
from .services import dispatch_order_notifications
from .pdf_statement import generate_order_statement_pdf
from products.models import Product, Coupon
from core.models import AuditLog, ShopSetting
from core.recycle_bin import move_to_recycle_bin, snapshot_order
from core.utils import calculate_delivery_fee, assess_order_risk, normalize_bangladesh_phone, get_id_prefixes
from users.models import CustomerProfile

def prepare_order_checkout_data(data):
    """
    Validates checkout items, verifies stock, and computes authoritative server totals.
    Returns (prepared_dict, error_message_or_None).
    """
    items = data.get('items', [])
    address = dict(data.get('address') or {})
    payment_method = (data.get('paymentMethod') or 'COD').upper()
    coupon_code = data.get('couponCode')
    utm = data.get('utm', {})

    if not items or not address or not address.get('phone') or not address.get('customerName'):
        return None, 'Missing required order details'

    subtotal = 0
    for item in items:
        prod_id = item.get('productId')
        prod = Product.objects.filter(id=prod_id).first()
        if prod and (prod.isOutOfStock or prod.stock <= 0):
            return None, f'"{prod.name}" is currently Out of Stock. Please remove it from your cart.'
        unit_price = prod.price if prod else item.get('price', 0)
        subtotal += int(unit_price) * int(item.get('quantity', 1))

    district = address.get('district', '')
    delivery_calc = calculate_delivery_fee(district, address.get('fullAddress', ''))
    delivery_fee = delivery_calc['deliveryFee']
    address['deliveryZone'] = delivery_calc['deliveryZone']

    discount = 0
    if coupon_code:
        coupon = Coupon.objects.filter(code__iexact=coupon_code.strip(), isActive=True).first()
        if coupon and subtotal >= coupon.minSpend:
            if coupon.discountType == 'percentage':
                discount = int(round((subtotal * coupon.value) / 100))
            else:
                discount = int(coupon.value)

    grand_total = max(0, subtotal + delivery_fee - discount)
    return {
        'items': items,
        'address': address,
        'payment_method': payment_method,
        'coupon_code': coupon_code,
        'utm': utm,
        'subtotal': subtotal,
        'delivery_fee': delivery_fee,
        'discount': discount,
        'grand_total': grand_total,
        'district': district,
        'payment_trx_id': data.get('paymentTrxId') or address.get('paymentTrxId'),
        'sender_wallet': data.get('senderWallet') or address.get('senderWallet'),
        'customer_email': (data.get('customerEmail') or data.get('email') or address.get('email') or '').strip() or None,
    }, None


def create_order_from_payload(
    data,
    client_ip='127.0.0.1',
    order_id_override=None,
    payment_method_override=None,
    payment_status_override=None,
    initial_status='PENDING',
    origin_note_override=None,
    extra_address=None,
    create_payment_txn=True,
):
    """
    Creates a confirmed/pending Order in the database from validated checkout data,
    deducts stock, saves customer profile, logs audit, and creates unified Live Gateway txn.
    """
    prepared, error_msg = prepare_order_checkout_data(data)
    if error_msg:
        return None, None, error_msg

    items = prepared['items']
    address = prepared['address']
    if isinstance(extra_address, dict):
        address.update(extra_address)

    payment_method = (payment_method_override or prepared['payment_method'] or 'COD').upper()
    subtotal = prepared['subtotal']
    delivery_fee = prepared['delivery_fee']
    discount = prepared['discount']
    grand_total = prepared['grand_total']
    district = prepared['district']
    utm = prepared['utm']

    id_prefixes = get_id_prefixes()
    order_prefix = id_prefixes.get('orderIdPrefix') or 'BD'
    order_id = order_id_override
    if order_id and Order.objects.filter(id=order_id).exists():
        order_id = None

    while True:
        if not order_id:
            order_id = f"{order_prefix}-{random.randint(800000, 999999)}"
        tracking_token = f"tr_{order_id.lower()}_{random.randint(1000, 9999)}"
        if not Order.objects.filter(id=order_id).exists() and not Order.objects.filter(trackingToken=tracking_token).exists():
            break
        order_id = None

    now_str = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')
    risk_assessment = assess_order_risk(
        address.get('phone', ''),
        address.get('fullAddress', ''),
        grand_total,
        payment_method
    )

    source_tag = utm.get('source') if isinstance(utm, dict) else None
    origin_note = origin_note_override or (f"Order placed via Ad ({source_tag})" if source_tag else "Direct Guest Checkout")

    payment_trx_id = prepared['payment_trx_id']
    sender_wallet = prepared['sender_wallet']
    if payment_method in ('BKASH', 'NAGAD', 'ROCKET'):
        if not payment_trx_id or len(str(payment_trx_id).strip()) < 4:
            return None, None, f'A valid {payment_method} Transaction ID (TrxID) is required.'
        try:
            from payments.models import PaymentTransaction
            dup_txn = PaymentTransaction.objects.filter(
                tran_id__iexact=str(payment_trx_id).strip(),
                status='VALIDATED',
            ).first()
            if dup_txn:
                return None, None, f'Transaction ID {payment_trx_id} has already been verified for another order.'
        except Exception:
            pass

    if payment_trx_id:
        address['paymentTrxId'] = str(payment_trx_id).strip()
    if sender_wallet:
        address['senderWallet'] = str(sender_wallet).strip()

    if 'verificationStatus' not in address:
        address['verificationStatus'] = 'PENDING_VERIFICATION'
    if 'paymentVerified' not in address:
        address['paymentVerified'] = False

    internal_notes = [f"Initial order submitted from IP {client_ip}"]
    if payment_trx_id and payment_method != 'SSLCOMMERZ':
        internal_notes.append(f"MFS Payment submitted: Method={payment_method}, TrxID={payment_trx_id}, Sender={sender_wallet or 'N/A'} (Awaiting Verification)")

    if payment_status_override:
        pay_status = payment_status_override
    elif payment_method == 'COD':
        pay_status = 'UNPAID'
    else:
        pay_status = 'PENDING'

    cust_email = prepared['customer_email']
    if cust_email:
        address['email'] = cust_email

    order = Order.objects.create(
        id=order_id,
        trackingToken=tracking_token,
        createdAt=now_str,
        customerEmail=cust_email,
        items=items,
        address=address,
        subtotal=subtotal,
        deliveryFee=delivery_fee,
        discount=discount,
        total=grand_total,
        paymentMethod=payment_method,
        paymentStatus=pay_status,
        status=initial_status,
        statusHistory=[{
            'status': initial_status,
            'timestamp': now_str,
            'note': origin_note,
        }],
        utm=utm,
        riskScore=risk_assessment['riskScore'],
        riskFactors=risk_assessment['riskFactors'],
        internalNotes=internal_notes
    )

    for item in items:
        prod_id = item.get('productId')
        if prod_id:
            prod = Product.objects.filter(id=prod_id).first()
            if prod:
                qty = int(item.get('quantity', 1))
                prod.stock = max(0, prod.stock - qty)
                if prod.stock <= 0:
                    prod.isOutOfStock = True
                    prod.save(update_fields=['stock', 'isOutOfStock'])
                else:
                    prod.save(update_fields=['stock'])

    cust_phone = normalize_bangladesh_phone(address.get('phone', ''))
    if cust_phone:
        cust_profile = CustomerProfile.objects.filter(phone=cust_phone).first()
        if not cust_profile:
            CustomerProfile.objects.create(
                id=f"usr-{cust_phone}-{int(timezone.now().timestamp())}",
                name=address.get('customerName') or 'Valued Customer',
                phone=cust_phone,
                email=cust_email or f"{cust_phone}@customer.amardokan.com",
                district=district or 'Dhaka',
                thana=address.get('thana') or 'Mirpur',
                fullAddress=address.get('fullAddress') or '',
            )
        else:
            if cust_email:
                cust_profile.email = cust_email
            if address.get('customerName') and cust_profile.name in ['Valued Customer', 'Guest Customer', '']:
                cust_profile.name = address.get('customerName')
            if address.get('district'):
                cust_profile.district = address.get('district')
            if address.get('thana'):
                cust_profile.thana = address.get('thana')
            if address.get('fullAddress'):
                cust_profile.fullAddress = address.get('fullAddress')
            cust_profile.save()

    # Record unified Live Gateway transaction for COD / bKash / Nagad / Rocket
    if create_payment_txn:
        try:
            from payments.models import PaymentTransaction
            raw_trx = str(payment_trx_id).strip() if payment_trx_id else f"{payment_method}-{order.id}"
            unique_trx = raw_trx
            if PaymentTransaction.objects.filter(tran_id=unique_trx).exists():
                unique_trx = f"{raw_trx}-{order.id}"
            PaymentTransaction.objects.create(
                order_id=order.id,
                tran_id=unique_trx[:128],
                amount=grand_total,
                currency='BDT',
                payment_method=payment_method,
                status='INITIATED',
                card_type='Cash on Delivery' if payment_method == 'COD' else f'{payment_method} Manual',
                card_issuer=str(sender_wallet or ('Cash on Delivery' if payment_method == 'COD' else 'MFS Wallet'))[:128],
                customer_name=str(address.get('customerName') or 'Customer')[:128],
                customer_phone=str(address.get('phone') or '')[:32],
                raw_payload={
                    'source': 'checkout',
                    'customer_trx_id': payment_trx_id,
                    'sender_wallet': sender_wallet,
                }
            )
        except Exception as txn_err:
            logger.warning(f"Failed creating unified PaymentTransaction for {order.id}: {txn_err}")

    dispatch_order_notifications(order, 'ORDER_CREATED')

    AuditLog.log(
        actor=address.get('customerName', 'Guest Customer'),
        role='SYSTEM',
        action='ORDER_CREATED',
        target_type='ORDER',
        target_id=order.id,
        details=f"New order created for ৳{grand_total} ({payment_method}). Phone: {address.get('phone')}, District: {district}",
        ip_address=client_ip
    )

    return order, tracking_token, None


@api_view(['POST'])
@permission_classes([AllowAny])
def checkout_view(request):
    data = request.data
    client_ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
    order, tracking_token, error_msg = create_order_from_payload(data, client_ip=client_ip)
    if error_msg or not order:
        return Response({
            'success': False,
            'message': error_msg or 'Failed to create order'
        }, status=status.HTTP_400_BAD_REQUEST)

    serializer = OrderSerializer(order)
    return Response({
        'success': True,
        'orderId': order.id,
        'trackingToken': tracking_token,
        'order': serializer.data,
        'message': 'Order created successfully!'
    }, status=status.HTTP_201_CREATED)


_bdt_migrated = False

def ensure_bdt_timestamps():
    global _bdt_migrated
    if _bdt_migrated:
        return
    _bdt_migrated = True
    try:
        from datetime import datetime, timedelta
        # Check an order to see if it's overshifted to 20:34 (should be 14:34 BDT, UTC was 08:34)
        test_order = Order.objects.filter(id='BD-895460').first()
        if test_order and test_order.createdAt.startswith('2026-09-30 20:'):
            # Shift back by 6 hours to get exactly +6h from UTC
            for o in Order.objects.all():
                try:
                    dt = datetime.strptime(o.createdAt, '%Y-%m-%d %H:%M:%S')
                    o.createdAt = (dt - timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S')
                    if isinstance(o.statusHistory, list):
                        new_hist = []
                        for h in o.statusHistory:
                            if isinstance(h, dict) and 'timestamp' in h:
                                try:
                                    h_dt = datetime.strptime(h['timestamp'], '%Y-%m-%d %H:%M:%S')
                                    h['timestamp'] = (h_dt - timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S')
                                except Exception:
                                    pass
                            new_hist.append(h)
                        o.statusHistory = new_hist
                    o.save()
                except Exception:
                    pass

        # Check audit logs
        test_log = AuditLog.objects.filter(targetId='BD-895460').first()
        if test_log and (test_log.timestamp.startswith('2026-09-30 20:') or test_log.timestamp.startswith('2026-09-30 16:')):
            # If overshifted by 6h
            if test_log.timestamp.startswith('2026-09-30 20:'):
                for l in AuditLog.objects.all():
                    try:
                        l_dt = datetime.strptime(l.timestamp, '%Y-%m-%d %H:%M:%S')
                        l.timestamp = (l_dt - timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S')
                        l.save()
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"BDT migration error: {e}")

@api_view(['GET'])
@permission_classes([AllowAny])
def orders_list(request):
    ensure_bdt_timestamps()
    status_filter = request.query_params.get('status')
    search = request.query_params.get('search')

    orders = Order.objects.all()

    if status_filter and status_filter != 'ALL':
        orders = orders.filter(status=status_filter)

    results = []
    for order in orders:
        if search:
            q = search.lower()
            order_id_match = q in order.id.lower()
            customer_match = False
            phone_match = False
            tracking_match = False

            if isinstance(order.address, dict):
                customer_match = q in order.address.get('customerName', '').lower()
                phone_match = q in order.address.get('phone', '')

            if isinstance(order.courier, dict):
                tracking_match = q in order.courier.get('trackingId', '').lower()

            if not (order_id_match or customer_match or phone_match or tracking_match):
                continue

        results.append(order)

    serializer = OrderSerializer(results, many=True)
    return Response({
        'success': True,
        'count': len(results),
        'orders': serializer.data
    })


@api_view(['GET', 'DELETE'])
@permission_classes([AllowAny])
def order_detail(request, pk):
    order = Order.objects.filter(id=pk).first()
    if not order:
        return Response({'success': False, 'message': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        oid = order.id
        snap = snapshot_order(order)
        order.delete()
        rb = move_to_recycle_bin(**snap, deleted_by='Admin')
        try:
            from core.models import MarketingEvent
            MarketingEvent.objects.filter(orderId=oid).delete()
        except Exception:
            pass
        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='ORDER_MOVED_TO_TRASH',
            target_type='ORDER',
            target_id=oid,
            details=f"Order {oid} moved to Recycle Bin",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )
        return Response({
            'success': True,
            'id': oid,
            'recycleBinId': rb.id,
            'message': f'Order {oid} moved to Recycle Bin.'
        })

    serializer = OrderSerializer(order)
    return Response({'success': True, 'order': serializer.data})


@api_view(['POST'])
@permission_classes([AllowAny])
def bulk_delete_orders(request):
    data = request.data or {}
    order_ids = data.get('order_ids', [])
    if not isinstance(order_ids, list) or not order_ids:
        return Response({'success': False, 'message': 'No order IDs provided.'}, status=status.HTTP_400_BAD_REQUEST)

    targets = list(Order.objects.filter(id__in=order_ids))
    deleted_count = 0
    deleted_ids = []
    for order in targets:
        deleted_ids.append(order.id)
        snap = snapshot_order(order)
        order.delete()
        move_to_recycle_bin(**snap, deleted_by='Admin')
        deleted_count += 1

    if deleted_ids:
        try:
            from core.models import MarketingEvent
            MarketingEvent.objects.filter(orderId__in=deleted_ids).delete()
        except Exception:
            pass

    AuditLog.log(
        actor='Admin',
        role='ADMIN',
        action='BULK_ORDER_MOVED_TO_TRASH',
        target_type='ORDER',
        target_id='MULTIPLE',
        details=f'Bulk moved {deleted_count} orders to Recycle Bin: {", ".join(deleted_ids[:5])}{"..." if len(deleted_ids) > 5 else ""}',
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )
    return Response({
        'success': True,
        'deletedCount': deleted_count,
        'message': f'Moved {deleted_count} orders to Recycle Bin.',
    })


@api_view(['PATCH'])
@permission_classes([AllowAny])
def update_order_status(request, pk):
    order = Order.objects.filter(id=pk).first()
    if not order:
        return Response({'success': False, 'message': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)

    new_status = request.data.get('status')
    note = request.data.get('note', '')
    actor_name = request.data.get('actorName', 'Operations Staff')
    actor_role = request.data.get('actorRole', 'OPERATIONS')

    if not new_status:
        return Response({'success': False, 'message': 'Missing status'}, status=status.HTTP_400_BAD_REQUEST)

    prev_status = order.status
    if new_status == 'CONFIRMED' and prev_status == 'PENDING':
        addr = order.address if isinstance(order.address, dict) else {}
        ver_status = addr.get('verificationStatus')
        is_cod = str(order.paymentMethod or '').upper() == 'COD'
        if is_cod:
            if ver_status == 'NOT_VERIFIED' or order.paymentStatus == 'FAILED':
                return Response({
                    'success': False,
                    'message': 'This COD order is marked Not Verified and cannot be confirmed.'
                }, status=status.HTTP_400_BAD_REQUEST)
        elif ver_status != 'VERIFIED' or order.paymentStatus == 'FAILED':
            return Response({
                'success': False,
                'message': 'Payment/order verification is required before confirming this order.'
            }, status=status.HTTP_400_BAD_REQUEST)

    order.status = new_status
    now_str = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')

    default_notes = {
        'CONFIRMED': 'Order details and address confirmed via phone call',
        'COURIER_CREATED': 'Courier booking and consignment creation completed',
        'PICKED_UP': 'Courier rider picked up parcel from merchant hub',
        'IN_TRANSIT': 'Parcel in transit to destination hub',
        'OUT_FOR_DELIVERY': 'Rider is out for delivery to your address',
        'DELIVERED': 'Parcel delivered and payment completed',
        'RETURNED': 'Parcel returned to merchant',
        'CANCELLED': 'Order cancelled by customer or admin',
    }

    effective_note = note or default_notes.get(new_status, f"Status updated from {prev_status} to {new_status}")

    history = list(order.statusHistory) if isinstance(order.statusHistory, list) else []
    history.append({
        'status': new_status,
        'timestamp': now_str,
        'note': effective_note,
        'updatedBy': actor_name,
    })
    order.statusHistory = history

    if new_status == 'DELIVERED':
        order.paymentStatus = 'PAID'

    order.save()

    # Dispatch email notification on every order status update via configured SMTP
    status_event_map = {
        'CONFIRMED': 'ORDER_CONFIRMED',
        'COURIER_CREATED': 'SHIPMENT_CREATED',
    }
    dispatch_order_notifications(order, status_event_map.get(new_status, new_status))

    AuditLog.log(
        actor=actor_name,
        role=actor_role if actor_role in ['ADMIN', 'OPERATIONS', 'MARKETING', 'SYSTEM'] else 'OPERATIONS',
        action='ORDER_STATUS_CHANGED',
        target_type='ORDER',
        target_id=order.id,
        details=f"Status transitioned from {prev_status} -> {new_status}. Note: {note or 'None'}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    serializer = OrderSerializer(order)
    return Response({'success': True, 'order': serializer.data})


@api_view(['POST'])
@permission_classes([AllowAny])
def tracking_lookup(request):
    order_id = request.data.get('orderId', '').strip().lstrip('#')
    phone = request.data.get('phone', '').strip()

    if not order_id:
        return Response({
            'success': False,
            'message': 'Please provide Order ID'
        }, status=status.HTTP_400_BAD_REQUEST)

    clean_input_phone = re.sub(r'\D', '', phone) if phone else ''

    matched_order = None
    for order in Order.objects.all():
        clean_db_id = order.id.strip().lstrip('#')
        if clean_db_id.lower() == order_id.lower():
            if clean_input_phone:
                order_phone = ''
                if isinstance(order.address, dict):
                    order_phone = re.sub(r'\D', '', str(order.address.get('phone', '')))
                
                clean_in = clean_input_phone[-10:] if len(clean_input_phone) >= 10 else clean_input_phone
                clean_ord = order_phone[-10:] if len(order_phone) >= 10 else order_phone
                
                if clean_in and clean_ord and (clean_in == clean_ord or clean_input_phone.endswith(order_phone) or order_phone.endswith(clean_input_phone)):
                    matched_order = order
                    break
            else:
                matched_order = order
                break

    if not matched_order:
        return Response({
            'success': False,
            'message': 'No matching order found for this Order ID. Please verify your details.'
        }, status=status.HTTP_404_NOT_FOUND)

    raw_phone = matched_order.address.get('phone', '') if isinstance(matched_order.address, dict) else ''
    masked_phone = (raw_phone[:3] + '*****' + raw_phone[-3:]) if len(raw_phone) >= 6 else (raw_phone or '017*****000')

    courier_data = matched_order.courier if isinstance(matched_order.courier, dict) else {}
    has_valid_courier = bool(
        courier_data.get('trackingId') or 
        courier_data.get('consignmentId') or 
        courier_data.get('provider')
    )

    return Response({
        'success': True,
        'order': {
            'id': matched_order.id,
            'createdAt': matched_order.createdAt,
            'status': matched_order.status,
            'statusHistory': matched_order.statusHistory if isinstance(matched_order.statusHistory, list) else [],
            'subtotal': matched_order.subtotal,
            'deliveryFee': matched_order.deliveryFee,
            'discount': matched_order.discount,
            'total': matched_order.total,
            'paymentMethod': matched_order.paymentMethod,
            'paymentStatus': matched_order.paymentStatus,
            'courier': courier_data if has_valid_courier else None,
            'items': matched_order.items if isinstance(matched_order.items, list) else [],
            'customerName': matched_order.address.get('customerName', '') if isinstance(matched_order.address, dict) else '',
            'phone': raw_phone,
            'address': matched_order.address if isinstance(matched_order.address, dict) else {},
            'addressSummary': {
                'customerName': matched_order.address.get('customerName', '') if isinstance(matched_order.address, dict) else '',
                'maskedPhone': masked_phone,
                'district': matched_order.address.get('district', '') if isinstance(matched_order.address, dict) else '',
                'thana': matched_order.address.get('thana', '') if isinstance(matched_order.address, dict) else '',
            }
        }
    })


def _parse_order_bdt_date(order):
    """Extract BDT calendar date from order.createdAt (YYYY-MM-DD ...) or order.created_at."""
    raw = getattr(order, 'createdAt', '') or ''
    if isinstance(raw, str) and len(raw) >= 10:
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(raw[:len(fmt.replace('%Y', '2026').replace('%m', '01').replace('%d', '01').replace('%H', '00').replace('%M', '00').replace('%S', '00'))], fmt).date()
            except Exception:
                pass
        try:
            return date.fromisoformat(raw[:10])
        except Exception:
            pass
    dt_val = getattr(order, 'created_at', None)
    if dt_val is not None:
        try:
            return timezone.localtime(dt_val).date()
        except Exception:
            return dt_val.date()
    return None


@api_view(['GET'])
@permission_classes([AllowAny])
def order_statement_pdf(request):
    """
    Generate and download a live database-backed PDF Order Statement.
    Query params:
      - period: 'weekly' | '15days' | 'monthly' | 'custom' (default: 'weekly')
      - start_date: YYYY-MM-DD (required/used for custom, optional override)
      - end_date: YYYY-MM-DD (required/used for custom, optional override)
      - status: 'ALL' or specific Order.ORDER_STATUS (default: 'ALL')
    """
    ensure_bdt_timestamps()
    period = (request.query_params.get('period') or 'weekly').strip().lower()
    status_filter = (request.query_params.get('status') or 'ALL').strip().upper()
    start_str = (request.query_params.get('start_date') or '').strip()
    end_str = (request.query_params.get('end_date') or '').strip()

    today_bdt = timezone.localtime(timezone.now()).date()

    if period == 'weekly':
        start_d = today_bdt - timedelta(days=6)
        end_d = today_bdt
        period_label = 'Weekly Statement (Last 7 Days)'
    elif period in ('15days', '15_days', 'biweekly', 'fifteen_days'):
        start_d = today_bdt - timedelta(days=14)
        end_d = today_bdt
        period_label = '15 Days Statement (Last 15 Days)'
    elif period == 'monthly':
        start_d = today_bdt - timedelta(days=29)
        end_d = today_bdt
        period_label = 'Monthly Statement (Last 30 Days)'
    elif period == 'custom':
        try:
            start_d = date.fromisoformat(start_str) if start_str else (today_bdt - timedelta(days=6))
        except ValueError:
            return Response({'success': False, 'message': 'Invalid start_date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            end_d = date.fromisoformat(end_str) if end_str else today_bdt
        except ValueError:
            return Response({'success': False, 'message': 'Invalid end_date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        if end_d < start_d:
            start_d, end_d = end_d, start_d
        period_label = 'Custom Date Statement'
    else:
        return Response({'success': False, 'message': f"Unsupported statement period '{period}'."}, status=status.HTTP_400_BAD_REQUEST)

    qs = Order.objects.all()
    if status_filter and status_filter != 'ALL':
        qs = qs.filter(status=status_filter)

    filtered_orders = []
    for order in qs:
        ord_date = _parse_order_bdt_date(order)
        if ord_date is None:
            continue
        if start_d <= ord_date <= end_d:
            filtered_orders.append(order)

    # Sort descending by createdAt string so newest appear first
    filtered_orders.sort(key=lambda o: str(getattr(o, 'createdAt', '') or ''), reverse=True)

    shop = ShopSetting.objects.first()
    shop_name = (getattr(shop, 'shopName', None) or 'AmarDokan')
    shop_hotline = (getattr(shop, 'hotline', None) or '')
    shop_email = (getattr(shop, 'supportEmail', None) or '')
    generated_at_str = timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S')

    pdf_bytes = generate_order_statement_pdf(
        orders=filtered_orders,
        period_label=period_label,
        start_date_str=start_d.isoformat(),
        end_date_str=end_d.isoformat(),
        status_filter=status_filter,
        shop_name=shop_name,
        shop_hotline=shop_hotline,
        shop_email=shop_email,
        generated_at_str=generated_at_str,
    )

    safe_period_slug = re.sub(r'[^a-z0-9_-]', '', period) or 'statement'
    filename = f"order-statement-{safe_period_slug}-{start_d.isoformat()}-to-{end_d.isoformat()}.pdf"

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Content-Length'] = str(len(pdf_bytes))
    response['Cache-Control'] = 'no-store'
    return response

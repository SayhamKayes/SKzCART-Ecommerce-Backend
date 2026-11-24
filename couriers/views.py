from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from .models import CourierConfig
from .serializers import CourierConfigSerializer
from .adapters import courier_adapters
from order.models import Order
from order.serializers import OrderSerializer
from order.services import dispatch_order_notifications
from core.models import AuditLog

@api_view(['GET'])
@permission_classes([AllowAny])
def couriers_list(request):
    configs = CourierConfig.objects.all()
    serializer = CourierConfigSerializer(configs, many=True)
    webhook_sim_enabled = False
    try:
        from core.models import ShopSetting
        s = ShopSetting.get_settings()
        extra = s.extraConfig if isinstance(s.extraConfig, dict) else {}
        controls = extra.get('courierControls') if isinstance(extra.get('courierControls'), dict) else {}
        webhook_sim_enabled = bool(controls.get('webhookSimulatorEnabled', False))
    except Exception:
        webhook_sim_enabled = False
    return Response({
        'success': True,
        'couriers': serializer.data,
        'controls': {
            'webhookSimulatorEnabled': webhook_sim_enabled,
        },
    })


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def courier_controls_view(request):
    """
    GET/POST /api/v1/couriers/controls — Manage Admin-level courier simulator toggle in central DB.
    """
    from core.models import ShopSetting
    s = ShopSetting.get_settings()
    extra = dict(s.extraConfig) if isinstance(s.extraConfig, dict) else {}
    controls = dict(extra.get('courierControls')) if isinstance(extra.get('courierControls'), dict) else {}

    if request.method == 'POST':
        data = request.data or {}
        if 'webhookSimulatorEnabled' in data:
            controls['webhookSimulatorEnabled'] = bool(data['webhookSimulatorEnabled'])
        extra['courierControls'] = controls
        s.extraConfig = extra
        s.save(update_fields=['extraConfig', 'updated_at'])
        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='COURIER_CONTROLS_UPDATED',
            target_type='SETTINGS',
            target_id='default_shop_setting',
            details=f"Courier Webhook Simulator {'ENABLED' if controls.get('webhookSimulatorEnabled') else 'DISABLED'}",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
        )

    return Response({
        'success': True,
        'controls': {
            'webhookSimulatorEnabled': bool(controls.get('webhookSimulatorEnabled', False)),
        },
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def courier_mode_preset_view(request):
    """
    POST /api/v1/couriers/mode — One-click toggle between Testing/Sandbox Mode
    (enables tracing test mode & Webhook Simulator) and Live Production Mode.
    Supports { "mode": "sandbox" | "live", "provider": "all" | "<id>" }.
    """
    from core.models import ShopSetting

    data = request.data if isinstance(request.data, dict) else {}
    mode = str(data.get('mode') or '').strip().lower()
    provider = str(data.get('provider') or 'all').strip().lower()

    if mode not in ('sandbox', 'live'):
        return Response({'success': False, 'message': "Mode must be 'sandbox' or 'live'."}, status=status.HTTP_400_BAD_REQUEST)

    qs = CourierConfig.objects.all() if provider in ('', 'all') else CourierConfig.objects.filter(id=provider)
    if not qs.exists():
        return Response({'success': False, 'message': f"Courier provider '{provider}' not found."}, status=status.HTTP_404_NOT_FOUND)

    is_sandbox = mode == 'sandbox'
    for cfg in qs:
        extra = dict(cfg.extraCredentials) if isinstance(cfg.extraCredentials, dict) else {}
        extra['sandboxMode'] = is_sandbox
        extra['sandboxTracing'] = is_sandbox
        if is_sandbox:
            extra['tracingLabel'] = f"SANDBOX-{cfg.id.upper()}"
            cfg.isActive = True
        else:
            extra.pop('tracingLabel', None)
        cfg.extraCredentials = extra
        cfg.save(update_fields=['isActive', 'extraCredentials'])

    s = ShopSetting.get_settings()
    extra_shop = dict(s.extraConfig) if isinstance(s.extraConfig, dict) else {}
    controls = dict(extra_shop.get('courierControls')) if isinstance(extra_shop.get('courierControls'), dict) else {}
    if is_sandbox:
        controls['webhookSimulatorEnabled'] = True
    elif provider in ('', 'all'):
        controls['webhookSimulatorEnabled'] = False
    extra_shop['courierControls'] = controls
    s.extraConfig = extra_shop
    s.save(update_fields=['extraConfig', 'updated_at'])

    AuditLog.log(
        actor='Admin',
        role='ADMIN',
        action='COURIER_MODE_SWITCHED',
        target_type='SETTINGS',
        target_id=provider or 'all',
        details=f"Switched courier environment ({provider}) to {'Sandbox Testing Mode' if is_sandbox else 'Live Production Mode'}",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
    )

    all_couriers = CourierConfigSerializer(CourierConfig.objects.all(), many=True).data
    return Response({
        'success': True,
        'mode': mode,
        'couriers': all_couriers,
        'controls': {
            'webhookSimulatorEnabled': bool(controls.get('webhookSimulatorEnabled', False)),
        },
        'message': (
            "Sandbox Testing Mode & Webhook Simulator enabled!"
            if is_sandbox
            else "Switched to Live Production Mode. Real merchant API credentials will be used."
        ),
    })


@api_view(['PUT'])
@permission_classes([AllowAny])
def courier_update(request, pk):
    config = CourierConfig.objects.filter(id=pk).first()
    if not config:
        return Response({'success': False, 'message': 'Courier not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = CourierConfigSerializer(config, data=request.data, partial=True)
    if serializer.is_valid():
        config = serializer.save()
        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='COURIER_CONFIG_UPDATED',
            target_type='SETTINGS',
            target_id=config.id,
            details=f"Updated courier settings for {config.id}",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )
        return Response({'success': True, 'courier': serializer.data})
    return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def assign_courier(request):
    data = request.data
    order_id = data.get('orderId')
    provider = data.get('provider', '').lower()
    weight_kg = float(data.get('weightKg') or 0.5)
    special_instruction = data.get('specialInstruction') or data.get('instructions') or ''
    actor_name = data.get('actorName', 'Operations Staff')
    requested_status = str(data.get('targetStatus') or 'COURIER_CREATED').strip().upper()
    if requested_status not in ('COURIER_CREATED', 'PICKED_UP'):
        requested_status = 'COURIER_CREATED'

    order = Order.objects.filter(id=order_id).first()
    if not order:
        return Response({'success': False, 'message': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)

    adapter = courier_adapters.get(provider)
    if not adapter:
        return Response({'success': False, 'message': f"Unsupported courier provider: {provider}"}, status=status.HTTP_400_BAD_REQUEST)

    # Verify courier is active in CourierConfig
    courier_config = CourierConfig.objects.filter(id=provider).first()
    if courier_config and not courier_config.isActive:
        return Response({
            'success': False,
            'message': f"Courier provider '{courier_config.name}' is currently disabled in Courier Hub."
        }, status=status.HTTP_400_BAD_REQUEST)

    try:
        shipment = adapter.create_shipment(order, item_weight_kg=weight_kg, special_instruction=special_instruction)
        cod_amt = int(shipment.get('codAmount') or 0)
        if cod_amt > 0:
            shipment['codAccounted'] = True

        order.courier = shipment
        order.status = requested_status
        now_str = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')

        history = list(order.statusHistory) if isinstance(order.statusHistory, list) else []
        history.append({
            'status': requested_status,
            'timestamp': now_str,
            'note': f"Dispatched directly to {adapter.name}.",
            'updatedBy': actor_name,
        })
        order.statusHistory = history
        order.save()

        if courier_config:
            if cod_amt > 0:
                courier_config.pendingCodAmount = max(0, int(courier_config.pendingCodAmount or 0) + cod_amt)
            courier_config.lastSyncTime = timezone.localtime().strftime('%I:%M %p')
            courier_config.save(update_fields=['pendingCodAmount', 'lastSyncTime'])

        # Notify customer
        dispatch_order_notifications(order, 'SHIPMENT_CREATED')

        # Audit log
        AuditLog.log(
            actor=actor_name,
            role='OPERATIONS',
            action='COURIER_ASSIGNED',
            target_type='ORDER',
            target_id=order.id,
            details=f"Booked shipment via {adapter.name}. Consignment: {shipment['consignmentId']}",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )

        order_serializer = OrderSerializer(order)
        return Response({'success': True, 'shipment': shipment, 'order': order_serializer.data})

    except ValueError as e:
        return Response({'success': False, 'message': str(e) or 'Courier credentials missing or invalid'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({'success': False, 'message': str(e) or 'Courier booking failed'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([AllowAny])
def courier_balance(request, provider):
    prov = str(provider).strip().lower()
    adapter = courier_adapters.get(prov)
    if not adapter:
        return Response({'success': False, 'message': f'Courier provider {provider} not found'}, status=status.HTTP_404_NOT_FOUND)
    result = adapter.get_balance()
    return Response(result)


@api_view(['GET'])
@permission_classes([AllowAny])
def courier_check_status(request, provider, tracking_code):
    prov = str(provider).strip().lower()
    adapter = courier_adapters.get(prov)
    if not adapter:
        return Response({'success': False, 'message': f'Courier provider {provider} not found'}, status=status.HTTP_404_NOT_FOUND)
    result = adapter.check_status(tracking_code)
    return Response(result)


@api_view(['GET'])
@permission_classes([AllowAny])
def steadfast_balance(request):
    return courier_balance(request, 'steadfast')


@api_view(['GET'])
@permission_classes([AllowAny])
def steadfast_check_status(request, tracking_code):
    return courier_check_status(request, 'steadfast', tracking_code)


def process_courier_webhook_data(data, ip_address='127.0.0.1'):
    if data.get('isSimulated'):
        try:
            from core.models import ShopSetting
            s = ShopSetting.get_settings()
            extra = s.extraConfig if isinstance(s.extraConfig, dict) else {}
            controls = extra.get('courierControls') if isinstance(extra.get('courierControls'), dict) else {}
            if not controls.get('webhookSimulatorEnabled', False):
                return {
                    'success': False,
                    'status': 403,
                    'message': 'Courier Webhook Simulator is disabled by administrator. Enable it in Admin → Courier Hub to run test webhooks.',
                    'status_code': status.HTTP_403_FORBIDDEN,
                }
        except Exception:
            pass

    provider = str(data.get('provider') or '').strip().lower()

    invoice_val = str(
        data.get('merchant_invoice_id') or
        data.get('merchant_order_id') or
        data.get('invoice') or
        data.get('order_id') or
        data.get('orderId') or ''
    ).strip()
    tracking_val = str(data.get('tracking_code') or data.get('tracking_id') or data.get('trackingId') or '').strip()
    consignment_val = str(data.get('consignment_id') or data.get('consignmentId') or '').strip()

    if not (invoice_val or tracking_val or consignment_val):
        return {'success': False, 'message': 'Missing tracking_code, consignment_id, invoice or order_id', 'status_code': status.HTTP_400_BAD_REQUEST}

    order = None
    if invoice_val:
        order = Order.objects.filter(id__iexact=invoice_val).first()

    if not order and (tracking_val or consignment_val):
        for o in Order.objects.all():
            c = o.courier if isinstance(o.courier, dict) else {}
            c_track = str(c.get('trackingId') or '').strip().lower()
            c_cons = str(c.get('consignmentId') or '').strip().lower()
            if (tracking_val and c_track == tracking_val.lower()) or (consignment_val and c_cons == consignment_val.lower()):
                order = o
                break

    if not order:
        lookup_ref = invoice_val or tracking_val or consignment_val
        return {'success': True, 'acknowledged': True, 'message': f"Webhook acknowledged. Order not found for reference: {lookup_ref}", 'status_code': status.HTTP_200_OK}

    # Resolve courier provider from URL/payload, matched order.courier, or provider payload signature
    if not provider and isinstance(order.courier, dict) and order.courier.get('provider'):
        provider = str(order.courier.get('provider')).strip().lower()
    if not provider:
        ev_str = str(data.get('event') or '').lower()
        if ev_str.startswith('order.'):
            provider = 'pathao'
        elif 'parcel' in data or 'parcel_status' in data:
            provider = 'redx'
        elif 'query_data' in data:
            provider = 'ecourier'
        elif 'tracking_code' in data or 'consignment_id' in data or 'invoice' in data:
            provider = 'steadfast'
        else:
            provider = 'pathao'

    adapter = courier_adapters.get(provider) or courier_adapters.get('steadfast')
    if not adapter:
        adapter = courier_adapters['steadfast']

    # If order does not have courier assigned yet, generate shipment details on the fly
    if not isinstance(order.courier, dict) or not order.courier.get('trackingId'):
        order.courier = adapter.create_shipment(order)

    parsed = adapter.parse_webhook(data)

    prev_status = order.status
    order.status = parsed['orderStatus']

    courier_dict = dict(order.courier) if isinstance(order.courier, dict) else {}
    courier_dict['courierStatus'] = parsed['notes']
    rider_info = adapter.extract_rider_info(data)
    if rider_info.get('assignedRiderName'):
        courier_dict['assignedRiderName'] = rider_info['assignedRiderName']
    if rider_info.get('assignedRiderPhone'):
        courier_dict['assignedRiderPhone'] = rider_info['assignedRiderPhone']
    if rider_info.get('hubLocation'):
        courier_dict['hubLocation'] = rider_info['hubLocation']
    if tracking_val:
        courier_dict['trackingId'] = tracking_val
    if consignment_val:
        courier_dict['consignmentId'] = consignment_val

    courier_cfg = CourierConfig.objects.filter(id=adapter.provider_id).first()
    if parsed['orderStatus'] == 'DELIVERED':
        order.paymentStatus = 'PAID'
        if not courier_dict.get('codSettled'):
            cod_val = int(parsed.get('collectedAmount') or courier_dict.get('codAmount') or 0)
            if cod_val > 0 and courier_cfg:
                if courier_dict.get('codAccounted'):
                    courier_cfg.pendingCodAmount = max(0, int(courier_cfg.pendingCodAmount or 0) - cod_val)
                courier_cfg.settledCodAmount = int(courier_cfg.settledCodAmount or 0) + cod_val
            courier_dict['codSettled'] = True
            courier_dict['collectedAmount'] = cod_val
        if prev_status != 'DELIVERED':
            dispatch_order_notifications(order, 'DELIVERED')
    elif parsed['orderStatus'] == 'OUT_FOR_DELIVERY' and prev_status != 'OUT_FOR_DELIVERY':
        dispatch_order_notifications(order, 'OUT_FOR_DELIVERY')

    order.courier = courier_dict

    now_str = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')
    history = list(order.statusHistory) if isinstance(order.statusHistory, list) else []
    last_entry = history[-1] if history and isinstance(history[-1], dict) else None
    if not (last_entry and last_entry.get('status') == parsed['orderStatus'] and last_entry.get('note') == parsed['notes']):
        history.append({
            'status': parsed['orderStatus'],
            'timestamp': now_str,
            'note': parsed['notes'],
            'updatedBy': f"Courier Webhook ({adapter.name})",
        })
    order.statusHistory = history
    order.save()

    if courier_cfg:
        courier_cfg.lastSyncTime = timezone.localtime().strftime('%I:%M %p')
        courier_cfg.save(update_fields=['pendingCodAmount', 'settledCodAmount', 'lastSyncTime'])

    AuditLog.log(
        actor=f"{adapter.name} Webhook",
        role='SYSTEM',
        action='COURIER_WEBHOOK_PROCESSED',
        target_type='ORDER',
        target_id=order.id,
        details=f"Received event: {data.get('status') or data.get('event')}. Status transitioned from {prev_status} to {parsed['orderStatus']}. Collected: ৳{parsed.get('collectedAmount', 0)}",
        ip_address=ip_address
    )

    order_serializer = OrderSerializer(order)
    return {
        'success': True,
        'status': 200,
        'message': 'Webhook processed successfully',
        'order': order_serializer.data,
        'status_code': status.HTTP_200_OK
    }


@api_view(['POST'])
@permission_classes([AllowAny])
def steadfast_webhook(request):
    """
    Dedicated webhook endpoint for Steadfast Courier.
    Configured in Steadfast merchant panel as: https://yourdomain.com/api/v1/webhooks/couriers/steadfast
    """
    data = dict(request.data)
    data['provider'] = 'steadfast'
    result = process_courier_webhook_data(data, ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'))
    status_code = result.pop('status_code', status.HTTP_200_OK)
    return Response(result, status=status_code)


@api_view(['POST'])
@permission_classes([AllowAny])
def provider_webhook(request, provider):
    """
    Dedicated webhook endpoint for any provider (/api/v1/webhooks/couriers/<provider>).
    Supports steadfast, pathao, redx, ecourier.
    """
    data = dict(request.data)
    data['provider'] = str(provider).strip().lower()
    result = process_courier_webhook_data(data, ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'))
    status_code = result.pop('status_code', status.HTTP_200_OK)
    return Response(result, status=status_code)


@api_view(['POST'])
@permission_classes([AllowAny])
def courier_webhook(request):
    data = dict(request.data)
    result = process_courier_webhook_data(data, ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'))
    status_code = result.pop('status_code', status.HTTP_200_OK)
    return Response(result, status=status_code)


@api_view(['POST'])
@permission_classes([AllowAny])
def sync_order_courier_status(request, order_id):
    """Actively poll the assigned live courier API and sync Order + Rider + COD settlement status."""
    order = Order.objects.filter(id=order_id).first()
    if not order or not isinstance(order.courier, dict) or not order.courier.get('trackingId'):
        return Response({
            'success': False,
            'message': 'Order or assigned courier tracking ID not found.'
        }, status=status.HTTP_404_NOT_FOUND)

    provider = str(order.courier.get('provider') or '').strip().lower()
    adapter = courier_adapters.get(provider)
    if not adapter:
        return Response({
            'success': False,
            'message': f"Unsupported courier provider: {provider}"
        }, status=status.HTTP_400_BAD_REQUEST)

    tracking_code = order.courier.get('consignmentId') if provider == 'pathao' else order.courier.get('trackingId')
    live_res = adapter.check_status(tracking_code or order.courier.get('trackingId'))
    if not isinstance(live_res, dict) or live_res.get('status') == 500 or live_res.get('status') == 401:
        return Response({
            'success': False,
            'message': live_res.get('message') if isinstance(live_res, dict) else 'Courier status fetch failed',
            'raw': live_res,
        }, status=status.HTTP_400_BAD_REQUEST)

    if live_res.get('isDemo'):
        return Response({
            'success': True,
            'isDemo': True,
            'message': 'Sandbox/Demo courier active. Connect live API credentials or use Webhook Simulator to advance status.',
            'order': OrderSerializer(order).data,
        })

    raw_payload = dict(live_res.get('raw') or live_res)
    raw_payload['provider'] = provider
    raw_payload['order_id'] = order.id
    raw_payload['tracking_id'] = order.courier.get('trackingId')

    q_data = raw_payload.get('query_data') if isinstance(raw_payload.get('query_data'), list) else []
    latest_q = q_data[0] if (q_data and isinstance(q_data[0], dict)) else {}
    raw_status_text = str(
        raw_payload.get('delivery_status')
        or raw_payload.get('order_status')
        or raw_payload.get('parcel_status')
        or raw_payload.get('status')
        or (raw_payload.get('data', {}).get('order_status') if isinstance(raw_payload.get('data'), dict) else '')
        or (raw_payload.get('parcel', {}).get('status') if isinstance(raw_payload.get('parcel'), dict) else '')
        or latest_q.get('status')
        or ''
    ).strip()

    if raw_status_text and raw_status_text not in ('200', '201'):
        raw_payload['status'] = raw_status_text
        res = process_courier_webhook_data(raw_payload, ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'))
        res.pop('status_code', None)
        return Response(res)

    return Response({
        'success': True,
        'message': 'Checked live courier API; parcel status unchanged.',
        'order': OrderSerializer(order).data,
        'raw': live_res,
    })

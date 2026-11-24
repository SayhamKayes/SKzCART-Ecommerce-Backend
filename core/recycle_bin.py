import secrets
import uuid
from django.utils import timezone
from django.utils.text import slugify
from .models import AuditLog, RecycleBinItem, ShopSetting, StaffMember


DEFAULT_PRODUCT_IMAGE = '/assets/default-product-placeholder.png'


def _gen_rb_id():
    return f"rb-{int(timezone.now().timestamp() * 1000)}-{uuid.uuid4().hex[:6]}"


def move_to_recycle_bin(
    item_type,
    original_id,
    title,
    subtitle='',
    thumbnail_url='',
    payload=None,
    deleted_by='Admin',
):
    return RecycleBinItem.objects.create(
        id=_gen_rb_id(),
        item_type=item_type,
        original_id=str(original_id),
        title=str(title or original_id)[:255],
        subtitle=str(subtitle or '')[:255],
        thumbnail_url=str(thumbnail_url or ''),
        payload=payload if isinstance(payload, dict) else {},
        deleted_by=str(deleted_by or 'Admin')[:150],
    )


def snapshot_order(order):
    addr = order.address if isinstance(order.address, dict) else {}
    cust_name = addr.get('customerName') or 'Guest Customer'
    cust_phone = addr.get('phone') or ''
    items = order.items if isinstance(order.items, list) else []
    first_img = ''
    for it in items:
        if isinstance(it, dict) and it.get('image'):
            first_img = str(it.get('image'))
            break

    payload = {
        'id': order.id,
        'trackingToken': order.trackingToken,
        'createdAt': order.createdAt,
        'items': items,
        'address': addr,
        'customerEmail': order.customerEmail,
        'subtotal': order.subtotal,
        'deliveryFee': order.deliveryFee,
        'discount': order.discount,
        'total': order.total,
        'paymentMethod': order.paymentMethod,
        'paymentStatus': order.paymentStatus,
        'status': order.status,
        'statusHistory': order.statusHistory if isinstance(order.statusHistory, list) else [],
        'courier': order.courier if isinstance(order.courier, dict) else None,
        'utm': order.utm if isinstance(order.utm, dict) else None,
        'riskScore': order.riskScore,
        'riskFactors': order.riskFactors if isinstance(order.riskFactors, list) else [],
        'internalNotes': order.internalNotes if isinstance(order.internalNotes, list) else [],
    }
    subtitle = f"{cust_phone} • BDT {order.total:,} • {order.status}".strip(' •')
    return {
        'item_type': 'ORDER',
        'original_id': order.id,
        'title': f"Order #{order.id} — {cust_name}",
        'subtitle': subtitle,
        'thumbnail_url': first_img,
        'payload': payload,
    }


def snapshot_product(product):
    images = product.images if isinstance(product.images, list) else []
    thumb = images[0] if images else DEFAULT_PRODUCT_IMAGE
    linked_review_ids = list(product.reviews.values_list('id', flat=True))
    payload = {
        'id': product.id,
        'name': product.name,
        'nameBn': product.nameBn,
        'slug': product.slug,
        'category': product.category,
        'price': product.price,
        'regularPrice': product.regularPrice,
        'sku': product.sku,
        'stock': product.stock,
        'isOutOfStock': product.isOutOfStock,
        'images': images,
        'shortDescription': product.shortDescription,
        'shortDescriptionBn': product.shortDescriptionBn,
        'description': product.description,
        'descriptionBn': product.descriptionBn,
        'features': product.features if isinstance(product.features, list) else [],
        'featuresBn': product.featuresBn if isinstance(product.featuresBn, list) else [],
        'variants': product.variants if isinstance(product.variants, list) else [],
        'productType': product.productType,
        'rating': product.rating,
        'reviewsCount': product.reviewsCount,
        'isHotDeal': product.isHotDeal,
        'badge': product.badge,
        'warranty': product.warranty,
        'deliveryDays': product.deliveryDays,
        'sortOrder': product.sortOrder,
        'linkedReviewIds': linked_review_ids,
    }
    subtitle = f"SKU: {product.sku} • BDT {product.price:,} • Stock: {product.stock}"
    return {
        'item_type': 'PRODUCT',
        'original_id': product.id,
        'title': product.name,
        'subtitle': subtitle,
        'thumbnail_url': thumb,
        'payload': payload,
    }


def snapshot_review(review):
    payload = {
        'id': review.id,
        'order_id': review.order_id,
        'product_id': review.product_id,
        'product_name': review.product_name,
        'customer_name': review.customer_name,
        'customer_phone': review.customer_phone,
        'location': review.location,
        'rating': review.rating,
        'comment': review.comment,
        'is_verified_purchase': review.is_verified_purchase,
        'is_approved': review.is_approved,
        'source': review.source,
    }
    prod_label = f" • {review.product_name}" if review.product_name else ""
    subtitle = f"{review.rating}★{prod_label} — {(review.comment or '')[:80]}"
    return {
        'item_type': 'REVIEW',
        'original_id': review.id,
        'title': f"Review by {review.customer_name}",
        'subtitle': subtitle,
        'thumbnail_url': '',
        'payload': payload,
    }


def snapshot_media(asset):
    payload = {
        'id': asset.id,
        'title': asset.title,
        'alt_text': asset.alt_text,
        'url': asset.url,
        'public_id': asset.public_id,
        'storage': asset.storage,
        'mime_type': asset.mime_type,
        'file_size': asset.file_size,
        'width': asset.width,
        'height': asset.height,
        'folder': asset.folder,
    }
    kb = round((asset.file_size or 0) / 1024, 1) if asset.file_size else 0
    size_str = f"{kb} KB • " if kb else ""
    subtitle = f"{size_str}{asset.storage.upper()} • Folder: {asset.folder}"
    return {
        'item_type': 'MEDIA',
        'original_id': asset.id,
        'title': asset.title or asset.id,
        'subtitle': subtitle,
        'thumbnail_url': asset.url,
        'payload': payload,
    }


def snapshot_coupon(coupon):
    payload = {
        'code': coupon.code,
        'discountType': coupon.discountType,
        'value': coupon.value,
        'minSpend': coupon.minSpend,
        'isActive': coupon.isActive,
    }
    val_label = f"{coupon.value}%" if coupon.discountType == 'percentage' else f"BDT {coupon.value}"
    subtitle = f"Discount: {val_label} • Min Spend: BDT {coupon.minSpend}"
    return {
        'item_type': 'COUPON',
        'original_id': coupon.code,
        'title': f"Coupon {coupon.code}",
        'subtitle': subtitle,
        'thumbnail_url': '',
        'payload': payload,
    }


def snapshot_staff(staff):
    payload = {
        'id': staff.id,
        'name': staff.name,
        'username': staff.username,
        'phone': staff.phone,
        'email': staff.email,
        'password': staff.password,
        'role': staff.role,
        'status': staff.status,
        'notes': staff.notes,
    }
    subtitle = f"@{staff.username} • {staff.role}"
    return {
        'item_type': 'STAFF',
        'original_id': staff.id,
        'title': staff.name,
        'subtitle': subtitle,
        'thumbnail_url': '',
        'payload': payload,
    }


def snapshot_audit_log(log):
    payload = {
        'id': log.id,
        'timestamp': log.timestamp,
        'actor': log.actor,
        'role': log.role,
        'action': log.action,
        'targetType': log.targetType,
        'targetId': log.targetId,
        'details': log.details,
        'ipAddress': log.ipAddress,
    }
    subtitle = f"{log.actor} ({log.role}) • {log.targetType} #{log.targetId} • {log.ipAddress}"
    return {
        'item_type': 'AUDIT_LOG',
        'original_id': log.id,
        'title': f"Audit: {log.action}",
        'subtitle': subtitle,
        'thumbnail_url': '',
        'payload': payload,
    }


def snapshot_payment_transaction(txn):
    payload = {
        'id': txn.id,
        'order_id': txn.order_id,
        'tran_id': txn.tran_id,
        'val_id': txn.val_id,
        'amount': str(txn.amount),
        'currency': txn.currency,
        'payment_method': txn.payment_method,
        'status': txn.status,
        'card_type': txn.card_type,
        'card_issuer': txn.card_issuer,
        'bank_tran_id': txn.bank_tran_id,
        'customer_name': txn.customer_name,
        'customer_phone': txn.customer_phone,
        'session_key': txn.session_key,
        'gateway_url': txn.gateway_url,
        'error_reason': txn.error_reason,
        'raw_payload': txn.raw_payload if isinstance(txn.raw_payload, dict) else {},
    }
    subtitle = f"Order #{txn.order_id} • BDT {txn.amount} • {txn.status}"
    return {
        'item_type': 'PAYMENT_TRANSACTION',
        'original_id': str(txn.tran_id or txn.id),
        'title': f"Trx {txn.tran_id} — {txn.customer_name or 'Guest'}",
        'subtitle': subtitle,
        'thumbnail_url': '',
        'payload': payload,
    }


def restore_recycle_bin_item(rb_item, actor='Admin', ip_address='127.0.0.1'):
    """Restore a trashed item back into its active database table."""
    from order.models import Order
    from products.models import Product, Review, MediaAsset, Coupon

    p = rb_item.payload if isinstance(rb_item.payload, dict) else {}
    itype = rb_item.item_type

    if itype == 'ORDER':
        oid = p.get('id') or rb_item.original_id
        if Order.objects.filter(id=oid).exists():
            oid = f"{oid}-R{uuid.uuid4().hex[:3].upper()}"
        tok = p.get('trackingToken') or f"TRK-{uuid.uuid4().hex[:8].upper()}"
        if Order.objects.filter(trackingToken=tok).exists():
            tok = f"{tok}-{uuid.uuid4().hex[:4].upper()}"

        Order.objects.create(
            id=oid,
            trackingToken=tok,
            createdAt=p.get('createdAt') or timezone.localtime().strftime('%Y-%m-%d %H:%M:%S'),
            items=p.get('items') if isinstance(p.get('items'), list) else [],
            address=p.get('address') if isinstance(p.get('address'), dict) else {},
            customerEmail=p.get('customerEmail'),
            subtotal=int(p.get('subtotal') or 0),
            deliveryFee=int(p.get('deliveryFee') if p.get('deliveryFee') is not None else 60),
            discount=int(p.get('discount') or 0),
            total=int(p.get('total') or 0),
            paymentMethod=p.get('paymentMethod') or 'COD',
            paymentStatus=p.get('paymentStatus') or 'UNPAID',
            status=p.get('status') or 'PENDING',
            statusHistory=p.get('statusHistory') if isinstance(p.get('statusHistory'), list) else [],
            courier=p.get('courier'),
            utm=p.get('utm'),
            riskScore=int(p.get('riskScore') if p.get('riskScore') is not None else 10),
            riskFactors=p.get('riskFactors') if isinstance(p.get('riskFactors'), list) else [],
            internalNotes=p.get('internalNotes') if isinstance(p.get('internalNotes'), list) else [],
        )
        AuditLog.log(
            actor=actor,
            role='ADMIN',
            action='ORDER_RESTORED',
            target_type='ORDER',
            target_id=oid,
            details=f"Restored order {oid} from Recycle Bin",
            ip_address=ip_address,
        )

    elif itype == 'PRODUCT':
        pid = p.get('id') or rb_item.original_id
        if Product.objects.filter(id=pid).exists():
            pid = f"{pid}-r{uuid.uuid4().hex[:4]}"
        base_slug = p.get('slug') or slugify(p.get('name') or pid) or pid
        slug_val = base_slug
        counter = 1
        while Product.objects.filter(slug=slug_val).exists():
            slug_val = f"{base_slug}-{counter}"
            counter += 1

        sku_val = p.get('sku') or f"SKU-{uuid.uuid4().hex[:6].upper()}"
        sku_counter = 1
        base_sku = sku_val
        while Product.objects.filter(sku=sku_val).exists():
            sku_val = f"{base_sku}-R{sku_counter}"
            sku_counter += 1

        images = p.get('images') if isinstance(p.get('images'), list) and p.get('images') else [DEFAULT_PRODUCT_IMAGE]
        restored_product = Product.objects.create(
            id=pid,
            name=p.get('name') or rb_item.title,
            nameBn=p.get('nameBn') or '',
            slug=slug_val,
            category=p.get('category') or 'general',
            price=int(p.get('price') or 0),
            regularPrice=int(p.get('regularPrice') or p.get('price') or 0),
            sku=sku_val,
            stock=int(p.get('stock') if p.get('stock') is not None else 0),
            isOutOfStock=bool(p.get('isOutOfStock', False)),
            images=images,
            shortDescription=p.get('shortDescription') or '',
            shortDescriptionBn=p.get('shortDescriptionBn') or '',
            description=p.get('description') or '',
            descriptionBn=p.get('descriptionBn') or '',
            features=p.get('features') if isinstance(p.get('features'), list) else [],
            featuresBn=p.get('featuresBn') if isinstance(p.get('featuresBn'), list) else [],
            variants=p.get('variants') if isinstance(p.get('variants'), list) else [],
            productType=p.get('productType') or 'standard',
            rating=float(p.get('rating') if p.get('rating') is not None else 4.8),
            reviewsCount=int(p.get('reviewsCount') if p.get('reviewsCount') is not None else 1),
            isHotDeal=bool(p.get('isHotDeal', False)),
            badge=p.get('badge') or '',
            warranty=p.get('warranty') or '',
            deliveryDays=p.get('deliveryDays') or '2-3 Days',
            sortOrder=int(p.get('sortOrder') or 0),
        )
        linked_rids = p.get('linkedReviewIds') if isinstance(p.get('linkedReviewIds'), list) else []
        if linked_rids:
            Review.objects.filter(id__in=linked_rids, product__isnull=True).update(product=restored_product)
            for trashed_rev in RecycleBinItem.objects.filter(item_type='REVIEW', original_id__in=linked_rids):
                rp = trashed_rev.payload if isinstance(trashed_rev.payload, dict) else {}
                rp['product_id'] = restored_product.id
                trashed_rev.payload = rp
                trashed_rev.save(update_fields=['payload'])
        AuditLog.log(
            actor=actor,
            role='ADMIN',
            action='PRODUCT_RESTORED',
            target_type='PRODUCT',
            target_id=pid,
            details=f"Restored product '{p.get('name') or pid}' from Recycle Bin",
            ip_address=ip_address,
        )

    elif itype == 'REVIEW':
        rid = p.get('id') or rb_item.original_id
        if Review.objects.filter(id=rid).exists():
            rid = f"{rid}-r{uuid.uuid4().hex[:4]}"
        prod_obj = None
        if p.get('product_id'):
            prod_obj = Product.objects.filter(id=p.get('product_id')).first()
        elif p.get('product_name'):
            prod_obj = Product.objects.filter(name__iexact=p.get('product_name')).first()
        Review.objects.create(
            id=rid,
            order_id=p.get('order_id') or '',
            product=prod_obj,
            product_name=p.get('product_name') or '',
            customer_name=p.get('customer_name') or 'Customer',
            customer_phone=p.get('customer_phone') or '',
            location=p.get('location') or '',
            rating=int(p.get('rating') or 5),
            comment=p.get('comment') or '',
            is_verified_purchase=bool(p.get('is_verified_purchase', True)),
            is_approved=bool(p.get('is_approved', True)),
            source=p.get('source') or 'ORDER_TRACKING',
        )
        AuditLog.log(
            actor=actor,
            role='ADMIN',
            action='REVIEW_RESTORED',
            target_type='REVIEW',
            target_id=rid,
            details=f"Restored review '{rid}' from Recycle Bin",
            ip_address=ip_address,
        )

    elif itype == 'MEDIA':
        mid = p.get('id') or rb_item.original_id
        if MediaAsset.objects.filter(id=mid).exists():
            mid = f"{mid}-r{uuid.uuid4().hex[:4]}"
        MediaAsset.objects.create(
            id=mid,
            title=p.get('title') or rb_item.title,
            alt_text=p.get('alt_text') or '',
            url=p.get('url') or rb_item.thumbnail_url,
            public_id=p.get('public_id') or '',
            storage=p.get('storage') or 'local',
            mime_type=p.get('mime_type') or 'image/jpeg',
            file_size=int(p.get('file_size') or 0),
            width=p.get('width'),
            height=p.get('height'),
            folder=p.get('folder') or 'library',
        )
        AuditLog.log(
            actor=actor,
            role='ADMIN',
            action='MEDIA_RESTORED',
            target_type='MEDIA',
            target_id=mid,
            details=f"Restored media asset '{p.get('title') or mid}' from Recycle Bin",
            ip_address=ip_address,
        )

    elif itype == 'COUPON':
        code_val = str(p.get('code') or rb_item.original_id).strip().upper()
        if not Coupon.objects.filter(code=code_val).exists():
            Coupon.objects.create(
                code=code_val,
                discountType=p.get('discountType') or 'fixed',
                value=int(p.get('value') or 0),
                minSpend=int(p.get('minSpend') or 0),
                isActive=bool(p.get('isActive', True)),
            )
        AuditLog.log(
            actor=actor,
            role='ADMIN',
            action='COUPON_RESTORED',
            target_type='COUPON',
            target_id=code_val,
            details=f"Restored coupon '{code_val}' from Recycle Bin",
            ip_address=ip_address,
        )

    elif itype == 'STAFF':
        sid = p.get('id') or rb_item.original_id
        if StaffMember.objects.filter(id=sid).exists():
            sid = f"staff-{uuid.uuid4().hex[:8]}"
        uname = p.get('username') or f"user_{uuid.uuid4().hex[:5]}"
        if StaffMember.objects.filter(username__iexact=uname).exists():
            uname = f"{uname}_{uuid.uuid4().hex[:3]}"
        StaffMember.objects.create(
            id=sid,
            name=p.get('name') or rb_item.title,
            username=uname,
            phone=p.get('phone') or '',
            email=p.get('email') or '',
            password=p.get('password') or secrets.token_urlsafe(12),
            role=p.get('role') or 'MANAGER',
            status=p.get('status') or 'ACTIVE',
            notes=p.get('notes') or '',
        )
        AuditLog.log(
            actor=actor,
            role='ADMIN',
            action='STAFF_RESTORED',
            target_type='SETTINGS',
            target_id=sid,
            details=f"Restored staff '{p.get('name') or sid}' from Recycle Bin",
            ip_address=ip_address,
        )

    elif itype == 'SLIDER':
        shop = ShopSetting.get_settings()
        extra = shop.extraConfig if isinstance(shop.extraConfig, dict) else {}
        home_page = extra.get('homePage') if isinstance(extra.get('homePage'), dict) else {}
        slides = home_page.get('heroSlides') if isinstance(home_page.get('heroSlides'), list) else []
        slide_data = dict(p) if isinstance(p, dict) else {}
        if slide_data:
            existing_ids = {s.get('id') for s in slides if isinstance(s, dict)}
            if not slide_data.get('id') or slide_data.get('id') in existing_ids:
                slide_data['id'] = f"slide-{int(timezone.now().timestamp() * 1000)}"
            slides.append(slide_data)
            home_page['heroSlides'] = slides
            extra['homePage'] = home_page
            shop.extraConfig = extra
            shop.save(update_fields=['extraConfig', 'updated_at'])
        AuditLog.log(
            actor=actor,
            role='ADMIN',
            action='SLIDER_RESTORED',
            target_type='SETTINGS',
            target_id=rb_item.original_id,
            details=f"Restored slider '{rb_item.title}' from Recycle Bin",
            ip_address=ip_address,
        )

    elif itype == 'AUDIT_LOG':
        aid = p.get('id') or rb_item.original_id
        if AuditLog.objects.filter(id=aid).exists():
            aid = f"aud-{int(timezone.now().timestamp() * 1000)}-{uuid.uuid4().hex[:4]}"
        AuditLog.objects.create(
            id=aid,
            timestamp=p.get('timestamp') or timezone.localtime().strftime('%Y-%m-%d %H:%M:%S'),
            actor=p.get('actor') or 'SYSTEM',
            role=p.get('role') or 'SYSTEM',
            action=p.get('action') or 'UNKNOWN',
            targetType=p.get('targetType') or 'ORDER',
            targetId=p.get('targetId') or '',
            details=p.get('details') or '',
            ipAddress=p.get('ipAddress') or '127.0.0.1',
        )

    elif itype == 'PAYMENT_TRANSACTION':
        from decimal import Decimal
        from payments.models import PaymentTransaction
        tid = p.get('tran_id') or rb_item.original_id
        if PaymentTransaction.objects.filter(tran_id=tid).exists():
            tid = f"{tid}-R{uuid.uuid4().hex[:4].upper()}"
        PaymentTransaction.objects.create(
            order_id=p.get('order_id') or 'UNKNOWN',
            tran_id=tid,
            val_id=p.get('val_id'),
            amount=Decimal(str(p.get('amount') or '0')),
            currency=p.get('currency') or 'BDT',
            payment_method=p.get('payment_method') or 'SSLCOMMERZ',
            status=p.get('status') or 'INITIATED',
            card_type=p.get('card_type'),
            card_issuer=p.get('card_issuer'),
            bank_tran_id=p.get('bank_tran_id'),
            customer_name=p.get('customer_name'),
            customer_phone=p.get('customer_phone'),
            session_key=p.get('session_key'),
            gateway_url=p.get('gateway_url'),
            error_reason=p.get('error_reason'),
            raw_payload=p.get('raw_payload') if isinstance(p.get('raw_payload'), dict) else {},
        )
        AuditLog.log(
            actor=actor,
            role='ADMIN',
            action='PAYMENT_TRANSACTION_RESTORED',
            target_type='PAYMENT',
            target_id=tid,
            details=f"Restored payment transaction '{tid}' (Order #{p.get('order_id')}) from Recycle Bin",
            ip_address=ip_address,
        )

    rb_item.delete()
    return {'success': True, 'item_type': itype, 'original_id': rb_item.original_id}


def _url_still_referenced(url):
    """Ensure we don't delete a Cloudinary/local file if another active Product, MediaAsset, or ShopSetting uses it."""
    if not url or str(url).startswith('/assets/'):
        return True
    from products.models import Product, MediaAsset
    if MediaAsset.objects.filter(url=url).exists():
        return True
    for prod in Product.objects.only('images'):
        if isinstance(prod.images, list) and url in prod.images:
            return True
    return False


def permanently_delete_recycle_bin_item(rb_item, actor='Admin', ip_address='127.0.0.1'):
    """Permanently delete a RecycleBinItem from DB (and physical/Cloudinary media if unreferenced)."""
    from products.cloudinary_service import delete_image

    p = rb_item.payload if isinstance(rb_item.payload, dict) else {}
    itype = rb_item.item_type

    if itype == 'MEDIA':
        target = p.get('public_id') or p.get('url') or rb_item.thumbnail_url
        url = p.get('url') or rb_item.thumbnail_url
        storage = p.get('storage') or ''
        if target and (storage in ('local', 'cloudinary') or '/media/' in str(url) or 'cloudinary.com' in str(url)):
            if not _url_still_referenced(url):
                try:
                    delete_image(target)
                except Exception:
                    pass
    elif itype == 'PRODUCT':
        imgs = p.get('images') if isinstance(p.get('images'), list) else []
        for img_url in imgs:
            if img_url and not _url_still_referenced(img_url):
                try:
                    delete_image(img_url)
                except Exception:
                    pass

    title = rb_item.title
    orig_id = rb_item.original_id
    rb_item.delete()

    AuditLog.log(
        actor=actor,
        role='ADMIN',
        action='RECYCLE_BIN_PURGED',
        target_type='ORDER' if itype == 'ORDER' else ('PRODUCT' if itype == 'PRODUCT' else 'SETTINGS'),
        target_id=orig_id,
        details=f"Permanently deleted [{itype}] '{title}' (ID: {orig_id}) from Recycle Bin",
        ip_address=ip_address,
    )
    return {'success': True, 'deleted_id': orig_id, 'item_type': itype}

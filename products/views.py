from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q
from django.utils.text import slugify
from django.conf import settings as django_settings
import time
import uuid
import os
from pathlib import Path
import logging

from .models import Product, Coupon, Review, MediaAsset
from .serializers import ProductSerializer, CouponSerializer, ReviewSerializer, MediaAssetSerializer
from .cloudinary_service import upload_image, delete_image, is_cloudinary_configured
from core.models import AuditLog, ShopSetting, RecycleBinItem
from core.recycle_bin import (
    move_to_recycle_bin,
    snapshot_product,
    snapshot_media,
    snapshot_coupon,
    snapshot_review,
)

logger = logging.getLogger(__name__)

DEFAULT_PRODUCT_IMAGE = '/assets/default-product-placeholder.png'

def auto_translate_product_data(data: dict) -> dict:
    """
    Passthrough for English store - translations are disabled.
    """
    return data

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def products_list_create(request):
    if request.method == 'GET':
        category = request.query_params.get('category')
        search = request.query_params.get('search')

        queryset = Product.objects.all().order_by('sortOrder', '-created_at')

        if category and category != 'All' and category != 'ALL':
            queryset = queryset.filter(category__iexact=category)

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(nameBn__icontains=search) |
                Q(sku__icontains=search) |
                Q(category__icontains=search)
            )

        serializer = ProductSerializer(queryset, many=True)
        return Response({
            'success': True,
            'count': queryset.count(),
            'products': serializer.data
        })

    elif request.method == 'POST':
        data = request.data.copy()
        if not data.get('id'):
            data['id'] = f"prod-{int(time.time() * 1000)}"
        if not data.get('slug'):
            base_slug = slugify(data.get('name', '')) or f"prod-{int(time.time())}"
            data['slug'] = f"{base_slug}-{int(time.time() % 10000)}"
        if 'images' not in data and 'image' in data:
            data['images'] = [data['image']] if data['image'] else [DEFAULT_PRODUCT_IMAGE]
        elif 'images' not in data or not isinstance(data.get('images'), list) or len(data.get('images') or []) == 0:
            data['images'] = [DEFAULT_PRODUCT_IMAGE]
        if 'regularPrice' not in data:
            data['regularPrice'] = data.get('originalPrice', data.get('price', 0))
        if 'stock' not in data:
            data['stock'] = 20
        if 'rating' not in data:
            data['rating'] = 4.8
        if 'reviewsCount' not in data:
            data['reviewsCount'] = 1
        if 'tag' in data and not data.get('badge'):
            data['badge'] = data['tag']
        if data.get('badge') in ['BESTSELLER', 'HOT_DEAL']:
            data['isHotDeal'] = True

        if data.get('productType') == 'standard':
            data['variants'] = []

        # Handle features / keyFeatures
        if 'keyFeatures' in data and ('features' not in data or not data['features']):
            kf = data['keyFeatures']
            if isinstance(kf, list):
                data['features'] = kf
            elif isinstance(kf, str):
                lines = [line.strip().lstrip('•-*1234567890. ') for line in kf.split('\n') if line.strip()]
                data['features'] = lines
        elif 'features' in data and isinstance(data['features'], str):
            lines = [line.strip().lstrip('•-*1234567890. ') for line in data['features'].split('\n') if line.strip()]
            data['features'] = lines

        data = auto_translate_product_data(data)

        serializer = ProductSerializer(data=data)
        if serializer.is_valid():
            try:
                product = serializer.save()
                try:
                    AuditLog.log(
                        actor='Admin',
                        role='ADMIN',
                        action='PRODUCT_CREATED',
                        target_type='PRODUCT',
                        target_id=product.id,
                        details=f'Created product "{product.name}" (SKU: {product.sku}, Category: {product.category}, Stock: {product.stock})',
                        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
                    )
                except Exception as audit_err:
                    print(f"[Warning] AuditLog creation failed: {audit_err}")
                return Response({'success': True, 'product': serializer.data}, status=status.HTTP_201_CREATED)
            except Exception as save_err:
                return Response({
                    'success': False,
                    'message': f'Failed to save product to database: {str(save_err)}',
                    'error': str(save_err)
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([AllowAny])
def product_detail_update(request, pk):
    try:
        product = Product.objects.filter(Q(id=pk) | Q(slug=pk)).first()
        if not product:
            return Response({'success': False, 'message': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception:
        return Response({'success': False, 'message': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        serializer = ProductSerializer(product)
        return Response({'success': True, 'product': serializer.data})

    elif request.method in ['PUT', 'PATCH']:
        data = request.data.copy()
        if 'images' not in data and 'image' in data:
            data['images'] = [data['image']] if data['image'] else [DEFAULT_PRODUCT_IMAGE]
        elif 'images' in data and (not isinstance(data.get('images'), list) or len(data.get('images') or []) == 0):
            data['images'] = [DEFAULT_PRODUCT_IMAGE]
        if 'regularPrice' not in data and 'originalPrice' in data:
            data['regularPrice'] = data['originalPrice']
        if 'slug' not in data:
            data['slug'] = product.slug
        if 'tag' in data and 'badge' not in data:
            data['badge'] = data['tag']
        if 'badge' in data and data['badge'] in ['BESTSELLER', 'HOT_DEAL']:
            data['isHotDeal'] = True
        elif 'badge' in data and data['badge'] not in ['BESTSELLER', 'HOT_DEAL']:
            data['isHotDeal'] = False

        if data.get('productType') == 'standard':
            data['variants'] = []

        # Handle features / keyFeatures
        if 'keyFeatures' in data:
            kf = data['keyFeatures']
            if isinstance(kf, list):
                data['features'] = kf
            elif isinstance(kf, str):
                lines = [line.strip().lstrip('•-*1234567890. ') for line in kf.split('\n') if line.strip()]
                data['features'] = lines
        elif 'features' in data and isinstance(data['features'], str):
            lines = [line.strip().lstrip('•-*1234567890. ') for line in data['features'].split('\n') if line.strip()]
            data['features'] = lines

        data = auto_translate_product_data(data)

        serializer = ProductSerializer(product, data=data, partial=True)
        if serializer.is_valid():
            try:
                product = serializer.save()
                try:
                    AuditLog.log(
                        actor='Admin',
                        role='ADMIN',
                        action='PRODUCT_UPDATED',
                        target_type='PRODUCT',
                        target_id=product.id,
                        details=f'Updated product {product.id} (Stock: {product.stock}, Price: {product.price})',
                        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
                    )
                except Exception as audit_err:
                    print(f"[Warning] AuditLog creation failed: {audit_err}")
                return Response({'success': True, 'product': serializer.data})
            except Exception as save_err:
                return Response({
                    'success': False,
                    'message': f'Failed to update product: {str(save_err)}',
                    'error': str(save_err)
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == 'DELETE':
        prod_id = product.id
        prod_name = product.name
        snap = snapshot_product(product)
        product.delete()
        rb = move_to_recycle_bin(**snap, deleted_by='Admin')
        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='PRODUCT_MOVED_TO_TRASH',
            target_type='PRODUCT',
            target_id=prod_id,
            details=f'Moved product "{prod_name}" (ID: {prod_id}) to Recycle Bin',
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )
        return Response({
            'success': True,
            'message': f'Product "{prod_name}" moved to Recycle Bin.',
            'id': prod_id,
            'recycleBinId': rb.id,
        })

@api_view(['POST'])
@permission_classes([AllowAny])
def bulk_update_stock(request):
    """
    Bulk update product stock in database.
    Accepts:
    1) { "updates": [{ "id": "prod-1", "stock": 50 }, ...] }
    2) { "adjustment": 10, "product_ids": ["prod-1", ...] } (or all products if product_ids empty)
    3) { "set_stock": 25, "product_ids": [...] }
    """
    data = request.data
    updated_products = []

    if 'updates' in data and isinstance(data['updates'], list):
        for item in data['updates']:
            p_id = item.get('id')
            new_stock = item.get('stock')
            if p_id is not None and new_stock is not None:
                p = Product.objects.filter(id=p_id).first()
                if p:
                    p.stock = max(0, int(new_stock))
                    p.save(update_fields=['stock', 'updated_at'])
                    updated_products.append(p)

    elif 'adjustment' in data:
        adj = int(data.get('adjustment', 0))
        p_ids = data.get('product_ids', [])
        qs = Product.objects.filter(id__in=p_ids) if p_ids else Product.objects.all()
        for p in qs:
            p.stock = max(0, p.stock + adj)
            p.save(update_fields=['stock', 'updated_at'])
            updated_products.append(p)

    elif 'set_stock' in data:
        target = max(0, int(data.get('set_stock', 0)))
        p_ids = data.get('product_ids', [])
        qs = Product.objects.filter(id__in=p_ids) if p_ids else Product.objects.all()
        for p in qs:
            p.stock = target
            p.save(update_fields=['stock', 'updated_at'])
            updated_products.append(p)

    else:
        return Response({'success': False, 'message': 'Invalid bulk stock update payload'}, status=status.HTTP_400_BAD_REQUEST)

    AuditLog.log(
        actor='Admin',
        role='ADMIN',
        action='BULK_STOCK_UPDATED',
        target_type='PRODUCT',
        target_id='MULTIPLE',
        details=f'Bulk updated stock for {len(updated_products)} products',
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    serializer = ProductSerializer(updated_products, many=True)
    return Response({
        'success': True,
        'count': len(updated_products),
        'products': serializer.data
    })

def _register_media_asset(file_obj, upload_res, folder='library', title=None, alt_text=''):
    """Creates a MediaAsset record in the central DB for every uploaded asset."""
    asset_url = upload_res.get('url') or ''
    if not asset_url:
        return None
    orig_name = getattr(file_obj, 'name', '') or 'uploaded-image'
    clean_title = (title or os.path.splitext(os.path.basename(orig_name))[0] or 'Media Asset')[:240]
    asset = MediaAsset.objects.create(
        id=f"media-{uuid.uuid4().hex[:12]}",
        title=clean_title,
        alt_text=alt_text or clean_title,
        url=asset_url,
        public_id=upload_res.get('public_id') or '',
        storage=upload_res.get('storage') or 'local',
        mime_type=getattr(file_obj, 'content_type', '') or 'image/jpeg',
        file_size=getattr(file_obj, 'size', 0) or 0,
        width=upload_res.get('width'),
        height=upload_res.get('height'),
        folder=folder or 'library',
    )
    return asset


@api_view(['POST'])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def upload_image_view(request):
    """
    Upload an image file to Cloudinary (or fallback to local media) and register in Media Library.
    """
    file_obj = request.FILES.get('image') or request.FILES.get('file')
    if not file_obj:
        return Response({
            'success': False,
            'message': 'No image file provided in request.'
        }, status=status.HTTP_400_BAD_REQUEST)

    library_folder = (request.data.get('library_folder') or request.data.get('category') or 'products').strip()
    target_folder = (request.data.get('folder') or os.getenv('CLOUDINARY_FOLDER', 'amardokan/products')).strip()
    res = upload_image(file_obj, folder=target_folder)
    if res.get('success'):
        asset = _register_media_asset(
            file_obj,
            res,
            folder=library_folder,
            title=request.data.get('title'),
            alt_text=request.data.get('alt_text', ''),
        )
        if asset:
            res['asset'] = MediaAssetSerializer(asset).data
        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='IMAGE_UPLOADED',
            target_type='ASSET',
            target_id=res.get('public_id', 'unknown'),
            details=f"Uploaded image to {res.get('storage')} storage ({res.get('url')})",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )
        return Response(res, status=status.HTTP_201_CREATED)
    else:
        return Response(res, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST', 'DELETE'])
@permission_classes([AllowAny])
def delete_image_view(request):
    """
    Delete an image from Cloudinary or local media and remove matching MediaAsset DB record.
    Accepts: { "public_id": "...", "url": "..." } in body or query param
    """
    public_id = request.data.get('public_id') or request.query_params.get('public_id')
    url = request.data.get('url') or request.query_params.get('url')

    target = public_id or url
    if not target:
        return Response({
            'success': False,
            'message': 'Either public_id or url must be provided.'
        }, status=status.HTTP_400_BAD_REQUEST)

    res = delete_image(target)
    # Remove any matching MediaAsset entries from DB
    q_filter = Q()
    if url:
        q_filter |= Q(url=url)
    if public_id:
        q_filter |= Q(public_id=public_id)
    if target:
        q_filter |= Q(url=target) | Q(public_id=target)
    MediaAsset.objects.filter(q_filter).delete()

    if res.get('success'):
        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='IMAGE_DELETED',
            target_type='ASSET',
            target_id=str(target),
            details=f"Deleted image {target} from {res.get('storage', 'cloudinary')} storage",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )
        return Response(res, status=status.HTTP_200_OK)
    else:
        return Response(res, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def media_list_or_upload(request):
    """
    WordPress-style Media Library API:
    GET: list all MediaAsset entries with optional search `q` and `folder` filter.
    POST: upload 1 or more images to Cloudinary/local storage and save MediaAsset DB rows.
    """
    if request.method == 'GET':
        q = (request.query_params.get('q') or '').strip()
        folder = (request.query_params.get('folder') or '').strip().lower()

        qs = MediaAsset.objects.all()
        if folder and folder != 'all':
            qs = qs.filter(folder=folder)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(alt_text__icontains=q) | Q(url__icontains=q))

        serializer = MediaAssetSerializer(qs, many=True)
        return Response({
            'success': True,
            'count': qs.count(),
            'media': serializer.data,
        })

    # POST: multi or single file upload
    files = (
        request.FILES.getlist('files')
        or request.FILES.getlist('file')
        or request.FILES.getlist('image')
    )
    if not files:
        return Response({
            'success': False,
            'message': 'No media files provided.'
        }, status=status.HTTP_400_BAD_REQUEST)

    library_folder = (request.data.get('folder') or 'library').strip().lower()
    cloud_folder = os.getenv('CLOUDINARY_FOLDER', 'amardokan/products').strip()
    custom_title = request.data.get('title')
    alt_text = request.data.get('alt_text', '')

    created_assets = []
    errors = []
    for file_obj in files:
        res = upload_image(file_obj, folder=cloud_folder)
        if res.get('success'):
            asset = _register_media_asset(
                file_obj,
                res,
                folder=library_folder,
                title=custom_title if len(files) == 1 else None,
                alt_text=alt_text,
            )
            if asset:
                created_assets.append(MediaAssetSerializer(asset).data)
                AuditLog.log(
                    actor='Admin',
                    role='ADMIN',
                    action='MEDIA_LIBRARY_UPLOAD',
                    target_type='MEDIA',
                    target_id=asset.id,
                    details=f"Added '{asset.title}' to Media Library ({asset.storage})",
                    ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
                )
        else:
            errors.append(res.get('error') or 'Upload failed')

    if not created_assets:
        return Response({
            'success': False,
            'message': errors[0] if errors else 'Failed to upload media files.',
            'errors': errors,
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({
        'success': True,
        'asset': created_assets[0],
        'assets': created_assets,
        'errors': errors,
    }, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([AllowAny])
def media_detail_update_delete(request, pk):
    """
    Get, update metadata (title/alt_text/folder), or permanently delete a MediaAsset
    both from Cloudinary/local disk and the central database.
    """
    try:
        asset = MediaAsset.objects.get(pk=pk)
    except MediaAsset.DoesNotExist:
        return Response({'success': False, 'message': 'Media asset not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return Response({'success': True, 'asset': MediaAssetSerializer(asset).data})

    if request.method == 'PATCH':
        for field in ('title', 'alt_text', 'folder'):
            if field in request.data:
                setattr(asset, field, str(request.data[field]).strip())
        asset.save()
        return Response({'success': True, 'asset': MediaAssetSerializer(asset).data})

    # DELETE: Move asset metadata to Recycle Bin without destroying underlying file yet
    snap = snapshot_media(asset)
    deleted_title = asset.title
    asset.delete()
    rb = move_to_recycle_bin(**snap, deleted_by='Admin')

    AuditLog.log(
        actor='Admin',
        role='ADMIN',
        action='MEDIA_MOVED_TO_TRASH',
        target_type='MEDIA',
        target_id=pk,
        details=f"Moved media asset '{deleted_title}' to Recycle Bin",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )
    return Response({
        'success': True,
        'recycleBinId': rb.id,
        'message': 'Media asset moved to Recycle Bin.',
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def sync_media_library_view(request):
    """
    Scans existing local /media/products/ files, active Product images, ShopSetting branding URLs,
    and Hero Slides so pre-existing site assets are indexed into MediaAsset without duplicates.
    """
    existing_urls = set(MediaAsset.objects.values_list('url', flat=True))
    trashed_media_urls = set(
        RecycleBinItem.objects.filter(item_type='MEDIA').values_list('thumbnail_url', flat=True)
    )
    existing_urls.update(u for u in trashed_media_urls if u)
    imported = 0

    def add_if_new(url, title, folder, storage='external', public_id='', file_size=0):
        nonlocal imported
        if not url or not isinstance(url, str):
            return
        clean = url.strip()
        if not clean or clean.startswith('data:') or clean in existing_urls:
            return
        existing_urls.add(clean)
        detected_storage = storage
        if 'cloudinary.com' in clean:
            detected_storage = 'cloudinary'
        elif '/media/products/' in clean:
            detected_storage = 'local'
        MediaAsset.objects.create(
            id=f"media-{uuid.uuid4().hex[:12]}",
            title=str(title or os.path.basename(clean.split('?')[0]) or 'Imported Asset')[:240],
            alt_text=str(title or '')[:240],
            url=clean,
            public_id=public_id,
            storage=detected_storage,
            file_size=file_size,
            folder=folder,
        )
        imported += 1

    # 1. Local disk /media/products/
    media_dir = getattr(django_settings, 'MEDIA_ROOT', django_settings.BASE_DIR / 'media') / 'products'
    if media_dir.exists():
        for fp in media_dir.iterdir():
            if fp.is_file() and fp.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg', '.ico', '.avif'):
                rel_url = f"/media/products/{fp.name}"
                add_if_new(
                    rel_url,
                    fp.stem,
                    'library',
                    storage='local',
                    public_id=f"local_{fp.name}",
                    file_size=fp.stat().st_size,
                )

    # 2. Products & variants
    for prod in Product.objects.all():
        imgs = prod.images if isinstance(prod.images, list) else []
        for idx, img_url in enumerate(imgs):
            add_if_new(img_url, f"{prod.name} #{idx + 1}", 'products')
        variants = prod.variants if isinstance(prod.variants, list) else []
        for grp in variants:
            for opt in (grp.get('options') or []):
                if isinstance(opt, dict) and opt.get('image'):
                    add_if_new(opt.get('image'), f"{prod.name} - {opt.get('name', 'Variant')}", 'products')

    # 3. ShopSetting logos & hero slides
    shop = ShopSetting.get_settings()
    for field_name, label in [('logoUrl', 'Shop Logo'), ('headerLogoUrl', 'Header Logo'), ('footerLogoUrl', 'Footer Logo'), ('faviconUrl', 'Favicon')]:
        add_if_new(getattr(shop, field_name, ''), label, 'branding')

    extra = shop.extraConfig if isinstance(shop.extraConfig, dict) else {}
    slides = (extra.get('homePage') or {}).get('heroSlides') or []
    for idx, slide in enumerate(slides):
        if isinstance(slide, dict):
            add_if_new(slide.get('bgImage'), f"Slider {idx + 1} Background", 'sliders')
            add_if_new(slide.get('productImage'), f"Slider {idx + 1} Product Image", 'sliders')

    return Response({
        'success': True,
        'imported': imported,
        'total': MediaAsset.objects.count(),
    })

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def coupons_list_or_create(request):
    if request.method == 'GET':
        include_all = request.query_params.get('include_all', 'false').lower() == 'true'
        if include_all:
            coupons = Coupon.objects.all().order_by('code')
        else:
            coupons = Coupon.objects.filter(isActive=True).order_by('code')
        serializer = CouponSerializer(coupons, many=True)
        return Response({'success': True, 'coupons': serializer.data})

    elif request.method == 'POST':
        data = request.data
        code = str(data.get('code', '')).strip().upper()
        if not code:
            return Response({'success': False, 'message': 'Coupon code is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        discount_type = data.get('discountType', 'fixed')
        if discount_type not in ['fixed', 'percentage']:
            discount_type = 'fixed'
        
        try:
            value = int(data.get('value', 0))
            min_spend = int(data.get('minSpend', 0))
        except (ValueError, TypeError):
            return Response({'success': False, 'message': 'Value and minSpend must be numbers'}, status=status.HTTP_400_BAD_REQUEST)
        
        is_active = bool(data.get('isActive', True))

        coupon, created = Coupon.objects.update_or_create(
            code=code,
            defaults={
                'discountType': discount_type,
                'value': value,
                'minSpend': min_spend,
                'isActive': is_active
            }
        )

        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='COUPON_CREATED' if created else 'COUPON_UPDATED',
            target_type='COUPON',
            target_id=code,
            details=f"Coupon {code} {'created' if created else 'updated'}: {discount_type} {value}, min spend {min_spend}",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )

        serializer = CouponSerializer(coupon)
        return Response({
            'success': True,
            'coupon': serializer.data,
            'message': f"Coupon {code} {'created' if created else 'updated'} successfully"
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([AllowAny])
def coupon_detail_update_delete(request, code):
    code_clean = str(code).strip().upper()
    coupon = Coupon.objects.filter(code__iexact=code_clean).first()
    if not coupon:
        return Response({'success': False, 'message': f'Coupon {code} not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        serializer = CouponSerializer(coupon)
        return Response({'success': True, 'coupon': serializer.data})

    elif request.method in ['PUT', 'PATCH']:
        data = request.data
        if 'discountType' in data:
            dtype = data['discountType']
            if dtype in ['fixed', 'percentage']:
                coupon.discountType = dtype
        if 'value' in data:
            try:
                coupon.value = int(data['value'])
            except (ValueError, TypeError):
                pass
        if 'minSpend' in data:
            try:
                coupon.minSpend = int(data['minSpend'])
            except (ValueError, TypeError):
                pass
        if 'isActive' in data:
            coupon.isActive = bool(data['isActive'])

        coupon.save()

        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='COUPON_UPDATED',
            target_type='COUPON',
            target_id=coupon.code,
            details=f"Coupon {coupon.code} updated: active={coupon.isActive}, val={coupon.value}",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )

        serializer = CouponSerializer(coupon)
        return Response({'success': True, 'coupon': serializer.data, 'message': f'Coupon {coupon.code} updated successfully'})

    elif request.method == 'DELETE':
        coupon_code = coupon.code
        snap = snapshot_coupon(coupon)
        coupon.delete()
        rb = move_to_recycle_bin(**snap, deleted_by='Admin')

        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='COUPON_MOVED_TO_TRASH',
            target_type='COUPON',
            target_id=coupon_code,
            details=f"Coupon {coupon_code} moved to Recycle Bin",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )

        return Response({
            'success': True,
            'recycleBinId': rb.id,
            'message': f'Coupon {coupon_code} moved to Recycle Bin.'
        })


@api_view(['POST'])
@permission_classes([AllowAny])
def reorder_products(request):
    """
    Update sort order of products from drag-and-drop.
    Accepts: { "order": ["prod-1", "prod-2", ...] }
    """
    order = request.data.get('order', [])
    if not isinstance(order, list):
        return Response({'success': False, 'message': 'order must be a list of product IDs'}, status=status.HTTP_400_BAD_REQUEST)

    for idx, prod_id in enumerate(order):
        Product.objects.filter(id=prod_id).update(sortOrder=idx)

    AuditLog.log(
        actor='Admin',
        role='ADMIN',
        action='PRODUCTS_REORDERED',
        target_type='PRODUCT',
        target_id='all',
        details=f'Reordered {len(order)} products via drag and drop',
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )
    return Response({'success': True, 'message': 'Products reordered successfully', 'order': order})


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def reviews_list_create(request):
    """
    GET  /api/v1/reviews - list reviews (optional filters: ?product_id=..., ?order_id=..., ?approved_only=true)
    POST /api/v1/reviews - submit new review from tracking portal or admin
    """
    if request.method == 'GET':
        queryset = Review.objects.all().order_by('-created_at')
        product_id = request.query_params.get('product_id')
        order_id = request.query_params.get('order_id')
        approved_only = request.query_params.get('approved_only', 'false').lower() in ('true', '1')

        if product_id:
            queryset = queryset.filter(
                Q(product_id=product_id) |
                Q(product__slug=product_id) |
                Q(product_name__iexact=product_id)
            )
        if order_id:
            queryset = queryset.filter(order_id=order_id)
        if approved_only:
            queryset = queryset.filter(is_approved=True)

        # Auto-repair legacy reviews that have generic 'Customer' name by looking up their linked order
        from order.models import Order
        for r in queryset:
            if (not r.customer_name or r.customer_name.strip().lower() in ['customer', 'guest', 'valued customer']) and r.order_id:
                ord_obj = Order.objects.filter(id=r.order_id).first()
                if ord_obj and isinstance(ord_obj.address, dict):
                    resolved = ord_obj.address.get('customerName') or ord_obj.address.get('name')
                    if resolved:
                        r.customer_name = resolved
                        if not r.customer_phone and ord_obj.address.get('phone'):
                            r.customer_phone = ord_obj.address.get('phone')
                        if (not r.location or r.location == 'Dhaka, Bangladesh') and ord_obj.address.get('district'):
                            r.location = f"{ord_obj.address.get('district')}, Bangladesh"
                        r.save(update_fields=['customer_name', 'customer_phone', 'location'])

        serializer = ReviewSerializer(queryset, many=True)
        return Response({
            'success': True,
            'count': queryset.count(),
            'reviews': serializer.data
        })

    elif request.method == 'POST':
        data = request.data.copy()
        if not data.get('id'):
            data['id'] = f"rev-{int(time.time() * 1000)}"

        from order.models import Order
        order_id = (data.get('order_id') or '').strip()
        cust_name = (data.get('customer_name') or '').strip()

        # If customer_name is missing or generic 'Customer', auto-resolve from linked Order
        if not cust_name or cust_name.lower() in ['customer', 'guest', 'valued customer']:
            if order_id:
                ord_obj = Order.objects.filter(id=order_id).first()
                if ord_obj and isinstance(ord_obj.address, dict):
                    resolved_name = (
                        ord_obj.address.get('customerName') or
                        ord_obj.address.get('name') or
                        ord_obj.address.get('recipient_name')
                    )
                    if resolved_name:
                        data['customer_name'] = resolved_name
                        cust_name = resolved_name
                    if not data.get('customer_phone') and ord_obj.address.get('phone'):
                        data['customer_phone'] = ord_obj.address.get('phone')
                    if (not data.get('location') or data.get('location') == 'Dhaka, Bangladesh') and ord_obj.address.get('district'):
                        dist = ord_obj.address.get('district')
                        thana = ord_obj.address.get('thana')
                        loc = f"{dist}, Bangladesh" if not thana else f"{thana}, {dist}"
                        data['location'] = loc
                    if not data.get('product_name') and ord_obj.items:
                        first_item = ord_obj.items[0] if isinstance(ord_obj.items, list) and len(ord_obj.items) > 0 else {}
                        data['product_name'] = first_item.get('name', '')
                        if not data.get('product') and first_item.get('productId'):
                            data['product'] = first_item.get('productId')

        # Fallback if still generic
        if not data.get('customer_name') or data.get('customer_name').strip().lower() in ['customer', 'guest']:
            data['customer_name'] = 'Verified Customer'

        # Validate rating 1..5
        try:
            rating_val = int(data.get('rating', 5))
            data['rating'] = max(1, min(5, rating_val))
        except (ValueError, TypeError):
            data['rating'] = 5

        # If product is specified, safely link to existing Product or fallback gracefully
        if data.get('product'):
            prod = Product.objects.filter(Q(id=data['product']) | Q(slug=data['product'])).first()
            if prod:
                data['product'] = prod.id
                if not data.get('product_name'):
                    data['product_name'] = prod.name
            else:
                data['product'] = None

        serializer = ReviewSerializer(data=data)
        if serializer.is_valid():
            review = serializer.save()

            # Update product rating and reviewsCount if attached to a product
            if review.product:
                prod = review.product
                approved_reviews = Review.objects.filter(product=prod, is_approved=True)
                if approved_reviews.exists():
                    avg_rating = sum(r.rating for r in approved_reviews) / approved_reviews.count()
                    prod.rating = round(avg_rating, 1)
                    prod.reviewsCount = approved_reviews.count()
                    prod.save(update_fields=['rating', 'reviewsCount'])

            AuditLog.log(
                actor=data.get('customer_name') or 'Customer',
                role='CUSTOMER' if data.get('source') != 'ADMIN_MANUAL' else 'ADMIN',
                action='REVIEW_SUBMITTED',
                target_type='REVIEW',
                target_id=review.id,
                details=f"New {review.rating}★ review by {review.customer_name} ({review.source})",
                ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
            )
            return Response({'success': True, 'review': serializer.data}, status=status.HTTP_201_CREATED)
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'DELETE', 'PATCH'])
@permission_classes([AllowAny])
def review_detail_update_delete(request, pk):
    """
    GET, PUT, PATCH, DELETE /api/v1/reviews/<pk>
    """
    try:
        review = Review.objects.get(id=pk)
    except Review.DoesNotExist:
        return Response({'success': False, 'message': 'Review not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        serializer = ReviewSerializer(review)
        return Response({'success': True, 'review': serializer.data})

    elif request.method in ['PUT', 'PATCH']:
        serializer = ReviewSerializer(review, data=request.data, partial=True)
        if serializer.is_valid():
            updated_review = serializer.save()
            return Response({'success': True, 'review': serializer.data})
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == 'DELETE':
        review_id = review.id
        snap = snapshot_review(review)
        review.delete()
        rb = move_to_recycle_bin(**snap, deleted_by='Admin')
        AuditLog.log(
            actor='Admin',
            role='ADMIN',
            action='REVIEW_MOVED_TO_TRASH',
            target_type='REVIEW',
            target_id=review_id,
            details=f"Review {review_id} moved to Recycle Bin",
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
        )
        return Response({
            'success': True,
            'recycleBinId': rb.id,
            'message': 'Review moved to Recycle Bin.'
        })


@api_view(['POST'])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def bulk_upload_csv(request):
    """
    Accepts CSV file upload (file/csv) or raw JSON string/array (csv_text / products).
    Processes products in bulk, updates existing by SKU or creates new,
    and returns created/updated counts along with the full updated product list.
    """
    import csv
    import io
    import re

    uploaded_file = request.FILES.get('file') or request.FILES.get('csv')
    csv_text = request.data.get('csv_text')
    raw_products = request.data.get('products')

    rows = []

    if uploaded_file:
        try:
            content_bytes = uploaded_file.read()
            # Try UTF-8-sig (handles Excel UTF-8 BOM), then utf-8, then latin-1
            try:
                decoded_str = content_bytes.decode('utf-8-sig')
            except UnicodeDecodeError:
                try:
                    decoded_str = content_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    decoded_str = content_bytes.decode('latin-1', errors='replace')

            reader = csv.DictReader(io.StringIO(decoded_str))
            for r in reader:
                if r:
                    rows.append(r)
        except Exception as e:
            return Response({'success': False, 'message': f'Failed to parse CSV file: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

    elif csv_text and isinstance(csv_text, str) and csv_text.strip():
        try:
            reader = csv.DictReader(io.StringIO(csv_text.strip()))
            for r in reader:
                if r:
                    rows.append(r)
        except Exception as e:
            return Response({'success': False, 'message': f'Failed to parse CSV text: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

    elif raw_products and isinstance(raw_products, list):
        rows = raw_products

    else:
        return Response({'success': False, 'message': 'No CSV file or data payload provided.'}, status=status.HTTP_400_BAD_REQUEST)

    if not rows:
        return Response({'success': False, 'message': 'No product rows found in CSV.'}, status=status.HTTP_400_BAD_REQUEST)

    created_count = 0
    updated_count = 0
    errors = []

    for idx, raw_row in enumerate(rows, start=1):
        try:
            # Normalize dictionary keys
            row = {}
            for k, v in raw_row.items():
                if k is not None:
                    clean_k = str(k).strip()
                    row[clean_k] = v.strip() if isinstance(v, str) else v

            # Helper function to get value with multiple alias keys
            def get_val(*aliases, default=''):
                for a in aliases:
                    for k in row.keys():
                        if k.lower() == a.lower() and row[k] is not None:
                            val = str(row[k]).strip()
                            if val:
                                return val
                return default

            name = get_val('name', 'product_name', 'title', 'Title')
            name_bn = get_val('nameBn', 'name_bn', 'titleBn')

            if not name and not name_bn:
                errors.append(f"Row #{idx}: Product name is missing.")
                continue

            category = get_val('category', 'Category', 'category_slug', default='gadgets')
            sku = get_val('sku', 'SKU', 'code', 'product_code')

            # Parse Prices
            raw_price = get_val('price', 'Price', 'sale_price', default='0')
            raw_regular = get_val('regularPrice', 'regular_price', 'originalPrice', default='0')

            clean_price_num = re.sub(r'[^\d.]', '', raw_price)
            price = int(float(clean_price_num)) if clean_price_num else 0

            clean_reg_num = re.sub(r'[^\d.]', '', raw_regular)
            regular_price = int(float(clean_reg_num)) if clean_reg_num else price

            # Parse Stock
            raw_stock = get_val('stock', 'Stock', 'quantity', default='20')
            clean_stock_num = re.sub(r'[^\d]', '', raw_stock)
            stock = int(clean_stock_num) if clean_stock_num else 20

            # Images: pipe '|' separated or comma separated
            raw_images = get_val('images', 'image', 'Images', 'Image')
            images_list = []
            if raw_images:
                if '|' in raw_images:
                    images_list = [img.strip() for img in raw_images.split('|') if img.strip()]
                elif ',' in raw_images and ('http://' in raw_images or 'https://' in raw_images or '/media' in raw_images):
                    images_list = [img.strip() for img in raw_images.split(',') if img.strip()]
                else:
                    images_list = [raw_images.strip()]
            if not images_list:
                images_list = [DEFAULT_PRODUCT_IMAGE]

            # Features: pipe '|' separated or newline
            raw_features = get_val('features', 'keyFeatures')
            features_list = []
            if raw_features:
                split_char = '|' if '|' in raw_features else '\n'
                features_list = [f.strip().lstrip('•-*✓').strip() for f in raw_features.split(split_char) if f.strip()]

            raw_features_bn = get_val('featuresBn', 'features_bn')
            features_bn_list = []
            if raw_features_bn:
                split_char = '|' if '|' in raw_features_bn else '\n'
                features_bn_list = [f.strip().lstrip('•-*✓').strip() for f in raw_features_bn.split(split_char) if f.strip()]

            short_desc = get_val('shortDescription', 'short_description')
            short_desc_bn = get_val('shortDescriptionBn', 'short_description_bn')
            desc = get_val('description', 'Description')
            desc_bn = get_val('descriptionBn', 'description_bn')
            badge = get_val('badge', 'tag', 'Badge')
            warranty = get_val('warranty', 'Warranty', default='7 Days Replacement')
            delivery_days = get_val('deliveryDays', 'delivery_days', default='2-3 Days')

            is_out_of_stock_str = get_val('isOutOfStock', 'is_out_of_stock', default='').lower()
            is_out_of_stock = is_out_of_stock_str in ['true', '1', 'yes', 'y'] or stock <= 0

            is_hot_deal_str = get_val('isHotDeal', 'is_hot_deal', default='').lower()
            is_hot_deal = is_hot_deal_str in ['true', '1', 'yes', 'y']

            # If SKU is provided, search existing
            existing_prod = None
            if sku:
                existing_prod = Product.objects.filter(sku=sku).first()

            if not existing_prod and name:
                cand_slug = slugify(name)
                if cand_slug:
                    existing_prod = Product.objects.filter(slug=cand_slug).first()

            if existing_prod:
                # Update product
                if name: existing_prod.name = name
                if name_bn: existing_prod.nameBn = name_bn
                if category: existing_prod.category = category
                existing_prod.price = price
                existing_prod.regularPrice = regular_price
                existing_prod.stock = stock
                existing_prod.isOutOfStock = is_out_of_stock
                if images_list: existing_prod.images = images_list
                if short_desc: existing_prod.shortDescription = short_desc
                if short_desc_bn: existing_prod.shortDescriptionBn = short_desc_bn
                if desc: existing_prod.description = desc
                if desc_bn: existing_prod.descriptionBn = desc_bn
                if features_list: existing_prod.features = features_list
                if features_bn_list: existing_prod.featuresBn = features_bn_list
                if badge: existing_prod.badge = badge
                if warranty: existing_prod.warranty = warranty
                if delivery_days: existing_prod.deliveryDays = delivery_days
                existing_prod.isHotDeal = is_hot_deal

                existing_prod.save()
                updated_count += 1
            else:
                # Create new product
                final_name = name or name_bn
                generated_sku = sku or f"BD-{int(time.time() % 100000)}-{idx}"
                base_slug = slugify(name or name_bn) or f"prod-{int(time.time())}"
                final_slug = f"{base_slug}-{int(time.time() % 10000)}-{idx}"
                prod_id = f"prod-{int(time.time() * 1000)}-{idx}"

                new_p = Product(
                    id=prod_id,
                    name=final_name,
                    nameBn=name_bn or '',
                    slug=final_slug,
                    category=category,
                    price=price,
                    regularPrice=regular_price,
                    sku=generated_sku,
                    stock=stock,
                    isOutOfStock=is_out_of_stock,
                    images=images_list,
                    shortDescription=short_desc or '',
                    shortDescriptionBn=short_desc_bn or '',
                    description=desc or '',
                    descriptionBn=desc_bn or '',
                    features=features_list,
                    featuresBn=features_bn_list,
                    badge=badge or '',
                    warranty=warranty,
                    deliveryDays=delivery_days,
                    isHotDeal=is_hot_deal,
                    sortOrder=idx
                )
                new_p.save()
                created_count += 1

        except Exception as row_err:
            errors.append(f"Row #{idx}: {str(row_err)}")

    # Audit log
    AuditLog.log(
        actor='Admin',
        role='ADMIN',
        action='BULK_PRODUCT_IMPORT',
        target_type='PRODUCT',
        target_id='bulk-csv',
        details=f"Bulk imported {created_count} new and {updated_count} updated products via CSV ({len(errors)} errors)",
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    all_products = Product.objects.all().order_by('sortOrder', '-created_at')
    serializer = ProductSerializer(all_products, many=True)

    return Response({
        'success': True,
        'createdCount': created_count,
        'updatedCount': updated_count,
        'totalProcessed': created_count + updated_count,
        'errors': errors,
        'products': serializer.data,
        'message': f"Successfully created {created_count} new products and updated {updated_count} products."
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def bulk_delete_products(request):
    """
    Deletes multiple products by list of IDs: { "product_ids": ["prod-1", "prod-2", ...] }
    Also cleans up associated Cloudinary images and logs to AuditLog.
    """
    data = request.data or {}
    product_ids = data.get('product_ids', [])
    if not isinstance(product_ids, list) or not product_ids:
        return Response({'success': False, 'message': 'No product IDs provided.'}, status=status.HTTP_400_BAD_REQUEST)

    targets = Product.objects.filter(id__in=product_ids)
    deleted_count = 0
    deleted_names = []

    for prod in targets:
        deleted_names.append(prod.name)
        snap = snapshot_product(prod)
        prod.delete()
        move_to_recycle_bin(**snap, deleted_by='Admin')
        deleted_count += 1

    AuditLog.log(
        actor='Admin',
        role='ADMIN',
        action='BULK_PRODUCT_MOVED_TO_TRASH',
        target_type='PRODUCT',
        target_id='MULTIPLE',
        details=f'Bulk moved {deleted_count} products to Recycle Bin: {", ".join(deleted_names[:5])}{"..." if len(deleted_names) > 5 else ""}',
        ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1')
    )

    all_products = Product.objects.all().order_by('sortOrder', '-created_at')
    serializer = ProductSerializer(all_products, many=True)

    return Response({
        'success': True,
        'deletedCount': deleted_count,
        'message': f'Moved {deleted_count} products to Recycle Bin.',
        'products': serializer.data
    })




import os
import re
import uuid
import logging
from pathlib import Path
import cloudinary
import cloudinary.uploader
from django.conf import settings

logger = logging.getLogger(__name__)

def is_cloudinary_configured():
    url = os.getenv('CLOUDINARY_URL', '').strip()
    if url:
        return True
    cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME', '').strip()
    api_key = os.getenv('CLOUDINARY_API_KEY', '').strip()
    api_secret = os.getenv('CLOUDINARY_API_SECRET', '').strip()
    return bool(cloud_name and api_key and api_secret)

def configure_cloudinary():
    if not is_cloudinary_configured():
        return False
    url = os.getenv('CLOUDINARY_URL', '').strip()
    if url:
        # cloudinary handles CLOUDINARY_URL automatically
        return True
    cloudinary.config(
        cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
        api_key=os.getenv('CLOUDINARY_API_KEY'),
        api_secret=os.getenv('CLOUDINARY_API_SECRET'),
        secure=True
    )
    return True

def extract_public_id(val):
    if not val:
        return ""
    val = str(val).strip()
    if 'res.cloudinary.com' in val:
        m = re.search(r'/upload/(?:v\d+/)?(.+?)(?:\.[a-zA-Z0-9]+)?$', val)
        if m:
            return m.group(1)
    return val

def save_to_local_media(file_obj):
    """
    Saves an uploaded file to local MEDIA_ROOT/products directory.
    Returns standard metadata dict with local file URL and path.
    """
    media_dir = getattr(settings, 'MEDIA_ROOT', settings.BASE_DIR / 'media') / 'products'
    media_dir.mkdir(parents=True, exist_ok=True)
    
    orig_name = getattr(file_obj, 'name', 'upload.bin')
    ext = os.path.splitext(orig_name)[1] or '.jpg'
    filename = f"{uuid.uuid4().hex[:12]}{ext}"
    filepath = media_dir / filename
    
    if hasattr(file_obj, 'seek'):
        try:
            file_obj.seek(0)
        except Exception:
            pass

    with open(filepath, 'wb+') as destination:
        if hasattr(file_obj, 'chunks'):
            for chunk in file_obj.chunks():
                destination.write(chunk)
        else:
            destination.write(file_obj.read())
            
    relative_url = f"/media/products/{filename}"
    return {
        'success': True,
        'url': relative_url,
        'public_id': f"local_{filename}",
        'storage': 'local',
        'local_path': str(filepath),
        'format': ext.lstrip('.').lower(),
    }

def upload_image(file_obj, folder=None):
    """
    Upload an image or video either to Cloudinary (if configured) or to local media storage.
    If Cloudinary upload fails for any reason (e.g. rate limit, video format/size issue, network timeout),
    it automatically falls back to local media storage to ensure the user's action never fails.
    """
    if not folder:
        folder = os.getenv('CLOUDINARY_FOLDER', 'amardokan/products').strip() or "amardokan/products"
        
    file_name = getattr(file_obj, 'name', '').lower()
    content_type = getattr(file_obj, 'content_type', '').lower()
    is_video = content_type.startswith('video/') or file_name.endswith(('.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v'))

    if is_cloudinary_configured():
        configure_cloudinary()
        try:
            res_type = "video" if is_video else "auto"
            result = cloudinary.uploader.upload(
                file_obj,
                folder=folder,
                resource_type=res_type,
                use_filename=True,
                unique_filename=True
            )
            return {
                'success': True,
                'url': result.get('secure_url'),
                'public_id': result.get('public_id'),
                'storage': 'cloudinary',
                'format': result.get('format'),
                'width': result.get('width'),
                'height': result.get('height'),
            }
        except Exception as e:
            logger.warning(f"Cloudinary upload failed ({e}). Automatically falling back to local media storage.")
            fallback = save_to_local_media(file_obj)
            fallback['warning'] = f"Cloudinary upload failed: {str(e)}. Stored in local media."
            return fallback
    else:
        # Fallback to local storage if Cloudinary credentials are not configured
        fallback = save_to_local_media(file_obj)
        fallback['warning'] = 'Cloudinary not configured in .env; saved to local media storage.'
        return fallback

def delete_image(public_id_or_url):
    """
    Deletes an image from Cloudinary or local media storage.
    """
    if not public_id_or_url:
        return {'success': False, 'error': 'No image identifier or URL provided.'}

    public_id = extract_public_id(public_id_or_url)
    
    # Check if local image
    if str(public_id).startswith('local_') or '/media/products/' in str(public_id_or_url):
        filename = str(public_id).replace('local_', '') if str(public_id).startswith('local_') else Path(public_id_or_url).name
        media_dir = getattr(settings, 'MEDIA_ROOT', settings.BASE_DIR / 'media') / 'products'
        filepath = media_dir / filename
        if filepath.exists():
            try:
                filepath.unlink()
                return {'success': True, 'message': 'Local image file deleted successfully.', 'storage': 'local'}
            except Exception as e:
                return {'success': False, 'error': f"Failed to delete local file: {str(e)}", 'storage': 'local'}
        return {'success': True, 'message': 'File does not exist or already deleted.', 'storage': 'local'}

    # Otherwise it's a Cloudinary image
    if is_cloudinary_configured():
        configure_cloudinary()
        try:
            res = cloudinary.uploader.destroy(public_id, resource_type="image")
            status_result = res.get('result')
            # 'ok' or 'not found'
            return {
                'success': True,
                'result': status_result,
                'message': f"Image deleted from Cloudinary (result: {status_result})",
                'storage': 'cloudinary'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f"Cloudinary deletion failed: {str(e)}",
                'storage': 'cloudinary'
            }
    else:
        return {
            'success': False,
            'error': 'Cloudinary is not configured. Cannot delete remote image.',
            'storage': 'cloudinary'
        }

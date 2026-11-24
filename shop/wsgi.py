"""
WSGI config for amardokan_core project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os
import sys

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

application = get_wsgi_application()

# Optional auto-migration and explicit opt-in seeding on boot
try:
    from django.core.management import call_command

    if os.getenv('AUTO_MIGRATE_ON_BOOT', 'True').lower() in ('true', '1', 't'):
        print("[AmarDokan WSGI] Checking and applying database migrations...")
        call_command('migrate', interactive=False)
        print("[AmarDokan WSGI] Database migrations applied successfully.")

    # Never auto-seed demo products unless explicitly opted in via AUTO_SEED_ON_BOOT=True
    if os.getenv('AUTO_SEED_ON_BOOT', 'False').lower() in ('true', '1', 't'):
        try:
            from products.models import Product
            if Product.objects.count() == 0:
                print("[AmarDokan WSGI] AUTO_SEED_ON_BOOT=True & 0 products found. Running seed_data...")
                call_command('seed_data', interactive=False)
                print("[AmarDokan WSGI] Initial demo data seeded successfully.")
        except Exception as seed_err:
            print(f"[AmarDokan WSGI] Notice on seed data: {seed_err}", file=sys.stderr)

except Exception as err:
    print(f"[AmarDokan WSGI] Warning: Auto-migration on boot: {err}", file=sys.stderr)

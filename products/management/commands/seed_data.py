import os
from django.core.management.base import BaseCommand
from products.models import Product, Coupon, Review
from couriers.models import CourierConfig
from order.models import Order
from core.models import AuditLog, MarketingEvent, NotificationLog

class Command(BaseCommand):
    help = 'Seeds initial AmarDokan database with Bangladesh native products, couriers, and configs'

    def handle(self, *args, **kwargs):
        self.stdout.write("Ensuring core demo products exist...")

        products_data = [
            {
                'id': 'prod-1',
                'name': '360° Rotating Makeup Brush Organizer',
                'nameBn': '360° Rotating Makeup Brush & Cosmetic Organizer',
                'slug': '360-rotating-makeup-brush-organizer',
                'category': 'Beauty & Organizer',
                'price': 790,
                'regularPrice': 1250,
                'sku': 'ORG-360-BRUSH',
                'stock': 35,
                'badge': 'HOT',
                'images': [
                    '/src/assets/rotating_makeup_brush_holder.jpg',
                ],
                'description': 'Luxury crystal-clear makeup organizer with 360° silent rotation and airtight dustproof cover. Gives your vanity table an exquisite aesthetic look.',
                'features': [
                    '360° smooth silent ball-bearing rotation',
                    'Airtight dustproof and waterproof transparent acrylic dome',
                    '7 separate slots for all makeup brush sizes and cosmetic tools',
                    'Premium shatterproof ABS and crystal dome finish',
                ],
                'variants': [
                    {'name': 'Color', 'options': ['Classic White', 'Royal Emerald', 'Rose Pink'], 'stock': 35},
                ],
                'rating': 4.9,
                'reviewsCount': 148,
                'isHotDeal': True,
                'warranty': '7-Day Replacement Guarantee',
                'deliveryDays': '24-48 Hours Express Delivery',
            },
            {
                'id': 'prod-2',
                'name': 'Vintage Rechargeable Retro Camping Lantern',
                'nameBn': 'Vintage Rechargeable Retro Camping Lantern',
                'slug': 'retro-rechargeable-camping-lantern',
                'category': 'Lifestyle & Outdoor',
                'price': 990,
                'regularPrice': 1550,
                'sku': 'LGT-RETRO-CAMP',
                'stock': 28,
                'badge': 'TOP RATED',
                'images': [
                    '/src/assets/retro_camping_lantern.jpg',
                ],
                'description': 'A seamless blend of vintage aesthetic and warm LED lighting. Features stepless rotary dimming and USB-C rechargeable battery.',
                'features': [
                    'Comfortable soft warm LED filament light gentle on the eyes',
                    'Stepless smooth rotary dimmer knob for brightness control',
                    'USB Type-C fast charging with extended battery backup',
                    'Matte scarlet red finish with heavy-duty metal hanging hook',
                ],
                'variants': [
                    {'name': 'Color', 'options': ['Vintage Red', 'Metallic Black', 'Army Green'], 'stock': 28},
                ],
                'rating': 4.8,
                'reviewsCount': 92,
                'isHotDeal': True,
                'warranty': '7-Day Replacement Guarantee',
                'deliveryDays': '24-48 Hours Home Delivery',
            },
            {
                'id': 'prod-3',
                'name': '7-in-1 Professional Nail & Manicure/Pedicure Kit',
                'nameBn': '7-in-1 Professional Manicure & Pedicure Grooming Kit',
                'slug': '7-in-1-nail-manicure-pedicure-kit',
                'category': 'Personal Care',
                'price': 490,
                'regularPrice': 850,
                'sku': 'KIT-7IN1-NAIL',
                'stock': 50,
                'badge': 'VALUE DEAL',
                'images': [
                    '/src/assets/7in1_nail_manicure_kit.jpg',
                ],
                'description': '7-in-1 personal nail grooming set made from surgical-grade stainless steel with ultra-sharp blades and luxury travel case.',
                'features': [
                    '7 essential professional grooming tools in one compact case',
                    '100% rust-resistant premium surgical stainless steel',
                    'Ultra-sharp precision blades for smooth pain-free trimming',
                    'Luxury leatherette travel case for easy portability',
                ],
                'variants': [
                    {'name': 'Case Color', 'options': ['Royal Maroon', 'Classic Cream', 'Lavender'], 'stock': 50},
                ],
                'rating': 4.8,
                'reviewsCount': 112,
                'isHotDeal': True,
                'warranty': '7-Day Replacement Guarantee',
                'deliveryDays': '24-48 Hours Home Delivery',
            },
            {
                'id': 'prod-4',
                'name': 'Deep Tissue Electric Massage Gun',
                'nameBn': 'Deep Tissue Electric Massage Gun',
                'slug': 'facial-massage-gun-light-age',
                'category': 'Health & Wellness',
                'price': 1550,
                'regularPrice': 2400,
                'sku': 'MSG-GUN-LGT',
                'stock': 20,
                'badge': 'BESTSELLER',
                'images': [
                    '/src/assets/facial_massage_gun_light_age.jpg',
                ],
                'description': 'Ergonomic deep tissue massager featuring a powerful silent brushless motor, 6-level speed control, and 4 interchangeable massage heads.',
                'features': [
                    '24W high-torque super silent brushless motor technology',
                    '6 adjustable speed and vibration intensity levels',
                    '4 multifunctional interchangeable massage heads (Ball, Fork, Flat, Bullet)',
                    'Type-C fast charging and long-lasting cordless portability',
                ],
                'variants': [
                    {'name': 'Color', 'options': ['Ivory Cream', 'Classic Black'], 'stock': 20},
                ],
                'rating': 4.9,
                'reviewsCount': 88,
                'isHotDeal': True,
                'warranty': '7-Day Replacement & 1-Year Service Warranty',
                'deliveryDays': '24-48 Hours Express Delivery',
            },
        ]

        for p in products_data:
            Product.objects.update_or_create(id=p['id'], defaults=p)

        self.stdout.write("Seeding Coupons...")
        coupons = [
            {'code': 'WELCOME50', 'discountType': 'fixed', 'value': 50, 'minSpend': 500, 'isActive': True},
            {'code': 'EID100', 'discountType': 'fixed', 'value': 100, 'minSpend': 1200, 'isActive': True},
            {'code': 'SAVE10', 'discountType': 'percentage', 'value': 10, 'minSpend': 2000, 'isActive': True},
        ]
        for c in coupons:
            Coupon.objects.update_or_create(code=c['code'], defaults=c)

        self.stdout.write("Seeding Courier Configs...")
        couriers = [
            {
                'id': 'pathao',
                'name': 'Pathao Courier',
                'logo': 'Pathao',
                'isActive': True,
                'apiKey': os.getenv('PATHAO_CLIENT_ID', ''),
                'secret': os.getenv('PATHAO_CLIENT_SECRET', ''),
                'extraCredentials': {'sandboxMode': False},
                'coverage': '64 Districts (Full BD)',
                'codFeePercent': 1.0,
                'insideDhakaRate': 60,
                'outsideDhakaRate': 130,
                'lastSyncTime': 'Not synced yet',
                'pendingCodAmount': 0,
                'settledCodAmount': 0,
            },
            {
                'id': 'steadfast',
                'name': 'Steadfast Courier',
                'logo': 'Steadfast',
                'isActive': True,
                'apiKey': os.getenv('STEADFAST_API_KEY', ''),
                'secret': os.getenv('STEADFAST_SECRET_KEY', ''),
                'extraCredentials': {'sandboxMode': False},
                'coverage': 'Nationwide & Remote Upazila',
                'codFeePercent': 1.0,
                'insideDhakaRate': 60,
                'outsideDhakaRate': 120,
                'lastSyncTime': 'Not synced yet',
                'pendingCodAmount': 0,
                'settledCodAmount': 0,
            },
            {
                'id': 'redx',
                'name': 'RedX Logistics',
                'logo': 'RedX',
                'isActive': True,
                'apiKey': os.getenv('REDX_API_TOKEN', ''),
                'secret': os.getenv('REDX_SECRET_KEY', ''),
                'extraCredentials': {'sandboxMode': False},
                'coverage': 'All Metros & District Towns',
                'codFeePercent': 1.0,
                'insideDhakaRate': 65,
                'outsideDhakaRate': 130,
                'lastSyncTime': 'Not synced yet',
                'pendingCodAmount': 0,
                'settledCodAmount': 0,
            },
            {
                'id': 'ecourier',
                'name': 'eCourier Limited',
                'logo': 'eCourier',
                'isActive': False,
                'apiKey': os.getenv('ECOURIER_API_KEY', ''),
                'secret': os.getenv('ECOURIER_API_SECRET', ''),
                'extraCredentials': {'sandboxMode': False},
                'coverage': 'Nationwide Corporate Hubs',
                'codFeePercent': 1.5,
                'insideDhakaRate': 70,
                'outsideDhakaRate': 140,
                'lastSyncTime': 'Not synced yet',
                'pendingCodAmount': 0,
                'settledCodAmount': 0,
            },
        ]
        for cr in couriers:
            CourierConfig.objects.get_or_create(id=cr['id'], defaults=cr)

        self.stdout.write("Seeding Initial Order...")
        order_data = {
            'id': 'BD-849201',
            'trackingToken': 'tr_849201_99a',
            'createdAt': '2026-09-22 09:30:15',
            'items': [
                {
                    'id': 'item-1',
                    'productId': 'prod-1',
                    'name': 'T900 Ultra 2 Smartwatch with Gesture Control',
                    'nameBn': 'T900 Ultra 2 Smartwatch',
                    'sku': 'SMW-T900-BLK',
                    'variant': 'Orange Strap',
                    'price': 1850,
                    'quantity': 1,
                    'image': 'https://images.unsplash.com/photo-1579586337278-3befd40fd17a?auto=format&fit=crop&w=400&q=80',
                },
            ],
            'address': {
                'customerName': 'Zakir Hossain',
                'phone': '01712849201',
                'district': 'Dhaka',
                'thana': 'Mirpur-10',
                'fullAddress': 'House 14, Road 5, Block C, Section 10, Mirpur, Dhaka',
                'deliveryZone': 'inside_dhaka',
                'orderNote': 'Call before delivery please',
            },
            'subtotal': 1850,
            'deliveryFee': 60,
            'discount': 0,
            'total': 1910,
            'paymentMethod': 'COD',
            'paymentStatus': 'UNPAID',
            'status': 'COURIER_CREATED',
            'statusHistory': [
                {'status': 'PENDING', 'timestamp': '2026-09-22 09:30:15', 'note': 'Customer placed guest order'},
                {'status': 'CONFIRMED', 'timestamp': '2026-09-22 09:42:00', 'note': 'Address verified via phone call'},
                {'status': 'COURIER_CREATED', 'timestamp': '2026-09-22 10:00:22', 'note': 'Steadfast consignment booked'},
            ],
            'courier': {
                'provider': 'steadfast',
                'trackingId': 'STDF-88492019',
                'consignmentId': 'CNS-9481920',
                'courierStatus': 'Pickup Request Accepted',
                'createdDate': '2026-09-22 10:00 AM',
                'deliveryFee': 60,
                'codAmount': 1910,
                'trackingUrl': 'https://steadfast.com.bd/tracking/STDF-88492019',
                'assignedRiderName': 'Ashraful Alam',
                'assignedRiderPhone': '01811234910',
            },
            'riskScore': 12,
            'riskFactors': ['Verified Grameenphone SIM'],
            'internalNotes': ['Customer confirmed availability tomorrow afternoon.'],
        }
        Order.objects.update_or_create(id=order_data['id'], defaults=order_data)

        self.stdout.write("Seeding Audit Logs & Notifications...")
        AuditLog.objects.get_or_create(
            id='aud-1',
            defaults={
                'timestamp': '2026-09-22 10:45:12',
                'actor': 'System / Webhook',
                'role': 'SYSTEM',
                'action': 'COURIER_STATUS_UPDATE',
                'targetType': 'ORDER',
                'targetId': 'BD-849188',
                'details': 'Pathao Webhook status changed to DELIVERED. COD collection: ৳2,250.',
                'ipAddress': '103.114.98.22',
            }
        )

        NotificationLog.objects.get_or_create(
            id='notif-1',
            defaults={
                'orderId': 'BD-849201',
                'channel': 'SMS',
                'recipient': '01712849201',
                'templateName': 'ORDER_CONFIRMED',
                'messageText': 'AmarDokan: Dear Zakir Hossain, order #BD-849201 of ৳1,910 is confirmed! Your parcel is being packed. Helpline: 09612345678',
                'status': 'DELIVERED',
                'timestamp': '2026-09-22 09:42:10',
                'provider': 'Greenweb_SMS',
            }
        )


        self.stdout.write("Ensuring Django Admin Superuser exists...")
        admin_username = os.getenv('ADMIN_USER', 'admin')
        admin_password = os.getenv('ADMIN_PASS', '').strip()
        admin_email = os.getenv('ADMIN_EMAIL', '')

        if not admin_password:
            self.stdout.write(self.style.WARNING("ADMIN_PASS not set; skipping default superuser password initialization."))
            return

        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        user, created = User.objects.get_or_create(
            username=admin_username,
            defaults={
                'email': admin_email,
                'is_staff': True,
                'is_superuser': True,
            }
        )
        user.set_password(admin_password)
        user.is_staff = True
        user.is_superuser = True
        user.save()
        if created:
            self.stdout.write(self.style.SUCCESS(f"Superuser '{admin_username}' created successfully!"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Superuser '{admin_username}' credentials updated successfully!"))

        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully!"))

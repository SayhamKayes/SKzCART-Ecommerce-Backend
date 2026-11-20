import uuid
from django.db import models
from django.utils import timezone

class AuditLog(models.Model):
    ROLE_CHOICES = [
        ('ADMIN', 'Admin'),
        ('OPERATIONS', 'Operations'),
        ('MARKETING', 'Marketing'),
        ('SYSTEM', 'System'),
    ]
    TARGET_TYPE_CHOICES = [
        ('ORDER', 'Order'),
        ('PRODUCT', 'Product'),
        ('COURIER', 'Courier'),
        ('PAYMENT', 'Payment'),
        ('SETTINGS', 'Settings'),
    ]

    id = models.CharField(max_length=64, primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.CharField(max_length=50)
    actor = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='SYSTEM')
    action = models.CharField(max_length=100)
    targetType = models.CharField(max_length=50, choices=TARGET_TYPE_CHOICES)
    targetId = models.CharField(max_length=100)
    details = models.TextField()
    ipAddress = models.CharField(max_length=50, default='127.0.0.1')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def log(cls, actor='SYSTEM', role='SYSTEM', action='UNKNOWN', target_type='SYSTEM', target_id='', details='', ip_address='127.0.0.1'):
        now_str = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')
        log_id = f"aud-{int(timezone.now().timestamp() * 1000)}-{uuid.uuid4().hex[:4]}"
        return cls.objects.create(
            id=log_id,
            timestamp=now_str,
            actor=actor,
            role=role,
            action=action,
            targetType=target_type,
            targetId=target_id,
            details=details,
            ipAddress=ip_address
        )


class MarketingEvent(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    eventId = models.CharField(max_length=100, blank=True, null=True)
    eventName = models.CharField(max_length=100)
    timestamp = models.CharField(max_length=50)
    browserReported = models.BooleanField(default=False)
    serverCapiReported = models.BooleanField(default=True)
    source = models.CharField(max_length=100, default='direct')
    campaign = models.CharField(max_length=100, blank=True, null=True)
    value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    orderId = models.CharField(max_length=100, blank=True, null=True)
    productName = models.CharField(max_length=255, blank=True, null=True)
    productId = models.CharField(max_length=100, blank=True, null=True)
    dedupMatched = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class NotificationLog(models.Model):
    CHANNEL_CHOICES = [
        ('SMS', 'SMS'),
        ('WHATSAPP', 'WhatsApp'),
        ('EMAIL', 'Email'),
    ]
    STATUS_CHOICES = [
        ('SENT', 'Sent'),
        ('DELIVERED', 'Delivered'),
        ('FAILED', 'Failed'),
    ]

    id = models.CharField(max_length=64, primary_key=True)
    orderId = models.CharField(max_length=100)
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    recipient = models.CharField(max_length=100)
    templateName = models.CharField(max_length=100)
    messageText = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SENT')
    timestamp = models.CharField(max_length=50)
    provider = models.CharField(max_length=100, default='Greenweb_SMS')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class MarketingConfig(models.Model):
    SCOPE_CHOICES = [
        ('GLOBAL', 'Global (Entire Store)'),
        ('PRODUCT', 'Specific Product'),
        ('LANDING', 'Specific Landing Page'),
    ]

    id = models.CharField(max_length=64, primary_key=True, default='default_config')
    pixelId = models.CharField(max_length=100, blank=True, default='')
    capiToken = models.TextField(blank=True, default='')
    ga4Id = models.CharField(max_length=100, blank=True, default='')
    testEventCode = models.CharField(max_length=100, blank=True, default='')
    adAccountId = models.CharField(max_length=100, blank=True, default='')
    targetScope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default='GLOBAL')
    targetProductId = models.CharField(max_length=100, blank=True, default='')
    targetLandingSlug = models.CharField(max_length=100, blank=True, default='')
    isActive = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"MarketingConfig({self.targetScope}) - Pixel: {self.pixelId}"


class ShopSetting(models.Model):
    WHATSAPP_CHOICES = [
        ('META_CLOUD', 'Meta WhatsApp Cloud API'),
        ('ULTRAMSG', 'UltraMsg WhatsApp API'),
    ]

    id = models.CharField(max_length=64, primary_key=True, default='default_shop_setting')
    shopName = models.CharField(max_length=200, default='AmarDokan')
    shopTagline = models.TextField(blank=True, default='The #1 Trusted Premium Gadget & Lifestyle Shop in Bangladesh')
    hotline = models.CharField(max_length=50, default='09612345678')
    whatsappNumber = models.CharField(max_length=50, default='01712849201')
    supportEmail = models.CharField(max_length=150, default='support@amardokan.com')
    address = models.TextField(blank=True, default='AmarDokan Head Office, Dhanmondi 27, Dhaka, Bangladesh')
    logoUrl = models.TextField(blank=True, default='')
    faviconUrl = models.TextField(blank=True, default='')
    headerLogoUrl = models.TextField(blank=True, default='')
    footerLogoUrl = models.TextField(blank=True, default='')
    extraConfig = models.JSONField(default=dict, blank=True)

    # WhatsApp Gateway Configuration
    whatsappProvider = models.CharField(max_length=30, choices=WHATSAPP_CHOICES, default='META_CLOUD')
    whatsappToken = models.TextField(blank=True, default='')
    whatsappPhoneNumberId = models.CharField(max_length=100, blank=True, default='')
    whatsappInstanceId = models.CharField(max_length=100, blank=True, default='')
    adminAlertPhone = models.CharField(max_length=50, blank=True, default='')
    whatsappEnabled = models.BooleanField(default=True)

    # SMTP Email Gateway Configuration
    emailHost = models.CharField(max_length=150, default='smtp.gmail.com', blank=True)
    emailPort = models.IntegerField(default=587)
    emailUseTls = models.BooleanField(default=True)
    emailUseSsl = models.BooleanField(default=False)
    emailHostUser = models.CharField(max_length=150, blank=True, default='')
    emailHostPassword = models.CharField(max_length=255, blank=True, default='')
    defaultFromEmail = models.CharField(max_length=200, blank=True, default='AmarDokan <orders@amardokan.com>')
    emailEnabled = models.BooleanField(default=True)
    # Shop Language & Translation Configuration
    LANGUAGE_MODE_CHOICES = [
        ('en', 'English Only'),
    ]
    shopLanguageMode = models.CharField(max_length=10, choices=LANGUAGE_MODE_CHOICES, default='en')
    defaultLanguage = models.CharField(max_length=5, default='en', choices=[('en', 'English')])

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"ShopSetting: {self.shopName}"

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(
            id='default_shop_setting',
            defaults={
                'shopName': 'AmarDokan',
                'shopTagline': 'The #1 Trusted Premium Gadget & Lifestyle Shop in Bangladesh',
                'hotline': '09612345678',
                'whatsappNumber': '01712849201',
                'supportEmail': 'support@amardokan.com',
                'address': 'AmarDokan Head Office, Dhanmondi 27, Dhaka, Bangladesh',
                'whatsappProvider': 'META_CLOUD',
                'whatsappEnabled': True,
                'shopLanguageMode': 'en',
                'defaultLanguage': 'en'
            }
        )
        return obj


class StaffMember(models.Model):
    ROLE_CHOICES = [
        ('ADMIN', 'Super Admin'),
        ('MANAGER', 'Operations Manager'),
        ('DISPATCHER', 'Logistics Dispatcher'),
    ]
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('SUSPENDED', 'Suspended'),
    ]

    id = models.CharField(max_length=64, primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    username = models.CharField(max_length=100, unique=True)
    phone = models.CharField(max_length=50, blank=True, default='')
    email = models.CharField(max_length=150, blank=True, default='')
    password = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='MANAGER')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.name} (@{self.username}) - {self.role}"

    @classmethod
    def seed_defaults_if_empty(cls):
        if cls.objects.count() == 0:
            cls.objects.create(
                id='staff-admin-root',
                name='Super Admin (Owner)',
                username='admin',
                phone='01712849201',
                email='admin@amardokan.com',
                password='admin1234',
                role='ADMIN',
                status='ACTIVE',
                notes='Primary store owner with full unrestricted authority'
            )
            cls.objects.create(
                id='staff-manager-demo',
                name='Operations Manager',
                username='manager',
                phone='01812345678',
                email='manager@amardokan.com',
                password='manager1234',
                role='MANAGER',
                status='ACTIVE',
                notes='Manages daily operations, stock, orders, and customer queries'
            )
            cls.objects.create(
                id='staff-dispatcher-demo',
                name='Delivery Dispatcher (Logistics)',
                username='dispatcher',
                phone='01912345678',
                email='dispatcher@amardokan.com',
                password='dispatcher1234',
                role='DISPATCHER',
                status='ACTIVE',
                notes='Fulfills orders, prints packing slips, and hands over to Steadfast/Pathao/RedX'
            )


class RecycleBinItem(models.Model):
    """Central database recycle bin storing snapshots of soft-deleted admin items for cross-device recovery or permanent purge."""
    ITEM_TYPE_CHOICES = [
        ('ORDER', 'Order'),
        ('PRODUCT', 'Product'),
        ('REVIEW', 'Customer Review'),
        ('MEDIA', 'Media Library'),
        ('COUPON', 'Coupon'),
        ('STAFF', 'Staff Member'),
        ('SLIDER', 'Hero Slider'),
        ('AUDIT_LOG', 'Security Audit Log'),
        ('PAYMENT_TRANSACTION', 'Gateway Transaction'),
    ]

    id = models.CharField(max_length=64, primary_key=True)
    item_type = models.CharField(max_length=32, choices=ITEM_TYPE_CHOICES, db_index=True)
    original_id = models.CharField(max_length=128, db_index=True)
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=255, blank=True, default='')
    thumbnail_url = models.TextField(blank=True, default='')
    payload = models.JSONField(default=dict)
    deleted_by = models.CharField(max_length=150, default='Admin')
    deleted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-deleted_at']

    def __str__(self):
        return f"[{self.item_type}] {self.title} ({self.original_id})"


class StoreTenant(models.Model):
    """Multi-tenant SaaS Seller Store entity allowing isolated Meta Ads & Page Boost management per seller."""
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('SUSPENDED', 'Suspended'),
    ]
    PLAN_CHOICES = [
        ('STARTER', 'Starter'),
        ('PRO', 'Pro'),
        ('ENTERPRISE', 'Enterprise'),
    ]

    id = models.CharField(max_length=64, primary_key=True)
    tenantId = models.CharField(max_length=64, unique=True, db_index=True)
    storeName = models.CharField(max_length=200)
    domainUrl = models.CharField(max_length=255, blank=True, default='')
    ownerName = models.CharField(max_length=150, blank=True, default='')
    ownerEmail = models.CharField(max_length=150, blank=True, default='')
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='PRO')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.storeName} ({self.tenantId})"


class MetaTenantConnection(models.Model):
    """Encrypted, tenant-isolated Meta OAuth / System User credentials, Ad Account billing health, and Page/Pixel bindings."""
    TOKEN_TYPE_CHOICES = [
        ('OAUTH', 'Facebook OAuth User Token'),
        ('SYSTEM_USER', 'Business System User Token'),
    ]

    id = models.CharField(max_length=64, primary_key=True)
    tenantId = models.CharField(max_length=64, unique=True, db_index=True)
    metaAppId = models.CharField(max_length=100, blank=True, default='')
    metaUserId = models.CharField(max_length=100, blank=True, default='')
    metaUserName = models.CharField(max_length=200, blank=True, default='')

    accessTokenEncrypted = models.TextField(blank=True, default='')
    tokenMasked = models.CharField(max_length=64, blank=True, default='')
    tokenType = models.CharField(max_length=20, choices=TOKEN_TYPE_CHOICES, default='OAUTH')
    tokenExpiresAt = models.DateTimeField(null=True, blank=True)
    grantedScopes = models.JSONField(default=list, blank=True)

    # Bound Meta Assets for this Seller Store
    adAccountId = models.CharField(max_length=100, blank=True, default='')
    adAccountName = models.CharField(max_length=200, blank=True, default='')
    adAccountCurrency = models.CharField(max_length=16, blank=True, default='BDT')
    adAccountStatus = models.CharField(max_length=50, blank=True, default='UNKNOWN')
    billingStatus = models.CharField(max_length=50, blank=True, default='UNVERIFIED')
    billingAlertMessage = models.TextField(blank=True, default='')
    fundingSourceSummary = models.CharField(max_length=255, blank=True, default='')
    spendCap = models.FloatField(default=0)
    amountSpent = models.FloatField(default=0)
    accountBalance = models.FloatField(default=0)
    isSandboxMode = models.BooleanField(default=False)

    pageId = models.CharField(max_length=100, blank=True, default='')
    pageName = models.CharField(max_length=200, blank=True, default='')
    pageCategory = models.CharField(max_length=150, blank=True, default='')
    pageAccessTokenEncrypted = models.TextField(blank=True, default='')
    instagramActorId = models.CharField(max_length=100, blank=True, default='')
    pixelId = models.CharField(max_length=100, blank=True, default='')

    availableAdAccounts = models.JSONField(default=list, blank=True)
    availablePages = models.JSONField(default=list, blank=True)
    availablePixels = models.JSONField(default=list, blank=True)

    isConnected = models.BooleanField(default=False)
    lastError = models.TextField(blank=True, default='')
    lastSyncedAt = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"MetaTenantConnection({self.tenantId}) - {self.adAccountId or 'Unbound'}"


class SaaSAdCampaign(models.Model):
    """Tenant-isolated Meta Ad Campaign synced two-way with Meta Graph API and store UTM attribution."""
    CAMPAIGN_TYPE_CHOICES = [
        ('PRODUCT_SALES', 'Product Sales Campaign'),
        ('QUICK_BOOST_PRODUCT', 'Quick Product Boost'),
        ('QUICK_BOOST_POST', 'Facebook Page Post Boost'),
        ('TRAFFIC', 'Website Traffic'),
        ('MESSAGES', 'Page Messages'),
    ]

    id = models.CharField(max_length=64, primary_key=True)
    tenantId = models.CharField(max_length=64, db_index=True, default='default')
    metaCampaignId = models.CharField(max_length=100, blank=True, default='', db_index=True)
    adAccountId = models.CharField(max_length=100, blank=True, default='')
    source = models.CharField(max_length=30, default='facebook')
    campaignType = models.CharField(max_length=30, choices=CAMPAIGN_TYPE_CHOICES, default='PRODUCT_SALES')

    name = models.CharField(max_length=255)
    objective = models.CharField(max_length=60, default='OUTCOME_SALES')
    status = models.CharField(max_length=30, default='PAUSED')
    effectiveStatus = models.CharField(max_length=50, default='PAUSED')

    productId = models.CharField(max_length=100, blank=True, default='')
    productName = models.CharField(max_length=255, blank=True, default='')
    postId = models.CharField(max_length=150, blank=True, default='')
    landingUrl = models.TextField(blank=True, default='')
    utmCampaign = models.CharField(max_length=150, blank=True, default='', db_index=True)

    dailyBudget = models.FloatField(default=0)
    lifetimeBudget = models.FloatField(default=0)
    currency = models.CharField(max_length=16, default='BDT')

    # Meta Delivery & Billing Guard feedback
    billingGuardAlert = models.TextField(blank=True, default='')
    deliveryStatusDetail = models.TextField(blank=True, default='')

    # Live Meta Insights + Store Order Attribution
    spend = models.FloatField(default=0)
    impressions = models.IntegerField(default=0)
    reach = models.IntegerField(default=0)
    clicks = models.IntegerField(default=0)
    ctr = models.FloatField(default=0)
    cpc = models.FloatField(default=0)
    cpm = models.FloatField(default=0)
    metaPurchases = models.IntegerField(default=0)
    storeOrders = models.IntegerField(default=0)
    storeRevenue = models.FloatField(default=0)
    roas = models.FloatField(default=0)

    isRemoteSynced = models.BooleanField(default=False)
    lastSyncedAt = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.tenantId}] {self.name} ({self.effectiveStatus})"


class SaaSAdSet(models.Model):
    """Tenant-isolated Meta Ad Set belonging to a SaaSAdCampaign."""
    id = models.CharField(max_length=64, primary_key=True)
    tenantId = models.CharField(max_length=64, db_index=True, default='default')
    campaign = models.ForeignKey(SaaSAdCampaign, related_name='adsets', on_delete=models.CASCADE)
    metaAdSetId = models.CharField(max_length=100, blank=True, default='', db_index=True)

    name = models.CharField(max_length=255)
    status = models.CharField(max_length=30, default='PAUSED')
    effectiveStatus = models.CharField(max_length=50, default='PAUSED')
    optimizationGoal = models.CharField(max_length=60, default='OFFSITE_CONVERSIONS')
    billingEvent = models.CharField(max_length=40, default='IMPRESSIONS')

    dailyBudget = models.FloatField(default=0)
    lifetimeBudget = models.FloatField(default=0)
    startTime = models.DateTimeField(null=True, blank=True)
    endTime = models.DateTimeField(null=True, blank=True)
    targeting = models.JSONField(default=dict, blank=True)
    estimatedReachMin = models.IntegerField(default=0)
    estimatedReachMax = models.IntegerField(default=0)

    spend = models.FloatField(default=0)
    impressions = models.IntegerField(default=0)
    reach = models.IntegerField(default=0)
    clicks = models.IntegerField(default=0)
    ctr = models.FloatField(default=0)
    cpc = models.FloatField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']


class SaaSAdCreativeAd(models.Model):
    """Tenant-isolated Meta Creative & Ad node with policy review feedback."""
    id = models.CharField(max_length=64, primary_key=True)
    tenantId = models.CharField(max_length=64, db_index=True, default='default')
    adset = models.ForeignKey(SaaSAdSet, related_name='ads', on_delete=models.CASCADE)
    metaCreativeId = models.CharField(max_length=100, blank=True, default='')
    metaAdId = models.CharField(max_length=100, blank=True, default='', db_index=True)

    name = models.CharField(max_length=255)
    status = models.CharField(max_length=30, default='PAUSED')
    effectiveStatus = models.CharField(max_length=50, default='PAUSED')

    primaryText = models.TextField(blank=True, default='')
    headline = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')
    callToAction = models.CharField(max_length=40, default='SHOP_NOW')
    imageUrl = models.TextField(blank=True, default='')
    imageHash = models.CharField(max_length=150, blank=True, default='')
    linkUrl = models.TextField(blank=True, default='')
    objectStoryId = models.CharField(max_length=150, blank=True, default='')
    reviewFeedback = models.JSONField(default=dict, blank=True)

    spend = models.FloatField(default=0)
    impressions = models.IntegerField(default=0)
    reach = models.IntegerField(default=0)
    clicks = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']



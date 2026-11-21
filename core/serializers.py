from rest_framework import serializers
from .models import (
    AuditLog, MarketingEvent, NotificationLog, MarketingConfig, ShopSetting,
    StaffMember, RecycleBinItem, StoreTenant, MetaTenantConnection,
    SaaSAdCampaign, SaaSAdSet, SaaSAdCreativeAd, MarketingUtmCampaign
)

class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ['id', 'timestamp', 'actor', 'role', 'action', 'targetType', 'targetId', 'details', 'ipAddress']

class MarketingEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketingEvent
        fields = ['id', 'eventId', 'eventName', 'timestamp', 'browserReported', 'serverCapiReported', 'source', 'campaign', 'value', 'orderId', 'productName', 'productId', 'dedupMatched', 'created_at']

class NotificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = ['id', 'orderId', 'channel', 'recipient', 'templateName', 'messageText', 'status', 'timestamp', 'provider']

class MarketingConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketingConfig
        fields = [
            'id', 'pixelId', 'capiToken', 'ga4Id', 'testEventCode', 'adAccountId',
            'targetScope', 'targetProductId', 'targetLandingSlug',
            'isActive', 'updated_at'
        ]

class ShopSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopSetting
        fields = [
            'id', 'shopName', 'shopTagline', 'hotline', 'whatsappNumber',
            'supportEmail', 'address', 'logoUrl', 'faviconUrl',
            'headerLogoUrl', 'footerLogoUrl', 'extraConfig',
            'whatsappProvider', 'whatsappToken', 'whatsappPhoneNumberId',
            'whatsappInstanceId', 'adminAlertPhone', 'whatsappEnabled',
            'emailHost', 'emailPort', 'emailUseTls', 'emailUseSsl',
            'emailHostUser', 'emailHostPassword', 'defaultFromEmail', 'emailEnabled',
            'shopLanguageMode', 'defaultLanguage',
            'updated_at'
        ]

class StaffMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffMember
        fields = [
            'id', 'name', 'username', 'phone', 'email', 'role',
            'status', 'notes', 'created_at', 'updated_at'
        ]


class RecycleBinItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecycleBinItem
        fields = [
            'id', 'item_type', 'original_id', 'title', 'subtitle',
            'thumbnail_url', 'payload', 'deleted_by', 'deleted_at'
        ]


class StoreTenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreTenant
        fields = [
            'id', 'tenantId', 'storeName', 'domainUrl', 'ownerName',
            'ownerEmail', 'plan', 'status', 'created_at', 'updated_at'
        ]


class MetaTenantConnectionSerializer(serializers.ModelSerializer):
    hasAccessToken = serializers.SerializerMethodField()
    hasPageToken = serializers.SerializerMethodField()

    class Meta:
        model = MetaTenantConnection
        # Strictly exclude encrypted access tokens from API responses
        fields = [
            'id', 'tenantId', 'metaAppId', 'metaUserId', 'metaUserName',
            'hasAccessToken', 'hasPageToken', 'tokenMasked', 'tokenType',
            'tokenExpiresAt', 'grantedScopes', 'adAccountId', 'adAccountName',
            'adAccountCurrency', 'adAccountStatus', 'billingStatus',
            'billingAlertMessage', 'fundingSourceSummary', 'spendCap',
            'amountSpent', 'accountBalance', 'isSandboxMode',
            'pageId', 'pageName', 'pageCategory', 'instagramActorId', 'pixelId',
            'availableAdAccounts', 'availablePages', 'availablePixels',
            'isConnected', 'lastError', 'lastSyncedAt', 'updated_at'
        ]

    def get_hasAccessToken(self, obj):
        return bool(obj.accessTokenEncrypted)

    def get_hasPageToken(self, obj):
        return bool(obj.pageAccessTokenEncrypted)


class SaaSAdCreativeAdSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaaSAdCreativeAd
        fields = [
            'id', 'tenantId', 'adset', 'metaCreativeId', 'metaAdId', 'name',
            'status', 'effectiveStatus', 'primaryText', 'headline', 'description',
            'callToAction', 'imageUrl', 'imageHash', 'linkUrl', 'objectStoryId',
            'reviewFeedback', 'spend', 'impressions', 'reach', 'clicks',
            'created_at', 'updated_at'
        ]


class SaaSAdSetSerializer(serializers.ModelSerializer):
    ads = SaaSAdCreativeAdSerializer(many=True, read_only=True)

    class Meta:
        model = SaaSAdSet
        fields = [
            'id', 'tenantId', 'campaign', 'metaAdSetId', 'name', 'status',
            'effectiveStatus', 'optimizationGoal', 'billingEvent',
            'dailyBudget', 'lifetimeBudget', 'startTime', 'endTime',
            'targeting', 'estimatedReachMin', 'estimatedReachMax',
            'spend', 'impressions', 'reach', 'clicks', 'ctr', 'cpc',
            'ads', 'created_at', 'updated_at'
        ]


class SaaSAdCampaignSerializer(serializers.ModelSerializer):
    adsets = SaaSAdSetSerializer(many=True, read_only=True)

    class Meta:
        model = SaaSAdCampaign
        fields = [
            'id', 'tenantId', 'metaCampaignId', 'adAccountId', 'source',
            'campaignType', 'name', 'objective', 'status', 'effectiveStatus',
            'productId', 'productName', 'postId', 'landingUrl', 'utmCampaign',
            'dailyBudget', 'lifetimeBudget', 'currency',
            'billingGuardAlert', 'deliveryStatusDetail',
            'spend', 'impressions', 'reach', 'clicks', 'ctr', 'cpc', 'cpm',
            'metaPurchases', 'storeOrders', 'storeRevenue', 'roas',
            'isRemoteSynced', 'lastSyncedAt', 'adsets', 'created_at', 'updated_at'
        ]


class MarketingUtmCampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketingUtmCampaign
        fields = [
            'id', 'name', 'source', 'productId', 'productName', 'utmCampaign',
            'landingUrl', 'spend', 'clicks', 'isPaused', 'created_at', 'updated_at'
        ]




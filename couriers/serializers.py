from rest_framework import serializers
from .models import CourierConfig

class CourierConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourierConfig
        fields = [
            'id', 'name', 'logo', 'isActive', 'apiKey', 'secret', 'extraCredentials',
            'coverage', 'codFeePercent', 'insideDhakaRate', 'outsideDhakaRate',
            'lastSyncTime', 'pendingCodAmount', 'settledCodAmount'
        ]

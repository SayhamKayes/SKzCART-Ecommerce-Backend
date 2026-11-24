from django.urls import path, include
from .views import api_status, trigger_migration, system_blueprint

urlpatterns = [
    path('', api_status, name='api_status'),
    path('system/migrate', trigger_migration, name='trigger_migration'),
    path('system/blueprint', system_blueprint, name='system_blueprint'),
    path('system/blueprint/', system_blueprint, name='system_blueprint_slash'),
    path('', include('core.urls')),
    path('', include('products.urls')),
    path('', include('order.urls')),
    path('', include('couriers.urls')),
    path('', include('users.urls')),
    path('', include('payments.urls')),
]

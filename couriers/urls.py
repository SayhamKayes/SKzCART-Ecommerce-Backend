from django.urls import path
from . import views

urlpatterns = [
    path('couriers', views.couriers_list, name='couriers_list'),
    path('couriers/', views.couriers_list, name='couriers_list_slash'),
    path('couriers/controls', views.courier_controls_view, name='courier_controls'),
    path('couriers/controls/', views.courier_controls_view, name='courier_controls_slash'),
    path('couriers/mode', views.courier_mode_preset_view, name='courier_mode_preset'),
    path('couriers/mode/', views.courier_mode_preset_view, name='courier_mode_preset_slash'),
    path('couriers/assign', views.assign_courier, name='assign_courier'),
    path('couriers/assign/', views.assign_courier, name='assign_courier_slash'),
    path('couriers/sync/<str:order_id>', views.sync_order_courier_status, name='sync_order_courier_status'),
    path('couriers/sync/<str:order_id>/', views.sync_order_courier_status, name='sync_order_courier_status_slash'),
    path('couriers/<str:provider>/balance', views.courier_balance, name='courier_balance'),
    path('couriers/<str:provider>/balance/', views.courier_balance, name='courier_balance_slash'),
    path('couriers/<str:provider>/status/<str:tracking_code>', views.courier_check_status, name='courier_check_status'),
    path('couriers/<str:provider>/status/<str:tracking_code>/', views.courier_check_status, name='courier_check_status_slash'),
    path('couriers/<str:pk>', views.courier_update, name='courier_update'),
    path('couriers/<str:pk>/', views.courier_update, name='courier_update_slash'),
    path('webhooks/couriers', views.courier_webhook, name='courier_webhook'),
    path('webhooks/couriers/', views.courier_webhook, name='courier_webhook_slash'),
    path('webhooks/couriers/<str:provider>', views.provider_webhook, name='provider_webhook'),
    path('webhooks/couriers/<str:provider>/', views.provider_webhook, name='provider_webhook_slash'),
]

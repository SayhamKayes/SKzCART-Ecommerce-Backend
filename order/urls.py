from django.urls import path
from . import views

urlpatterns = [
    path('checkout', views.checkout_view, name='checkout'),
    path('checkout/', views.checkout_view, name='checkout_slash'),
    path('orders', views.orders_list, name='orders_list'),
    path('orders/', views.orders_list, name='orders_list_slash'),
    path('orders/bulk-delete', views.bulk_delete_orders, name='bulk_delete_orders'),
    path('orders/bulk-delete/', views.bulk_delete_orders, name='bulk_delete_orders_slash'),
    path('orders/statement/pdf', views.order_statement_pdf, name='order_statement_pdf'),
    path('orders/statement/pdf/', views.order_statement_pdf, name='order_statement_pdf_slash'),
    path('orders/<str:pk>', views.order_detail, name='order_detail'),
    path('orders/<str:pk>/', views.order_detail, name='order_detail_slash'),
    path('orders/<str:pk>/status', views.update_order_status, name='update_order_status'),
    path('orders/<str:pk>/status/', views.update_order_status, name='update_order_status_slash'),
    path('tracking/lookup', views.tracking_lookup, name='tracking_lookup'),
    path('tracking/lookup/', views.tracking_lookup, name='tracking_lookup_slash'),
]

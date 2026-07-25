from django.urls import path

from . import views

app_name = 'netbox_oxidized_viewer'

urlpatterns = [
    path('source/', views.OxidizedInventoryView.as_view(), name='source-inventory'),
    # Read-only config access (same object-level RBAC as the UI).
    path('devices/<int:pk>/config/', views.DeviceConfigAPIView.as_view(), name='device-config'),
    path('devices/<int:pk>/history/', views.DeviceHistoryAPIView.as_view(), name='device-history'),
    path(
        'devices/<int:pk>/diff/<str:sha_old>/<str:sha_new>/',
        views.DeviceDiffAPIView.as_view(),
        name='device-diff',
    ),
    # Commit notes (stored in NetBox) + on-demand Oxidized sync trigger
    path(
        'devices/<int:pk>/commits/<str:sha>/note/',
        views.DeviceCommitNoteAPIView.as_view(),
        name='device-commit-note',
    ),
    path('devices/<int:pk>/sync/', views.DeviceSyncAPIView.as_view(), name='device-sync'),
]

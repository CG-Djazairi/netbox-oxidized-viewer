from django.urls import path
from netbox.api.routers import NetBoxRouter

from .. import converters
from . import views

converters.register()

app_name = 'netbox_oxidized_viewer'

router = NetBoxRouter()
router.APIRootView = views.OxidizedAPIRootView
router.register('sources', views.OxidizedSourceViewSet)
router.register('inventories', views.OxidizedInventoryViewSet)

urlpatterns = [
    # Oxidized HTTP-source inventory. 'inventory/' is the canonical path; 'source/'
    # is kept for existing Oxidized configs.
    path('inventory/', views.OxidizedInventoryView.as_view(), name='inventory'),
    path('inventory/<slug:slug>/', views.OxidizedInventoryView.as_view(), name='inventory-scoped'),
    path('source/', views.OxidizedInventoryView.as_view(), name='source-inventory'),
    # Read-only config access (same object-level RBAC as the UI).
    path('devices/<int:pk>/config/', views.DeviceConfigAPIView.as_view(), name='device-config'),
    path('devices/<int:pk>/history/', views.DeviceHistoryAPIView.as_view(), name='device-history'),
    path(
        'devices/<int:pk>/diff/<sha:sha_old>/<sha:sha_new>/',
        views.DeviceDiffAPIView.as_view(),
        name='device-diff',
    ),
    # Commit notes (stored in NetBox) + on-demand Oxidized sync trigger
    path(
        'devices/<int:pk>/commits/<sha:sha>/note/',
        views.DeviceCommitNoteAPIView.as_view(),
        name='device-commit-note',
    ),
    path('devices/<int:pk>/sync/', views.DeviceSyncAPIView.as_view(), name='device-sync'),
]

urlpatterns += router.urls

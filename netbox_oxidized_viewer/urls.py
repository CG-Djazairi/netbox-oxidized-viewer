from django.urls import include, path
from utilities.urls import get_model_urls

from . import converters, views

converters.register()

urlpatterns = [
    # Dashboard & Search
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('search/', views.ConfigSearchView.as_view(), name='config_search'),
    # Oxidized Sources
    path('sources/', views.OxidizedSourceListView.as_view(), name='oxidizedsource_list'),
    path('sources/add/', views.OxidizedSourceEditView.as_view(), name='oxidizedsource_add'),
    path('sources/<int:pk>/', views.OxidizedSourceView.as_view(), name='oxidizedsource'),
    path('sources/<int:pk>/edit/', views.OxidizedSourceEditView.as_view(), name='oxidizedsource_edit'),
    path('sources/<int:pk>/delete/', views.OxidizedSourceDeleteView.as_view(), name='oxidizedsource_delete'),
    path('sources/<int:pk>/reindex/', views.SourceReindexView.as_view(), name='oxidizedsource_reindex'),
    # Views NetBox registers for the model's features (changelog, jobs) and any
    # @register_model_view(OxidizedSource, ...) of our own.
    path('sources/<int:pk>/', include(get_model_urls('netbox_oxidized_viewer', 'oxidizedsource'))),
    # Named inventories (one per Oxidized instance / zone)
    path('inventories/', views.OxidizedInventoryListView.as_view(), name='oxidizedinventory_list'),
    path('inventories/add/', views.OxidizedInventoryEditView.as_view(), name='oxidizedinventory_add'),
    path('inventories/<int:pk>/', views.OxidizedInventoryView.as_view(), name='oxidizedinventory'),
    path('inventories/<int:pk>/edit/', views.OxidizedInventoryEditView.as_view(), name='oxidizedinventory_edit'),
    path('inventories/<int:pk>/delete/', views.OxidizedInventoryDeleteView.as_view(), name='oxidizedinventory_delete'),
    path('inventories/<int:pk>/', include(get_model_urls('netbox_oxidized_viewer', 'oxidizedinventory'))),
    # Device config tab (registered via @register_model_view on /dcim/devices/<pk>/config/)
    path('devices/<int:pk>/config/', views.DeviceConfigView.as_view(), name='device_oxidized_config'),
    # Diff views
    path('devices/<int:pk>/commit/<sha:sha_new>/', views.ConfigDiffView.as_view(), name='device_commit'),
    path('devices/<int:pk>/diff/<sha:sha_old>/<sha:sha_new>/', views.ConfigDiffView.as_view(), name='device_diff'),
    # Commit picker redirect
    path('devices/<int:pk>/compare/', views.ConfigCompareRedirectView.as_view(), name='device_compare'),
    # Commit notes (stored in NetBox, not git) + on-demand Oxidized sync
    path('devices/<int:pk>/commit/<sha:sha>/note/', views.AddCommitNoteView.as_view(), name='device_add_note'),
    path('devices/<int:pk>/sync/', views.DeviceSyncView.as_view(), name='device_sync'),
    # Downloads
    path('devices/<int:pk>/config/download/', views.DeviceConfigDownloadView.as_view(), name='device_config_download'),
    path(
        'devices/<int:pk>/commit/<sha:sha>/download/',
        views.CommitConfigDownloadView.as_view(),
        name='device_commit_download',
    ),
    path(
        'devices/<int:pk>/diff/<sha:sha_old>/<sha:sha_new>/download/',
        views.DiffDownloadView.as_view(),
        name='device_diff_download',
    ),
]

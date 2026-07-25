from django.urls import path
from netbox.views.generic import ObjectChangeLogView
from . import models, views

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
    path('sources/<int:pk>/changelog/', ObjectChangeLogView.as_view(), name='oxidizedsource_changelog', kwargs={
        'model': models.OxidizedSource
    }),

    # Device config tab (registered via @register_model_view on /dcim/devices/<pk>/config/)
    path('devices/<int:pk>/config/', views.DeviceConfigView.as_view(), name='device_oxidized_config'),

    # Diff views
    path('devices/<int:pk>/commit/<str:sha_new>/', views.ConfigDiffView.as_view(), name='device_commit'),
    path('devices/<int:pk>/diff/<str:sha_old>/<str:sha_new>/', views.ConfigDiffView.as_view(), name='device_diff'),

    # Commit picker redirect
    path('devices/<int:pk>/compare/', views.ConfigCompareRedirectView.as_view(), name='device_compare'),

    # Commit notes (stored in NetBox, not git) + on-demand Oxidized sync
    path('devices/<int:pk>/commit/<str:sha>/note/', views.AddCommitNoteView.as_view(), name='device_add_note'),
    path('devices/<int:pk>/sync/', views.DeviceSyncView.as_view(), name='device_sync'),

    # Downloads
    path('devices/<int:pk>/config/download/', views.DeviceConfigDownloadView.as_view(), name='device_config_download'),
    path('devices/<int:pk>/commit/<str:sha>/download/', views.CommitConfigDownloadView.as_view(), name='device_commit_download'),
    path('devices/<int:pk>/diff/<str:sha_old>/<str:sha_new>/download/', views.DiffDownloadView.as_view(), name='device_diff_download'),
]

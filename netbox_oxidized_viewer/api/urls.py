from django.urls import path
from . import views

app_name = 'netbox_oxidized_viewer'

urlpatterns = [
    path('source/', views.OxidizedInventoryView.as_view(), name='source-inventory'),
]

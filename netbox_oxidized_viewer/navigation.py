from netbox.plugins import PluginMenu, PluginMenuItem

from .permissions import CONFIG_VIEW_PERMISSION

menu = PluginMenu(
    label='Oxidized',
    groups=(
        (
            'Configs',
            (
                PluginMenuItem(
                    link='plugins:netbox_oxidized_viewer:dashboard',
                    link_text='Dashboard',
                    permissions=[CONFIG_VIEW_PERMISSION],
                ),
                PluginMenuItem(
                    link='plugins:netbox_oxidized_viewer:config_search',
                    link_text='Search Configs',
                    permissions=[CONFIG_VIEW_PERMISSION],
                ),
            ),
        ),
        (
            'Admin',
            (
                PluginMenuItem(
                    link='plugins:netbox_oxidized_viewer:oxidizedsource_list',
                    link_text='Sources',
                    permissions=['netbox_oxidized_viewer.view_oxidizedsource'],
                ),
                PluginMenuItem(
                    link='plugins:netbox_oxidized_viewer:oxidizedinventory_list',
                    link_text='Inventories',
                    permissions=['netbox_oxidized_viewer.view_oxidizedinventory'],
                ),
            ),
        ),
    ),
    icon_class='mdi mdi-file-document-multiple-outline',
)

from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label='Oxidized',
    groups=(
        (
            'Configs',
            (
                PluginMenuItem(
                    link='plugins:netbox_oxidized_viewer:dashboard',
                    link_text='Dashboard',
                ),
                PluginMenuItem(
                    link='plugins:netbox_oxidized_viewer:config_search',
                    link_text='Search Configs',
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
            ),
        ),
    ),
    icon_class='mdi mdi-file-document-multiple-outline',
)

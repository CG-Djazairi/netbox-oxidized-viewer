"""
The plugin's "may read configurations" rule, in one place.

NetBox has no per-plugin permission: each view decides what it checks. Every
entry point that shows configuration data (dashboard, search, the device tab,
diffs, downloads, commit notes, sync, the per-device API, the device card)
requires CONFIG_VIEW_PERMISSION on top of view on the device itself.

It is a model-level check. Which devices a user sees stays the job of
Device.objects.restrict(), so constraints set on this permission are not
evaluated.
"""

from django.contrib.auth.mixins import AccessMixin

CONFIG_VIEW_PERMISSION = 'netbox_oxidized_viewer.view_configsnapshot'


class ConfigViewPermissionMixin(AccessMixin):
    """403 for a logged-in user without CONFIG_VIEW_PERMISSION, login redirect
    for an anonymous one. List it before the view's other base classes."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.has_perm(CONFIG_VIEW_PERMISSION):
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .forms import UserAdminChangeForm
from .models import User

# Django's own admin panel. Since Stage 17b, the app has its own Administration section
# (in the menu), and this panel stays as a technical tool: its menu link is "Django admin (technical)".
admin.site.site_header = "IT Projects · Django admin (technical)"

# The admin panel (people, roles, every project) is for the app's admins only.
# Django already asks for "Staff status"; this also asks for the Admin role, so someone
# given "Staff status" by mistake still can't get in.
django_has_permission = admin.site.has_permission
admin.site.has_permission = lambda request: django_has_permission(request) and request.user.is_admin


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    form = UserAdminChangeForm  # a new picture is made small and square, like on the profile page
    list_display = ["username", "first_name", "last_name", "email", "phone", "role", "is_active"]
    list_filter = ["role", "is_active"]
    fieldsets = (
        (None, {"fields": ["username", "password"]}),
        ("Personal info", {"fields": ["first_name", "last_name", "email", "phone", "picture"]}),
        ("Role in the app", {"fields": ["role"]}),
    ) + UserAdmin.fieldsets[2:]  # then Django's "Permissions" and "Important dates"
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Personal info and role", {"fields": ["first_name", "last_name", "email", "role"]}),
    )

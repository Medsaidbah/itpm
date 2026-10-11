from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

admin.site.site_header = "IT Projects administration"

# The admin panel (people, roles, every project) is for the app's admins only.
# Django already asks for "Staff status"; this also asks for the Admin role, so someone
# given "Staff status" by mistake still can't get in.
django_has_permission = admin.site.has_permission
admin.site.has_permission = lambda request: django_has_permission(request) and request.user.is_admin


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ["username", "first_name", "last_name", "email", "role", "is_active"]
    list_filter = ["role", "is_active"]
    fieldsets = UserAdmin.fieldsets + (("Role in the app", {"fields": ["role"]}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Personal info and role", {"fields": ["first_name", "last_name", "email", "role"]}),
    )

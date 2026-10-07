from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

admin.site.site_header = "IT Projects administration"


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ["username", "first_name", "last_name", "email", "role", "is_active"]
    list_filter = ["role", "is_active"]
    fieldsets = UserAdmin.fieldsets + (("Role in the app", {"fields": ["role"]}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Personal info and role", {"fields": ["first_name", "last_name", "email", "role"]}),
    )
from django.contrib import admin

from .models import Host
from .models import HostMac


class HostMacInLine(admin.TabularInline):
    model = HostMac
    extra = 1


class HostAdmin(admin.ModelAdmin):
    fieldsets = [
        (None, {'fields': ['display_name']}),
        (None, {'fields': ['ident']}),
        (None, {'fields': ['login_datetime']}),
    ]
    inlines = [HostMacInLine]


admin.site.register(Host, HostAdmin)

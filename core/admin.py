from django import forms
from django.contrib import admin, messages

from core.models import Category, City, CollectionJob, MarketplaceSession, Seller, SellerContact


class MarketplaceSessionForm(forms.ModelForm):
    class Meta:
        model = MarketplaceSession
        fields = "__all__"
        widgets = {
            "cookies": forms.Textarea(attrs={"rows": 6, "class": "vLargeTextField"}),
        }

    def clean_cookies(self):
        value = self.cleaned_data.get("cookies", "")
        if not value.strip():
            raise forms.ValidationError("Cookies не могут быть пустыми")
        return value.strip()


class SellerContactInline(admin.TabularInline):
    model = SellerContact
    extra = 0


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "dest_code", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("title", "marketplace", "external_id", "is_active")
    list_filter = ("marketplace", "is_active")
    search_fields = ("title", "external_id")
    list_editable = ("is_active",)


class SellerAdminForm(admin.ModelAdmin):
    pass


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = ("name", "marketplace", "external_seller_id", "inn", "city", "rating", "last_seen_at")
    list_filter = ("marketplace", "city")
    search_fields = ("name", "inn", "external_seller_id", "legal_address")
    inlines = [SellerContactInline]
    readonly_fields = ("first_seen_at", "last_seen_at", "last_updated_at", "last_enriched_at")


@admin.register(SellerContact)
class SellerContactAdmin(admin.ModelAdmin):
    list_display = ("seller", "type", "value", "source")
    list_filter = ("type", "source")


@admin.register(CollectionJob)
class CollectionJobAdmin(admin.ModelAdmin):
    list_display = ("id", "marketplace", "user", "status", "processed", "found", "errors_count", "created_at")
    list_filter = ("marketplace", "status")


@admin.register(MarketplaceSession)
class MarketplaceSessionAdmin(admin.ModelAdmin):
    form = MarketplaceSessionForm
    list_display = ("marketplace", "source", "updated_at", "session_file_status")
    readonly_fields = ("updated_at", "session_file_status")
    fieldsets = [
        (None, {"fields": ("marketplace", "cookies", "note")}),
        ("Статус", {"fields": ("source", "updated_at", "session_file_status")}),
    ]

    @admin.display(description="Файл сессии (читают worker/адаптеры)")
    def session_file_status(self, obj):
        if not obj or not obj.marketplace:
            return "—"
        return MarketplaceSession.file_status(obj.marketplace)

    def save_model(self, request, obj, form, change):
        cookies = obj.parsed_cookies()
        if not cookies:
            self.message_user(
                request,
                "Не удалось разобрать cookies: проверьте формат (name=value; ...)",
                level=messages.ERROR,
            )
            raise forms.ValidationError("Cookies не распознаны")
        obj.source = "admin"
        super().save_model(request, obj, form, change)
        self.message_user(
            request,
            f"Сессия {obj.get_marketplace_display()} обновлена: "
            f"{len(cookies)} cookies записаны в runtime/sessions/{obj.marketplace}.json",
            level=messages.SUCCESS,
        )

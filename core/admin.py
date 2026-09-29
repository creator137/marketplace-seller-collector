from django import forms
from django.contrib import admin, messages

from core.models import Category, City, CollectionJob, ContactEnrichmentJob, MarketplaceSession, Seller, SellerContact

admin.site.site_header = "Seller Collector — справочники"
admin.site.site_title = "Seller Collector"
admin.site.index_title = "Справочники и данные"


class MarketplaceSessionForm(forms.ModelForm):
    class Meta:
        model = MarketplaceSession
        fields = "__all__"
        widgets = {
            "cookies": forms.Textarea(attrs={"rows": 8, "class": "vLargeTextField"}),
            "note": forms.TextInput(attrs={"class": "vTextField", "style": "width: 40em"}),
        }

    def clean_cookies(self):
        value = self.cleaned_data.get("cookies", "")
        if not value.strip():
            raise forms.ValidationError("Cookies не могут быть пустыми")
        return value.strip()


class SellerContactInline(admin.TabularInline):
    model = SellerContact
    extra = 0
    fields = ("type", "value", "source", "source_ref", "found_at")
    readonly_fields = ("found_at",)


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "normalized", "dest_code", "is_active")
    list_editable = ("is_active", "dest_code")
    list_filter = ("is_active",)
    search_fields = ("name", "normalized", "dest_code")
    ordering = ("name",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("title", "marketplace", "external_id", "is_active", "parent")
    list_filter = ("marketplace", "is_active")
    search_fields = ("title", "external_id")
    list_editable = ("is_active",)
    autocomplete_fields = ("parent",)
    ordering = ("marketplace", "title")


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = (
        "name", "marketplace", "external_seller_id", "inn", "city",
        "rating", "last_seen_at", "last_enriched_at",
    )
    list_filter = ("marketplace", "city")
    search_fields = ("name", "inn", "external_seller_id", "legal_address", "seller_url")
    inlines = [SellerContactInline]
    readonly_fields = ("first_seen_at", "last_seen_at", "last_updated_at", "last_enriched_at")
    autocomplete_fields = ("city",)
    date_hierarchy = "last_seen_at"
    fieldsets = (
        ("Идентификация", {
            "fields": ("marketplace", "external_seller_id", "seller_url", "name"),
        }),
        ("Реквизиты", {
            "fields": ("inn", "ogrn", "legal_address", "city", "website", "rating", "registered_at"),
        }),
        ("Служебное", {
            "fields": ("raw", "first_seen_at", "last_seen_at", "last_updated_at", "last_enriched_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(SellerContact)
class SellerContactAdmin(admin.ModelAdmin):
    list_display = ("seller", "type", "value", "source", "found_at")
    list_filter = ("type", "source")
    search_fields = ("value", "seller__name", "seller__external_seller_id")
    autocomplete_fields = ("seller",)
    date_hierarchy = "found_at"


@admin.register(CollectionJob)
class CollectionJobAdmin(admin.ModelAdmin):
    list_display = (
        "id", "marketplace", "user", "status", "source_status",
        "max_sellers", "processed", "found", "errors_count", "created_at", "finished_at",
    )
    list_filter = ("marketplace", "status", "source_status")
    search_fields = ("id", "error_message", "last_error")
    readonly_fields = (
        "created_at", "started_at", "finished_at", "checkpoint",
        "error_message", "last_error", "retry_after",
    )
    date_hierarchy = "created_at"
    filter_horizontal = ("cities", "categories")


@admin.register(MarketplaceSession)
class MarketplaceSessionAdmin(admin.ModelAdmin):
    form = MarketplaceSessionForm
    list_display = ("marketplace", "source", "updated_at", "session_file_status")
    readonly_fields = ("updated_at", "session_file_status")
    fieldsets = [
        (None, {
            "fields": ("marketplace", "cookies", "note"),
            "description": (
                "Вставьте строку Cookie-заголовка или JSON. "
                "После сохранения файл сессии сразу читают worker и адаптеры."
            ),
        }),
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


@admin.register(ContactEnrichmentJob)
class ContactEnrichmentJobAdmin(admin.ModelAdmin):
    list_display = ("id", "marketplace", "status", "processed", "total", "matched", "contacts_added", "errors_count", "created_at")
    list_filter = ("marketplace", "status")
    readonly_fields = ("created_at", "started_at", "finished_at")

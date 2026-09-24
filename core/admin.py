from django.contrib import admin

from core.models import Category, City, CollectionJob, Seller, SellerContact


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

from django.db import models
from django.utils import timezone


class Marketplace(models.TextChoices):
    OZON = "ozon", "Ozon"
    WB = "wildberries", "Wildberries"
    YM = "yandex_market", "Яндекс.Маркет"


class City(models.Model):
    name = models.CharField("Название", max_length=100, unique=True)
    normalized = models.CharField("Нормализованное название", max_length=100, unique=True, editable=False)
    dest_code = models.CharField(
        "dest-код WB", max_length=32, blank=True, default="",
        help_text="Внутренний dest-код Wildberries; пусто — глобальный поиск",
    )
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Город"
        verbose_name_plural = "Города"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from core.citymatch import normalize_city_name

        self.normalized = normalize_city_name(self.name)
        super().save(*args, **kwargs)


class Category(models.Model):
    marketplace = models.CharField("Маркетплейс", max_length=32, choices=Marketplace.choices)
    external_id = models.CharField("Внешний id / query / путь", max_length=255)
    title = models.CharField("Название", max_length=255)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ["marketplace", "title"]
        constraints = [
            models.UniqueConstraint(fields=["marketplace", "external_id"], name="uniq_category_per_marketplace"),
        ]

    def __str__(self):
        return f"[{self.get_marketplace_display()}] {self.title}"


class Seller(models.Model):
    marketplace = models.CharField("Маркетплейс", max_length=32, choices=Marketplace.choices, db_index=True)
    external_seller_id = models.CharField("Внешний id продавца", max_length=128)
    seller_url = models.URLField("Страница продавца", max_length=512, blank=True, default="")
    seller_url_normalized = models.CharField(max_length=512, blank=True, default="", editable=False)
    name = models.CharField("Название продавца", max_length=512, blank=True, default="")
    inn = models.CharField("ИНН", max_length=12, blank=True, default="", db_index=True)
    ogrn = models.CharField("ОГРН", max_length=15, blank=True, default="")
    legal_address = models.TextField("Юридический адрес", blank=True, default="")
    city = models.ForeignKey(City, null=True, blank=True, on_delete=models.SET_NULL, related_name="sellers")
    categories = models.ManyToManyField(Category, blank=True, related_name="sellers")
    rating = models.CharField("Рейтинг", max_length=16, blank=True, default="")
    registered_at = models.DateField("Дата регистрации на маркетплейсе", null=True, blank=True)
    website = models.URLField("Сайт", max_length=512, blank=True, default="")
    raw = models.JSONField("Raw payload", null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    last_updated_at = models.DateTimeField("Обновлён", auto_now=True)
    last_enriched_at = models.DateTimeField("Обогащён DaData", null=True, blank=True)

    class Meta:
        verbose_name = "Продавец"
        verbose_name_plural = "Продавцы"
        ordering = ["-last_seen_at"]
        constraints = [
            models.UniqueConstraint(fields=["marketplace", "external_seller_id"], name="uniq_seller_per_marketplace"),
        ]

    def __str__(self):
        return f"{self.name or self.external_seller_id} ({self.get_marketplace_display()})"

    @property
    def years_on_marketplace(self):
        if not self.registered_at:
            return None
        from django.utils import timezone

        return round((timezone.now().date() - self.registered_at).days / 365.25, 1)

    @property
    def telegram_link(self):
        from core.phoneutils import mobile_phone, telegram_link

        return telegram_link(mobile_phone(self.phone_list))

    @property
    def phone_list(self):
        return [c.value for c in self.contacts.filter(type__in=("phone", "city_phone"))]

    @property
    def mobile_phones(self):
        return [c.value for c in self.contacts.filter(type="phone")]

    @property
    def city_phones(self):
        return [c.value for c in self.contacts.filter(type="city_phone")]

    @property
    def emails(self):
        return [c.value for c in self.contacts.filter(type="email")]

    @property
    def category_titles(self):
        return [c.title for c in self.categories.all()]


class SellerContact(models.Model):
    TYPE_PHONE = "phone"          # мобильный
    TYPE_CITY_PHONE = "city_phone"
    TYPE_EMAIL = "email"
    TYPE_SITE = "site"
    TYPE_CHOICES = [
        (TYPE_PHONE, "Мобильный телефон"),
        (TYPE_CITY_PHONE, "Городской телефон"),
        (TYPE_EMAIL, "Email"),
        (TYPE_SITE, "Сайт"),
    ]

    SOURCE_MARKETPLACE = "marketplace"
    SOURCE_DADATA = "dadata"
    SOURCE_CHOICES = [
        (SOURCE_MARKETPLACE, "Маркетплейс"),
        (SOURCE_DADATA, "DaData"),
    ]

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="contacts")
    type = models.CharField("Тип", max_length=16, choices=TYPE_CHOICES)
    value = models.CharField("Значение", max_length=512)
    source = models.CharField("Источник", max_length=16, choices=SOURCE_CHOICES, default=SOURCE_MARKETPLACE)
    source_ref = models.CharField(max_length=255, blank=True, default="")
    found_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Контакт продавца"
        verbose_name_plural = "Контакты продавцов"
        constraints = [
            models.UniqueConstraint(fields=["seller", "type", "value"], name="uniq_contact_per_seller"),
        ]

    def __str__(self):
        return f"{self.get_type_display()}: {self.value}"


class CollectionJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Выполняется"
        PAUSED = "paused", "Приостановлен"
        COMPLETED = "completed", "Завершён"
        FAILED = "failed", "Ошибка"

    user = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    marketplace = models.CharField("Маркетплейс", max_length=32, choices=Marketplace.choices)
    cities = models.ManyToManyField(City, blank=True, related_name="jobs")
    categories = models.ManyToManyField(Category, blank=True, related_name="jobs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    total = models.PositiveIntegerField("Всего", null=True, blank=True)
    processed = models.PositiveIntegerField("Обработано", default=0)
    found = models.PositiveIntegerField("Найдено", default=0)
    errors_count = models.PositiveIntegerField("Ошибок", default=0)
    error_message = models.TextField(blank=True, default="")
    source_status = models.CharField(max_length=32, default="queued", db_index=True)
    last_error = models.TextField(blank=True, default="")
    retry_after = models.DateTimeField(null=True, blank=True)
    checkpoint = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Задача сбора"
        verbose_name_plural = "Задачи сбора"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Job #{self.pk} {self.get_marketplace_display()} [{self.status}]"

    def fail(self, message, source_status="temporary_error"):
        self.status = self.Status.FAILED
        self.source_status = source_status
        self.error_message = (message or "")[:2000]
        self.last_error = self.error_message
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "source_status", "error_message", "last_error", "finished_at"])


class CollectionJobSeller(models.Model):
    """The immutable discovery ledger for one job, including partial details."""

    job = models.ForeignKey(CollectionJob, on_delete=models.CASCADE, related_name="job_sellers")
    seller = models.ForeignKey(Seller, null=True, blank=True, on_delete=models.SET_NULL, related_name="collection_links")
    external_seller_id = models.CharField(max_length=128)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    city = models.ForeignKey(City, null=True, blank=True, on_delete=models.SET_NULL)
    discovered_at = models.DateTimeField(auto_now_add=True)
    detail_status = models.CharField(max_length=24, default="pending", db_index=True)
    detail_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["job", "external_seller_id"], name="uniq_job_seller_ref"),
        ]
        indexes = [
            models.Index(fields=["job", "detail_status"]),
            models.Index(fields=["external_seller_id"]),
        ]

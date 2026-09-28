import logging
import threading

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.export import export_all_xlsx, export_job_xlsx
from core.citymatch import normalize_city_name
from core.models import Category, City, CollectionJob, Marketplace, MarketplaceSession, Seller
from core.tasks import run_collection_job

log = logging.getLogger("core.views")


def _rq_queue():
    import django_rq

    return django_rq.get_queue("default")


def _run_job_in_background(job_id):
    """Run a job outside the request when Redis is unavailable.

    The Docker deployment normally uses RQ. Keeping this fallback asynchronous
    prevents a slow marketplace request from holding the browser request open.
    """
    thread = threading.Thread(
        target=run_collection_job,
        args=(job_id,),
        name=f"collection-job-{job_id}",
        daemon=True,
    )
    thread.start()


@login_required
def dashboard(request):
    context = {
        "marketplaces": Marketplace.choices,
    }
    if request.method == "POST":
        marketplace = request.POST.get("marketplace")
        category_ids = request.POST.getlist("categories")
        city_ids = request.POST.getlist("cities")
        try:
            max_sellers = int(request.POST.get("max_sellers") or 0)
        except (TypeError, ValueError):
            max_sellers = 0
        if marketplace not in Marketplace.values or not category_ids:
            messages.error(request, "Выберите маркетплейс и хотя бы одну категорию")
            return redirect("dashboard")
        if not city_ids:
            messages.error(request, "Выберите хотя бы один город — лимит считается по продавцам из этих городов")
            return redirect("dashboard")
        if max_sellers < 1:
            messages.error(request, "Укажите, сколько продавцов собирать (минимум 1)")
            return redirect("dashboard")
        if max_sellers > 100000:
            messages.error(request, "Слишком большой лимит — максимум 100 000 за один запуск")
            return redirect("dashboard")
        job = CollectionJob.objects.create(
            user=request.user,
            marketplace=marketplace,
            max_sellers=max_sellers,
            total=max_sellers,
        )
        cities = City.objects.filter(is_active=True) if "__all__" in city_ids else City.objects.filter(id__in=city_ids, is_active=True)
        categories = (
            Category.objects.filter(is_active=True, marketplace=marketplace)
            if "__all__" in category_ids
            else Category.objects.filter(id__in=category_ids, marketplace=marketplace, is_active=True)
        )
        job.cities.set(cities)
        job.categories.set(categories)
        try:
            _rq_queue().enqueue(run_collection_job, job.id)
        except Exception as exc:
            # Redis недоступен (локальная разработка) — не блокируем HTTP-запрос.
            log.warning("RQ enqueue failed (%s); running job %s in background", exc, job.id)
            _run_job_in_background(job.id)
        return redirect("job_detail", job_id=job.id)
    return render(request, "core/dashboard.html", context)


@login_required
def how_it_works(request):
    return render(request, "core/how_it_works.html")


@login_required
def catalogs(request):
    """Manage cities, categories and marketplace sessions from the main UI."""
    section = request.GET.get("section", "cities")
    if section not in ("cities", "categories", "sessions"):
        section = "cities"
    marketplace_filter = request.GET.get("marketplace", Marketplace.OZON)
    if marketplace_filter not in Marketplace.values:
        marketplace_filter = Marketplace.OZON

    def _redirect(sec=None, marketplace=None):
        sec = sec or section
        params = f"?section={sec}"
        if sec == "categories":
            params += f"&marketplace={marketplace or marketplace_filter}"
        return redirect(f"{reverse('catalogs')}{params}")

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "add_city":
            name = (request.POST.get("name") or "").strip()
            dest_code = (request.POST.get("dest_code") or "").strip()
            if not name:
                messages.error(request, "Укажите название города")
            else:
                normalized = normalize_city_name(name)
                if City.objects.filter(Q(name__iexact=name) | Q(normalized=normalized)).exists():
                    messages.error(request, f"Город «{name}» уже есть в справочнике")
                else:
                    City.objects.create(name=name, dest_code=dest_code, is_active=True)
                    messages.success(request, f"Город «{name}» добавлен")
            return _redirect("cities")

        if action == "toggle_city":
            city = City.objects.filter(pk=request.POST.get("city_id")).first()
            if city:
                city.is_active = not city.is_active
                city.save(update_fields=["is_active"])
                messages.success(
                    request,
                    f"Город «{city.name}» {'включён' if city.is_active else 'выключен'}",
                )
            return _redirect("cities")

        if action == "delete_city":
            city = City.objects.filter(pk=request.POST.get("city_id")).first()
            if city:
                name = city.name
                city.delete()
                messages.success(request, f"Город «{name}» удалён")
            return _redirect("cities")

        if action == "add_category":
            marketplace = request.POST.get("marketplace", "")
            title = (request.POST.get("title") or "").strip()
            external_id = (request.POST.get("external_id") or "").strip()
            if marketplace not in Marketplace.values:
                messages.error(request, "Выберите маркетплейс")
            elif not title or not external_id:
                messages.error(request, "Укажите название и внешний id / запрос категории")
            elif Category.objects.filter(marketplace=marketplace, external_id=external_id).exists():
                messages.error(request, "Такая категория уже есть для этого маркетплейса")
            else:
                Category.objects.create(
                    marketplace=marketplace,
                    title=title,
                    external_id=external_id,
                    is_active=True,
                )
                messages.success(request, f"Категория «{title}» добавлена")
            return _redirect("categories", marketplace)

        if action == "toggle_category":
            category = Category.objects.filter(pk=request.POST.get("category_id")).first()
            marketplace = request.POST.get("marketplace") or marketplace_filter
            if category:
                category.is_active = not category.is_active
                category.save(update_fields=["is_active"])
                messages.success(
                    request,
                    f"Категория «{category.title}» {'включена' if category.is_active else 'выключена'}",
                )
            return _redirect("categories", marketplace)

        if action == "delete_category":
            category = Category.objects.filter(pk=request.POST.get("category_id")).first()
            marketplace = request.POST.get("marketplace") or marketplace_filter
            if category:
                title = category.title
                category.delete()
                messages.success(request, f"Категория «{title}» удалена")
            return _redirect("categories", marketplace)

        if action == "save_session":
            marketplace = request.POST.get("marketplace", "")
            cookies = (request.POST.get("cookies") or "").strip()
            note = (request.POST.get("note") or "").strip()
            if marketplace not in Marketplace.values:
                messages.error(request, "Выберите маркетплейс")
            elif not cookies:
                messages.error(request, "Вставьте cookies сессии")
            else:
                from core.httpclient import HttpClient

                parsed = HttpClient.parse_cookies(cookies)
                if not parsed:
                    messages.error(request, "Не удалось разобрать cookies — проверьте формат")
                else:
                    session, _created = MarketplaceSession.objects.update_or_create(
                        marketplace=marketplace,
                        defaults={"cookies": cookies, "note": note, "source": "ui"},
                    )
                    messages.success(
                        request,
                        f"Сессия {session.get_marketplace_display()} сохранена "
                        f"({len(parsed)} cookies)",
                    )
            return _redirect("sessions")

        if action == "delete_session":
            session = MarketplaceSession.objects.filter(pk=request.POST.get("session_id")).first()
            if session:
                label = session.get_marketplace_display()
                marketplace = session.marketplace
                session.delete()
                from pathlib import Path

                from django.conf import settings

                path = Path(settings.BASE_DIR) / "runtime" / "sessions" / f"{marketplace}.json"
                if path.exists():
                    path.unlink()
                messages.success(request, f"Сессия {label} удалена")
            return _redirect("sessions")
        messages.error(request, "Неизвестное действие")
        return _redirect()

    sessions = []
    for value, label in Marketplace.choices:
        obj = MarketplaceSession.objects.filter(marketplace=value).first()
        sessions.append({
            "marketplace": value,
            "label": label,
            "object": obj,
            "file_status": MarketplaceSession.file_status(value),
        })

    return render(request, "core/catalogs.html", {
        "section": section,
        "cities": City.objects.all(),
        "categories": Category.objects.filter(marketplace=marketplace_filter),
        "marketplace_filter": marketplace_filter,
        "marketplaces": Marketplace.choices,
        "sessions": sessions,
    })


@login_required
def categories_partial(request):
    marketplace = request.GET.get("marketplace", "")
    cities = City.objects.filter(is_active=True)
    cats = Category.objects.filter(is_active=True, marketplace=marketplace) if marketplace in Marketplace.values else []
    return render(request, "core/_selects.html", {"categories": cats, "cities": cities, "marketplace": marketplace})


@login_required
def job_detail(request, job_id):
    job = CollectionJob.objects.get(pk=job_id)
    stats = _job_stats(job)
    return render(request, "core/job_detail.html", {"job": job, "stats": stats})


def _job_stats(job):
    discovered = Seller.objects.filter(collection_links__job=job).distinct()
    unknown_city = discovered.filter(city=None).count()
    base = discovered
    if job.cities.exists():
        base = base.filter(city__in=job.cities.all()).distinct()
    return {
        "total": base.count(),
        "discovered": discovered.count(),
        "matched_cities": base.count(),
        "unknown_city": unknown_city,
        "with_phones": base.filter(contacts__type__in=("phone", "city_phone")).distinct().count(),
        "with_email": base.filter(contacts__type="email").distinct().count(),
        "with_site": base.exclude(website="").count(),
        "with_city": base.exclude(city=None).count(),
    }


@login_required
def jobs_history(request):
    jobs = CollectionJob.objects.select_related("user").prefetch_related("cities", "categories")
    return render(request, "core/jobs.html", {"jobs": jobs})


@login_required
def results(request):
    marketplace = request.GET.get("marketplace", "")
    city_id = request.GET.get("city", "")
    has_contacts = request.GET.get("has_contacts", "")
    q = request.GET.get("q", "").strip()
    job_id = request.GET.get("job", "")
    page = int(request.GET.get("page", "1") or 1)

    qs = Seller.objects.select_related("city").prefetch_related("contacts", "categories")
    if marketplace in Marketplace.values:
        qs = qs.filter(marketplace=marketplace)
    if job_id.isdigit():
        qs = qs.filter(collection_links__job_id=int(job_id)).distinct()
        job_filter = CollectionJob.objects.filter(pk=int(job_id)).first()
        # Цель запуска — продавцы выбранных городов; остальные refs не показываем.
        if job_filter and job_filter.cities.exists():
            qs = qs.filter(city__in=job_filter.cities.all()).distinct()
    if city_id:
        qs = qs.filter(city_id=city_id)
    if has_contacts:
        cond = Q(contacts__type="phone") | Q(contacts__type="city_phone") | Q(contacts__type="email")
        qs = qs.filter(cond).distinct()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(inn__icontains=q) | Q(legal_address__icontains=q))

    per_page = 50
    total = qs.count()
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    sellers = qs[(page - 1) * per_page: page * per_page]
    querystring = request.GET.copy()
    querystring.pop("page", None)
    qs_str = querystring.urlencode()
    if qs_str:
        qs_str += "&"
    return render(request, "core/results.html", {
        "sellers": sellers, "total": total, "page": page, "pages": pages,
        "cities": City.objects.all(), "marketplace": marketplace,
        "view_marketplaces": Marketplace.choices,
        "city_id": city_id, "has_contacts": has_contacts, "q": q,
        "job_id": job_id,
        "querystring": qs_str,
    })


@login_required
@require_POST
def export_job(request, job_id):
    return export_job_xlsx(job_id)


@login_required
@require_POST
def resume_job(request, job_id):
    job = CollectionJob.objects.get(pk=job_id)
    job.status = CollectionJob.Status.QUEUED
    job.save(update_fields=["status"])
    try:
        _rq_queue().enqueue(run_collection_job, job.id)
    except Exception as exc:
        log.warning("RQ enqueue failed (%s); resuming job %s in background", exc, job.id)
        _run_job_in_background(job.id)
    return redirect("job_detail", job_id=job.id)


@login_required
def export_all(request):
    return export_all_xlsx()


@login_required
@require_POST
def enrich_seller(request, seller_id):
    from core.services import enrich_with_dadata

    seller = Seller.objects.get(pk=seller_id)
    enrich_with_dadata(seller, force=True)
    return redirect(request.META.get("HTTP_REFERER") or reverse("results"))

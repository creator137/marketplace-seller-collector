import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.export import export_all_xlsx, export_job_xlsx
from core.models import Category, City, CollectionJob, Marketplace, Seller
from core.tasks import run_collection_job

log = logging.getLogger("core.views")


def _rq_queue():
    import django_rq

    return django_rq.get_queue("default")


@login_required
def dashboard(request):
    context = {
        "marketplaces": Marketplace.choices,
    }
    if request.method == "POST":
        marketplace = request.POST.get("marketplace")
        category_ids = request.POST.getlist("categories")
        city_ids = request.POST.getlist("cities")
        if marketplace not in Marketplace.values or not category_ids:
            messages.error(request, "Выберите маркетплейс и хотя бы одну категорию")
            return redirect("dashboard")
        job = CollectionJob.objects.create(user=request.user, marketplace=marketplace)
        job.cities.set(City.objects.filter(id__in=city_ids))
        job.categories.set(Category.objects.filter(id__in=category_ids, marketplace=marketplace))
        try:
            _rq_queue().enqueue(run_collection_job, job.id)
        except Exception as exc:
            # Redis недоступен (локальная разработка) — выполняем задачу синхронно.
            log.warning("RQ enqueue failed (%s); running job %s synchronously", exc, job.id)
            run_collection_job(job.id)
        return redirect("job_detail", job_id=job.id)
    return render(request, "core/dashboard.html", context)


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
    base = Seller.objects.filter(marketplace=job.marketplace, categories__in=job.categories.all()).distinct()
    return {
        "total": base.count(),
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
    page = int(request.GET.get("page", "1") or 1)

    qs = Seller.objects.select_related("city").prefetch_related("contacts", "categories")
    if marketplace in Marketplace.values:
        qs = qs.filter(marketplace=marketplace)
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
        "querystring": qs_str,
    })


@login_required
@require_POST
def export_job(request, job_id):
    return export_job_xlsx(job_id)


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

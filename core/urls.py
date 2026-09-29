from django.urls import path

from core import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("how-it-works/", views.how_it_works, name="how_it_works"),
    path("catalogs/", views.catalogs, name="catalogs"),
    path("selects/", views.categories_partial, name="selects"),
    path("jobs/", views.jobs_history, name="jobs"),
    path("jobs/<int:job_id>/", views.job_detail, name="job_detail"),
    path("jobs/<int:job_id>/export/", views.export_job, name="export_job"),
    path("jobs/<int:job_id>/resume/", views.resume_job, name="resume_job"),
    path("results/", views.results, name="results"),
    path("export/all/", views.export_all, name="export_all"),
    path("sellers/<int:seller_id>/enrich/", views.enrich_seller, name="enrich_seller"),
    path("contacts/enrich/", views.enrich_contacts, name="enrich_contacts"),
    path("contacts/2gis/", views.open_2gis_browser, name="open_2gis_browser"),
]

from django.urls import path

from core import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("selects/", views.categories_partial, name="selects"),
    path("jobs/", views.jobs_history, name="jobs"),
    path("jobs/<int:job_id>/", views.job_detail, name="job_detail"),
    path("jobs/<int:job_id>/export/", views.export_job, name="export_job"),
    path("results/", views.results, name="results"),
    path("export/all/", views.export_all, name="export_all"),
    path("sellers/<int:seller_id>/enrich/", views.enrich_seller, name="enrich_seller"),
]

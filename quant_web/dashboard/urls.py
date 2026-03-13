from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard_page, name="dashboard"),
    path("api/ingest/snapshot", views.ingest_snapshot, name="ingest_snapshot"),
    path("api/ingest/event", views.ingest_event, name="ingest_event"),
    path("api/dashboard/summary", views.dashboard_summary, name="dashboard_summary"),
    path("api/dashboard/timeseries", views.dashboard_timeseries, name="dashboard_timeseries"),
    path("api/dashboard/alerts", views.dashboard_alerts, name="dashboard_alerts"),
    path("api/dashboard/alerts/<int:alert_id>/read", views.mark_alert_read, name="mark_alert_read"),
]

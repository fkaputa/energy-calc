from django.urls import path

from . import views

app_name = "projects_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("project/<str:project_name>/", views.project_detail, name="detail"),
    path(
        "project/<str:project_name>/normalized/",
        views.project_normalized,
        name="normalized",
    ),
    path(
        "project/<str:project_name>/run/",
        views.run_simulation,
        name="run_simulation",
    ),
    path(
        "project/<str:project_name>/status/",
        views.project_status,
        name="status",
    ),
    path(
        "project/<str:project_name>/chart-data/",
        views.project_chart_data,
        name="chart_data",
    ),
]


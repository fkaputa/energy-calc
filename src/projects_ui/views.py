from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from .visualization_data import load_visualization_data


def _projects_root() -> Path:
    return Path(settings.PROJECTS_ROOT)


def _output_root() -> Path:
    return Path(settings.OUTPUT_ROOT)


def _project_input_dir(name: str) -> Path:
    return _projects_root() / name


def _project_output_dir(name: str) -> Path:
    return _output_root() / name


def index(request: HttpRequest) -> HttpResponse:
    root = _projects_root()
    if not root.exists():
        projects: list[dict[str, object]] = []
    else:
        projects = []
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            out_dir = _project_output_dir(p.name)
            has_output = out_dir.exists()
            projects.append(
                {
                    "name": p.name,
                    "has_output": has_output,
                    "detail_url": reverse("projects_ui:detail", args=[p.name]),
                }
            )

    context = {
        "projects": projects,
        "projects_root": root,
        "output_root": _output_root(),
    }
    return render(request, "projects_ui/index.html", context)


def project_detail(request: HttpRequest, project_name: str) -> HttpResponse:
    in_dir = _project_input_dir(project_name)
    if not in_dir.exists():
        raise Http404("Project not found")

    out_dir = _project_output_dir(project_name)
    has_output = out_dir.exists()
    viz_data = None
    # Initial view: small sample (first 14 days) for fast load; CSI detail comes from
    # dedicated chart-data endpoint per selected window.
    chart_days = 14
    if has_output:
        try:
            viz_data = load_visualization_data(out_dir, chart_days=chart_days)
            viz_data["chart_data"] = viz_data.get("chart_data") or {}
        except Exception:
            viz_data = {
                "summary": None,
                "tables": [],
                "chart_data": {},
                "has_pypsa": False,
                "has_ea_sim_distribution": False,
            }

    context = {
        "project_name": project_name,
        "input_dir": in_dir,
        "output_dir": out_dir if has_output else None,
        "has_output": has_output,
        "viz": viz_data,
    }
    return render(request, "projects_ui/detail.html", context)


def project_chart_data(request: HttpRequest, project_name: str) -> HttpResponse:
    """Return high‑resolution chart data for a selected date window."""
    out_dir = _project_output_dir(project_name)
    if not out_dir.exists():
        raise Http404("No output for this project yet")

    start = request.GET.get("start")
    end = request.GET.get("end")
    try:
        chart_days = 0
        viz_data = load_visualization_data(out_dir, chart_days=chart_days)
    except Exception:
        return JsonResponse({"chart_data": {}}, status=500)

    chart_data = viz_data.get("chart_data") or {}
    # Optional: filter labels in backend to requested window
    if start and end:
        for key, val in list(chart_data.items()):
            labels = val.get("labels") or []
            ds = val.get("datasets") or []
            idx = [
                i
                for i, lbl in enumerate(labels)
                if str(lbl)[:10] >= start and str(lbl)[:10] <= end
            ]
            if not idx:
                continue
            labels_new = [labels[i] for i in idx]
            for d in ds:
                d["data"] = [d["data"][i] for i in idx]
            val["labels"] = labels_new
            val["datasets"] = ds
            chart_data[key] = val

    return JsonResponse({"chart_data": chart_data})


def project_normalized(request: HttpRequest, project_name: str) -> HttpResponse:
    out_dir = _project_output_dir(project_name)
    if not out_dir.exists():
        raise Http404("No output for this project yet")

    csv_path = out_dir / "consumption_profile.csv"
    if not csv_path.exists():
        raise Http404("Normalized consumption_profile.csv not found")

    df = pd.read_csv(csv_path)
    preview = df.head(200).to_html(classes="table table-sm table-striped", index=False)

    context = {
        "project_name": project_name,
        "output_dir": out_dir,
        "table_html": preview,
    }
    return render(request, "projects_ui/normalized.html", context)


@csrf_exempt
def run_simulation(request: HttpRequest, project_name: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    in_dir = _project_input_dir(project_name)
    if not in_dir.exists():
        return JsonResponse({"error": "project not found"}, status=404)

    out_dir = _project_output_dir(project_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = _output_root() / "_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{project_name}-{timestamp}.log"

    cmd = [
        sys.executable,
        "-m",
        "energy_pipeline.scripts.run_pipeline",
        str(in_dir),
        "-o",
        str(out_dir),
        "--visualize",
        "--save-monthly",
    ]

    with open(log_path, "w", encoding="utf-8") as log_file:
        subprocess.Popen(
            cmd,
            cwd=str(settings.REPO_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    return JsonResponse(
        {
            "status": "started",
            "project": project_name,
            "log": str(log_path),
        }
    )


def project_status(request: HttpRequest, project_name: str) -> HttpResponse:
    out_dir = _project_output_dir(project_name)
    has_output = out_dir.exists()
    # Ready when we have any result (consumption or pypsa) so we can show HTML viz
    has_consumption = (out_dir / "consumption_profile.csv").exists()
    has_pypsa = (out_dir / "pypsa_results" / "snapshots.csv").exists()
    ready = has_output and (has_consumption or has_pypsa)

    detail_url = (
        reverse("projects_ui:detail", args=[project_name]) if ready else None
    )

    return JsonResponse(
        {
            "project": project_name,
            "has_output": bool(has_output),
            "ready": bool(ready),
            "detail_url": detail_url,
        }
    )


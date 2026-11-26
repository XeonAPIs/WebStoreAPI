import calendar
import hashlib
import json
import logging
from collections import Counter
from datetime import date

import requests
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .forms import ApiEndpointForm, JsonImportForm
from .models import ApiEndpoint, Category, Plugin, RequestLog
from .plugin_keys import matches_api_key
from .plugin_runner import (
    MAX_PLUGIN_REQUEST_BYTES,
    PluginExecutionError,
    PluginExecutionTimeout,
    PluginOutputLimitExceeded,
    PluginWorkerBusy,
    run_plugin,
)
from .security import validate_upstream_url
from .serializers import ApiEndpointSerializer, PluginSerializer


logger = logging.getLogger(__name__)

API_RESPONSE_CACHE_SECONDS = 30
DASHBOARD_STATS_CACHE_SECONDS = 10
DASHBOARD_DATA_CACHE_SECONDS = 5


def client_ip(request):
    # Avoid trusting forwarded headers unless the deployment explicitly configures a trusted proxy.
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


def dashboard(request):
    dashboard_data = cache.get("dashboard-data:v2")
    if dashboard_data is None:
        categories = list(Category.objects.all())
        endpoints = list(
            ApiEndpoint.objects.filter(is_active=True).select_related("category")
        )
        plugins = list(Plugin.objects.filter(is_active=True).select_related("category"))
        active_counts = Counter(endpoint.category_id for endpoint in endpoints)
        active_counts.update(plugin.category_id for plugin in plugins)
        for category in categories:
            category.active_count = active_counts[category.pk]
        dashboard_data = {
            "categories": categories,
            "endpoints": endpoints,
            "plugins": plugins,
            "active_feature_count": len(endpoints) + len(plugins),
        }
        cache.set("dashboard-data:v2", dashboard_data, DASHBOARD_DATA_CACHE_SECONDS)
    today = date.today()
    month = calendar.Calendar(firstweekday=6).monthdayscalendar(today.year, today.month)
    return render(request, "dashboard/index.html", {
        **dashboard_data,
        "active_section": "dashboard",
        "month_name": today.strftime("%b %Y").upper(),
        "calendar_weeks": month,
        "today": today,
    })


def documentation(request):
    return render(request, "dashboard/docs.html")


@api_view(["GET"])
def stats_api(request):
    stats = cache.get("dashboard-stats:v2")
    if stats is None:
        dashboard_data = cache.get("dashboard-data:v2")
        if dashboard_data is None:
            total_features = (
                ApiEndpoint.objects.filter(is_active=True).count()
                + Plugin.objects.filter(is_active=True).count()
            )
        else:
            total_features = dashboard_data["active_feature_count"]
        request_counts = RequestLog.objects.aggregate(
            total_visitors=Count("ip", distinct=True),
            total_requests=Count("pk"),
        )
        stats = {
            "total_visitors": request_counts["total_visitors"],
            "total_features": total_features,
            "total_requests": request_counts["total_requests"],
        }
        cache.set("dashboard-stats:v2", stats, DASHBOARD_STATS_CACHE_SECONDS)
    return Response({**stats, "your_ip": client_ip(request)})


def api_response_cache_key(kind, identifier, revision, request_values):
    cache_data = json.dumps(
        [kind, identifier, revision, request_values],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(cache_data.encode("utf-8")).hexdigest()
    return f"api-response:{digest}"


@staff_member_required
@require_http_methods(["GET", "POST"])
def upload_api(request):
    form = ApiEndpointForm(request.POST or None)
    import_form = JsonImportForm(request.POST or None, request.FILES or None)
    import_errors = []
    if request.method == "POST":
        if "import_json" in request.POST:
            import_form = JsonImportForm(request.POST, request.FILES)
            if import_form.is_valid():
                imported, import_errors = import_api_config(import_form.cleaned_data["file"], request.user)
                if imported:
                    messages.success(request, f"Imported {imported} API endpoint(s).")
                if not import_errors:
                    return redirect("dashboard")
        else:
            form = ApiEndpointForm(request.POST)
            if form.is_valid():
                endpoint = form.save(commit=False)
                endpoint.created_by = request.user
                endpoint.save()
                messages.success(request, f"{endpoint.name} was added.")
                return redirect("dashboard")
    return render(request, "dashboard/upload.html", {
        "form": form,
        "import_form": import_form,
        "import_errors": import_errors,
        "active_section": "upload",
    })


def import_api_config(entries, user):
    imported, errors = 0, []
    allowed = {"name", "category", "method", "http_method", "upstream_url", "params_schema", "description"}
    for index, item in enumerate(entries, start=1):
        try:
            if not isinstance(item, dict):
                raise ValueError("entry must be a JSON object")
            unknown = set(item) - allowed
            if unknown:
                raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
            name, category_name = item.get("name"), item.get("category")
            if not isinstance(name, str) or not name.strip() or len(name) > 120:
                raise ValueError("name is required and must be 120 characters or fewer")
            if not isinstance(category_name, str) or not category_name.strip() or len(category_name) > 80:
                raise ValueError("category is required and must be 80 characters or fewer")
            if not slugify(category_name.strip()):
                raise ValueError("category must contain letters or numbers")
            method = item.get("http_method", item.get("method", "GET"))
            if method not in ("GET", "POST"):
                raise ValueError("method must be GET or POST")
            params = item.get("params_schema", {})
            if not isinstance(params, dict) or len(json.dumps(params)) > 16_384:
                raise ValueError("params_schema must be a JSON object no larger than 16 KB")
            upstream = item.get("upstream_url", "")
            if not isinstance(upstream, str) or len(upstream) > 1000:
                raise ValueError("upstream_url must be a string of 1,000 characters or fewer")
            validate_upstream_url(upstream)
            description = item.get("description", "")
            if not isinstance(description, str) or len(description) > 4000:
                raise ValueError("description must be 4,000 characters or fewer")
            category_slug = slugify(category_name.strip())
            category, _ = Category.objects.get_or_create(
                slug=category_slug, defaults={"name": category_name.strip()}
            )
            slug = slugify(name.strip())
            if not slug:
                raise ValueError("name must contain letters or numbers")
            if ApiEndpoint.objects.filter(category=category, slug=slug).exists():
                raise ValueError("an endpoint with this name already exists in the category")
            if Plugin.objects.filter(category=category, slug=slug).exists():
                raise ValueError("a plugin with this name already exists in the category")
            ApiEndpoint.objects.create(
                name=name.strip(), slug=slug, category=category, description=description,
                http_method=method, upstream_url=upstream, params_schema=params, created_by=user,
            )
            imported += 1
        except (ValueError, TypeError, OSError, RecursionError) as exc:
            errors.append(f"Entry {index}: {exc}")
    return imported, errors


@staff_member_required
@require_http_methods(["GET", "POST"])
def edit_api(request, pk):
    endpoint = get_object_or_404(ApiEndpoint, pk=pk)
    form = ApiEndpointForm(request.POST or None, instance=endpoint)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{endpoint.name} was updated.")
        return redirect("dashboard")
    return render(request, "dashboard/upload.html", {
        "form": form, "import_form": JsonImportForm(), "editing": endpoint,
        "active_section": "upload",
    })


@staff_member_required
@require_http_methods(["POST"])
def delete_api(request, pk):
    endpoint = get_object_or_404(ApiEndpoint, pk=pk)
    endpoint.delete()
    messages.success(request, "API endpoint deleted.")
    return redirect("dashboard")


@api_view(["GET", "POST"])
def proxy_endpoint(request, category, slug):
    plugin = Plugin.objects.filter(category__slug=category, slug=slug).first()
    if plugin:
        return plugin_endpoint(request, plugin)

    endpoint = get_object_or_404(ApiEndpoint, category__slug=category, slug=slug, is_active=True)
    ip = client_ip(request)
    bucket = f"api-rate:{ip}"
    if not cache.add(bucket, 1, timeout=60):
        count = cache.get(bucket, 0) + 1
        cache.set(bucket, count, timeout=60)
    else:
        count = 1
    if count > 60:
        RequestLog.objects.create(endpoint=endpoint, ip=ip, status_code=429)
        return Response({"error": "Rate limit exceeded. Try again in one minute."}, status=429)

    params = request.query_params.dict() if request.method == "GET" else request.data
    if not isinstance(params, dict):
        return Response({"error": "Request body must be a JSON object."}, status=400)
    schema = endpoint.params_schema or {}
    unknown = set(params) - set(schema)
    missing = [key for key, definition in schema.items()
               if isinstance(definition, dict) and definition.get("required") and key not in params]
    if unknown or missing:
        return Response({
            "error": "Invalid parameters.",
            "unknown": sorted(unknown),
            "missing": sorted(missing),
        }, status=400)
    cache_key = None
    if request.method == "GET" and endpoint.http_method == "GET":
        cache_key = api_response_cache_key(
            "endpoint",
            endpoint.pk,
            [endpoint.http_method, endpoint.upstream_url, endpoint.params_schema],
            params,
        )
        cached_response = cache.get(cache_key)
        if cached_response is not None:
            status = cached_response["status"]
            RequestLog.objects.create(endpoint=endpoint, ip=ip, status_code=status)
            return Response(cached_response["payload"], status=status)
    try:
        validate_upstream_url(endpoint.upstream_url)
        response = requests.request(
            endpoint.http_method,
            endpoint.upstream_url,
            params=params if endpoint.http_method == "GET" else None,
            json=params if endpoint.http_method == "POST" else None,
            timeout=(3.05, 12),
            allow_redirects=False,
            headers={"Accept": "application/json"},
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": "Upstream returned a non-JSON response."}
            status = 502
        else:
            status = response.status_code
        if 300 <= status < 400:
            status, payload = 502, {"error": "Upstream redirects are not followed."}
    except (ValueError, requests.RequestException) as exc:
        status, payload = 502, {"error": "Upstream request failed.", "detail": str(exc)[:300]}
    if cache_key and 200 <= status < 300:
        cache.set(
            cache_key,
            {"status": status, "payload": payload},
            API_RESPONSE_CACHE_SECONDS,
        )
    RequestLog.objects.create(endpoint=endpoint, ip=ip, status_code=status)
    return Response(payload, status=status)


def plugin_endpoint(request, plugin):
    if not plugin.is_active:
        return Response({"error": "API endpoint not found."}, status=404)
    if request.method != plugin.http_method:
        return Response(
            {"error": f"This plugin accepts {plugin.http_method} requests only."},
            status=405,
            headers={"Allow": plugin.http_method},
        )

    ip = client_ip(request)
    bucket = f"plugin-rate:{plugin.pk}:{ip}"
    if cache.add(bucket, 1, timeout=60):
        count = 1
    else:
        try:
            count = cache.incr(bucket)
        except ValueError:
            cache.set(bucket, 1, timeout=60)
            count = 1
    if count > 30:
        RequestLog.objects.create(plugin=plugin, ip=ip, status_code=429)
        return Response({"error": "Rate limit exceeded. Try again in one minute."}, status=429)

    if plugin.requires_api_key:
        api_key = request.headers.get("X-API-Key", "")
        authorization = request.headers.get("Authorization", "")
        if not api_key and authorization.lower().startswith("bearer "):
            api_key = authorization[7:].strip()
        if not api_key or not matches_api_key(api_key, plugin.api_key_hash):
            RequestLog.objects.create(plugin=plugin, ip=ip, status_code=401)
            return Response({"error": "A valid API key is required."}, status=401)

    if request.method == "POST":
        content_length = request.META.get("CONTENT_LENGTH")
        if content_length:
            try:
                body_size = int(content_length)
            except ValueError:
                RequestLog.objects.create(plugin=plugin, ip=ip, status_code=400)
                return Response({"error": "Invalid Content-Length header."}, status=400)
            if body_size > MAX_PLUGIN_REQUEST_BYTES:
                RequestLog.objects.create(plugin=plugin, ip=ip, status_code=413)
                return Response({"error": "Request body exceeds the 64 KB limit."}, status=413)
        if request.content_type != "application/json":
            RequestLog.objects.create(plugin=plugin, ip=ip, status_code=415)
            return Response({"error": "POST requests must use application/json."}, status=415)
        body = request.data
        if not isinstance(body, dict):
            RequestLog.objects.create(plugin=plugin, ip=ip, status_code=400)
            return Response({"error": "Request body must be a JSON object."}, status=400)
    else:
        body = {}

    plugin_request = {
        "method": request.method,
        "path": request.path,
        "query": request.query_params.dict(),
        "body": body,
    }
    try:
        serialized_request = json.dumps(
            plugin_request, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError):
        RequestLog.objects.create(plugin=plugin, ip=ip, status_code=400)
        return Response({"error": "Request data must contain valid JSON values."}, status=400)
    if len(serialized_request) > MAX_PLUGIN_REQUEST_BYTES:
        RequestLog.objects.create(plugin=plugin, ip=ip, status_code=413)
        return Response({"error": "Request data exceeds the 64 KB limit."}, status=413)

    cache_key = None
    if request.method == "GET":
        cache_key = api_response_cache_key(
            "plugin",
            plugin.pk,
            [plugin.updated_at.isoformat(), plugin.http_method],
            plugin_request,
        )
        cached_response = cache.get(cache_key)
        if cached_response is not None:
            RequestLog.objects.create(
                plugin=plugin, ip=ip, status_code=cached_response["status"]
            )
            return Response(
                cached_response["payload"], status=cached_response["status"]
            )

    try:
        response_data, status = run_plugin(
            plugin.code,
            plugin_request,
            timeout_seconds=plugin.timeout_seconds,
        )
    except PluginWorkerBusy:
        status, response_data = 503, {"error": "Plugin capacity is busy. Try again shortly."}
    except PluginExecutionTimeout:
        status, response_data = 504, {"error": "Plugin execution timed out."}
    except PluginOutputLimitExceeded:
        status, response_data = 502, {"error": "Plugin output exceeded the size limit."}
    except PluginExecutionError:
        logger.exception("Plugin execution failed for plugin id %s.", plugin.pk)
        status, response_data = 502, {"error": "Plugin execution failed."}
    if cache_key and 200 <= status < 300:
        cache.set(
            cache_key,
            {"status": status, "payload": response_data},
            API_RESPONSE_CACHE_SECONDS,
        )
    RequestLog.objects.create(plugin=plugin, ip=ip, status_code=status)
    return Response(response_data, status=status)


@api_view(["GET"])
def endpoint_list(request):
    endpoints = ApiEndpoint.objects.filter(is_active=True).select_related("category")
    plugins = Plugin.objects.filter(is_active=True).select_related("category")
    context = {"request": request}
    return Response(
        ApiEndpointSerializer(endpoints, many=True, context=context).data
        + PluginSerializer(plugins, many=True, context=context).data
    )

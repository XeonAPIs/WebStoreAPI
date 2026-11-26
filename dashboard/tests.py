import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.staticfiles.finders import find as find_static
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import ApiEndpoint, Category, Plugin, RequestLog
from .plugin_keys import hash_api_key
from .plugin_runner import (
    PluginExecutionError,
    PluginExecutionTimeout,
    PluginOutputLimitExceeded,
    run_plugin,
)
from .security import validate_upstream_url


class UpstreamValidationTests(TestCase):
    def test_blocks_local_addresses_and_hostnames(self):
        for url in ("http://127.0.0.1/api", "http://localhost/api", "http://[::1]/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_upstream_url(url)

    @patch("dashboard.security.socket.getaddrinfo")
    def test_blocks_public_host_resolving_to_private_ip(self, resolve):
        resolve.return_value = [(None, None, None, None, ("10.0.0.2", 0))]
        with self.assertRaises(ValueError):
            validate_upstream_url("https://example.test/api")


class DashboardApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.category = Category.objects.create(name="Search", slug="search")
        self.endpoint = ApiEndpoint.objects.create(
            name="Sample Search",
            slug="sample-search",
            category=self.category,
            upstream_url="https://example.com/search",
            params_schema={"q": {"required": True}},
        )
        self.client = APIClient()

    def test_stats_reflect_database_and_request_ip(self):
        RequestLog.objects.create(endpoint=self.endpoint, ip="203.0.113.8", status_code=200)
        response = self.client.get(reverse("stats_api"), REMOTE_ADDR="198.51.100.4")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_requests"], 1)
        self.assertEqual(response.data["total_features"], 2)
        self.assertEqual(response.data["your_ip"], "198.51.100.4")

    def test_demo_endpoint_is_seeded(self):
        demo = ApiEndpoint.objects.get(category__slug="demo", slug="example-todo")
        self.assertTrue(demo.is_active)
        self.assertEqual(demo.path, "/api/demo/example-todo/")

    def test_dashboard_renders_database_categories_and_endpoints(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back, Xeon")
        self.assertContains(response, '<div class="user-chip"><strong>Xeon</strong><small>MEMBER</small></div>')
        self.assertContains(response, "V1.0 · OPEN TO USE")
        self.assertContains(response, "dashboard/js/dashboard.obfuscated.js")
        self.assertIsNotNone(find_static("dashboard/js/dashboard.obfuscated.js"))
        self.assertIsNone(find_static("dashboard/js/dashboard.js"))
        self.assertContains(response, "GPL-3.0")
        self.assertContains(response, 'href="https://t.me/XeonModz"')
        self.assertContains(response, 'href="/docs/"')
        self.assertNotContains(response, 'href="https://www.gnu.org/licenses/gpl-3.0.html"')
        self.assertContains(response, "Quick API Access")
        self.assertContains(response, "Sample Search")
        self.assertContains(response, "Search")
        self.assertContains(response, 'data-category="plugins"')
        self.assertContains(response, 'aria-controls="category-menu-search"')
        self.assertContains(
            response,
            '<button class="category-endpoint-link api-preview-trigger" type="button"',
        )
        self.assertContains(
            response,
            f'id="api-{self.endpoint.pk}" data-api-category="search" data-api-url="/api/search/sample-search/"',
        )
        self.assertContains(response, "data-api-url-details hidden")
        self.assertContains(
            response,
            'data-copy-url="http://testserver/api/search/sample-search/"',
        )

    def test_dashboard_shows_get_plugin_url_with_query_placeholder(self):
        Plugin.objects.create(
            name="Sample Plugin",
            slug="sample-plugin",
            category=self.category,
            http_method="GET",
            code='def handler(request):\n    return {"ok": True}',
            api_key_hash=hash_api_key("test-plugin-key"),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "http://testserver/api/search/sample-plugin/?url=")
        self.assertContains(response, 'data-api-url="/api/search/sample-plugin/?url="')
        self.assertContains(
            response,
            'data-api-url="/api/search/sample-plugin/?url=" data-api-method="GET"',
        )
        self.assertContains(response, '<div class="api-row-url" data-api-url-details>')
        self.assertContains(response, 'data-api-preview aria-live="polite" hidden')
        self.assertContains(response, "data-filter-empty")
        self.assertContains(response, 'data-api-url="/api/search/sample-search/"')

    def test_dashboard_reuses_cached_database_data(self):
        self.client.get(reverse("dashboard"))
        with self.assertNumQueries(0):
            response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_docs_page_explains_how_to_use_the_website(self):
        response = self.client.get(reverse("docs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "How to use XEON'S API")
        self.assertContains(response, "GET /api/demo/example-todo/")
        self.assertContains(response, "POST /api/&lt;category-slug&gt;/&lt;endpoint-slug&gt;/")
        self.assertContains(response, "Copy URL")

    def test_login_page_uses_custom_template(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRIVATE CONSOLE")
        self.assertContains(response, "Make a better")
        self.assertContains(response, "COPY A URL")
        self.assertContains(response, "Test the idea.")
        self.assertContains(response, "See the response")

    def test_proxy_requires_required_parameters(self):
        response = self.client.get(reverse(
            "proxy_endpoint", kwargs={"category": "search", "slug": "sample-search"}
        ))
        self.assertEqual(response.status_code, 400)
        self.assertIn("q", response.data["missing"])
        self.assertEqual(RequestLog.objects.count(), 0)

    @patch("dashboard.views.requests.request")
    def test_proxy_logs_successful_upstream_request(self, send):
        send.return_value.status_code = 200
        send.return_value.json.return_value = {"ok": True}
        url = reverse("proxy_endpoint", kwargs={"category": "search", "slug": "sample-search"})
        response = self.client.get(url, {"q": "xeon"}, REMOTE_ADDR="203.0.113.10")
        repeated_response = self.client.get(url, {"q": "xeon"}, REMOTE_ADDR="203.0.113.10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"ok": True})
        self.assertEqual(repeated_response.data, {"ok": True})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(RequestLog.objects.count(), 2)
        self.assertEqual(RequestLog.objects.first().ip, "203.0.113.10")
        self.assertFalse(send.call_args.kwargs["allow_redirects"])


class UploadAccessTests(TestCase):
    def test_api_upload_requires_staff_login(self):
        response = self.client.get(reverse("upload_api"))
        self.assertEqual(response.status_code, 302)

    def test_json_import_rejects_non_object_params(self):
        from .views import import_api_config

        user = get_user_model().objects.create_user(username="admin")
        imported, errors = import_api_config([{
            "name": "Search", "category": "Search",
            "upstream_url": "https://example.com/search",
            "params_schema": [],
        }], user)
        self.assertEqual(imported, 0)
        self.assertIn("params_schema must be a JSON object", errors[0])


class PluginEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.category = Category.objects.create(name="Tools", slug="tools")
        self.api_key = "test-plugin-key"
        self.plugin = Plugin.objects.create(
            name="Greeting",
            slug="greeting",
            category=self.category,
            http_method="GET",
            code='def handler(request):\n    return {"name": request["query"].get("name", "world")}',
            api_key_hash=hash_api_key(self.api_key),
            requires_api_key=True,
        )
        self.client = APIClient()

    def test_plugin_returns_handler_response_with_valid_api_key(self):
        response = self.client.get(
            reverse("proxy_endpoint", kwargs={"category": "tools", "slug": "greeting"}),
            {"name": "Xeon"},
            HTTP_X_API_KEY=self.api_key,
            REMOTE_ADDR="203.0.113.12",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"name": "Xeon"})
        self.assertEqual(RequestLog.objects.get(plugin=self.plugin).status_code, 200)

    @patch("dashboard.views.run_plugin", return_value=({"result": "cached"}, 200))
    def test_plugin_caches_identical_get_requests(self, run_handler):
        url = reverse("proxy_endpoint", kwargs={"category": "tools", "slug": "greeting"})
        first = self.client.get(
            url, {"name": "Xeon"}, HTTP_X_API_KEY=self.api_key,
            REMOTE_ADDR="203.0.113.14",
        )
        repeated = self.client.get(
            url, {"name": "Xeon"}, HTTP_X_API_KEY=self.api_key,
            REMOTE_ADDR="203.0.113.14",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.data, {"result": "cached"})
        run_handler.assert_called_once()
        self.assertEqual(RequestLog.objects.filter(plugin=self.plugin).count(), 2)

    @patch("dashboard.views.run_plugin", return_value=({"error": "invalid"}, 422))
    def test_plugin_returns_handler_status_and_passes_configured_timeout(self, run_handler):
        self.plugin.timeout_seconds = 30
        self.plugin.save(update_fields=["timeout_seconds"])
        response = self.client.get(
            reverse("proxy_endpoint", kwargs={"category": "tools", "slug": "greeting"}),
            HTTP_X_API_KEY=self.api_key,
            REMOTE_ADDR="203.0.113.15",
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data, {"error": "invalid"})
        self.assertEqual(run_handler.call_args.kwargs["timeout_seconds"], 30)
        self.assertEqual(RequestLog.objects.get(plugin=self.plugin).status_code, 422)

    def test_plugin_rejects_missing_api_key(self):
        response = self.client.get(
            reverse("proxy_endpoint", kwargs={"category": "tools", "slug": "greeting"})
        )
        self.assertEqual(response.status_code, 401)

    def test_plugin_exception_returns_json_error_and_logs_failure(self):
        self.plugin.code = (
            "def handler(request):\n"
            "    raise RuntimeError('private plugin details')"
        )
        self.plugin.save(update_fields=["code"])
        response = self.client.get(
            reverse("proxy_endpoint", kwargs={"category": "tools", "slug": "greeting"}),
            HTTP_X_API_KEY=self.api_key,
            REMOTE_ADDR="203.0.113.13",
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data, {"error": "Plugin execution failed."})
        self.assertEqual(RequestLog.objects.get(plugin=self.plugin).status_code, 502)

    def test_disabled_plugin_is_not_routable(self):
        self.plugin.is_active = False
        self.plugin.save(update_fields=["is_active"])
        response = self.client.get(
            reverse("proxy_endpoint", kwargs={"category": "tools", "slug": "greeting"}),
            HTTP_X_API_KEY=self.api_key,
        )
        self.assertEqual(response.status_code, 404)

    def test_plugin_listing_exposes_url_and_key_requirement_not_source_or_key(self):
        response = self.client.get(reverse("endpoint_list"))
        self.assertEqual(response.status_code, 200)
        plugin_data = next(item for item in response.data if item["name"] == "Greeting")
        self.assertEqual(plugin_data["endpoint_url"], "http://testserver/api/tools/greeting/")
        self.assertTrue(plugin_data["requires_api_key"])
        self.assertNotIn("code", plugin_data)
        self.assertNotIn("api_key_hash", plugin_data)


class PluginRunnerTests(SimpleTestCase):
    def test_handler_returns_a_json_object(self):
        result = run_plugin(
            'def handler(request):\n    return {"method": request["method"]}',
            {"method": "GET", "query": {}, "body": {}},
        )
        self.assertEqual(result, ({"method": "GET"}, 200))

    def test_handler_can_return_payload_and_status_code(self):
        result = run_plugin(
            'def handler(request):\n    return {"error": "invalid"}, 422',
            {},
        )
        self.assertEqual(result, ({"error": "invalid"}, 422))

    def test_handler_can_import_installed_third_party_packages(self):
        result = run_plugin(
            "import requests\n"
            "def handler(request):\n"
            "    return {'version': requests.__version__}",
            {},
        )
        self.assertEqual(result[0]["version"], __import__("requests").__version__)

    def test_handler_rejects_non_object_response(self):
        with self.assertRaises(PluginExecutionError):
            run_plugin("def handler(request):\n    return []", {})

    def test_handler_rejects_invalid_status_code(self):
        with self.assertRaises(PluginExecutionError):
            run_plugin(
                "def handler(request):\n    return {}, 600",
                {},
            )

    def test_handler_output_is_bounded(self):
        source = (
            "import os\n"
            "def handler(request):\n"
            "    os.write(1, b'x' * (300 * 1024))\n"
            "    return {}\n"
        )
        with self.assertRaises(PluginOutputLimitExceeded):
            run_plugin(source, {})

    def test_timeout_is_reported(self):
        with self.assertRaises(PluginExecutionTimeout):
            run_plugin(
                "import time\ndef handler(request):\n    time.sleep(2)\n    return {}",
                {},
                timeout_seconds=0.1,
            )

    def test_timeout_cannot_exceed_configured_maximum(self):
        with self.assertRaises(PluginExecutionError):
            run_plugin(
                "def handler(request):\n    return {}",
                {},
                timeout_seconds=121,
            )


class PluginAdminAccessTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Tools", slug="tools")

    def test_non_staff_cannot_open_plugin_admin(self):
        user = get_user_model().objects.create_user(
            username="member", password="test-pass"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("admin:dashboard_plugin_changelist"))
        self.assertEqual(response.status_code, 302)

    def test_plugin_admin_list_shows_absolute_api_url(self):
        staff = get_user_model().objects.create_user(
            username="staff", password="test-pass", is_staff=True, is_superuser=True
        )
        Plugin.objects.create(
            name="Greeting",
            slug="greeting",
            category=self.category,
            code='def handler(request):\n    return {"ok": True}',
            api_key_hash=hash_api_key("test-plugin-key"),
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("admin:dashboard_plugin_changelist"))
        self.assertContains(response, "http://testserver/api/tools/greeting/?url=")

    def test_staff_can_create_plugin_and_receives_one_time_api_key(self):
        staff = get_user_model().objects.create_user(
            username="staff", password="test-pass", is_staff=True, is_superuser=True
        )
        self.client.force_login(staff)
        response = self.client.post(
            reverse("admin:dashboard_plugin_add"),
            {
                "name": "Hello World",
                "category": self.category.pk,
                "slug": "hello-world",
                "description": "A test plugin",
                "http_method": "GET",
                "timeout_seconds": 15,
                "code": 'def handler(request):\n    return {"ok": True}',
                "is_active": "on",
                "requires_api_key": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Copy this plugin API key now")
        plugin = Plugin.objects.get(slug="hello-world")
        self.assertEqual(plugin.created_by, staff)
        self.assertTrue(plugin.requires_api_key)
        match = re.search(
            r"Copy this plugin API key now; it will not be shown again: ([A-Za-z0-9_-]+)",
            response.content.decode(),
        )
        self.assertIsNotNone(match)
        self.assertEqual(hash_api_key(match.group(1)), plugin.api_key_hash)


class AdminThemeTests(TestCase):
    def test_admin_index_uses_xeon_branding_and_theme_css(self):
        user = get_user_model().objects.create_user(
            username="staff", password="test-pass", is_staff=True
        )
        self.client.force_login(user)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "XEON'S API")
        self.assertContains(response, "Welcome, <strong>HRITHIK UDAY</strong>.")
        self.assertContains(response, "dashboard/css/admin.css")
        self.client.logout()
        login_response = self.client.get(reverse("admin:login"))
        self.assertEqual(login_response.status_code, 200)
        self.assertContains(login_response, "dashboard/css/style.css")
        self.assertContains(login_response, 'class="login-page"')
        self.assertContains(login_response, 'class="login-layout"')
        self.assertContains(login_response, 'id="login-form"')
        self.assertContains(login_response, 'name="username"')
        self.assertContains(login_response, 'name="next"')
        self.assertContains(login_response, "ADMIN SIGN IN")
        self.assertContains(login_response, "V1.0 · OPEN TO USE")
        self.assertContains(login_response, "Continue to admin workspace")
        login_submission = self.client.post(reverse("admin:login"), {
            "username": "staff",
            "password": "test-pass",
            "next": reverse("admin:index"),
        })
        self.assertRedirects(login_submission, reverse("admin:index"))
        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard_response, '<div class="user-chip"><strong>HRITHIK UDAY</strong><small>ADMIN</small></div>')
        upload_response = self.client.get(reverse("upload_api"))
        self.assertContains(upload_response, '<div class="user-chip"><strong>HRITHIK UDAY</strong><small>ADMIN</small></div>')

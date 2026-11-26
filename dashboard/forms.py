import ast
import json

from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .models import ApiEndpoint, Category, Plugin
from .plugin_runner import MAX_PLUGIN_CODE_BYTES
from .security import validate_upstream_url


class ApiEndpointForm(forms.ModelForm):
    category_name = forms.CharField(max_length=80, required=False, label="Category")

    class Meta:
        model = ApiEndpoint
        fields = ["name", "category", "http_method", "upstream_url", "params_schema", "description", "is_active"]
        widgets = {
            "params_schema": forms.Textarea(attrs={"rows": 7, "placeholder": '{"query": {"required": true}}'}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].required = False
        self.fields["params_schema"].widget = forms.Textarea(attrs={
            "rows": 7, "placeholder": '{"query": {"required": true}}'
        })

    def clean_upstream_url(self):
        url = self.cleaned_data["upstream_url"]
        try:
            validate_upstream_url(url)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return url

    def clean_params_schema(self):
        value = self.cleaned_data["params_schema"]
        if not isinstance(value, dict):
            raise ValidationError("Parameters must be a JSON object.")
        if len(json.dumps(value)) > 16_384:
            raise ValidationError("Parameter configuration must be 16 KB or less.")
        return value

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        category_name = cleaned.get("category_name", "").strip()
        if not category and not category_name:
            self.add_error("category_name", "Select an existing category or enter a new one.")
        name = cleaned.get("name", "")
        if name:
            category_slug = category.slug if category else slugify(category_name)
            endpoint_slug = self.instance.slug or slugify(name)
            if category_slug and Plugin.objects.filter(
                category__slug=category_slug, slug=endpoint_slug
            ).exists():
                self.add_error("name", "A plugin already uses this category and endpoint path.")
        return cleaned

    def save(self, commit=True):
        endpoint = super().save(commit=False)
        category_name = self.cleaned_data.get("category_name", "").strip()
        if category_name:
            category, _ = Category.objects.get_or_create(
                slug=slugify(category_name), defaults={"name": category_name}
            )
            endpoint.category = category
        if not endpoint.slug:
            endpoint.slug = slugify(endpoint.name)
        if commit:
            endpoint.save()
            self.save_m2m()
        return endpoint


class JsonImportForm(forms.Form):
    file = forms.FileField(label="JSON configuration file")

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded.size > 1_048_576:
            raise ValidationError("JSON file must be 1 MB or smaller.")
        if not uploaded.name.lower().endswith(".json"):
            raise ValidationError("Upload a .json file.")
        try:
            data = json.loads(uploaded.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValidationError("File must contain valid UTF-8 encoded JSON.") from exc
        if not isinstance(data, list) or not data or len(data) > 100:
            raise ValidationError("JSON must be a non-empty array of at most 100 APIs.")
        uploaded.seek(0)
        return data


class PluginAdminForm(forms.ModelForm):
    slug = forms.SlugField(required=False, max_length=140)

    class Meta:
        model = Plugin
        fields = [
            "name", "category", "slug", "description", "http_method", "timeout_seconds", "code",
            "is_active", "requires_api_key",
        ]
        widgets = {
            "code": forms.Textarea(attrs={
                "rows": 20,
                "spellcheck": "false",
                "placeholder": 'def handler(request):\n    return {"ok": True}',
            }),
        }

    def clean_code(self):
        code = self.cleaned_data["code"]
        if len(code.encode("utf-8")) > MAX_PLUGIN_CODE_BYTES:
            raise ValidationError("Plugin code must be 64 KB or smaller.")
        try:
            tree = ast.parse(code)
            compile(tree, "<plugin>", "exec")
        except (SyntaxError, ValueError, RecursionError) as error:
            raise ValidationError(f"Plugin code is invalid: {error}") from error
        if not any(
            isinstance(node, ast.FunctionDef) and node.name == "handler"
            for node in tree.body
        ):
            raise ValidationError("Plugin code must define handler(request).")
        return code

    def clean_slug(self):
        slug = self.cleaned_data.get("slug") or slugify(self.cleaned_data.get("name", ""))
        if not slug:
            raise ValidationError("Enter a name containing letters or numbers.")
        return slug

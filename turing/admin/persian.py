"""Persian Admin presentation helpers (no model / API / schema changes)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from django.contrib import admin
from django.db import models
from django.http import HttpRequest

from turing.admin import fa as fa_labels


@contextmanager
def _persian_model_meta(model: type[models.Model]) -> Iterator[None]:
    """Temporarily set Persian verbose names for admin page titles only."""
    titles = fa_labels.model_titles(model.__name__)
    if not titles:
        yield
        return
    meta = model._meta
    old = (meta.verbose_name, meta.verbose_name_plural)
    meta.verbose_name, meta.verbose_name_plural = titles
    try:
        yield
    finally:
        meta.verbose_name, meta.verbose_name_plural = old


class PersianAdminMixin:
    """
    Apply Persian labels/titles in Admin views without mutating models globally.

    Safe for shared processes: verbose_name is restored after each admin view.
    """

    empty_value_display = fa_labels.EMPTY_VALUE

    def label_for_field(self, name, model=None, return_attr=False, form=None):
        field_name = name.split("__")[-1] if isinstance(name, str) else name
        persian = fa_labels.FIELD_LABELS.get(name) or fa_labels.FIELD_LABELS.get(field_name)
        if persian and not return_attr:
            return persian
        if persian and return_attr:
            label, attr = super().label_for_field(  # type: ignore[misc]
                name, model=model, return_attr=True, form=form
            )
            return persian, attr
        return super().label_for_field(  # type: ignore[misc]
            name, model=model, return_attr=return_attr, form=form
        )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in fa_labels.FIELD_LABELS:
            kwargs.setdefault("label", fa_labels.FIELD_LABELS[db_field.name])
        if db_field.name in fa_labels.FIELD_HELP:
            kwargs.setdefault("help_text", fa_labels.FIELD_HELP[db_field.name])
        return super().formfield_for_dbfield(db_field, request, **kwargs)  # type: ignore[misc]

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        formfield = super().formfield_for_choice_field(  # type: ignore[misc]
            db_field, request, **kwargs
        )
        if formfield is not None and getattr(formfield, "choices", None):
            formfield.choices = [
                (value, fa_labels.status_label(str(value), label) if value != "" else label)
                for value, label in formfield.choices
            ]
        return formfield

    def changelist_view(self, request, extra_context=None):
        with _persian_model_meta(self.model):  # type: ignore[attr-defined]
            return super().changelist_view(request, extra_context=extra_context)  # type: ignore[misc]

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        with _persian_model_meta(self.model):  # type: ignore[attr-defined]
            return super().changeform_view(  # type: ignore[misc]
                request, object_id=object_id, form_url=form_url, extra_context=extra_context
            )

    def delete_view(self, request, object_id, extra_context=None):
        with _persian_model_meta(self.model):  # type: ignore[attr-defined]
            return super().delete_view(request, object_id, extra_context=extra_context)  # type: ignore[misc]

    def history_view(self, request, object_id, extra_context=None):
        with _persian_model_meta(self.model):  # type: ignore[attr-defined]
            return super().history_view(request, object_id, extra_context=extra_context)  # type: ignore[misc]


class PersianInlineMixin:
    """Persian labels for tabular/stacked inlines."""

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in fa_labels.FIELD_LABELS:
            kwargs.setdefault("label", fa_labels.FIELD_LABELS[db_field.name])
        if db_field.name in fa_labels.FIELD_HELP:
            kwargs.setdefault("help_text", fa_labels.FIELD_HELP[db_field.name])
        return super().formfield_for_dbfield(db_field, request, **kwargs)  # type: ignore[misc]


def configure_admin_site(site: admin.AdminSite | None = None) -> None:
    """Configure default AdminSite chrome + Persian model names on the index."""
    site = site or admin.site
    site.site_header = fa_labels.SITE_HEADER
    site.site_title = fa_labels.SITE_TITLE
    site.index_title = fa_labels.INDEX_TITLE

    if not getattr(site, "_turing_fa_patched", False):
        original_get_app_list = site.get_app_list

        def get_app_list(request: HttpRequest, app_label: str | None = None) -> list[dict[str, Any]]:
            app_list = original_get_app_list(request, app_label)
            for app in app_list:
                if app.get("app_label") == "turing":
                    app["name"] = fa_labels.APP_LABEL
                for model in app.get("models", []):
                    object_name = model.get("object_name") or ""
                    titles = fa_labels.model_titles(object_name)
                    if titles:
                        model["name"] = titles[1]
            return app_list

        site.get_app_list = get_app_list  # type: ignore[method-assign]
        site._turing_fa_patched = True  # type: ignore[attr-defined]

    if not getattr(site, "_turing_logout_patched", False):
        _patch_admin_logout(site)
        site._turing_logout_patched = True  # type: ignore[attr-defined]


def _patch_admin_logout(site: admin.AdminSite) -> None:
    """
    Django 5+ LogoutView is POST-only; GET /admin/logout/ returns 405 + empty body.

    Provide a confirmation page on GET and redirect to admin login after POST.
    """
    from django.contrib.auth.views import LogoutView
    from django.shortcuts import redirect
    from django.template.response import TemplateResponse
    from django.urls import reverse
    from django.utils.translation import gettext as _

    class TuringAdminLogoutView(LogoutView):
        http_method_names = ["get", "post", "options"]

        def get(self, request, *args, **kwargs):
            login_url = reverse("admin:login")
            if not request.user.is_authenticated:
                return redirect(login_url)
            context = self.get_context_data()
            context.update(
                {
                    "title": _("Log out"),
                    "subtitle": None,
                }
            )
            return TemplateResponse(request, "admin/logout_confirm.html", context)

    def logout(request, extra_context=None):
        defaults = {
            "next_page": reverse("admin:login"),
            "extra_context": {
                **site.each_context(request),
                "has_permission": False,
                **(extra_context or {}),
            },
        }
        request.current_app = site.name
        return TuringAdminLogoutView.as_view(**defaults)(request)

    site.logout = logout  # type: ignore[method-assign]

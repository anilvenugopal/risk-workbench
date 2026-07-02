"""Unit tests for _safe_next open-redirect guard."""

from __future__ import annotations


class TestSafeNext:
    def _safe_next(self, url):
        from app.routers.auth import _safe_next
        return _safe_next(url)

    def test_none_returns_root(self):
        assert self._safe_next(None) == "/"

    def test_empty_string_returns_root(self):
        assert self._safe_next("") == "/"

    def test_valid_relative_path_returned(self):
        assert self._safe_next("/submissions") == "/submissions"

    def test_nested_relative_path_returned(self):
        assert self._safe_next("/admin/users") == "/admin/users"

    def test_absolute_url_rejected(self):
        assert self._safe_next("https://evil.com/steal") == "/"

    def test_http_url_rejected(self):
        assert self._safe_next("http://evil.com") == "/"

    def test_protocol_relative_rejected(self):
        assert self._safe_next("//evil.com/path") == "/"

    def test_no_leading_slash_rejected(self):
        assert self._safe_next("evil.com/path") == "/"

    def test_root_path_returned(self):
        assert self._safe_next("/") == "/"

    def test_path_with_query_string_returned(self):
        assert self._safe_next("/submissions?status=active") == "/submissions?status=active"

"""Thin API client. The dashboard never touches the database: all access goes through the authorised API."""
from __future__ import annotations

import os

import httpx

API_URL = os.environ.get("API_URL", "http://localhost:8000")


class ApiError(Exception):
    pass


class Api:
    def __init__(self, token: str | None = None):
        self.token = token
        self.client = httpx.Client(base_url=API_URL, timeout=120)

    def _h(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _check(self, r: httpx.Response):
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
                raise ApiError(f"{err.get('code', r.status_code)}: {err.get('message', r.text)}")
            except ValueError:
                raise ApiError(f"HTTP {r.status_code}") from None
        return r.json()

    def login(self, tenant, username, password):
        data = self._check(self.client.post("/api/v1/auth/token", json={"tenant_id": tenant, "username": username, "password": password}))
        self.token = data["access_token"]
        return data

    def get(self, path, **params):
        return self._check(self.client.get(path, params={k: v for k, v in params.items() if v is not None}, headers=self._h()))

    def post(self, path, json=None, headers=None):
        return self._check(self.client.post(path, json=json, headers={**self._h(), **(headers or {})}))

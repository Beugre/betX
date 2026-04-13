"""
betX – Classe de base pour les collecteurs de données.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import httpx

from betx.logger import get_logger


class BaseCollector(ABC):
    """Interface commune pour tous les collecteurs."""

    def __init__(self, name: str, base_url: str, api_key: str = ""):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.log = get_logger(f"data.{name}")
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["x-apisports-key"] = self.api_key
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        """Effectue un GET avec logging et gestion d'erreur."""
        self.log.info(f"GET {endpoint} params={params}")
        try:
            resp = self.client.get(endpoint, params=params or {})
            resp.raise_for_status()
            data = resp.json()
            return data
        except httpx.HTTPStatusError as e:
            self.log.error(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
            raise
        except Exception as e:
            self.log.error(f"Erreur requête: {e}")
            raise

    @abstractmethod
    def fetch_matches(self, target_date: date) -> list[dict[str, Any]]:
        """Récupère les matchs pour une date donnée."""
        ...

    @abstractmethod
    def fetch_results(self, target_date: date) -> list[dict[str, Any]]:
        """Récupère les résultats pour une date donnée."""
        ...

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

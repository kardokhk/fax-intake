"""App-only sign-in to Microsoft Graph (client credentials) for an Entra app registration.

The app should hold only Mail.ReadWrite limited to the fax mailbox (Exchange RBAC for
Applications) and Sites.Selected granted on the intake site. A certificate is preferred to a
client secret; with a secret, put it in an environment variable or a key vault, never in config.
"""
from __future__ import annotations

import os
import time

import httpx


class TokenAuth(httpx.Auth):
    def __init__(self, tenant_id: str, client_id: str, client_secret: str | None = None):
        self.tenant_id, self.client_id = tenant_id, client_id
        self.secret = client_secret or os.environ["FAXINTAKE_CLIENT_SECRET"]
        self._token, self._exp = None, 0.0

    def _fetch(self) -> None:
        r = httpx.post(f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                       data={"grant_type": "client_credentials", "client_id": self.client_id,
                             "client_secret": self.secret, "scope": "https://graph.microsoft.com/.default"},
                       timeout=30)
        r.raise_for_status()
        d = r.json()
        self._token, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 3600)) - 300

    def auth_flow(self, request):
        if not self._token or time.time() > self._exp:
            self._fetch()
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request

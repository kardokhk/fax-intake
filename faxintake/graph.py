"""Minimal Microsoft Graph client for the calls fax intake needs.

Every request goes through ``request``, which retries throttling (429), server errors (5xx)
and dropped connections with the delay Graph asks for (Retry-After) or capped exponential
backoff. Writes are made safe to repeat by the callers (unique register key, upload with
conflictBehavior=fail, eTag checks), so a retry after a lost response never stores a fax twice.
"""
from __future__ import annotations

import base64
import random
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .clock import Clock

RETRYABLE = {429, 500, 502, 503, 504}


class GraphError(Exception):
    def __init__(self, status: int, code: str, message: str, method: str = "", path: str = ""):
        super().__init__(f"{status} {code}: {message} ({method} {path})")
        self.status, self.code, self.message = status, code, message

    @property
    def transient(self) -> bool:
        return self.status in RETRYABLE or self.status == 0


class Conflict(GraphError):
    """409, or 412 precondition failed: the resource exists or changed under us."""


class NotFound(GraphError):
    pass


@dataclass
class Attachment:
    id: str
    name: str
    content_type: str
    size: int
    content: bytes


class GraphClient:
    def __init__(self, http: httpx.Client, clock: Clock, max_retries: int = 6,
                 backoff_base_s: float = 2.0, backoff_cap_s: float = 120.0, rng: random.Random | None = None):
        self.http, self.clock = http, clock
        self.max_retries, self.base, self.cap = max_retries, backoff_base_s, backoff_cap_s
        self.rng = rng or random.Random(0)
        self.retries = 0  # counted for the run report

    # ---- transport -------------------------------------------------------------------------
    def request(self, method: str, path: str, *, json=None, content: bytes | None = None,
                params=None, headers=None) -> httpx.Response:
        h = {"Prefer": 'IdType="ImmutableId"'}  # message ids stay valid when a message is moved
        h.update(headers or {})
        attempt = 0
        while True:
            try:
                r = self.http.request(method, path, json=json, content=content, params=params, headers=h)
            except (httpx.TransportError,) as e:
                status, code, msg, wait = 0, "transport", str(e), None
            else:
                if r.status_code < 400:
                    return r
                status = r.status_code
                body = _error_body(r)
                code, msg = body.get("code", ""), body.get("message", "")
                wait = r.headers.get("Retry-After")
                if status not in RETRYABLE:
                    cls = Conflict if status in (409, 412) else NotFound if status == 404 else GraphError
                    raise cls(status, code, msg, method, path)
            if attempt >= self.max_retries:
                raise GraphError(status, code, msg, method, path)
            delay = float(wait) if wait else min(self.cap, self.base * 2 ** attempt) * (0.5 + self.rng.random())
            attempt += 1
            self.retries += 1
            self.clock.sleep(delay)

    # ---- mail ------------------------------------------------------------------------------
    def folder_id(self, mailbox: str, name: str) -> str:
        if name.lower() == "inbox":
            return "inbox"
        r = self.request("GET", f"/users/{mailbox}/mailFolders",
                         params={"$filter": f"displayName eq '{name}'", "$top": "10"})
        vals = r.json()["value"]
        if not vals:
            raise NotFound(404, "folderNotFound", name)
        return vals[0]["id"]

    def list_messages(self, mailbox: str, folder_id: str, since_iso: str) -> list[dict]:
        out, params = [], {"$filter": f"receivedDateTime ge {since_iso}", "$orderby": "receivedDateTime asc",
                           "$top": "50", "$select": "id,internetMessageId,subject,from,receivedDateTime,"
                                                    "hasAttachments,body,categories"}
        url = f"/users/{mailbox}/mailFolders/{folder_id}/messages"
        while url:
            r = self.request("GET", url, params=params)
            d = r.json()
            out.extend(d["value"])
            url, params = d.get("@odata.nextLink"), None
        return out

    def attachments(self, mailbox: str, message_id: str) -> list[Attachment]:
        r = self.request("GET", f"/users/{mailbox}/messages/{message_id}/attachments")
        out = []
        for a in r.json()["value"]:
            if a.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            out.append(Attachment(a["id"], a["name"], a.get("contentType", ""), a.get("size", 0),
                                  base64.b64decode(a["contentBytes"])))
        return out

    def move_message(self, mailbox: str, message_id: str, dest_folder_id: str) -> None:
        self.request("POST", f"/users/{mailbox}/messages/{message_id}/move", json={"destinationId": dest_folder_id})

    def send_mail(self, mailbox: str, to: list[str], subject: str, body: str) -> None:
        msg = {"subject": subject, "body": {"contentType": "Text", "content": body},
               "toRecipients": [{"emailAddress": {"address": a}} for a in to]}
        self.request("POST", f"/users/{mailbox}/sendMail", json={"message": msg, "saveToSentItems": True})

    # ---- files -----------------------------------------------------------------------------
    def upload_new(self, drive_id: str, path: str, data: bytes) -> dict:
        """Create a file; raises Conflict if anything already exists at ``path`` (never overwrites)."""
        r = self.request("PUT", f"/drives/{drive_id}/root:/{quote(path)}:/content",
                         params={"@microsoft.graph.conflictBehavior": "fail"}, content=data,
                         headers={"Content-Type": "application/pdf"})
        return r.json()

    def item_by_path(self, drive_id: str, path: str) -> dict:
        return self.request("GET", f"/drives/{drive_id}/root:/{quote(path)}").json()

    def item_by_id(self, drive_id: str, item_id: str) -> dict:
        return self.request("GET", f"/drives/{drive_id}/items/{item_id}").json()

    # ---- lists -----------------------------------------------------------------------------
    def create_item(self, site: str, lst: str, fields: dict) -> dict:
        return self.request("POST", f"/sites/{site}/lists/{lst}/items", json={"fields": fields}).json()

    def find_items(self, site: str, lst: str, filter_: str, top: int = 50) -> list[dict]:
        r = self.request("GET", f"/sites/{site}/lists/{lst}/items",
                         params={"$expand": "fields", "$filter": filter_, "$top": str(top)})
        return r.json()["value"]

    def update_item(self, site: str, lst: str, item_id: str, fields: dict, etag: str | None = None) -> dict:
        h = {"If-Match": etag} if etag else None
        return self.request("PATCH", f"/sites/{site}/lists/{lst}/items/{item_id}/fields",
                            json=fields, headers=h).json()


def _error_body(r: httpx.Response) -> dict:
    try:
        return r.json().get("error", {}) or {}
    except Exception:
        return {}

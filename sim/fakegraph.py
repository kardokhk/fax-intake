"""In-memory stand-in for the parts of Microsoft Graph that fax intake uses, with fault injection.

It models: a shared mailbox with folders, messages and file attachments (immutable message ids);
a SharePoint document library (create with conflictBehavior, QuickXorHash on every file);
SharePoint lists with an enforced-unique column and eTags; sendMail. Faults: random throttling
(429 + Retry-After) and server errors (503), timed outages per service (mail, files, lists, or
all), permission loss (403), and "lost responses" where the write is committed but the caller
never hears back. It also keeps a write log so the test bench can check what really happened.
"""
from __future__ import annotations

import base64
import json
import random
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from faxintake.clock import SimClock, iso, parse_iso
from faxintake.quickxor import quickxorhash


@dataclass
class Outage:
    service: str          # "mail", "files", "lists" or "all"
    start: datetime
    end: datetime
    status: int = 503     # 503 = unavailable, 403 = access denied (expired secret)


@dataclass
class FaultPlan:
    p_throttle: dict = field(default_factory=dict)   # service -> probability of 429
    p_error: dict = field(default_factory=dict)      # service -> probability of 503
    p_lost: float = 0.0                              # probability a committed write loses its response
    outages: list = field(default_factory=list)
    latency_s: float = 0.15


class LostResponse(httpx.ReadTimeout):
    pass


class FakeGraph:
    def __init__(self, clock: SimClock, mailbox: str, faults: FaultPlan | None = None, seed: int = 0):
        self.clock, self.mailbox = clock, mailbox.lower()
        self.faults = faults or FaultPlan()
        self.rng = random.Random(seed)
        self.lock = threading.RLock()
        self.folders = {"inbox": "Inbox"}
        self.messages: dict[str, dict] = {}            # id -> message incl. folder and attachments
        self.files: dict[str, dict] = {}               # normalised path -> driveItem incl. content
        self.lists: dict[str, dict] = {}               # list name -> {"items": {id: item}, "unique": col}
        self.sent: list[dict] = []
        self.file_writes: list[tuple] = []             # (time, path, sha) every committed create/overwrite
        self.requests = 0
        self.injected = {"throttle": 0, "error": 0, "lost": 0, "outage": 0}

    # ---- setup helpers ----------------------------------------------------------------------
    def add_folder(self, name: str) -> str:
        fid = "fld-" + re.sub(r"\W+", "-", name.lower())
        self.folders[fid] = name
        return fid

    def add_list(self, name: str, unique: str | None = None) -> None:
        self.lists[name] = {"items": {}, "unique": unique, "next": 1}

    def deliver(self, msg: dict) -> str:
        """Put a message into the Inbox. ``msg`` needs subject, from, received (datetime), body, attachments."""
        with self.lock:
            mid = "AAkALg" + uuid.UUID(int=self.rng.getrandbits(128)).hex
            self.messages[mid] = {"id": mid, "folder": "inbox", "subject": msg["subject"],
                                  "from": msg["from"], "receivedDateTime": iso(msg["received"]),
                                  "internetMessageId": msg.get("internetMessageId") or f"<{uuid.uuid4().hex}@mail.example>",
                                  "body": msg.get("body", ""), "attachments": msg.get("attachments", [])}
            return mid

    # ---- transport ---------------------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _service(self, path: str) -> str:
        return "mail" if path.startswith("/users") else "files" if path.startswith("/drives") else "lists"

    def handle(self, req: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests += 1
            self.clock.sleep(self.faults.latency_s)
            url = urlparse(str(req.url))
            path = unquote(url.path.replace("/v1.0", "", 1))
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            svc = self._service(path)
            now = self.clock.now()
            for o in self.faults.outages:
                if o.start <= now < o.end and o.service in (svc, "all"):
                    self.injected["outage"] += 1
                    return _err(o.status, "serviceUnavailable" if o.status >= 500 else "accessDenied", "injected outage")
            if self.rng.random() < self.faults.p_throttle.get(svc, 0):
                self.injected["throttle"] += 1
                return _err(429, "TooManyRequests", "injected throttle", {"Retry-After": str(self.rng.choice([1, 5, 10, 30]))})
            if self.rng.random() < self.faults.p_error.get(svc, 0):
                self.injected["error"] += 1
                return _err(503, "serviceUnavailable", "injected error")
            resp = self.route(req.method, path, q, req)
            if req.method in ("POST", "PUT", "PATCH") and resp.status_code < 300 and self.rng.random() < self.faults.p_lost:
                self.injected["lost"] += 1
                raise LostResponse("response lost after commit", request=req)
            return resp

    # ---- routes ------------------------------------------------------------------------------
    def route(self, method: str, path: str, q: dict, req: httpx.Request) -> httpx.Response:
        m = re.fullmatch(r"/users/([^/]+)/mailFolders", path)
        if m and method == "GET":
            name = re.search(r"displayName eq '(.+)'", q.get("$filter", "")).group(1)
            return _ok({"value": [{"id": k, "displayName": v} for k, v in self.folders.items() if v == name]})
        m = re.fullmatch(r"/users/([^/]+)/mailFolders/([^/]+)/messages", path)
        if m and method == "GET":
            fid = m.group(2)
            since = re.search(r"receivedDateTime ge (\S+)", q.get("$filter", ""))
            lo = parse_iso(since.group(1)) if since else None
            rows = sorted((x for x in self.messages.values() if x["folder"] == fid
                           and (lo is None or parse_iso(x["receivedDateTime"]) >= lo)),
                          key=lambda x: x["receivedDateTime"])
            skip, top = int(q.get("$skip", 0)), int(q.get("$top", 10))
            page = rows[skip: skip + top]
            out = {"value": [self._msg_view(x) for x in page]}
            if skip + top < len(rows):
                out["@odata.nextLink"] = (f"https://graph.microsoft.com/v1.0{path}?$filter={q.get('$filter', '')}"
                                          f"&$top={top}&$skip={skip + top}")
            return _ok(out)
        m = re.fullmatch(r"/users/([^/]+)/messages/([^/]+)/attachments", path)
        if m and method == "GET":
            x = self.messages.get(m.group(2))
            if not x:
                return _err(404, "ErrorItemNotFound", "message not found")
            return _ok({"value": [{"@odata.type": "#microsoft.graph.fileAttachment", "id": f"att{i}",
                                   "name": a["name"], "contentType": a["contentType"], "size": len(a["content"]),
                                   "contentBytes": base64.b64encode(a["content"]).decode()}
                                  for i, a in enumerate(x["attachments"])]})
        m = re.fullmatch(r"/users/([^/]+)/messages/([^/]+)/move", path)
        if m and method == "POST":
            x = self.messages.get(m.group(2))
            if not x:
                return _err(404, "ErrorItemNotFound", "message not found")
            x["folder"] = json.loads(req.content)["destinationId"]
            return _ok(self._msg_view(x), 201)
        m = re.fullmatch(r"/users/([^/]+)/sendMail", path)
        if m and method == "POST":
            self.sent.append({"time": self.clock.now(), **json.loads(req.content)["message"]})
            return httpx.Response(202)
        m = re.fullmatch(r"/drives/([^/]+)/root:/(.+):/content", path)
        if m and method == "PUT":
            p = m.group(2)
            behaviour = q.get("@microsoft.graph.conflictBehavior", "fail")
            if p in self.files and behaviour == "fail":
                return _err(409, "nameAlreadyExists", "The specified item name already exists.")
            if p in self.files and behaviour == "rename":
                stem, ext = p.rsplit(".", 1)
                n = 1
                while f"{stem} {n}.{ext}" in self.files:
                    n += 1
                p = f"{stem} {n}.{ext}"
            data = req.content
            item = {"id": "01" + uuid.UUID(int=self.rng.getrandbits(128)).hex.upper()[:30], "name": p.rsplit("/", 1)[-1],
                    "size": len(data), "webUrl": f"https://agency.sharepoint.example/sites/intake/{p}",
                    "file": {"mimeType": "application/pdf", "hashes": {"quickXorHash": quickxorhash(data)}},
                    "createdDateTime": iso(self.clock.now()), "_content": data, "_path": p}
            self.files[p] = item
            self.file_writes.append((self.clock.now(), p, data))
            return _ok(_public(item), 201)
        m = re.fullmatch(r"/drives/([^/]+)/root:/(.+)", path)
        if m and method == "GET":
            it = self.files.get(m.group(2))
            return _ok(_public(it)) if it else _err(404, "itemNotFound", "not found")
        m = re.fullmatch(r"/drives/([^/]+)/items/([^/]+)", path)
        if m and method == "GET":
            it = next((f for f in self.files.values() if f["id"] == m.group(2)), None)
            return _ok(_public(it)) if it else _err(404, "itemNotFound", "not found")
        m = re.fullmatch(r"/sites/([^/]+)/lists/([^/]+)/items", path)
        if m and method == "POST":
            lst = self.lists[m.group(2)]
            fields = json.loads(req.content)["fields"]
            u = lst["unique"]
            if u and any(i["fields"].get(u) == fields.get(u) for i in lst["items"].values()):
                return _err(409, "duplicateValue", f"A value in column {u} must be unique.")
            iid = str(lst["next"])
            lst["next"] += 1
            item = {"id": iid, "eTag": f'"{uuid.uuid4()},1"', "fields": dict(fields, id=iid),
                    "createdDateTime": iso(self.clock.now())}
            lst["items"][iid] = item
            return _ok(item, 201)
        if m and method == "GET":
            lst = self.lists[m.group(2)]
            rows = [i for i in lst["items"].values() if _match(i["fields"], q.get("$filter", ""))]
            return _ok({"value": rows[: int(q.get("$top", 50))]})
        m = re.fullmatch(r"/sites/([^/]+)/lists/([^/]+)/items/([^/]+)/fields", path)
        if m and method == "PATCH":
            lst = self.lists[m.group(2)]
            item = lst["items"].get(m.group(3))
            if not item:
                return _err(404, "itemNotFound", "not found")
            want = req.headers.get("If-Match")
            if want and want != item["eTag"]:
                return _err(412, "preconditionFailed", "eTag mismatch")
            item["fields"].update(json.loads(req.content))
            v = int(item["eTag"].strip('"').rsplit(",", 1)[1]) + 1
            item["eTag"] = '"%s,%d"' % (item["eTag"].strip('"').rsplit(",", 1)[0], v)
            return _ok(dict(item["fields"], **{"@odata.etag": item["eTag"]}) | {"eTag": item["eTag"]})
        return _err(400, "badRoute", f"{method} {path}")

    def _msg_view(self, x: dict) -> dict:
        return {"id": x["id"], "internetMessageId": x["internetMessageId"], "subject": x["subject"],
                "from": {"emailAddress": {"address": x["from"]}}, "receivedDateTime": x["receivedDateTime"],
                "hasAttachments": bool(x["attachments"]), "body": {"contentType": "text", "content": x["body"]},
                "categories": []}


def _ok(body, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _err(status: int, code: str, msg: str, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": msg}}, headers=headers or {})


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _match(fields: dict, flt: str) -> bool:
    if not flt:
        return True
    for clause in flt.split(" and "):
        m = re.fullmatch(r"\s*fields/(\w+) (eq|ge) '(.*)'\s*", clause)
        if not m:
            raise ValueError(f"unsupported filter {clause!r}")
        k, op, v = m.groups()
        have = str(fields.get(k, ""))
        if op == "eq" and have != v:
            return False
        if op == "ge" and not have >= v:
            return False
    return True

"""
Thin client for the GoHighLevel (LeadConnector) v2 API.

Credentials are loaded from the project's .env file at runtime and are never
logged, returned to the frontend, or included in error messages.
"""
import os
import threading
import time
import requests

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"

_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

# Users/pipelines change rarely; a new GHLClient is built per Flask request, so
# without this every page load re-fetches them, adding to GHL's rate-limit
# pressure on top of the much heavier call-log scan running in parallel.
_STATIC_CACHE_TTL_SECONDS = 300
_static_cache_lock = threading.Lock()
_static_cache = {}  # (location_id, key) -> (expires_at, value)


def _cached(location_id, key, ttl, fetch_fn):
    cache_key = (location_id, key)
    with _static_cache_lock:
        hit = _static_cache.get(cache_key)
        if hit and hit[0] > time.time():
            return hit[1]
    value = fetch_fn()
    with _static_cache_lock:
        _static_cache[cache_key] = (time.time() + ttl, value)
    return value


class GHLConfigError(Exception):
    pass


class GHLAPIError(Exception):
    def __init__(self, status_code, message, endpoint):
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(message)


def load_env():
    env = {}
    if not os.path.exists(_ENV_PATH):
        raise GHLConfigError(".env file not found")
    with open(_ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


class GHLClient:
    def __init__(self):
        env = load_env()
        self.token = env.get("GHL_API_TOKEN")
        self.location_id = env.get("GHL_LOCATION_ID")
        if not self.token:
            raise GHLConfigError("GHL_API_TOKEN is missing from .env")
        if not self.location_id:
            raise GHLConfigError("GHL_LOCATION_ID is missing from .env")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Version": API_VERSION,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _request(self, method, path, params=None, json_body=None, base_url=None, max_retries=7):
        url = f"{base_url or BASE_URL}{path}"
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                resp = self.session.request(method, url, params=params, json=json_body, timeout=30)
            except requests.RequestException as e:
                last_exc = e
                time.sleep(min(2 ** attempt * 0.5, 12))
                continue

            if resp.status_code == 429 and attempt < max_retries:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(2 ** attempt * 0.5, 12)
                time.sleep(wait)
                continue

            if resp.status_code >= 500 and attempt < max_retries:
                time.sleep(min(2 ** attempt * 0.5, 12))
                continue

            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("message", resp.text)
                except Exception:
                    detail = resp.text
                raise GHLAPIError(resp.status_code, str(detail), path)

            if not resp.content:
                return {}
            return resp.json()

        raise GHLAPIError(0, f"Request failed after retries: {last_exc}", path)

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def post(self, path, json_body=None):
        return self._request("POST", path, json_body=json_body)

    # ---- Users / agents ----
    def list_users(self):
        def fetch():
            data = self.get("/users/", params={"locationId": self.location_id})
            return data.get("users", [])
        return _cached(self.location_id, "users", _STATIC_CACHE_TTL_SECONDS, fetch)

    # ---- Pipelines ----
    def list_pipelines(self):
        def fetch():
            data = self.get("/opportunities/pipelines", params={"locationId": self.location_id})
            return data.get("pipelines", [])
        return _cached(self.location_id, "pipelines", _STATIC_CACHE_TTL_SECONDS, fetch)

    # ---- Opportunities ----
    def search_opportunities_page(self, filters=None, limit=100, search_after=None):
        body = {
            "locationId": self.location_id,
            "limit": limit,
            "page": 0,
        }
        if filters:
            body["filters"] = filters
        if search_after:
            body["searchAfter"] = search_after
        return self.post("/opportunities/search", json_body=body)

    def iter_all_opportunities(self, filters=None, page_size=100, max_records=20000):
        search_after = None
        fetched = 0
        while True:
            data = self.search_opportunities_page(filters=filters, limit=page_size, search_after=search_after)
            opps = data.get("opportunities", [])
            if not opps:
                break
            for o in opps:
                yield o
            fetched += len(opps)
            if fetched >= max_records or len(opps) < page_size:
                break
            search_after = opps[-1].get("sort")

    def count_opportunities(self, filters=None):
        data = self.search_opportunities_page(filters=filters, limit=1)
        return data.get("total", 0)

    # ---- Contacts ----
    def count_contacts(self, filters=None):
        body = {"locationId": self.location_id, "pageLimit": 1, "page": 0}
        if filters:
            body["filters"] = filters
        data = self.post("/contacts/search", json_body=body)
        return data.get("total", 0)

    def get_contact(self, contact_id):
        data = self.get(f"/contacts/{contact_id}")
        return data.get("contact", {})

    # ---- Calendars / appointments ----
    def list_calendars(self):
        data = self.get("/calendars/", params={"locationId": self.location_id})
        return data.get("calendars", [])

    def get_calendar_events(self, calendar_id, start_ms, end_ms):
        params = {
            "locationId": self.location_id,
            "calendarId": calendar_id,
            "startTime": start_ms,
            "endTime": end_ms,
        }
        data = self.get("/calendars/events", params=params)
        return data.get("events", [])

    # ---- Conversations / calls ----
    def search_conversations_page(self, limit=20, sort="desc", sort_by="last_message_date", start_after_date=None, start_after=None):
        params = {
            "locationId": self.location_id,
            "limit": limit,
            "sort": sort,
            "sortBy": sort_by,
        }
        if start_after_date:
            params["startAfterDate"] = start_after_date
        if start_after:
            params["startAfter"] = start_after
        return self.get("/conversations/search", params=params)

    def get_conversation_messages(self, conversation_id, limit=100):
        data = self.get(f"/conversations/{conversation_id}/messages", params={"limit": limit})
        return data.get("messages", {}).get("messages", [])

    def stream_message_recording(self, message_id, range_header=None):
        """Native GHL call recordings use a dedicated endpoint (not part of the
        message object itself) and require the 'v3' API version instead of the
        2021-07-28 version this client otherwise uses everywhere else."""
        url = f"{BASE_URL}/conversations/messages/{message_id}/locations/{self.location_id}/recording"
        headers = {"Version": "v3"}
        if range_header:
            headers["Range"] = range_header
        return self.session.get(url, headers=headers, stream=True, timeout=30)

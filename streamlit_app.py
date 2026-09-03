from base64 import b64encode
import base64
from datetime import datetime, timedelta
import hashlib
import hmac
import html
import http.client
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote, urlencode, urlparse
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(
    page_title="新增行銷活動",
    page_icon="CS",
    layout="wide",
    initial_sidebar_state="collapsed",
)


STATE_SEQUENCE = [
    {
        "key": "configuring",
        "label": "設定中",
        "en": "Configuring",
        "caption": "設定活動條件與名單",
        "detail": "選擇子名單、客群、發送通路，並輸入 EDM / SMS 內容。",
    },
    {
        "key": "previewing",
        "label": "內容預覽",
        "en": "Previewing",
        "caption": "預覽 EDM / SMS",
        "detail": "檢查主旨、文案、連結、版位和個人化欄位。",
    },
    {
        "key": "testing",
        "label": "測試信發送",
        "en": "Testing",
        "caption": "寄送測試給內部審核人",
        "detail": "顯示測試發送結果：成功、失敗與原因。",
    },
    {
        "key": "disclaimer",
        "label": "免責聲明確認",
        "en": "Disclaimer Check",
        "caption": "確認條款已閱讀",
        "detail": "顯示免責聲明內容，必須勾選確認後才能送出檢查。",
    },
    {
        "key": "scheduling",
        "label": "排程設定",
        "en": "Scheduling",
        "caption": "設定一次性或週期性排程",
        "detail": "排程由 SEGMA 資料同步觸發，非發送流程 A。",
    },
    {
        "key": "activated",
        "label": "已啟用",
        "en": "Activated",
        "caption": "等待排程觸發",
        "detail": "套用活動後啟用，可在排程觸發前取消。",
    },
    {
        "key": "waiting",
        "label": "等待排程觸發",
        "en": "Waiting for Schedule",
        "caption": "等待指定時間到達",
        "detail": "系統保留可取消狀態，尚未開始建立任務。",
    },
    {
        "key": "triggered",
        "label": "排程觸發",
        "en": "Schedule Triggered",
        "caption": "SEGMA 同步完成並建立批次",
        "detail": "寫入活動訊息任務表，狀態為 NEW。",
    },
    {
        "key": "jobs_ready",
        "label": "發送任務就緒",
        "en": "Jobs Ready",
        "caption": "任務已建立，等待發送流程 A",
        "detail": "發送流程 A 處理狀態為 NEW 的任務並開始發送。",
    },
]

CHANNELS = ["EDM", "SMS", "APP"]
SUB_LISTS = ["2026 春季活動", "高資產潛力名單", "企業贊助名單"]
SCHEDULE_MODES = ["一次性排程", "每日排程", "每週排程", "每月排程", "每年排程"]
CAMPAIGN_DELIVERY_MODES = ["一般活動", "差異化活動"]
WEEKDAY_OPTIONS = {
    "星期日": "0",
    "星期一": "1",
    "星期二": "2",
    "星期三": "3",
    "星期四": "4",
    "星期五": "5",
    "星期六": "6",
}
LOGO_PATH = Path(__file__).with_name("capital_logo.png")
SEGMA_API_BASE_URL = os.getenv("SEGMA_API", os.getenv("SEGMA_API_BASE_URL", "")).rstrip("/")
SEGMA_API_TOKEN = os.getenv("SEGMA_TOKEN", os.getenv("SEGMA_API_TOKEN", ""))
SEGMA_USER_ID = int(os.getenv("SEGMA_USER_ID", "7"))
SEGMA_SEGMENTS_JSON = os.getenv("SEGMA_SEGMENTS_JSON", "")
SEGMA_MSSQL_DESTINATION_ID = os.getenv("MSSQL_DESTINATION_ID", "")
SEGMA_SYNC_DESTINATIONS_JSON = os.getenv("SEGMA_SYNC_DESTINATIONS_JSON", "")
SEGMA_SYNC_CHUNKSIZE = int(os.getenv("SEGMA_SYNC_CHUNKSIZE", "1000"))
MSSQL_SERVER = os.getenv("MSSQL_SERVER", "")
MSSQL_PORT = int(os.getenv("MSSQL_PORT", "1433"))
MSSQL_DATABASE = os.getenv("MSSQL_DATABASE", "")
MSSQL_USERNAME = os.getenv("MSSQL_USERNAME", "")
MSSQL_PASSWORD = os.getenv("MSSQL_PASSWORD", "")
MSSQL_LOGIN_TIMEOUT = int(os.getenv("MSSQL_LOGIN_TIMEOUT", "10"))
MSSQL_SCHEMA = os.getenv("MSSQL_SCHEMA", "marketing")
SENDGRID_API_BASE_URL = os.getenv("SENDGRID_API_BASE_URL", "https://api.sendgrid.com").rstrip("/")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL = os.getenv("SENDGRID_FROM_EMAIL", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
EDM_DEFAULT_SENDER_EMAIL = os.getenv("EDM_DEFAULT_SENDER_EMAIL", SENDGRID_FROM_EMAIL)
SMS_DEFAULT_SENDER_NUMBER = os.getenv("SMS_DEFAULT_SENDER_NUMBER", TWILIO_FROM_NUMBER)


def default_sender(channel: str) -> str:
    if channel == "EDM":
        return EDM_DEFAULT_SENDER_EMAIL
    if channel == "SMS":
        return SMS_DEFAULT_SENDER_NUMBER
    return ""


def normalize_segma_profile(payload: dict) -> dict:
    user = payload.get("user") if isinstance(payload, dict) else {}
    if not isinstance(user, dict):
        user = {}
    username = (
        user.get("username")
        or user.get("name")
        or user.get("account")
        or user.get("email")
        or ""
    )
    return {
        "username": str(username),
        "email": str(user.get("email") or ""),
        "role": str(user.get("role") or ""),
    }


@st.cache_data(ttl=300)
def load_segma_profile() -> dict:
    if not SEGMA_API_BASE_URL:
        return {}
    headers = {"Accept": "application/json"}
    if SEGMA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SEGMA_API_TOKEN}"
    query_params = {
        "resources": "none",
        "limit": "5",
        "offset": "0",
        "include_permissions": "true",
    }
    if SEGMA_API_TOKEN:
        query_params["access_token"] = SEGMA_API_TOKEN
    request = Request(
        f"{SEGMA_API_BASE_URL}/api/v1/profile?{urlencode(query_params)}",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            payload = json.loads(response_body) if response_body else {}
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SEGMA profile API returned HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach SEGMA profile API: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("SEGMA profile response must be an object.")
    return normalize_segma_profile(payload)


def current_segma_username() -> str:
    try:
        return load_segma_profile().get("username", "")
    except Exception:
        return ""


def load_segma_segment_sources() -> dict:
    if SEGMA_API_BASE_URL:
        try:
            return load_segma_segments_from_api()
        except Exception:
            if not SEGMA_SEGMENTS_JSON:
                return {}
    if SEGMA_SEGMENTS_JSON:
        items = json.loads(SEGMA_SEGMENTS_JSON)
        segments = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = normalize_segma_segment(item)
            if normalized:
                label, source = normalized
                segments[label] = source
        return segments
    return {}


def normalize_segma_segment(item: dict) -> tuple[str, dict] | None:
    source_id = item.get("id") or item.get("source_id") or item.get("action_dataset_id")
    if source_id is None:
        return None
    label = item.get("label") or item.get("name") or item.get("display_name") or f"Segment {source_id}"
    source_name = item.get("source_name") or item.get("name") or label
    dim_id = item.get("dim_id") or item.get("dimension_id")
    dim_name = item.get("dim_name") or item.get("dimension_name")
    traits_by_name = {}
    for trait in item.get("traits") or item.get("columns") or item.get("fields") or []:
        if not isinstance(trait, dict):
            continue
        trait_id = trait.get("id") or trait.get("trait_id")
        trait_name = trait.get("name") or trait.get("key") or trait.get("label") or trait.get("alias")
        if trait_id is not None and trait_name:
            traits_by_name[str(trait_name)] = int(trait_id)
    return str(label), {
        "source_id": int(source_id),
        "source_name": str(source_name),
        "source_type": "Segment",
        "dim_id": int(dim_id) if dim_id is not None else None,
        "dim_name": str(dim_name) if dim_name else "",
        "traits_by_name": traits_by_name,
    }


def required_segment_trait_names(channel: str) -> list[str]:
    if channel == "EDM":
        return ["customer_id", "email"]
    if channel == "SMS":
        return ["customer_id", "phone_number"]
    return ["customer_id", "user_id", "device_handle"]


def missing_segment_trait_names(source: dict, channel: str) -> list[str]:
    if source.get("source_type") != "Segment":
        return []
    traits_by_name = source.get("traits_by_name", {})
    return [name for name in required_segment_trait_names(channel) if name not in traits_by_name]


def required_action_dataset_column_names(channel: str) -> list[str]:
    if channel == "EDM":
        return ["customer_id", "email"]
    if channel == "SMS":
        return ["customer_id", "phone_number"]
    return ["customer_id", "user_id", "device_handle"]


def missing_action_dataset_column_names(source: dict, channel: str) -> list[str]:
    if source.get("source_type") != "ActionDataset":
        return []
    columns_by_name = source.get("columns_by_name", {})
    return [name for name in required_action_dataset_column_names(channel) if name not in columns_by_name]


def validate_seed_action_dataset_source(seed_source: dict, channel: str) -> list[str]:
    errors = []
    if not required_action_dataset_column_names(channel):
        return errors
    if not seed_source:
        errors.append("無法驗證 SEGMA seed ActionDataset 欄位，請重新選擇 seed list。")
    elif not seed_source.get("has_column_metadata"):
        errors.append("SEGMA seed ActionDataset 未回傳 columns/fields metadata，無法驗證建立此通路 sync 所需欄位。")
    else:
        missing_columns = missing_action_dataset_column_names(seed_source, channel)
        if missing_columns:
            errors.append(
                "SEGMA seed ActionDataset 缺少建立此通路 sync 所需的 column："
                + "、".join(missing_columns)
                + "。請確認 /api/v1/action_datasets 回傳的 columns/fields 包含必要欄位。"
            )
    return errors


def normalize_segma_traits(payload: list | dict) -> dict:
    if isinstance(payload, dict):
        items = payload.get("traits") or payload.get("data") or payload.get("items") or []
    else:
        items = payload
    if not isinstance(items, list):
        raise RuntimeError("SEGMA traits response must be a list or contain traits/data/items list.")

    traits_by_name = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        trait_id = item.get("id") or item.get("trait_id")
        trait_name = item.get("name") or item.get("key") or item.get("label") or item.get("alias")
        if trait_id is not None and trait_name:
            traits_by_name[str(trait_name)] = int(trait_id)
    return traits_by_name


def normalize_action_dataset_columns(payload: list | dict) -> dict:
    if isinstance(payload, dict):
        items = payload.get("columns") or payload.get("fields") or payload.get("data") or payload.get("items") or []
    else:
        items = payload
    if not isinstance(items, list):
        raise RuntimeError("SEGMA ActionDataset columns response must be a list or contain columns/fields/data/items list.")

    columns_by_name = {}
    for column in items:
        if isinstance(column, str):
            column_name = column
        elif isinstance(column, dict):
            column_name = (
                column.get("name")
                or column.get("key")
                or column.get("label")
                or column.get("alias")
                or column.get("column_name")
            )
        else:
            column_name = None
        if column_name:
            columns_by_name[str(column_name)] = str(column_name)
    return columns_by_name


def normalize_action_dataset(item: dict) -> tuple[str, dict] | None:
    source_id = item.get("id") or item.get("source_id") or item.get("action_dataset_id")
    if source_id is None:
        return None
    label = item.get("label") or item.get("name") or item.get("display_name") or f"ActionDataset {source_id}"
    source_name = item.get("source_name") or item.get("name") or label
    columns = item.get("columns") if "columns" in item else item.get("fields")
    columns_by_name = normalize_action_dataset_columns(columns) if isinstance(columns, list) else {}
    return str(label), {
        "source_id": int(source_id),
        "source_name": str(source_name),
        "source_type": "ActionDataset",
        "columns_by_name": columns_by_name,
        "has_column_metadata": isinstance(columns, list),
    }


def is_seed_action_dataset(item: dict) -> bool:
    name_parts = [
        item.get("label"),
        item.get("name"),
        item.get("display_name"),
        item.get("source_name"),
    ]
    searchable_name = " ".join(str(part) for part in name_parts if part).lower()
    return "seed" in searchable_name or "種子" in searchable_name


def is_sql_server_table_sync_destination(item: dict) -> bool:
    action_type = str(item.get("action_type", "")).lower()
    if action_type == "mssql_table":
        return True
    searchable_values = [
        item.get("type"),
        item.get("destination_type"),
        item.get("sync_type"),
        item.get("action_type"),
        item.get("adapter"),
        item.get("provider"),
        item.get("kind"),
        item.get("category"),
    ]
    searchable = " ".join(str(value) for value in searchable_values if value).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", searchable)
    compact = normalized.replace(" ", "")
    return (
        ("mssql" in compact or "sqlserver" in compact or "sql server" in normalized)
        and "table" in normalized
        and ("sync" in normalized or "mssql_table" in searchable)
    )


def normalize_segma_sync_destination(item: dict) -> tuple[str, dict] | None:
    destination_id = item.get("id") or item.get("destination_id") or item.get("sync_destination_id")
    if destination_id is None or not is_sql_server_table_sync_destination(item):
        return None
    name = item.get("name") or item.get("label") or item.get("display_name") or f"Destination {destination_id}"
    destination_type = item.get("type") or item.get("destination_type") or item.get("action_type") or "SQL Server table sync"
    label = str(name)
    return str(label), {
        "destination_id": int(destination_id),
        "destination_name": str(name),
        "destination_type": str(destination_type),
    }


@st.cache_data(ttl=300)
def load_segma_traits_for_dim(dim_id: int) -> dict:
    if not SEGMA_API_BASE_URL:
        return {}
    headers = {"Accept": "application/json"}
    if SEGMA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SEGMA_API_TOKEN}"
    query = urlencode({"dim_id": str(dim_id), "limit": "1000"})
    request = Request(
        f"{SEGMA_API_BASE_URL}/api/v1/traits?{query}",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            payload = json.loads(response_body) if response_body else []
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SEGMA traits API returned HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach SEGMA traits API: {exc.reason}") from exc
    return normalize_segma_traits(payload)


@st.cache_data(ttl=300)
def load_segma_action_dataset_columns(action_dataset_id: int) -> dict:
    if not SEGMA_API_BASE_URL:
        return {}
    headers = {"Accept": "application/json"}
    if SEGMA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SEGMA_API_TOKEN}"
    request = Request(
        f"{SEGMA_API_BASE_URL}/api/v1/action_datasets/{action_dataset_id}/columns",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            payload = json.loads(response_body) if response_body else []
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SEGMA ActionDataset columns API returned HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach SEGMA ActionDataset columns API: {exc.reason}") from exc
    return normalize_action_dataset_columns(payload)


@st.cache_data(ttl=300)
def load_segma_segments_from_api() -> dict:
    if not SEGMA_API_BASE_URL:
        return {}
    headers = {"Accept": "application/json"}
    if SEGMA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SEGMA_API_TOKEN}"
    request = Request(
        f"{SEGMA_API_BASE_URL}/api/v1/segments",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            payload = json.loads(response_body) if response_body else []
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SEGMA segments API returned HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach SEGMA segments API: {exc.reason}") from exc

    if isinstance(payload, dict):
        items = payload.get("segments") or payload.get("data") or payload.get("items") or []
    else:
        items = payload
    if not isinstance(items, list):
        raise RuntimeError("SEGMA segments API response must be a list or contain segments/data/items list.")

    segments = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = normalize_segma_segment(item)
        if normalized:
            label, source = normalized
            if source.get("dim_id") and not source.get("traits_by_name"):
                source["traits_by_name"] = load_segma_traits_for_dim(source["dim_id"])
            segments[label] = source
    return segments


@st.cache_data(ttl=300)
def load_segma_sync_destinations_from_api() -> dict:
    if not SEGMA_API_BASE_URL:
        return {}
    headers = {"Accept": "application/json"}
    if SEGMA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SEGMA_API_TOKEN}"

    query = urlencode(
        {
            "q[action_type_eq]": "mssql_table",
            "limit": "100",
            "order_by": "-created_at",
        }
    )
    request = Request(
        f"{SEGMA_API_BASE_URL}/api/v1/destinations?{query}",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            payload = json.loads(response_body) if response_body else []
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SEGMA destinations API returned HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach SEGMA destinations API: {exc.reason}") from exc

    if isinstance(payload, dict):
        items = payload.get("destinations") or payload.get("data") or payload.get("items") or []
    else:
        items = payload
    if not isinstance(items, list):
        raise RuntimeError("SEGMA destinations response must be a list or contain destinations/data/items list.")

    destinations = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = normalize_segma_sync_destination(item)
        if normalized:
            label, destination = normalized
            destinations[label] = destination
    return destinations


def load_segma_sync_destinations() -> dict:
    if SEGMA_API_BASE_URL:
        try:
            destinations = load_segma_sync_destinations_from_api()
            if destinations:
                return destinations
        except Exception:
            if not SEGMA_SYNC_DESTINATIONS_JSON:
                return {}
    if SEGMA_SYNC_DESTINATIONS_JSON:
        items = json.loads(SEGMA_SYNC_DESTINATIONS_JSON)
        destinations = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = normalize_segma_sync_destination(item)
            if normalized:
                label, destination = normalized
                destinations[label] = destination
        return destinations
    if SEGMA_MSSQL_DESTINATION_ID:
        return {
            f"Configured SQL Server table sync destination ({SEGMA_MSSQL_DESTINATION_ID})": {
                "destination_id": int(SEGMA_MSSQL_DESTINATION_ID),
                "destination_name": "Configured SQL Server table sync destination",
                "destination_type": "SQL Server table sync",
            }
        }
    return {}


@st.cache_data(ttl=300)
def load_segma_seed_action_datasets() -> dict:
    if not SEGMA_API_BASE_URL:
        return {}
    headers = {"Accept": "application/json"}
    if SEGMA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SEGMA_API_TOKEN}"

    def fetch_action_datasets(query_params: dict) -> list[dict]:
        query = urlencode(query_params)
        request = Request(
            f"{SEGMA_API_BASE_URL}/api/v1/action_datasets?{query}",
            headers=headers,
            method="GET",
        )
        try:
            with urlopen(request, timeout=20) as response:
                response_body = response.read().decode("utf-8")
                payload = json.loads(response_body) if response_body else []
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"SEGMA seed ActionDataset API returned HTTP {exc.code}: {error_body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach SEGMA seed ActionDataset API: {exc.reason}") from exc

        if isinstance(payload, dict):
            items = payload.get("action_datasets") or payload.get("data") or payload.get("items") or []
        else:
            items = payload
        if not isinstance(items, list):
            raise RuntimeError("SEGMA seed ActionDataset response must be a list or contain action_datasets/data/items list.")
        return [item for item in items if isinstance(item, dict)]

    items = fetch_action_datasets({"limit": "100", "offset": "0"})

    datasets = {}
    for item in items:
        if not is_seed_action_dataset(item):
            continue
        normalized = normalize_action_dataset(item)
        if normalized:
            label, source = normalized
            datasets[label] = source
    return datasets


def current_segma_segment_sources() -> dict:
    return load_segma_segment_sources()


def current_segments() -> list[str]:
    return list(current_segma_segment_sources().keys())


def current_seed_action_dataset_sources() -> dict:
    try:
        return load_segma_seed_action_datasets()
    except Exception:
        return {}


def current_seed_lists() -> list[str]:
    return list(current_seed_action_dataset_sources().keys())


def current_segma_sync_destinations() -> dict:
    try:
        return load_segma_sync_destinations()
    except Exception:
        return {}


SEGMA_TARGET_TABLES = {
    "EDM": {"schema": MSSQL_SCHEMA, "table": "campaign_job_edm"},
    "SMS": {"schema": MSSQL_SCHEMA, "table": "campaign_job_sms"},
    "APP": {"schema": MSSQL_SCHEMA, "table": "campaign_job_app_notification"},
}
CANCELLABLE_STATUSES = {"Draft", "Activated", "Waiting for Schedule", "Sync Failed"}
STATUS_LABELS = {
    "Draft": "草稿",
    "Activated": "已啟用",
    "Waiting for Schedule": "等待排程觸發",
    "Jobs Ready": "發送任務就緒",
    "Cancelled": "已取消",
    "Sync Failed": "同步失敗",
}
DEFAULT_TIMEZONE = "Asia/Taipei"


def default_schedule_values() -> dict:
    start_at = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)) + timedelta(minutes=2)
    return {
        "send_date": start_at.strftime("%Y/%m/%d"),
        "schedule_end_date": start_at.strftime("%Y/%m/%d"),
        "send_time": start_at.strftime("%H:%M"),
    }


def schedule_timezone(schedule_ui: dict) -> ZoneInfo:
    return ZoneInfo(schedule_ui.get("timezone") or DEFAULT_TIMEZONE)


def localize_schedule_datetime(value: datetime, schedule_ui: dict) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=schedule_timezone(schedule_ui))


def datetimeoffset_literal(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return value.isoformat(timespec="seconds")


def schedule_ui_config_from_state() -> dict:
    return {
        "mode": st.session_state.schedule_mode,
        "send_date": st.session_state.send_date,
        "send_time": st.session_state.send_time,
        "end_date": st.session_state.schedule_end_date,
        "weekday": st.session_state.schedule_weekday,
        "month_day": st.session_state.schedule_month_day,
        "timezone": DEFAULT_TIMEZONE,
    }


def schedule_ui_config_from_campaign_config(config: dict) -> dict:
    return config["schedule_ui"]


def parse_schedule_start_from_config(schedule_ui: dict) -> datetime:
    raw_date = str(schedule_ui.get("send_date", "")).strip().replace("/", "-")
    raw_time = str(schedule_ui.get("send_time", "")).strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{raw_date} {raw_time}", pattern)
        except ValueError:
            continue
    raise ValueError("請使用 YYYY-MM-DD 或 YYYY/MM/DD 日期格式，以及 HH:MM 時間格式。")


def parse_schedule_month_day_from_config(schedule_ui: dict) -> int:
    try:
        day = int(str(schedule_ui.get("month_day", "")).strip())
    except ValueError as exc:
        raise ValueError("每月排程日期必須是 1 到 31 的數字。") from exc
    if day < 1 or day > 31:
        raise ValueError("每月排程日期必須是 1 到 31 的數字。")
    return day


def parse_schedule_end_from_config(schedule_ui: dict, start_at: datetime) -> datetime | None:
    if schedule_ui.get("mode") == "一次性排程":
        return start_at + timedelta(minutes=1)
    raw_date = str(schedule_ui.get("end_date", "")).strip().replace("/", "-")
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            end_at = datetime.strptime(f"{raw_date} 23:59", pattern)
            break
        except ValueError:
            continue
    else:
        raise ValueError("請使用 YYYY-MM-DD 或 YYYY/MM/DD 結束日期格式。")
    if end_at <= start_at:
        raise ValueError("結束日期必須晚於起始日期。")
    return end_at


def sync_cron_from_config(schedule_ui: dict, start_at: datetime) -> str:
    mode = schedule_ui.get("mode")
    if mode == "一次性排程":
        return f"{start_at.minute} {start_at.hour} * * *"
    if mode == "每日排程":
        return f"{start_at.minute} {start_at.hour} * * *"
    if mode == "每週排程":
        weekday = WEEKDAY_OPTIONS.get(schedule_ui.get("weekday"), "5")
        return f"{start_at.minute} {start_at.hour} * * {weekday}"
    if mode == "每月排程":
        return f"{start_at.minute} {start_at.hour} {parse_schedule_month_day_from_config(schedule_ui)} * *"
    return f"{start_at.minute} {start_at.hour} {start_at.day} {start_at.month} *"


def normalized_schedule_from_config(config: dict) -> dict:
    schedule_ui = schedule_ui_config_from_campaign_config(config)
    start_at = parse_schedule_start_from_config(schedule_ui)
    end_at = parse_schedule_end_from_config(schedule_ui, start_at)
    start_at = localize_schedule_datetime(start_at, schedule_ui)
    end_at = localize_schedule_datetime(end_at, schedule_ui)
    return {
        "start_at": start_at,
        "end_at": end_at,
        "schedule_cron": sync_cron_from_config(schedule_ui, start_at),
        "timezone": schedule_ui["timezone"],
    }


def init_state() -> None:
    segments = current_segments()
    segment_default = segments[0] if segments else ""
    initial_channel = st.session_state.get("selected_channel", "EDM")
    schedule_defaults = default_schedule_values()
    defaults = {
        "campaign_name": "2026 資本募集再啟動",
        "campaign_description": "",
        "campaign_delivery_mode": CAMPAIGN_DELIVERY_MODES[0],
        "sender": default_sender(initial_channel),
        "edm_template_id": "",
        "edm_template_name": "",
        "edm_template_version_id": "",
        "edm_template_preview_html": "",
        "edm_dynamic_template_data": "{}",
        "edm_template_data_pair_count": 1,
        "edm_filter_null_email": True,
        "edm_deduplicate_email": True,
        "sms_filter_null_phone_number": True,
        "sms_deduplicate_phone_number": True,
        "segment": segment_default,
        "sub_list": SUB_LISTS[0],
        "use_seed_list": False,
        "seed_list": "",
        "seed_action_dataset_id": "",
        "seed_action_dataset_name": "",
        "selected_sync_destination_id": SEGMA_MSSQL_DESTINATION_ID,
        "selected_sync_destination_name": "",
        "channels": ["EDM"],
        "email_subject": "您的支持將推動下一階段里程碑",
        "sms_copy": "邀請您一同支持下一階段的重要里程碑。",
        "app_title": "下一階段活動提醒",
        "app_body": "邀請您查看最新活動資訊與專屬邀請。",
        "app_data_json": '{"screen":"campaign","campaign_type":"capital"}',
        "app_id": "",
        "app_name": "",
        "schedule_mode": "一次性排程",
        "schedule_weekday": "星期五",
        "schedule_month_day": "18",
        "send_date": schedule_defaults["send_date"],
        "schedule_end_date": schedule_defaults["schedule_end_date"],
        "send_time": schedule_defaults["send_time"],
        "disclaimer_ok": False,
        "current_state": "configuring",
        "validation_failed": False,
        "cancelled": False,
        "test_sent": False,
        "test_status": "尚未發送",
        "test_recipient": "",
        "test_personalization_json": "{}",
        "create_test_status": "尚未發送",
        "show_submit_disclaimer": False,
        "confirm_exclusion_processed": False,
        "selected_channel": "EDM",
        "previous_channel": "EDM",
        "campaign_view": "list",
        "selected_campaign_id": "",
        "pending_cancel_campaign_id": "",
        "segma_sync_response": None,
        "segma_sync_error": "",
        "campaign_action_warning": "",
        "mssql_error": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if st.session_state.schedule_mode == "一次性發送":
        st.session_state.schedule_mode = "一次性排程"
    if st.session_state.schedule_mode == "週期性發送":
        st.session_state.schedule_mode = "每日排程"
    if st.session_state.schedule_mode not in set(SCHEDULE_MODES):
        st.session_state.schedule_mode = "一次性排程"


def state_index(key: str) -> int:
    return next((i for i, item in enumerate(STATE_SEQUENCE) if item["key"] == key), 0)


def set_state(key: str) -> None:
    st.session_state.current_state = key
    st.session_state.validation_failed = False
    if key not in {"activated", "waiting"}:
        st.session_state.cancelled = False


def card(title: str, body: str, tone: str = "neutral") -> None:
    st.markdown(
        f"""
        <div class="metric-card {tone}">
          <div class="metric-title">{title}</div>
          <div class="metric-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
          --ink: #101828;
          --muted: #667085;
          --line: #d9e0ea;
          --blue: #0b6ffb;
          --green: #007f68;
          --amber: #a56512;
          --red: #b42318;
          --panel: #ffffff;
          --soft: #f8fafc;
        }
        .stApp {
          background: #f3f5f8;
        }
        header[data-testid="stHeader"] {
          height: 0;
          min-height: 0;
          visibility: hidden;
        }
        div[data-testid="stToolbar"] {
          display: none;
        }
        div[data-testid="stDecoration"] {
          display: none;
        }
        .block-container {
          padding-top: 0.15rem;
          padding-bottom: 1.4rem;
          max-width: 1680px;
        }
        div[data-testid="stSidebar"] {
          background: #f7f8fb;
          border-right: 1px solid var(--line);
        }
        h1, h2, h3, h4, p, li, label, span {
          letter-spacing: 0;
        }
        div[data-testid="stTextInput"] input,
        div[data-testid="stTextArea"] textarea,
        div[data-baseweb="select"] > div {
          border-color: #d7dee8;
          border-radius: 6px;
          box-shadow: none;
          min-height: 42px;
        }
        div[data-testid="stTextArea"] textarea {
          min-height: 86px;
        }
        div[data-testid="stWidgetLabel"] label,
        .stRadio label {
          color: #344054;
          font-size: 13px;
          font-weight: 650;
        }
        .draft-header {
          display: grid;
          grid-template-columns: 240px 1fr 240px;
          align-items: start;
          margin-bottom: 22px;
        }
        .brand {
          display: flex;
          align-items: center;
          min-width: 240px;
        }
        .brand-logo {
          width: 178px;
          height: auto;
          display: block;
        }
        .brand-logo-fallback {
          color: #006b54;
          font-size: 18px;
          line-height: 1.15;
          font-weight: 800;
        }
        .draft-title {
          text-align: center;
          color: #111827;
        }
        .draft-title h1 {
          font-size: 24px;
          line-height: 1.2;
          margin: 2px 0 12px;
          font-weight: 780;
        }
        .draft-title div {
          color: #667085;
          font-size: 15px;
          font-weight: 520;
        }
        .user-greeting {
          display: flex;
          justify-content: flex-end;
          align-items: flex-start;
          min-width: 0;
        }
        .user-greeting-box {
          border: 1px solid #d7dee8;
          background: #ffffff;
          border-radius: 8px;
          padding: 10px 12px;
          color: #344054;
          font-size: 13px;
          line-height: 1.35;
          text-align: right;
          max-width: 240px;
        }
        .user-greeting-name {
          color: #101828;
          font-size: 15px;
          font-weight: 760;
          margin-top: 2px;
          overflow-wrap: anywhere;
        }
        .user-greeting-meta {
          color: #667085;
          font-size: 12px;
          margin-top: 3px;
          overflow-wrap: anywhere;
        }
        .form-shell {
          border: 1px solid #e1e6ee;
          background: #fff;
          border-radius: 8px;
          box-shadow: 0 10px 24px rgba(16, 24, 40, 0.08);
          padding: 22px 26px 20px;
        }
        .form-section {
          border-bottom: 1px solid #eaedf2;
          padding-bottom: 20px;
          margin-bottom: 20px;
        }
        .form-section.compact {
          padding-bottom: 14px;
          margin-bottom: 16px;
        }
        .form-section.no-line {
          border-bottom: 0;
          margin-bottom: 0;
          padding-bottom: 0;
        }
        .form-section-title {
          color: #111827;
          font-size: 17px;
          font-weight: 780;
          margin-bottom: 12px;
        }
        .required {
          color: #c8102e;
          font-weight: 800;
        }
        .draft-link {
          color: var(--blue);
          font-size: 13px;
          margin: 4px 0 0;
        }
        .metadata-grid {
          display: grid;
          grid-template-columns: 96px 1fr;
          row-gap: 8px;
          column-gap: 14px;
          color: #667085;
          font-size: 13px;
          margin-top: 10px;
        }
        .metadata-grid strong {
          color: #344054;
          font-weight: 650;
        }
        .vertical-divider {
          border-left: 1px solid #e6eaf0;
          padding-left: 24px;
        }
        .info-banner {
          border: 1px solid #9ac6ff;
          background: #eef6ff;
          color: #075fc4;
          border-radius: 6px;
          padding: 12px 14px;
          font-size: 14px;
          font-weight: 620;
          margin-bottom: 12px;
        }
        .side-title {
          color: #101828;
          font-size: 17px;
          font-weight: 780;
          margin-bottom: 8px;
        }
        .side-copy {
          color: #667085;
          font-size: 13px;
          line-height: 1.45;
          margin-bottom: 14px;
        }
        .campaign-row {
          border: 1px solid #d7dee8;
          background: #fff;
          border-radius: 8px;
          padding: 11px 12px;
          margin: 10px 0 8px;
        }
        .campaign-list-grid {
          display: grid;
          grid-template-columns: 1fr;
          gap: 12px;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.campaign-card-marker) {
          background: #ffffff;
          border-color: #d7dee8;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.campaign-card-marker.selected) {
          background: #f7fbff;
          border-color: #8bbcff;
        }
        .campaign-table-head {
          border-bottom: 1px solid #d7dee8;
          padding: 9px 0 10px;
          color: #667085;
          font-size: 11px;
          font-weight: 780;
          text-transform: uppercase;
        }
        .campaign-table-row {
          border-bottom: 1px solid #e6eaf0;
          background: transparent;
          padding: 10px 0;
        }
        .campaign-table-row.selected {
          background: transparent;
          box-shadow: none;
        }
        .campaign-table-cell {
          color: #344054;
          font-size: 13px;
          line-height: 1.35;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .campaign-table-cell.wrap {
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          white-space: normal;
        }
        .campaign-table-cell strong {
          color: #101828;
          font-weight: 760;
        }
        .campaign-table-meta {
          color: #667085;
          font-size: 11px;
          margin-top: 3px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .campaign-list-row {
          padding: 2px 0 0;
        }
        .campaign-list-row.selected {
          border-color: #8bbcff;
          background: #f7fbff;
        }
        .list-row-head {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 12px;
          margin-bottom: 10px;
        }
        .campaign-row.selected {
          border-color: #8bbcff;
          background: #f2f8ff;
        }
        .campaign-name {
          color: #101828;
          font-size: 14px;
          font-weight: 760;
          line-height: 1.3;
        }
        .campaign-meta {
          color: #667085;
          font-size: 12px;
          line-height: 1.45;
          margin-top: 6px;
        }
        .campaign-summary-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 10px;
          margin: 10px 0;
        }
        .status-chip.green {
          background: #edfdf6;
          color: #007f68;
        }
        .status-chip.gray {
          background: #f2f4f7;
          color: #475467;
        }
        .status-chip.red {
          background: #fff4f2;
          color: #b42318;
        }
        .detail-panel {
          border: 1px solid #d7dee8;
          background: #fff;
          border-radius: 8px;
          padding: 20px;
        }
        .detail-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 16px;
          border-bottom: 1px solid #eaedf2;
          padding-bottom: 16px;
          margin-bottom: 16px;
        }
        .detail-title {
          color: #101828;
          font-size: 22px;
          line-height: 1.25;
          font-weight: 780;
          margin-bottom: 6px;
        }
        .detail-subtitle {
          color: #667085;
          font-size: 13px;
        }
        .detail-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
          margin-bottom: 16px;
        }
        .detail-cell {
          border: 1px solid #eaedf2;
          border-radius: 8px;
          padding: 12px;
          background: #fbfcfe;
        }
        .detail-label {
          color: #667085;
          font-size: 12px;
          font-weight: 700;
          text-transform: uppercase;
        }
        .detail-value {
          color: #101828;
          font-size: 15px;
          font-weight: 720;
          margin-top: 5px;
        }
        .content-preview {
          border: 1px solid #d7dee8;
          border-radius: 8px;
          background: #fbfcfe;
          padding: 14px;
          color: #344054;
          line-height: 1.55;
          margin-bottom: 16px;
        }
        .preview-box {
          height: 238px;
          border: 1px solid #d7dee8;
          border-radius: 8px;
          background: #fbfcfe;
          display: flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          color: #667085;
          margin-bottom: 20px;
        }
        .preview-envelope {
          width: 46px;
          height: 30px;
          margin: 0 auto 18px;
          border-radius: 6px;
          background: #d8dde5;
          position: relative;
        }
        .preview-envelope:before,
        .preview-envelope:after {
          content: "";
          position: absolute;
          top: 0;
          width: 26px;
          height: 26px;
          border-top: 4px solid #eef1f5;
        }
        .preview-envelope:before {
          left: 1px;
          transform: rotate(35deg);
        }
        .preview-envelope:after {
          right: 1px;
          transform: rotate(-35deg);
        }
        .preview-empty-title {
          color: #344054;
          font-weight: 760;
          margin-bottom: 10px;
        }
        .preview-empty-copy {
          color: #667085;
          font-size: 14px;
        }
        .state-ribbon {
          display: flex;
          justify-content: space-between;
          gap: 14px;
          align-items: center;
          border: 1px solid #d7dee8;
          background: #f8fafc;
          border-radius: 8px;
          padding: 10px 12px;
          margin-top: 12px;
          color: #475467;
          font-size: 13px;
        }
        .state-ribbon strong {
          color: #101828;
        }
        .bottom-actions {
          display: flex;
          justify-content: flex-end;
          align-items: center;
          gap: 14px;
          border-top: 1px solid #eaedf2;
          padding-top: 20px;
          margin-top: 18px;
        }
        .status-chip {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 30px;
          padding: 5px 10px;
          border-radius: 999px;
          background: #eef6ff;
          color: #075fc4;
          font-size: 12px;
          font-weight: 760;
        }
        .demo-state-banner {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          border: 1px solid #b7d4ff;
          background: #f0f7ff;
          color: #075fc4;
          border-radius: 8px;
          padding: 10px 14px;
          margin: -8px 0 18px;
          font-size: 13px;
        }
        .demo-state-banner strong {
          color: #0b3f8c;
        }
        .demo-state-banner.cancelled {
          border-color: #f0b8b1;
          background: #fff4f2;
          color: #b42318;
        }
        .demo-state-banner.validation {
          border-color: #efc27b;
          background: #fff8eb;
          color: #934f0b;
        }
        div[data-testid="stButton"] button {
          border-radius: 6px;
          min-height: 42px;
          font-weight: 720;
          box-shadow: 0 3px 8px rgba(16, 24, 40, 0.08);
        }
        div[data-testid="stButton"] button[kind="primary"] {
          background: #007f68;
          border-color: #007f68;
          color: #ffffff;
        }
        div[data-testid="stButton"] button[kind="secondary"] {
          background: #ffffff;
          border-color: #d7dee8;
          color: #344054;
        }
        .app-header {
          display: flex;
          justify-content: space-between;
          gap: 24px;
          align-items: flex-start;
          padding: 8px 0 18px;
          border-bottom: 1px solid var(--line);
          margin-bottom: 18px;
        }
        .eyebrow {
          color: var(--blue);
          font-size: 13px;
          line-height: 1.3;
          font-weight: 700;
          text-transform: uppercase;
        }
        .title {
          color: var(--ink);
          font-size: 30px;
          line-height: 1.2;
          font-weight: 750;
          margin: 3px 0 4px;
        }
        .subtitle {
          color: var(--muted);
          font-size: 15px;
          max-width: 820px;
        }
        .status-pill {
          min-width: 210px;
          text-align: center;
          border: 1px solid #a9bce9;
          color: #123f94;
          background: #eef4ff;
          padding: 10px 14px;
          border-radius: 8px;
          font-weight: 750;
        }
        .section {
          border: 1px solid var(--line);
          background: var(--panel);
          border-radius: 8px;
          padding: 18px;
          margin-bottom: 16px;
          box-shadow: 0 1px 2px rgba(20, 30, 50, 0.04);
        }
        .section-title {
          font-size: 16px;
          font-weight: 760;
          color: var(--ink);
          margin-bottom: 8px;
        }
        .helper {
          color: var(--muted);
          font-size: 13px;
          line-height: 1.45;
        }
        .timeline {
          display: grid;
          grid-template-columns: repeat(9, minmax(92px, 1fr));
          gap: 8px;
          margin: 12px 0 6px;
        }
        .step {
          min-height: 98px;
          border: 1px solid var(--line);
          border-radius: 8px;
          padding: 10px;
          background: #fbfcff;
          position: relative;
        }
        .step.done {
          background: #f0fbf5;
          border-color: #a8d8bd;
        }
        .step.active {
          background: #edf4ff;
          border-color: #6b99e7;
          box-shadow: inset 0 0 0 2px #d4e3ff;
        }
        .step.blocked {
          background: #fff3f1;
          border-color: #f2aaa3;
        }
        .step.cancelled {
          background: #f4f4f5;
          border-color: #c4c7cf;
        }
        .step-num {
          width: 22px;
          height: 22px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 99px;
          background: #dfe7f8;
          color: #113c8d;
          font-size: 12px;
          font-weight: 800;
          margin-bottom: 7px;
        }
        .step-title {
          color: var(--ink);
          font-weight: 760;
          font-size: 13px;
          line-height: 1.25;
        }
        .step-caption {
          color: var(--muted);
          font-size: 12px;
          line-height: 1.35;
          margin-top: 4px;
        }
        .metric-grid {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
          margin-bottom: 16px;
        }
        .metric-card {
          border: 1px solid var(--line);
          border-radius: 8px;
          padding: 12px 14px;
          background: #fff;
          min-height: 78px;
        }
        .metric-card.green { border-color: #a9d7bf; background: #f2fbf6; }
        .metric-card.amber { border-color: #e4c18c; background: #fff8eb; }
        .metric-card.blue { border-color: #adc5f5; background: #f0f6ff; }
        .metric-card.red { border-color: #efaaa3; background: #fff3f1; }
        .metric-title {
          color: var(--muted);
          font-size: 12px;
          font-weight: 700;
          text-transform: uppercase;
        }
        .metric-body {
          margin-top: 5px;
          color: var(--ink);
          font-weight: 780;
          font-size: 17px;
          line-height: 1.25;
        }
        .validation-list {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 8px;
          padding: 0;
          margin: 8px 0 0;
          list-style: none;
        }
        .validation-list li {
          border: 1px dashed #adc5f5;
          background: #f7faff;
          border-radius: 8px;
          padding: 9px 10px;
          color: #26364f;
          font-size: 13px;
        }
        .job-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
        }
        .job-table th, .job-table td {
          text-align: left;
          padding: 9px 10px;
          border-bottom: 1px solid #e2e6ef;
        }
        .job-table th {
          color: #435064;
          background: #f6f8fb;
          font-weight: 760;
        }
        .small-note {
          color: var(--muted);
          font-size: 12px;
          line-height: 1.45;
        }
        @media (max-width: 1100px) {
          .timeline { grid-template-columns: repeat(3, minmax(0, 1fr)); }
          .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
          .app-header { flex-direction: column; }
          .draft-header { grid-template-columns: 1fr; gap: 16px; }
          .draft-title { text-align: left; }
          .user-greeting { justify-content: flex-start; }
          .user-greeting-box { text-align: left; max-width: 100%; }
          .vertical-divider { border-left: 0; padding-left: 0; }
        }
        @media (max-width: 720px) {
          .timeline, .metric-grid, .validation-list { grid-template-columns: 1fr; }
          .detail-header, .state-ribbon { flex-direction: column; align-items: stretch; }
          .detail-grid { grid-template-columns: 1fr; }
          .campaign-summary-grid { grid-template-columns: 1fr; }
          .title { font-size: 24px; }
          .section { padding: 14px; }
          .form-shell { padding: 16px; }
          .bottom-actions { flex-direction: column-reverse; align-items: stretch; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def current_state() -> dict:
    return STATE_SEQUENCE[state_index(st.session_state.current_state)]


def logo_data_url() -> str:
    if not LOGO_PATH.exists():
        return ""
    encoded = b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def mssql_configured() -> bool:
    return bool(MSSQL_SERVER and MSSQL_DATABASE and MSSQL_USERNAME)


def get_mssql_connection():
    if not mssql_configured():
        raise RuntimeError("MSSQL is not configured. Set MSSQL_SERVER, MSSQL_DATABASE, MSSQL_USERNAME, and MSSQL_PASSWORD.")
    try:
        import pymssql
    except ImportError as exc:
        raise RuntimeError("pymssql is not installed.") from exc
    return pymssql.connect(
        server=MSSQL_SERVER,
        port=MSSQL_PORT,
        user=MSSQL_USERNAME,
        password=MSSQL_PASSWORD,
        database=MSSQL_DATABASE,
        login_timeout=MSSQL_LOGIN_TIMEOUT,
        as_dict=False,
    )


def db_status_to_ui(status: str) -> str:
    return {
        "ACTIVE": "Activated",
        "CANCELLED": "Cancelled",
        "COMPLETED": "Jobs Ready",
        "DRAFT": "Draft",
        "PAUSED": "Waiting for Schedule",
        "SYNC_FAILED": "Sync Failed",
    }.get(status or "", status or "Draft")


def ui_status_to_db(status: str) -> str:
    return {
        "Activated": "ACTIVE",
        "Waiting for Schedule": "ACTIVE",
        "Jobs Ready": "COMPLETED",
        "Cancelled": "CANCELLED",
        "Sync Failed": "SYNC_FAILED",
    }.get(status, "ACTIVE")


def parse_config_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def db_campaign_to_view(row: dict) -> dict:
    config = parse_config_json(row.get("config_json"))
    payload = config.get("channel_payload", {})
    content = config.get("content")
    if not content and row.get("channel") == "APP":
        content = f"{payload.get('title', '-')}: {payload.get('body', '-')}"
    schedule_ui = schedule_ui_config_from_campaign_config(config)
    schedule_value = "-"
    end_schedule_value = "-"
    if schedule_ui["send_date"] and schedule_ui["send_time"]:
        schedule_value = f"{schedule_ui['send_date']} {schedule_ui['send_time']}"
    if schedule_ui["mode"] != "一次性排程" and schedule_ui["end_date"]:
        end_schedule_value = schedule_ui["end_date"]
    return {
        "id": str(row.get("campaign_id")),
        "name": row.get("campaign_name"),
        "channel": row.get("channel"),
        "segment": row.get("segma_segment_name") or str(row.get("segma_segment_id")),
        "app_id": row.get("app_id"),
        "app_name": payload.get("app_name"),
        "schedule": schedule_value,
        "end_schedule": end_schedule_value,
        "status": db_status_to_ui(row.get("status")),
        "username": row.get("username") or row.get("created_by"),
        "sender": row.get("sender") or payload.get("sender"),
        "content": content or "-",
        "description": row.get("campaign_description") or "-",
        "segma_sync_id": row.get("segma_sync_id"),
        "segma_sync_name": row.get("segma_sync_id"),
        "seed_action_dataset_id": row.get("seed_action_dataset_id"),
        "seed_action_dataset_name": row.get("seed_action_dataset_name"),
        "seed_segma_sync_id": row.get("seed_segma_sync_id"),
        "seed_segma_sync_name": row.get("seed_segma_sync_id"),
        "config": config,
    }


def load_campaigns_from_mssql(channel: str) -> list[dict]:
    sql = f"""
    SELECT campaign_id,
           campaign_name,
           campaign_description,
           channel,
           app_id,
           segma_segment_id,
           segma_segment_name,
           segma_sync_id,
           seed_action_dataset_id,
           seed_action_dataset_name,
           seed_segma_sync_id,
           schedule_cron,
           start_at,
           end_at,
           timezone,
           status,
           username,
           sender,
           config_json,
           created_by,
           created_at,
           updated_at
      FROM {MSSQL_SCHEMA}.campaign
     WHERE channel = %s
     ORDER BY created_at DESC;
    """
    with get_mssql_connection() as conn:
        cursor = conn.cursor(as_dict=True)
        cursor.execute(sql, (channel,))
        rows = cursor.fetchall()
    return [db_campaign_to_view(row) for row in rows]


def build_campaign_config(channel: str) -> dict:
    schedule_ui = schedule_ui_config_from_state()
    config = {
        "content": channel_content(channel),
        "channel_payload": channel_payload(channel),
        "campaign_delivery_mode": st.session_state.campaign_delivery_mode,
        "edm_filter_null_email": st.session_state.edm_filter_null_email,
        "edm_deduplicate_email": st.session_state.edm_deduplicate_email,
        "sms_filter_null_phone_number": st.session_state.sms_filter_null_phone_number,
        "sms_deduplicate_phone_number": st.session_state.sms_deduplicate_phone_number,
        "sms_seed_fallback_values": sms_seed_fallback_values() if channel == "SMS" and st.session_state.use_seed_list else {},
        "use_seed_list": st.session_state.use_seed_list,
        "seed_list": st.session_state.seed_list if st.session_state.use_seed_list else "",
        "seed_action_dataset_id": st.session_state.seed_action_dataset_id if st.session_state.use_seed_list else "",
        "seed_action_dataset_name": st.session_state.seed_action_dataset_name if st.session_state.use_seed_list else "",
        "test_recipient": st.session_state.test_recipient,
        "schedule_ui": schedule_ui,
    }
    if channel != "APP":
        config["sender"] = st.session_state.sender
    return config


def load_applications_from_mssql() -> list[dict]:
    sql = f"""
    SELECT app_id,
           app_name,
           platforms_json,
           azure_notification_hub_name,
           azure_connection_secret_name,
           default_payload_json
      FROM {MSSQL_SCHEMA}.app_notification_application
     WHERE is_active = 1
     ORDER BY app_name ASC;
    """
    with get_mssql_connection() as conn:
        cursor = conn.cursor(as_dict=True)
        cursor.execute(sql)
        return cursor.fetchall()


def selected_application() -> dict | None:
    if st.session_state.selected_channel != "APP" or not st.session_state.app_id:
        return None
    for app in load_applications_from_mssql():
        if app["app_id"] == st.session_state.app_id:
            return app
    return None


def insert_campaign_to_mssql() -> str:
    channel = st.session_state.selected_channel
    source = current_segma_segment_sources()[st.session_state.segment]
    config = build_campaign_config(channel)
    normalized_schedule = normalized_schedule_from_config(config)
    config_json = json.dumps(config, ensure_ascii=False)
    sql = f"""
    INSERT INTO {MSSQL_SCHEMA}.campaign (
        campaign_name,
        campaign_description,
        channel,
        app_id,
        segma_segment_id,
        segma_segment_name,
        seed_action_dataset_id,
        seed_action_dataset_name,
        schedule_cron,
        start_at,
        end_at,
        timezone,
        status,
        username,
        sender,
        config_json,
        created_by
    )
    OUTPUT INSERTED.campaign_id
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'DRAFT', %s, %s, %s, %s);
    """
    username = current_segma_username()
    params = (
        st.session_state.campaign_name,
        st.session_state.campaign_description,
        channel,
        st.session_state.app_id if channel == "APP" else None,
        source["source_id"],
        source["source_name"],
        int(st.session_state.seed_action_dataset_id)
        if st.session_state.use_seed_list and st.session_state.seed_action_dataset_id
        else None,
        st.session_state.seed_action_dataset_name if st.session_state.use_seed_list else None,
        normalized_schedule["schedule_cron"],
        datetimeoffset_literal(normalized_schedule["start_at"]),
        datetimeoffset_literal(normalized_schedule["end_at"]),
        normalized_schedule["timezone"],
        username,
        None if channel == "APP" else st.session_state.sender,
        config_json,
        username or st.session_state.test_recipient or "streamlit",
    )
    with get_mssql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        campaign_id = cursor.fetchone()[0]
        conn.commit()
    return str(campaign_id)


def update_campaign_after_segma(campaign_id: str, sync: dict, seed_sync: dict | None = None) -> None:
    sync_id = str(sync.get("id") or sync.get("name") or "")
    seed_sync_id = str(seed_sync.get("id") or seed_sync.get("name") or "") if seed_sync else None
    sql = f"""
    UPDATE {MSSQL_SCHEMA}.campaign
       SET segma_sync_id = %s,
           seed_segma_sync_id = %s,
           status = 'ACTIVE',
           updated_at = SYSDATETIMEOFFSET()
     WHERE campaign_id = %s;
    """
    with get_mssql_connection() as conn:
        conn.cursor().execute(sql, (sync_id, seed_sync_id, campaign_id))
        conn.commit()


def mark_campaign_sync_failed(campaign_id: str, sync: dict | None = None, seed_sync: dict | None = None) -> None:
    sync_id = str(sync.get("id") or sync.get("name") or "") if sync else None
    seed_sync_id = str(seed_sync.get("id") or seed_sync.get("name") or "") if seed_sync else None
    sql = f"""
    UPDATE {MSSQL_SCHEMA}.campaign
       SET segma_sync_id = COALESCE(%s, segma_sync_id),
           seed_segma_sync_id = COALESCE(%s, seed_segma_sync_id),
           status = 'SYNC_FAILED',
           updated_at = SYSDATETIMEOFFSET()
     WHERE campaign_id = %s;
    """
    with get_mssql_connection() as conn:
        conn.cursor().execute(sql, (sync_id, seed_sync_id, campaign_id))
        conn.commit()


def mark_campaign_cancelled(campaign: dict) -> None:
    campaign_id = campaign["id"]
    channel = campaign.get("channel") or st.session_state.selected_channel
    target = SEGMA_TARGET_TABLES[channel]
    job_table = f"{target['schema']}.{target['table']}"
    campaign_sql = f"""
    UPDATE {MSSQL_SCHEMA}.campaign
       SET status = 'CANCELLED',
           updated_at = SYSDATETIMEOFFSET()
     WHERE campaign_id = %s;
    """
    job_sql = f"""
    UPDATE {job_table}
       SET status = 'CANCELLED',
           updated_at = SYSDATETIMEOFFSET(),
           last_error = NULL
     WHERE campaign_id = %s
       AND status = 'NEW';
    """
    with get_mssql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(campaign_sql, (campaign_id,))
        cursor.execute(job_sql, (campaign_id,))
        conn.commit()


def delete_campaign_and_jobs(campaign: dict) -> None:
    campaign_id = campaign["id"]
    channel = campaign.get("channel") or st.session_state.selected_channel
    target = SEGMA_TARGET_TABLES[channel]
    job_table = f"{target['schema']}.{target['table']}"
    delete_jobs_sql = f"DELETE FROM {job_table} WHERE campaign_id = %s;"
    delete_campaign_sql = f"DELETE FROM {MSSQL_SCHEMA}.campaign WHERE campaign_id = %s;"
    with get_mssql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(delete_jobs_sql, (campaign_id,))
        cursor.execute(delete_campaign_sql, (campaign_id,))
        conn.commit()


def delete_segma_sync(sync_id: str) -> None:
    if not SEGMA_API_BASE_URL:
        raise RuntimeError("SEGMA_API is not configured.")

    headers = {"Accept": "application/json"}
    if SEGMA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SEGMA_API_TOKEN}"

    request = Request(
        f"{SEGMA_API_BASE_URL}/api/v1/syncs/{quote(str(sync_id), safe='')}",
        headers=headers,
        method="DELETE",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SEGMA sync delete returned HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach SEGMA sync delete API: {exc.reason}") from exc


def trigger_segma_sync(sync_id: str) -> dict:
    if not SEGMA_API_BASE_URL:
        raise RuntimeError("SEGMA_API is not configured.")

    headers = {"Accept": "application/json"}
    if SEGMA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SEGMA_API_TOKEN}"

    request = Request(
        f"{SEGMA_API_BASE_URL}/api/v1/syncs/{quote(str(sync_id), safe='')}/trigger",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body) if response_body else {}
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SEGMA sync trigger returned HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach SEGMA sync trigger API: {exc.reason}") from exc


def trigger_campaign_syncs(campaign: dict) -> list[dict]:
    sync_ids = [
        str(sync_id).strip()
        for sync_id in (campaign.get("seed_segma_sync_id"), campaign.get("segma_sync_id"))
        if sync_id and str(sync_id).strip()
    ]
    return [trigger_segma_sync(sync_id) for sync_id in dict.fromkeys(sync_ids)]


def delete_campaign_segma_syncs(campaign: dict) -> None:
    cleanup_errors = []
    sync_ids = [
        str(sync_id).strip()
        for sync_id in (campaign.get("seed_segma_sync_id"), campaign.get("segma_sync_id"))
        if sync_id and str(sync_id).strip()
    ]
    for sync_id in dict.fromkeys(sync_ids):
        try:
            delete_segma_sync(sync_id)
        except Exception as exc:
            cleanup_errors.append(f"SEGMA sync {sync_id} 清理失敗：{exc}")
    st.session_state.campaign_action_warning = "；".join(cleanup_errors)


def cancel_campaign(campaign: dict) -> None:
    cleanup_errors = []
    mark_campaign_cancelled(campaign)
    sync_ids = [
        str(sync_id).strip()
        for sync_id in (campaign.get("seed_segma_sync_id"), campaign.get("segma_sync_id"))
        if sync_id and str(sync_id).strip()
    ]
    for sync_id in dict.fromkeys(sync_ids):
        try:
            delete_segma_sync(sync_id)
        except Exception as exc:
            cleanup_errors.append(f"SEGMA sync {sync_id} 清理失敗：{exc}")
    st.session_state.campaign_action_warning = "；".join(cleanup_errors)


def parse_azure_connection_string(connection_string: str) -> dict[str, str]:
    values = {}
    for part in connection_string.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key] = value
    missing = {"Endpoint", "SharedAccessKeyName", "SharedAccessKey"}.difference(values)
    if missing:
        raise RuntimeError(f"Azure Notification Hub connection string missing: {', '.join(sorted(missing))}")
    return values


def sendgrid_api_request(path: str) -> dict:
    if not SENDGRID_API_KEY:
        raise RuntimeError("SENDGRID_API_KEY must be configured to load SendGrid templates.")
    request = Request(
        f"{SENDGRID_API_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body) if response_body else {}
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SendGrid API returned HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach SendGrid API: {exc.reason}") from exc


@st.cache_data(ttl=300)
def load_sendgrid_dynamic_templates() -> list[dict]:
    payload = sendgrid_api_request("/v3/templates?generations=dynamic")
    templates = payload.get("templates", [])
    return [
        {
            "id": item.get("id", ""),
            "name": item.get("name") or item.get("id", ""),
            "updated_at": item.get("updated_at", ""),
        }
        for item in templates
        if item.get("id")
    ]


@st.cache_data(ttl=300)
def load_sendgrid_template(template_id: str) -> dict:
    return sendgrid_api_request(f"/v3/templates/{template_id}")


def active_sendgrid_version(template: dict) -> dict:
    versions = template.get("versions", [])
    active = next((version for version in versions if version.get("active") == 1), None)
    return active or (versions[0] if versions else {})


def parse_dynamic_template_data() -> dict:
    raw = st.session_state.edm_dynamic_template_data.strip()
    data = json.loads(raw) if raw else {}
    if not isinstance(data, dict):
        raise ValueError("Dynamic Template Data JSON must be an object.")
    return data


def selected_segment_source() -> dict:
    return current_segma_segment_sources().get(st.session_state.segment, {})


def selected_segment_traits() -> dict:
    return selected_segment_source().get("traits_by_name", {})


def selected_seed_action_dataset_source() -> dict:
    source = current_seed_action_dataset_sources().get(st.session_state.seed_list, {})
    if source and not source.get("has_column_metadata"):
        source = dict(source)
        source["columns_by_name"] = load_segma_action_dataset_columns(source["source_id"])
        source["has_column_metadata"] = True
    return source


def dynamic_template_data_pairs() -> list[dict]:
    pairs = []
    row_count = int(st.session_state.get("edm_template_data_pair_count", 0) or 0)
    for index in range(row_count):
        key_name = st.session_state.get(f"edm_template_data_key_{index}", "").strip()
        trait_name = st.session_state.get(f"edm_template_data_trait_{index}", "").strip()
        if key_name or trait_name:
            pairs.append({"key": key_name, "trait": trait_name})
    return pairs


def validate_dynamic_template_data_pairs() -> list[str]:
    errors = []
    seen_keys = set()
    available_traits = selected_segment_traits()
    for pair in dynamic_template_data_pairs():
        key_name = pair["key"]
        trait_name = pair["trait"]
        if not key_name:
            errors.append("Dynamic Template Data key 不可空白。")
        elif key_name in seen_keys:
            errors.append(f"Dynamic Template Data key 重複：{key_name}")
        else:
            seen_keys.add(key_name)
        if not trait_name:
            errors.append(f"Dynamic Template Data key「{key_name or '-'}」尚未選擇 trait。")
        elif trait_name not in available_traits:
            errors.append(f"Dynamic Template Data trait 不存在：{trait_name}")
    return errors


def refresh_dynamic_template_data_json() -> None:
    preview_data = {pair["key"]: f"[{pair['trait']}]" for pair in dynamic_template_data_pairs() if pair["key"] and pair["trait"]}
    st.session_state.edm_dynamic_template_data = json.dumps(preview_data, ensure_ascii=False)


def json_string_fragment(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)[1:-1]


def sql_nvarchar_string_literal(value: str | None) -> str:
    return f"CAST({sql_string_literal(value)} AS NVARCHAR(MAX))"


def sql_nvarchar_expression(value: str) -> str:
    return f"CAST({value} AS NVARCHAR(MAX))"


def dynamic_template_data_formula() -> str:
    pairs = dynamic_template_data_pairs()
    if not pairs:
        return sql_nvarchar_string_literal("{}")
    parts = [sql_nvarchar_string_literal("{")]
    for index, pair in enumerate(pairs):
        if index:
            parts.append(sql_nvarchar_string_literal(","))
        parts.append(sql_nvarchar_string_literal(f'"{json_string_fragment(pair["key"])}":"'))
        parts.append(sql_nvarchar_expression(f"[{pair['trait']}]"))
        parts.append(sql_nvarchar_string_literal('"'))
    parts.append(sql_nvarchar_string_literal("}"))
    return "CONCAT(" + ",".join(parts) + ")"


SMS_PLACEHOLDER_PATTERN = re.compile(r"\[([^\[\]]+)\]")


def sms_message_placeholders(message: str | None = None) -> list[str]:
    text = st.session_state.sms_copy if message is None else message
    placeholders = []
    for match in SMS_PLACEHOLDER_PATTERN.finditer(text or ""):
        trait_name = match.group(1).strip()
        if trait_name and trait_name not in placeholders:
            placeholders.append(trait_name)
    return placeholders


def validate_sms_message_placeholders() -> list[str]:
    errors = []
    available_traits = selected_segment_traits()
    for trait_name in sms_message_placeholders():
        if trait_name not in available_traits:
            errors.append(f"SMS 個人化欄位不存在：[{trait_name}]")
    return errors


def sms_seed_fallback_key(trait_name: str) -> str:
    return f"sms_seed_fallback_{trait_name}"


def sms_seed_fallback_values(placeholders: list[str] | None = None) -> dict:
    trait_names = sms_message_placeholders() if placeholders is None else placeholders
    return {
        trait_name: st.session_state.get(sms_seed_fallback_key(trait_name), "").strip()
        for trait_name in trait_names
    }


def validate_sms_seed_fallback_values() -> list[str]:
    if not st.session_state.use_seed_list:
        return []
    placeholders = sms_message_placeholders()
    if not placeholders:
        return []
    errors = []
    fallback_values = sms_seed_fallback_values(placeholders)
    for trait_name in placeholders:
        if not fallback_values.get(trait_name):
            errors.append(f"SMS seed fallback value 不可空白：[{trait_name}]")
    return errors


def sms_message_body_formula(message: str | None = None, fallback_values: dict | None = None) -> str:
    message = st.session_state.sms_copy if message is None else message
    message = message or ""
    matches = list(SMS_PLACEHOLDER_PATTERN.finditer(message))
    if not matches:
        return sql_string_literal(message)

    parts = []
    cursor = 0
    for match in matches:
        literal = message[cursor : match.start()]
        if literal:
            parts.append(sql_nvarchar_string_literal(literal))
        trait_name = match.group(1).strip()
        if fallback_values is not None and trait_name in fallback_values:
            parts.append(sql_nvarchar_string_literal(fallback_values[trait_name]))
        else:
            parts.append(sql_nvarchar_expression(f"[{trait_name}]"))
        cursor = match.end()
    trailing_literal = message[cursor:]
    if trailing_literal:
        parts.append(sql_nvarchar_string_literal(trailing_literal))
    if not parts:
        return sql_nvarchar_string_literal("")
    return "CONCAT(" + ",".join(parts) + ")"


def render_sms_message_with_values(message: str, values: dict) -> str:
    rendered = message
    for trait_name, value in values.items():
        rendered = rendered.replace(f"[{trait_name}]", str(value))
    return rendered


def sms_test_message_body(test_personalization: dict) -> str:
    if "message_body" in test_personalization:
        return str(test_personalization["message_body"])
    message_body = st.session_state.sms_copy
    if not st.session_state.use_seed_list:
        return message_body
    fallback_values = sms_seed_fallback_values()
    if sms_message_placeholders(message_body) and any(fallback_values.values()):
        return render_sms_message_with_values(message_body, fallback_values)
    return message_body


def append_sms_placeholder(trait_name: str) -> None:
    current_copy = st.session_state.sms_copy or ""
    separator = "" if not current_copy or current_copy.endswith((" ", "\n")) else " "
    st.session_state.sms_copy = f"{current_copy}{separator}[{trait_name}]"


def parse_test_personalization_json() -> dict:
    if st.session_state.selected_channel == "SMS":
        return {}
    raw = st.session_state.test_personalization_json.strip()
    data = json.loads(raw) if raw else {}
    if not isinstance(data, dict):
        raise ValueError("Test personalization_json must be an object.")
    for key in ("dynamic_template_data", "custom_args", "notification", "data"):
        if key in data and not isinstance(data[key], dict):
            raise ValueError(f"test personalization_json.{key} must be an object.")
    return data


def edm_template_id_from_payload(payload: dict) -> str:
    return payload.get("provider_template_id") or payload.get("sendgrid_template_id") or ""


def edm_test_personalization_data(personalization: dict) -> dict:
    advanced_keys = {
        "dynamic_template_data",
        "custom_args",
        "content_mode",
        "subject",
        "body_html",
        "provider_template_id",
        "sendgrid_template_id",
        "sender",
    }
    if any(key in personalization for key in advanced_keys):
        return personalization
    return {"dynamic_template_data": personalization}


def merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    merged.update(override)
    return merged


def format_provider_exception(exc: Exception) -> str:
    details = [str(exc)]
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if value and f"HTTP {value}" not in details[0]:
            details.append(f"HTTP {value}")
            break
    body = getattr(exc, "body", None)
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if body:
        details.append(f"body={body}")
    headers = getattr(exc, "headers", None)
    if headers:
        details.append(f"headers={dict(headers)}")
    return " | ".join(details)


def render_template_sample(html: str, values: dict) -> str:
    rendered = html
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
        rendered = rendered.replace("{{ " + key + " }}", str(value))
    return rendered


def build_azure_sas_token(resource_uri: str, key_name: str, key: str) -> str:
    expiry = str(int(time.time() + 300))
    encoded_uri = quote(resource_uri, safe="").lower()
    to_sign = f"{encoded_uri}\n{expiry}".encode("utf-8")
    signature = base64.b64encode(hmac.new(key.encode("utf-8"), to_sign, hashlib.sha256).digest()).decode("ascii")
    return (
        "SharedAccessSignature "
        f"sig={quote(signature, safe='')}&se={expiry}&skn={key_name}&sr={encoded_uri}"
    )


def send_azure_test_notification(
    target: str,
    title: str,
    body: str,
    data_json: str,
    connection_string: str,
    hub_name: str,
) -> str:
    if not connection_string or not hub_name:
        raise RuntimeError("Azure Notification Hub connection string and hub name must be configured.")
    parts = parse_azure_connection_string(connection_string)
    endpoint = "https://" + parts["Endpoint"][5:].lower() if parts["Endpoint"].startswith("sb://") else parts["Endpoint"]
    resource_uri = f"{endpoint.rstrip('/')}/{hub_name}"
    parsed = urlparse(endpoint)
    payload_data = json.loads(data_json or "{}")
    payload = {
        "notification": {"title": title, "body": body},
        "data": payload_data,
    }
    headers = {
        "Authorization": build_azure_sas_token(resource_uri, parts["SharedAccessKeyName"], parts["SharedAccessKey"]),
        "Content-Type": "application/json;charset=utf-8",
        "ServiceBusNotification-Format": "gcm",
        "ServiceBusNotification-Tags": target,
    }
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=30)
    try:
        connection.request(
            "POST",
            f"/{hub_name}/messages/?api-version=2015-01",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        response = connection.getresponse()
        response_body = response.read().decode("utf-8", errors="replace")
        if response.status != 201:
            raise RuntimeError(f"Azure Notification Hub returned HTTP {response.status}: {response_body}")
        return response.getheader("TrackingId") or response.getheader("Location") or "accepted"
    finally:
        connection.close()


def send_real_test_message(channel: str, recipient: str) -> str:
    test_personalization = parse_test_personalization_json()
    if channel == "EDM":
        test_personalization = edm_test_personalization_data(test_personalization)
        if not st.session_state.edm_template_id:
            raise RuntimeError("Select a SendGrid Dynamic Template before sending a real test email.")
        if not SENDGRID_API_KEY or not st.session_state.sender.strip():
            raise RuntimeError("SENDGRID_API_KEY and Sender must be configured.")
        from sendgrid import SendGridAPIClient

        dynamic_data = parse_dynamic_template_data()
        dynamic_data = merge_dict(dynamic_data, test_personalization.get("dynamic_template_data", {}))
        dynamic_data.setdefault("campaign_name", st.session_state.campaign_name)
        dynamic_data["is_test"] = True
        custom_args = {"campaign_name": st.session_state.campaign_name, "test_send": "true"}
        custom_args.update({str(key): str(value) for key, value in test_personalization.get("custom_args", {}).items()})
        request_body = {
            "from": {"email": st.session_state.sender.strip()},
            "template_id": st.session_state.edm_template_id,
            "personalizations": [
                {
                    "to": [{"email": recipient}],
                    "dynamic_template_data": dynamic_data,
                    "custom_args": custom_args,
                }
            ],
        }
        try:
            response = SendGridAPIClient(SENDGRID_API_KEY).client.mail.send.post(request_body=request_body)
        except Exception as exc:
            raise RuntimeError(f"SendGrid Mail Send failed: {format_provider_exception(exc)}") from exc
        message_id = response.headers.get("X-Message-Id") if response.headers else None
        return f"SendGrid accepted test email. message_id={message_id or '-'}"

    if channel == "SMS":
        if not st.session_state.sms_copy.strip():
            raise RuntimeError("Enter SMS content before sending a real test SMS.")
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not st.session_state.sender.strip():
            raise RuntimeError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and Sender must be configured.")
        from twilio.rest import Client

        message_body = sms_test_message_body(test_personalization)
        try:
            message = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN).messages.create(
                body=f"[TEST] {message_body}",
                from_=st.session_state.sender.strip(),
                to=recipient,
            )
        except Exception as exc:
            details = format_provider_exception(exc)
            if "401" in details or "Authenticate" in details:
                details = (
                    f"{details} | Check TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN "
                    "for the Streamlit process."
                )
            raise RuntimeError(f"Twilio SMS test send failed: {details}") from exc
        return f"Twilio accepted test SMS. sid={message.sid}"

    if not st.session_state.app_title.strip() or not st.session_state.app_body.strip():
        raise RuntimeError("Enter APP notification title and body before sending a real test push.")
    app = selected_application()
    if not app:
        raise RuntimeError("Select an active application before sending a real test push.")
    connection_string = os.getenv(app["azure_connection_secret_name"], "")
    if not connection_string:
        raise RuntimeError(f"Azure connection env is not configured: {app['azure_connection_secret_name']}")
    notification = test_personalization.get("notification", {})
    data_override = test_personalization.get("data", {})
    base_data = json.loads(st.session_state.app_data_json or "{}")
    data_json = json.dumps(merge_dict(base_data, data_override), ensure_ascii=False)
    tracking_id = send_azure_test_notification(
        recipient,
        f"[TEST] {notification.get('title', st.session_state.app_title)}",
        notification.get("body", st.session_state.app_body),
        data_json,
        connection_string,
        app["azure_notification_hub_name"],
    )
    return f"Azure Notification Hubs accepted test push. tracking_id={tracking_id}"


def test_send_succeeded() -> bool:
    status = st.session_state.get("create_test_status", "")
    return status.startswith(
        (
            "SendGrid accepted",
            "Twilio accepted",
            "Azure Notification Hubs accepted",
        )
    )


def slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or fallback


def parse_schedule_start() -> datetime:
    return parse_schedule_start_from_config(schedule_ui_config_from_state())


def parse_schedule_end(start_at: datetime) -> datetime | None:
    return parse_schedule_end_from_config(schedule_ui_config_from_state(), start_at)


def schedule_datetime_with_tz(value: datetime) -> str:
    return datetimeoffset_literal(value)


def parse_schedule_month_day() -> int:
    return parse_schedule_month_day_from_config(schedule_ui_config_from_state())


def sync_cron(start_at: datetime) -> str:
    return sync_cron_from_config(schedule_ui_config_from_state(), start_at)


def sync_schedule_fields(start_at: datetime) -> dict:
    fields = {"cron": sync_cron(start_at)}
    end_at = parse_schedule_end(start_at)
    if end_at:
        fields["start_date"] = schedule_datetime_with_tz(start_at)
        fields["end_date"] = schedule_datetime_with_tz(end_at)
    return fields


def schedule_preview_text(start_at: datetime) -> str:
    fields = sync_schedule_fields(start_at)
    cron = fields["cron"]
    mode = st.session_state.schedule_mode
    if mode == "一次性排程":
        return (
            f'SEGMA cron: "{cron}"，start_date: "{fields["start_date"]}"，'
            f'end_date: "{fields["end_date"]}"。'
        )
    return f'SEGMA cron: "{cron}"，start_date: "{fields["start_date"]}"，end_date: "{fields["end_date"]}"。'


def channel_content(channel: str) -> str:
    if channel == "EDM":
        return st.session_state.edm_template_name or st.session_state.edm_template_id
    if channel == "SMS":
        return st.session_state.sms_copy
    return f"{st.session_state.app_title} - {st.session_state.app_body}"


def channel_payload(channel: str) -> dict:
    if channel == "EDM":
        return {
            "content_mode": "SENDGRID_TEMPLATE",
            "sender": st.session_state.sender,
            "provider_template_id": st.session_state.edm_template_id,
            "sendgrid_template_name": st.session_state.edm_template_name,
            "sendgrid_template_version_id": st.session_state.edm_template_version_id,
            "dynamic_template_data_json": st.session_state.edm_dynamic_template_data,
        }
    if channel == "APP":
        app = selected_application()
        return {
            "app_id": st.session_state.app_id,
            "app_name": app["app_name"] if app else st.session_state.app_name,
            "azure_notification_hub_name": app["azure_notification_hub_name"] if app else "",
            "title": st.session_state.app_title,
            "body": st.session_state.app_body,
            "data_json": st.session_state.app_data_json,
        }
    return {"content": channel_content(channel), "sender": st.session_state.sender}


def segma_destination_id(channel: str) -> int:
    value = st.session_state.selected_sync_destination_id or os.getenv(f"SEGMA_{channel}_DESTINATION_ID") or SEGMA_MSSQL_DESTINATION_ID
    if not value:
        raise RuntimeError("請選擇 SEGMA SQL Server table sync 目的地。")
    return int(value)


def source_field_attribute(source: dict, field_name: str, alias: str, position: int) -> dict:
    if source.get("source_type") == "Segment":
        trait_id = source.get("traits_by_name", {}).get(field_name)
        if trait_id is None:
            raise ValueError(f"SEGMA segment 缺少必要 trait：{field_name}")
        return {"trait_id": trait_id, "alias": alias, "position": position}
    if source.get("source_type") == "ActionDataset":
        if not source.get("has_column_metadata"):
            raise ValueError("SEGMA ActionDataset 未回傳 columns/fields metadata，無法建立 field mapping。")
        column_name = source.get("columns_by_name", {}).get(field_name)
        if column_name is None:
            raise ValueError(f"SEGMA ActionDataset 缺少必要 column：{field_name}")
        return {"column_name": column_name, "alias": alias, "position": position}
    return {"column_name": field_name, "alias": alias, "position": position}


def formula_attribute(alias: str, formula: str, position: int) -> dict:
    return {"alias": alias, "formula": formula, "position": position}


def sql_string_literal(value: str | None) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def build_segma_sync_columns(channel: str, campaign_id: str, start_at: datetime, source: dict) -> list[dict]:
    username = current_segma_username()
    recipient_type = "SEED" if source.get("source_type") == "ActionDataset" else "CUSTOMER"
    base_columns = [
        source_field_attribute(source, "customer_id", "customer_id", 0),
        formula_attribute("campaign_id", sql_string_literal(campaign_id), 1),
        formula_attribute("recipient_type", sql_string_literal(recipient_type), 2),
        formula_attribute("username", sql_string_literal(username), 3),
    ]
    if channel == "EDM":
        return base_columns + [
            formula_attribute("sender", sql_string_literal(st.session_state.sender), 4),
            formula_attribute("scheduled_for", sql_string_literal(start_at.strftime("%Y-%m-%dT%H:%M:%S")), 5),
            source_field_attribute(source, "email", "email", 6),
            formula_attribute("subject", sql_string_literal(st.session_state.email_subject), 7),
            formula_attribute("body_html", sql_string_literal(""), 8),
            formula_attribute("content_mode", sql_string_literal("SENDGRID_TEMPLATE"), 9),
            formula_attribute("provider_template_id", sql_string_literal(st.session_state.edm_template_id), 10),
            formula_attribute("dynamic_template_data_json", dynamic_template_data_formula(), 11),
        ]
    if channel == "SMS":
        message_body_formula = sms_message_body_formula()
        if source.get("source_type") == "ActionDataset":
            message_body_formula = sms_message_body_formula(fallback_values=sms_seed_fallback_values())
        return base_columns + [
            formula_attribute("sender", sql_string_literal(st.session_state.sender), 4),
            formula_attribute("scheduled_for", sql_string_literal(start_at.strftime("%Y-%m-%dT%H:%M:%S")), 5),
            source_field_attribute(source, "phone_number", "phone_number", 6),
            formula_attribute("message_body", message_body_formula, 7),
        ]
    return base_columns + [
        formula_attribute("scheduled_for", sql_string_literal(start_at.strftime("%Y-%m-%dT%H:%M:%S")), 4),
        formula_attribute("app_id", sql_string_literal(st.session_state.app_id), 5),
        source_field_attribute(source, "user_id", "user_id", 6),
        source_field_attribute(source, "device_handle", "device_handle", 7),
        formula_attribute("notification_title", sql_string_literal(st.session_state.app_title), 8),
        formula_attribute("notification_body", sql_string_literal(st.session_state.app_body), 9),
        formula_attribute("data_json", sql_string_literal(st.session_state.app_data_json), 10),
    ]


def segma_sync_write_mode(channel: str, source: dict) -> str:
    if source.get("source_type") == "ActionDataset":
        return "append"
    if channel in ("EDM", "SMS") and st.session_state.campaign_delivery_mode == "差異化活動":
        return "insert_only_new"
    return "append"


def segma_sync_key_columns(channel: str) -> list[str]:
    if channel == "EDM":
        return ["campaign_id", "email"]
    if channel == "SMS":
        return ["campaign_id", "phone_number"]
    return []


def build_segma_sync_payload(campaign_id: str, source_override: dict | None = None) -> dict:
    channel = st.session_state.selected_channel
    config = build_campaign_config(channel)
    normalized_schedule = normalized_schedule_from_config(config)
    start_at = normalized_schedule["start_at"]
    source = source_override or current_segma_segment_sources()[st.session_state.segment]
    target_table = SEGMA_TARGET_TABLES[channel]

    params = {
        "target_table": target_table,
        "write_mode": segma_sync_write_mode(channel, source),
        "chunksize": SEGMA_SYNC_CHUNKSIZE,
    }
    if params["write_mode"] == "insert_only_new":
        params["key_columns"] = segma_sync_key_columns(channel)

    payload = {
        "description": st.session_state.campaign_description
        or f"Create {channel} campaign sync for {st.session_state.segment}",
        "source_type": source.get("source_type") or "Segment",
        "source_id": source["source_id"],
        "sync_destination_id": segma_destination_id(channel),
        "action_type": "mssql_table",
        "cron": normalized_schedule["schedule_cron"],
        "start_date": schedule_datetime_with_tz(start_at),
        "end_date": schedule_datetime_with_tz(normalized_schedule["end_at"]),
        "params": params,
        "sync_columns_attributes": build_segma_sync_columns(channel, campaign_id, start_at, source),
    }
    if channel == "EDM":
        if st.session_state.edm_filter_null_email:
            payload["sync_filters_attributes"] = [
                {"column_name": "email", "operator": "not-null", "position": 0}
            ]
        if st.session_state.edm_deduplicate_email:
            payload.update(
                {
                    "enable_deduplicate": True,
                    "dedup_columns": ["email"],
                    "dedup_keep": "first",
                }
            )
    if channel == "SMS":
        if st.session_state.sms_filter_null_phone_number:
            payload["sync_filters_attributes"] = [
                {"column_name": "phone_number", "operator": "not-null", "position": 0}
            ]
        if st.session_state.sms_deduplicate_phone_number:
            payload.update(
                {
                    "enable_deduplicate": True,
                    "dedup_columns": ["phone_number"],
                    "dedup_keep": "first",
                }
            )
    return payload


def segma_sync_request_debug(payload: dict) -> str:
    return "\n\nSEGMA sync request:\n" + json.dumps(
        {
            "method": "POST",
            "url": f"{SEGMA_API_BASE_URL}/api/v1/syncs",
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer <redacted>" if SEGMA_API_TOKEN else "",
            },
            "body": payload,
        },
        ensure_ascii=False,
        indent=2,
    )


def create_segma_sync(payload: dict) -> dict:
    if not SEGMA_API_BASE_URL:
        raise RuntimeError("SEGMA_API is not configured.")
    if not isinstance(payload, dict):
        raise RuntimeError(f"SEGMA sync payload must be an object, got {type(payload).__name__}.")

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if SEGMA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SEGMA_API_TOKEN}"

    request = Request(
        f"{SEGMA_API_BASE_URL}/api/v1/syncs",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body) if response_body else {}
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SEGMA API returned HTTP {exc.code}: {error_body}{segma_sync_request_debug(payload)}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach SEGMA API: {exc.reason}{segma_sync_request_debug(payload)}") from exc


def validate_campaign_form(seed_source: dict | None = None) -> list[str]:
    channel = st.session_state.selected_channel
    errors = []
    if not current_segments() or not st.session_state.segment:
        errors.append("請先確認 SEGMA /api/v1/segments 可用，或設定 SEGMA_SEGMENTS_JSON。")
    elif st.session_state.segment in current_segma_segment_sources():
        source = current_segma_segment_sources()[st.session_state.segment]
        missing_traits = missing_segment_trait_names(source, channel)
        if missing_traits:
            errors.append(
                "SEGMA segment 缺少建立此通路 sync 所需的 trait_id："
                + "、".join(missing_traits)
                + "。請確認 /api/v1/segments 回傳 dim_id，且 /api/v1/traits 回傳必要 traits，或在 SEGMA_SEGMENTS_JSON 中提供 traits。"
            )
    if st.session_state.use_seed_list and not st.session_state.seed_action_dataset_id:
        errors.append("已啟用 seed list，但尚未選擇 SEGMA seed ActionDataset。")
    elif st.session_state.use_seed_list:
        if seed_source is None:
            seed_source = selected_seed_action_dataset_source()
        if not seed_source:
            errors.append("無法驗證 SEGMA seed ActionDataset 欄位，請重新選擇 seed list。")
        else:
            errors.extend(validate_seed_action_dataset_source(seed_source, channel))
    if not st.session_state.selected_sync_destination_id:
        errors.append("請選擇 SEGMA SQL Server table sync 目的地。")
    else:
        try:
            int(st.session_state.selected_sync_destination_id)
        except ValueError:
            errors.append("SEGMA SQL Server table sync 目的地 ID 必須是數字。")
    if not st.session_state.campaign_name.strip():
        errors.append("請輸入活動名稱。")
    if st.session_state.campaign_delivery_mode not in CAMPAIGN_DELIVERY_MODES:
        errors.append("活動寫入模式無效。")
    if channel != "APP" and not st.session_state.sender.strip():
        errors.append("請輸入 Sender。")
    if channel == "EDM":
        if not st.session_state.edm_template_id:
            errors.append("請選擇 SendGrid Dynamic Template。")
        errors.extend(validate_dynamic_template_data_pairs())
    if channel == "SMS":
        if not st.session_state.sms_copy.strip():
            errors.append("請輸入 SMS 簡訊內容。")
        errors.extend(validate_sms_message_placeholders())
        errors.extend(validate_sms_seed_fallback_values())
    if channel == "APP":
        if not st.session_state.app_id:
            errors.append("請選擇要發送的 APP。")
        if not st.session_state.app_title.strip():
            errors.append("請輸入 APP 推播標題。")
        if not st.session_state.app_body.strip():
            errors.append("請輸入 APP 推播內容。")
        try:
            json.loads(st.session_state.app_data_json or "{}")
        except json.JSONDecodeError:
            errors.append("APP 附加資料必須是有效 JSON。")
    try:
        start_at = parse_schedule_start()
        parse_schedule_end(start_at)
    except ValueError as exc:
        errors.append(str(exc))
    if st.session_state.schedule_mode == "每月排程":
        try:
            parse_schedule_month_day()
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def channel_campaigns(channel: str) -> list[dict]:
    try:
        st.session_state.mssql_error = ""
        return load_campaigns_from_mssql(channel)
    except Exception as exc:
        st.session_state.mssql_error = str(exc)
        return []


def sync_workspace_state() -> None:
    channel = st.session_state.selected_channel
    if st.session_state.previous_channel != channel:
        st.session_state.previous_channel = channel
        st.session_state.campaign_view = "list"
        campaigns = channel_campaigns(channel)
        st.session_state.selected_campaign_id = campaigns[0]["id"] if campaigns else ""
        st.session_state.channels = [channel]


def selected_campaign() -> dict:
    campaign_id = st.session_state.selected_campaign_id
    for campaign in channel_campaigns(st.session_state.selected_channel):
        if campaign["id"] == campaign_id:
            return campaign
    campaigns = channel_campaigns(st.session_state.selected_channel)
    if not campaigns:
        return {
            "id": "",
            "name": "-",
            "segment": "-",
            "schedule": "-",
            "status": "Cancelled",
            "content": "-",
            "description": "-",
        }
    campaign = campaigns[0]
    st.session_state.selected_campaign_id = campaign["id"]
    return campaign


def campaign_status(campaign: dict) -> str:
    return campaign["status"]


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def yes_no(value: bool) -> str:
    return "是" if value else "否"


def status_tone(status: str) -> str:
    if status in CANCELLABLE_STATUSES:
        return "green"
    if status in {"Cancelled", "Sync Failed"}:
        return "red"
    return "gray"


def render_timeline() -> None:
    active_index = state_index(st.session_state.current_state)
    cells = []
    for idx, item in enumerate(STATE_SEQUENCE):
        css = "step"
        if st.session_state.cancelled and item["key"] in {"activated", "waiting"}:
            css += " cancelled"
        elif st.session_state.validation_failed and item["key"] == "scheduling":
            css += " blocked"
        elif idx < active_index:
            css += " done"
        elif idx == active_index:
            css += " active"
        cells.append(
            f"""
            <div class="{css}">
              <div class="step-num">{idx + 1}</div>
              <div class="step-title">{item['label']}</div>
              <div class="step-caption">{item['caption']}</div>
            </div>
            """
        )
    st.markdown(f"<div class='timeline'>{''.join(cells)}</div>", unsafe_allow_html=True)


def render_draft_header() -> None:
    logo_src = logo_data_url()
    logo_html = (
        f'<img class="brand-logo" src="{logo_src}" alt="Capital Securities logo">'
        if logo_src
        else '<div class="brand-logo-fallback">CAPITAL<br>群益金鼎證券</div>'
    )
    greeting_html = ""
    try:
        profile = load_segma_profile()
    except Exception as exc:
        profile = {}
        st.caption(f"SEGMA 使用者資訊讀取失敗：{exc}")
    username = profile.get("username", "")
    if username:
        display_name = html.escape(username)
        meta_parts = [profile.get("role", ""), profile.get("email", "")]
        meta = " · ".join(part for part in meta_parts if part)
        meta_html = f'<div class="user-greeting-meta">{html.escape(meta)}</div>' if meta else ""
        greeting_html = f"""
          <div class="user-greeting">
            <div class="user-greeting-box">
              <div>您好</div>
              <div class="user-greeting-name">{display_name}</div>
              {meta_html}
            </div>
          </div>
        """
    st.markdown(
        f"""
        <div class="draft-header">
          <div class="brand">
            {logo_html}
          </div>
            <div class="draft-title">
            <h1>行銷活動管理</h1>
            <div>管理 EDM / SMS / APP 行銷活動，建立新活動或取消尚未執行的活動</div>
          </div>
          {greeting_html or "<div></div>"}
        </div>
        """,
        unsafe_allow_html=True,
    )


def reset_form() -> None:
    channel = st.session_state.selected_channel
    segments = current_segments()
    st.session_state.campaign_name = ""
    st.session_state.campaign_description = ""
    st.session_state.campaign_delivery_mode = CAMPAIGN_DELIVERY_MODES[0]
    st.session_state.sender = default_sender(channel)
    st.session_state.segment = segments[0] if segments else ""
    st.session_state.sub_list = SUB_LISTS[0]
    st.session_state.use_seed_list = False
    st.session_state.seed_list = ""
    st.session_state.seed_action_dataset_id = ""
    st.session_state.seed_action_dataset_name = ""
    st.session_state.selected_sync_destination_id = SEGMA_MSSQL_DESTINATION_ID
    st.session_state.selected_sync_destination_name = ""
    st.session_state.channels = [channel]
    st.session_state.edm_template_id = ""
    st.session_state.edm_template_name = ""
    st.session_state.edm_template_version_id = ""
    st.session_state.edm_template_preview_html = ""
    st.session_state.edm_dynamic_template_data = "{}"
    st.session_state.edm_template_data_pair_count = 1
    st.session_state.edm_filter_null_email = True
    st.session_state.edm_deduplicate_email = True
    st.session_state.sms_filter_null_phone_number = True
    st.session_state.sms_deduplicate_phone_number = True
    for key in list(st.session_state.keys()):
        if (
            key.startswith("edm_template_data_key_")
            or key.startswith("edm_template_data_trait_")
            or key.startswith("sms_seed_fallback_")
        ):
            del st.session_state[key]
    st.session_state.email_subject = ""
    st.session_state.sms_copy = ""
    st.session_state.app_title = ""
    st.session_state.app_body = ""
    st.session_state.app_data_json = "{}"
    st.session_state.app_id = ""
    st.session_state.app_name = ""
    schedule_defaults = default_schedule_values()
    st.session_state.schedule_mode = "一次性排程"
    st.session_state.schedule_weekday = "星期五"
    st.session_state.schedule_month_day = "18"
    st.session_state.send_date = schedule_defaults["send_date"]
    st.session_state.schedule_end_date = schedule_defaults["schedule_end_date"]
    st.session_state.send_time = schedule_defaults["send_time"]
    st.session_state.disclaimer_ok = False
    st.session_state.validation_failed = False
    st.session_state.cancelled = False
    st.session_state.test_recipient = ""
    st.session_state.test_personalization_json = "{}"
    st.session_state.create_test_status = "尚未發送"
    st.session_state.show_submit_disclaimer = False
    st.session_state.confirm_exclusion_processed = False
    st.session_state.segma_sync_response = None
    st.session_state.segma_sync_error = ""
    st.session_state.campaign_action_warning = ""
    st.session_state.mssql_error = ""
    set_state("configuring")


def populate_form_from_campaign(campaign: dict) -> None:
    reset_form()
    config = campaign.get("config") or {}
    payload = config.get("channel_payload") or {}
    channel = campaign.get("channel") or st.session_state.selected_channel
    st.session_state.channels = [channel]
    st.session_state.campaign_name = f"{campaign.get('name', '').strip()} 複本".strip()
    st.session_state.campaign_description = campaign.get("description") if campaign.get("description") != "-" else ""
    delivery_mode = config.get("campaign_delivery_mode") or CAMPAIGN_DELIVERY_MODES[0]
    st.session_state.campaign_delivery_mode = (
        delivery_mode if delivery_mode in CAMPAIGN_DELIVERY_MODES else CAMPAIGN_DELIVERY_MODES[0]
    )
    st.session_state.sender = "" if channel == "APP" else campaign.get("sender") or payload.get("sender") or default_sender(channel)
    segments = current_segments()
    if campaign.get("segment") in segments:
        st.session_state.segment = campaign["segment"]
    st.session_state.use_seed_list = bool(config.get("use_seed_list"))
    st.session_state.seed_list = config.get("seed_list", "")
    st.session_state.seed_action_dataset_id = str(config.get("seed_action_dataset_id") or "")
    st.session_state.seed_action_dataset_name = config.get("seed_action_dataset_name", "")
    st.session_state.test_recipient = config.get("test_recipient", "")
    schedule_ui = schedule_ui_config_from_campaign_config(config)
    st.session_state.schedule_mode = schedule_ui["mode"]
    st.session_state.schedule_weekday = schedule_ui["weekday"]
    st.session_state.schedule_month_day = str(schedule_ui["month_day"])
    st.session_state.send_date = schedule_ui["send_date"]
    st.session_state.send_time = schedule_ui["send_time"]
    st.session_state.schedule_end_date = schedule_ui["end_date"]

    if channel == "EDM":
        st.session_state.edm_filter_null_email = config.get("edm_filter_null_email", True)
        st.session_state.edm_deduplicate_email = config.get("edm_deduplicate_email", True)
        st.session_state.edm_template_id = edm_template_id_from_payload(payload)
        st.session_state.edm_template_name = payload.get("sendgrid_template_name", "")
        st.session_state.edm_template_version_id = payload.get("sendgrid_template_version_id", "")
        st.session_state.edm_dynamic_template_data = payload.get("dynamic_template_data_json") or "{}"
        try:
            dynamic_data = json.loads(st.session_state.edm_dynamic_template_data)
        except json.JSONDecodeError:
            dynamic_data = {}
        if isinstance(dynamic_data, dict) and dynamic_data:
            st.session_state.edm_template_data_pair_count = len(dynamic_data)
            for index, (key, value) in enumerate(dynamic_data.items()):
                st.session_state[f"edm_template_data_key_{index}"] = key
                value_text = str(value)
                if value_text.startswith("[") and value_text.endswith("]"):
                    st.session_state[f"edm_template_data_trait_{index}"] = value_text[1:-1]
    elif channel == "SMS":
        st.session_state.sms_filter_null_phone_number = config.get("sms_filter_null_phone_number", True)
        st.session_state.sms_deduplicate_phone_number = config.get("sms_deduplicate_phone_number", True)
        st.session_state.sms_copy = payload.get("content") or config.get("content") or campaign.get("content", "")
        fallback_values = config.get("sms_seed_fallback_values", {})
        if isinstance(fallback_values, dict):
            for trait_name, fallback_value in fallback_values.items():
                st.session_state[sms_seed_fallback_key(str(trait_name))] = str(fallback_value)
    elif channel == "APP":
        st.session_state.app_id = payload.get("app_id") or campaign.get("app_id") or ""
        st.session_state.app_name = payload.get("app_name") or campaign.get("app_name") or ""
        st.session_state.app_title = payload.get("title", "")
        st.session_state.app_body = payload.get("body", "")
        st.session_state.app_data_json = payload.get("data_json") or "{}"

    st.session_state.selected_campaign_id = ""
    st.session_state.pending_cancel_campaign_id = ""
    st.session_state.campaign_view = "create"
    st.session_state.segma_sync_response = None
    st.session_state.segma_sync_error = ""
    st.session_state.mssql_error = ""


def render_left_campaign_settings() -> None:
    channel = st.session_state.selected_channel
    st.markdown('<div class="form-section">', unsafe_allow_html=True)
    st.markdown('<div class="form-section-title">活動基本資訊</div>', unsafe_allow_html=True)
    st.text_input("活動名稱 *", key="campaign_name", placeholder="請輸入活動名稱")
    st.text_area(
        "活動描述",
        key="campaign_description",
        placeholder="請輸入活動描述（選填）",
        max_chars=200,
        height=92,
    )
    description_len = len(st.session_state.campaign_description or "")
    st.caption(f"{description_len} / 200")
    if channel in ("EDM", "SMS"):
        st.radio("活動寫入模式", CAMPAIGN_DELIVERY_MODES, key="campaign_delivery_mode", horizontal=True)
    else:
        st.session_state.campaign_delivery_mode = CAMPAIGN_DELIVERY_MODES[0]
    if channel != "APP":
        st.text_input("Sender *", key="sender", placeholder="請輸入寄件者 email 或發送來源")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="form-section compact">', unsafe_allow_html=True)
    st.markdown('<div class="form-section-title">目標受眾</div>', unsafe_allow_html=True)
    if st.button("重新整理 SEGMA Segments", help="重新呼叫 SEGMA /api/v1/segments"):
        load_segma_segments_from_api.clear()
        load_segma_traits_for_dim.clear()
        st.rerun()
    segments = current_segments()
    if segments:
        st.selectbox("選擇客群 *", segments, key="segment", placeholder="請選擇客群")
    else:
        st.error("SEGMA 未回傳可用 segment。請確認 SEGMA_API / SEGMA_TOKEN，或設定 SEGMA_SEGMENTS_JSON。")
    st.checkbox("使用 seed list", key="use_seed_list")
    if st.session_state.use_seed_list:
        if st.button("重新整理 SEGMA Seed Lists", help="重新呼叫 SEGMA /api/v1/action_datasets"):
            load_segma_seed_action_datasets.clear()
            load_segma_action_dataset_columns.clear()
            st.rerun()
        try:
            seed_sources = load_segma_seed_action_datasets()
        except Exception as exc:
            seed_sources = {}
            st.error(f"SEGMA seed ActionDataset 載入失敗：{exc}")
        seed_lists = list(seed_sources.keys())
        if seed_lists:
            selected_index = seed_lists.index(st.session_state.seed_list) if st.session_state.seed_list in seed_lists else 0
            selected_seed = st.selectbox("選擇種子名單 *", seed_lists, index=selected_index, placeholder="請選擇種子名單")
            seed_source = seed_sources[selected_seed]
            st.session_state.seed_list = selected_seed
            st.session_state.seed_action_dataset_id = str(seed_source["source_id"])
            st.session_state.seed_action_dataset_name = seed_source["source_name"]
        else:
            st.error("SEGMA 未回傳可用的 ActionDataset。")
            st.session_state.seed_list = ""
            st.session_state.seed_action_dataset_id = ""
            st.session_state.seed_action_dataset_name = ""
    else:
        st.session_state.seed_list = ""
        st.session_state.seed_action_dataset_id = ""
        st.session_state.seed_action_dataset_name = ""
    st.markdown('<div class="draft-link">前往 SEGMA Data Studio 建立客群 ↗</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="form-section compact">', unsafe_allow_html=True)
    st.markdown('<div class="form-section-title">同步目的地</div>', unsafe_allow_html=True)
    if st.button("重新整理 SEGMA Destinations", help="重新呼叫 SEGMA /api/v1/destinations?q[action_type_eq]=mssql_table"):
        load_segma_sync_destinations_from_api.clear()
        st.rerun()
    try:
        sync_destinations = load_segma_sync_destinations()
    except Exception as exc:
        sync_destinations = {}
        st.error(f"SEGMA sync destination 載入失敗：{exc}")
    destination_labels = list(sync_destinations.keys())
    if destination_labels:
        selected_index = 0
        for index, label in enumerate(destination_labels):
            destination_id = str(sync_destinations[label]["destination_id"])
            if destination_id == str(st.session_state.selected_sync_destination_id):
                selected_index = index
                break
        selected_destination = st.selectbox(
            "選擇 SQL Server table sync 目的地 *",
            destination_labels,
            index=selected_index,
            placeholder="請選擇同步目的地",
        )
        destination = sync_destinations[selected_destination]
        st.session_state.selected_sync_destination_id = str(destination["destination_id"])
        st.session_state.selected_sync_destination_name = destination["destination_name"]
    else:
        st.error("SEGMA 未回傳 SQL Server table sync 目的地。請確認 SEGMA_API / SEGMA_TOKEN，或設定 SEGMA_SYNC_DESTINATIONS_JSON。")
        st.session_state.selected_sync_destination_id = ""
        st.session_state.selected_sync_destination_name = ""
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="form-section no-line">', unsafe_allow_html=True)
    st.markdown(f'<div class="form-section-title">{channel} 內容</div>', unsafe_allow_html=True)
    if channel == "EDM":
        if st.button("重新整理 SendGrid Templates", help="重新呼叫 SendGrid Dynamic Templates API"):
            load_sendgrid_dynamic_templates.clear()
            load_sendgrid_template.clear()
            st.rerun()
        try:
            templates = load_sendgrid_dynamic_templates()
        except Exception as exc:
            templates = []
            st.error(f"SendGrid template 載入失敗：{exc}")

        if templates:
            labels = [f"{item['name']} ({item['id']})" for item in templates]
            ids = [item["id"] for item in templates]
            selected_index = ids.index(st.session_state.edm_template_id) if st.session_state.edm_template_id in ids else 0
            selected_label = st.selectbox("選擇 SendGrid Dynamic Template *", labels, index=selected_index)
            selected_template = templates[labels.index(selected_label)]
            st.session_state.edm_template_id = selected_template["id"]
            st.session_state.edm_template_name = selected_template["name"]

            try:
                template_detail = load_sendgrid_template(st.session_state.edm_template_id)
                version = active_sendgrid_version(template_detail)
                st.session_state.edm_template_version_id = version.get("id", "")
                st.session_state.email_subject = version.get("subject") or st.session_state.email_subject
                st.session_state.edm_template_preview_html = version.get("html_content") or ""
            except Exception as exc:
                st.warning(f"SendGrid template detail 載入失敗：{exc}")
        else:
            st.info("目前沒有可選擇的 SendGrid Dynamic Template。")

        st.checkbox("排除空白 email", key="edm_filter_null_email")
        st.checkbox("依 email 去重", key="edm_deduplicate_email")
        st.markdown("Dynamic Template Data")
        traits = sorted(selected_segment_traits().keys())
        if not traits:
            st.warning("目前選擇的 segment 沒有可用 traits；請重新整理 SEGMA Segments 或確認 SEGMA /api/v1/traits。")
        row_count = int(st.session_state.get("edm_template_data_pair_count", 1) or 1)
        for index in range(row_count):
            key_col, trait_col = st.columns([0.42, 0.58], gap="medium")
            key_col.text_input("Key" if index == 0 else "Key ", key=f"edm_template_data_key_{index}", placeholder="name")
            current_trait = st.session_state.get(f"edm_template_data_trait_{index}", "")
            trait_options = [""] + traits
            selected_index = trait_options.index(current_trait) if current_trait in trait_options else 0
            trait_col.selectbox(
                "Trait" if index == 0 else "Trait ",
                trait_options,
                index=selected_index,
                key=f"edm_template_data_trait_{index}",
                format_func=lambda value: "請選擇 trait" if not value else value,
            )
        add_col, remove_col = st.columns([0.5, 0.5], gap="medium")
        if add_col.button("新增 Dynamic Template Data 欄位", use_container_width=True):
            refresh_dynamic_template_data_json()
            st.session_state.edm_template_data_pair_count = row_count + 1
            st.rerun()
        if remove_col.button("移除最後一列", use_container_width=True, disabled=row_count <= 1):
            refresh_dynamic_template_data_json()
            st.session_state.edm_template_data_pair_count = row_count - 1
            st.rerun()
        refresh_dynamic_template_data_json()
        st.caption("產生的 dynamic_template_data_json formula")
        st.code(dynamic_template_data_formula(), language="text")
        st.markdown(
            f"""
            <div class="metadata-grid">
              <span>內容來源</span><strong>SendGrid Dynamic Templates</strong>
              <span>Template ID</span><strong>{st.session_state.edm_template_id or '-'}</strong>
              <span>Template Name</span><strong>{st.session_state.edm_template_name or '-'}</strong>
              <span>Active Version</span><strong>{st.session_state.edm_template_version_id or '-'}</strong>
              <span>種子名單</span><strong>{st.session_state.seed_list if st.session_state.use_seed_list else '未使用'}</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif channel == "SMS":
        st.text_area(
            "SMS 簡訊內容 *",
            key="sms_copy",
            height=120,
            placeholder="例如：hello [first_name] [last_name], your code is [promo_code]",
        )
        st.caption(f"{len(st.session_state.sms_copy or '')} / 160")
        traits = sorted(selected_segment_traits().keys())
        st.markdown("SMS 個人化欄位")
        if not traits:
            st.warning("目前選擇的 segment 沒有可用 traits；請重新整理 SEGMA Segments 或確認 SEGMA /api/v1/traits。")
        else:
            insert_col, button_col = st.columns([0.72, 0.28], gap="small")
            selected_trait = insert_col.selectbox("選擇 trait", traits, key="sms_trait_to_insert")
            button_col.write("")
            button_col.button(
                "插入",
                key="sms_insert_trait",
                use_container_width=True,
                on_click=append_sms_placeholder,
                args=(selected_trait,),
            )
        placeholders = sms_message_placeholders()
        if placeholders:
            st.caption("使用中的個人化欄位：" + "、".join(f"[{trait}]" for trait in placeholders))
            if st.session_state.use_seed_list:
                st.markdown("Seed list fallback values")
                for trait in placeholders:
                    st.text_input(
                        f"[{trait}] fallback *",
                        key=sms_seed_fallback_key(trait),
                        placeholder=f"請輸入 seed list 發送時替代 [{trait}] 的值",
                    )
        st.caption("產生的 message_body formula")
        st.code(sms_message_body_formula(), language="text")
        if placeholders and st.session_state.use_seed_list:
            st.caption("Seed list message_body formula")
            st.code(sms_message_body_formula(fallback_values=sms_seed_fallback_values(placeholders)), language="text")
        st.checkbox("排除空白手機號碼", key="sms_filter_null_phone_number")
        st.checkbox("依手機號碼去重", key="sms_deduplicate_phone_number")
        st.markdown(
            """
            <div class="metadata-grid">
              <span>內容類型</span><strong>SMS</strong>
              <span>來源系統</span><strong>企業簡訊內容管理系統</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        try:
            apps = load_applications_from_mssql()
        except Exception as exc:
            apps = []
            st.error(f"APP 清單載入失敗：{exc}")
        if apps:
            labels = [f"{app['app_name']} ({app['app_id']})" for app in apps]
            ids = [app["app_id"] for app in apps]
            selected_index = ids.index(st.session_state.app_id) if st.session_state.app_id in ids else 0
            selected_label = st.selectbox("選擇 APP *", labels, index=selected_index)
            selected_app = apps[labels.index(selected_label)]
            st.session_state.app_id = selected_app["app_id"]
            st.session_state.app_name = selected_app["app_name"]
        else:
            st.info("目前 MSSQL 中沒有啟用中的 APP。請先在 marketing.app_notification_application 建立設定。")
        st.text_input("APP 推播標題 *", key="app_title", placeholder="請輸入推播標題")
        st.text_area("APP 推播內容 *", key="app_body", height=96, placeholder="請輸入推播內容")
        st.text_area("附加資料 JSON", key="app_data_json", height=96, placeholder='{"screen":"campaign"}')
        st.markdown(
            f"""
            <div class="metadata-grid">
              <span>APP</span><strong>{st.session_state.app_name or '-'}</strong>
              <span>App ID</span><strong>{st.session_state.app_id or '-'}</strong>
              <span>來源系統</span><strong>Azure Notification Hubs</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def render_right_campaign_settings() -> None:
    channel = st.session_state.selected_channel
    st.markdown('<div class="form-section">', unsafe_allow_html=True)
    st.markdown(f'<div class="form-section-title">{channel} 內容預覽</div>', unsafe_allow_html=True)

    if channel == "SMS":
        sms_copy = st.session_state.sms_copy or "尚未輸入 SMS 簡訊內容"
        st.markdown(
            f"""
            <div class="info-banner">ⓘ SMS 內容將在此預覽</div>
            <div class="preview-box">
              <div style="max-width:360px;text-align:left;">
                <div style="background:#007f68;color:white;border-radius:8px;padding:12px 14px;line-height:1.5;">
                  {sms_copy}
                </div>
                <div class="preview-empty-copy" style="margin-top:10px;">實際發送前會套用個人化欄位與退訂文字</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif channel == "APP":
        title = st.session_state.app_title or "尚未輸入推播標題"
        body = st.session_state.app_body or "尚未輸入推播內容"
        st.markdown(
            f"""
            <div class="info-banner">ⓘ APP 推播內容將在此預覽</div>
            <div class="preview-box">
              <div style="max-width:390px;text-align:left;border:1px solid #d7dee8;border-radius:12px;background:white;padding:14px 16px;box-shadow:0 8px 20px rgba(16,24,40,0.10);">
                <div style="color:#101828;font-weight:780;font-size:15px;margin-bottom:6px;">{title}</div>
                <div style="color:#475467;line-height:1.45;font-size:14px;">{body}</div>
                <div class="preview-empty-copy" style="margin-top:10px;">實際發送會以使用者或裝置標籤定位</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif not st.session_state.edm_template_id:
        st.markdown(
            """
            <div class="info-banner">ⓘ 請先選擇 SendGrid Dynamic Template 以查看預覽</div>
            <div class="preview-box">
              <div>
                <div class="preview-envelope"></div>
                <div class="preview-empty-title">尚未選擇 SendGrid Template</div>
                <div class="preview-empty-copy">選擇左側 template 後將載入 active version 預覽</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif not st.session_state.edm_template_preview_html:
        st.warning("已選擇 template，但 SendGrid active version 沒有可預覽的 HTML。請使用真實測試信確認內容。")
    else:
        try:
            sample_data = parse_dynamic_template_data()
            preview_html = render_template_sample(st.session_state.edm_template_preview_html, sample_data)
        except (json.JSONDecodeError, ValueError) as exc:
            st.error(f"Dynamic Template Data JSON 無效，無法套用預覽資料：{exc}")
            preview_html = st.session_state.edm_template_preview_html
        st.markdown(
            '<div class="info-banner">ⓘ 下方預覽使用 SendGrid active template version HTML 與目前 JSON 樣本資料</div>',
            unsafe_allow_html=True,
        )
        components.html(preview_html, height=420, scrolling=True)

    st.markdown("</div>", unsafe_allow_html=True)

    render_create_test_sender()

    st.markdown('<div class="form-section no-line">', unsafe_allow_html=True)
    st.markdown('<div class="form-section-title">排程設定</div>', unsafe_allow_html=True)
    st.selectbox("SEGMA sync 排程 *", SCHEDULE_MODES, key="schedule_mode")
    if st.session_state.schedule_mode == "每週排程":
        st.selectbox("每週執行日 *", list(WEEKDAY_OPTIONS.keys()), key="schedule_weekday")
    elif st.session_state.schedule_mode == "每月排程":
        st.text_input("每月日期 *", key="schedule_month_day", placeholder="18")
    date_label = "發送日期 *"
    recurring = st.session_state.schedule_mode != "一次性排程"
    if recurring:
        date_label = "起始日期 *"
    if recurring:
        date_col, end_col, time_col = st.columns([0.34, 0.34, 0.32], gap="large")
    else:
        date_col, time_col = st.columns(2, gap="large")
        end_col = None
    date_col.text_input(date_label, key="send_date", placeholder="2026/08/18")
    if end_col:
        end_col.text_input("結束日期 *", key="schedule_end_date", placeholder="2026/08/31")
    time_col.text_input("執行時間 *", key="send_time", placeholder="09:30")
    try:
        st.markdown(
            f'<div class="info-banner" style="margin-top:12px;">ⓘ {schedule_preview_text(parse_schedule_start())}</div>',
            unsafe_allow_html=True,
        )
    except ValueError:
        st.markdown(
            '<div class="info-banner" style="margin-top:12px;">ⓘ 輸入有效日期與時間後會顯示 SEGMA sync 排程。</div>',
            unsafe_allow_html=True,
        )
    if st.session_state.schedule_mode == "一次性排程":
        st.markdown(
            '<div class="info-banner" style="margin-top:12px;">ⓘ 一次性排程會送出 start_date 與 end_date，避免同一日期在下一年再次觸發。</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="info-banner" style="margin-top:12px;">ⓘ 週期性排程會在起始日期與結束日期之間依 cron 重複執行。</div>',
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def render_create_test_sender() -> None:
    channel = st.session_state.selected_channel
    st.markdown('<div class="form-section">', unsafe_allow_html=True)
    st.markdown('<div class="form-section-title">測試發送</div>', unsafe_allow_html=True)
    if channel == "EDM":
        label = "測試收件人 Email *"
        placeholder = "reviewer@example.com"
    elif channel == "SMS":
        label = "測試收件人手機或 Email *"
        placeholder = "+886 912 345 678 或 reviewer@example.com"
    else:
        label = "測試使用者 ID、裝置標籤或 Email *"
        placeholder = "user_123 或 reviewer@example.com"
    st.text_input(label, key="test_recipient", placeholder=placeholder)
    if channel != "SMS":
        st.text_area(
            "測試 personalization_json",
            key="test_personalization_json",
            height=120,
            placeholder='{"dynamic_template_data":{"customer_name":"王小明"},"custom_args":{"test_case":"vip"}}',
        )
    disabled_reasons = []
    if not st.session_state.test_recipient.strip():
        disabled_reasons.append("請輸入測試收件人；placeholder 不會被當作實際收件人。")
    if channel != "SMS":
        try:
            parse_test_personalization_json()
        except (json.JSONDecodeError, ValueError) as exc:
            disabled_reasons.append(f"測試 personalization_json 不是有效 JSON object：{exc}")
    send_disabled = bool(disabled_reasons)
    if disabled_reasons:
        st.warning(" ".join(disabled_reasons))
    if st.button(f"發送測試 {channel}", use_container_width=True, disabled=send_disabled):
        try:
            result = send_real_test_message(channel, st.session_state.test_recipient.strip())
            st.session_state.create_test_status = result
        except Exception as exc:
            st.session_state.create_test_status = f"測試發送失敗：{exc}"
    if st.session_state.create_test_status == "尚未發送":
        st.info(f"輸入測試收件人後，可先發送測試 {channel} 供審核。")
    elif st.session_state.create_test_status.startswith("測試發送失敗"):
        st.error(st.session_state.create_test_status)
    else:
        st.success(st.session_state.create_test_status)
    st.markdown("</div>", unsafe_allow_html=True)


def render_draft_actions() -> None:
    st.markdown(
        """
        <div class="state-ribbon">
          <div><strong>目前狀態</strong>：{status}</div>
          <div><span class="status-chip">{run_id}</span></div>
        </div>
        """.format(
            status="已取消"
            if st.session_state.cancelled
            else current_state()["label"],
            run_id="SEGMA sync 尚未建立"
            if not st.session_state.segma_sync_response
            else f"SEGMA sync: {st.session_state.segma_sync_response.get('name', '-')}",
        ),
        unsafe_allow_html=True,
    )
    if st.session_state.show_submit_disclaimer:
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown('<div class="form-section-title">送出前確認</div>', unsafe_allow_html=True)
        st.warning("請確認下列事項後，才能正式建立活動。")
        st.checkbox("使用名單已進行強制性排除名單處理", key="confirm_exclusion_processed")
        st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state.segma_sync_error:
        st.error("SEGMA sync 建立失敗")
        st.code(st.session_state.segma_sync_error, language="text")
    if st.session_state.segma_sync_response:
        sync = st.session_state.segma_sync_response
        st.success(f"SEGMA sync 已建立：{sync.get('name', '-')}")

    st.markdown('<div class="bottom-actions">', unsafe_allow_html=True)
    spacer, reset_col, submit_col = st.columns([0.68, 0.14, 0.18], gap="medium")
    with reset_col:
        if st.button("重設", use_container_width=True):
            reset_form()
            st.rerun()
    with submit_col:
        if not st.session_state.show_submit_disclaimer:
            if st.button(f"建立{st.session_state.selected_channel}活動", type="primary", use_container_width=True):
                st.session_state.show_submit_disclaimer = True
                st.rerun()
        else:
            ready_to_submit = st.session_state.confirm_exclusion_processed
            if st.button("確認並送出", type="primary", use_container_width=True, disabled=not ready_to_submit):
                st.session_state.segma_sync_error = ""
                st.session_state.segma_sync_response = None
                campaign_id = None
                sync = None
                seed_sync = None
                seed_source = None
                errors = []
                if st.session_state.use_seed_list:
                    try:
                        seed_source = selected_seed_action_dataset_source()
                    except Exception as exc:
                        errors.append(f"SEGMA seed ActionDataset 欄位載入失敗：{exc}")
                if not errors:
                    errors = validate_campaign_form(seed_source=seed_source)
                if errors:
                    st.session_state.segma_sync_error = " ".join(errors)
                    st.rerun()
                try:
                    campaign_id = insert_campaign_to_mssql()
                    payload = build_segma_sync_payload(campaign_id)
                    with st.spinner("正在建立 SEGMA sync..."):
                        sync = create_segma_sync(payload)
                    seed_sync = None
                    if st.session_state.use_seed_list and seed_source:
                        seed_payload = build_segma_sync_payload(
                            campaign_id,
                            source_override=seed_source,
                        )
                        with st.spinner("正在建立 SEGMA seed sync..."):
                            seed_sync = create_segma_sync(seed_payload)
                    update_campaign_after_segma(campaign_id, sync, seed_sync)
                except Exception as exc:
                    if campaign_id:
                        try:
                            mark_campaign_sync_failed(campaign_id, sync, seed_sync)
                        except Exception as mark_exc:
                            st.session_state.segma_sync_error = f"{exc}；標記同步失敗狀態也失敗：{mark_exc}"
                            st.rerun()
                    st.session_state.segma_sync_error = str(exc)
                    st.rerun()

                if seed_sync:
                    st.session_state.segma_sync_response = {
                        "name": f"{sync.get('name', '-')}, {seed_sync.get('name', '-')}",
                        "main_sync": sync,
                        "seed_sync": seed_sync,
                    }
                else:
                    st.session_state.segma_sync_response = sync
                st.session_state.selected_campaign_id = campaign_id
                st.session_state.disclaimer_ok = True
                set_state("activated")
                st.session_state.campaign_view = "list"
                st.session_state.show_submit_disclaimer = False
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def render_campaign_side_panel() -> None:
    with st.container(border=True):
        st.markdown('<div class="side-title">活動類型</div>', unsafe_allow_html=True)
        locked_channel = st.session_state.get("locked_channel")
        if locked_channel:
            st.session_state.selected_channel = locked_channel
            st.markdown(
                f'<div class="side-copy">目前頁面固定為 {locked_channel} 活動設定。</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="side-copy">選擇後，右側會顯示對應的活動清單。</div>',
                unsafe_allow_html=True,
            )
            st.radio("發送通路", CHANNELS, key="selected_channel")
        sync_workspace_state()

        st.markdown(
            f"""
            <div class="campaign-row selected">
              <div class="campaign-name">{st.session_state.selected_channel} 活動</div>
              <div class="campaign-meta">目前共有 {len(channel_campaigns(st.session_state.selected_channel))} 筆活動</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_campaign_list() -> None:
    channel = st.session_state.selected_channel
    campaigns = channel_campaigns(channel)
    top_left, top_right = st.columns([0.72, 0.28], gap="medium")
    with top_left:
        st.markdown(
            f'<div class="form-section-title">{channel} 活動清單</div>',
            unsafe_allow_html=True,
        )
        st.caption("可檢視活動細節，或取消尚未執行的活動。")
    with top_right:
        if st.button(f"新增 {channel} 活動", type="primary", use_container_width=True):
            reset_form()
            st.session_state.campaign_view = "create"
            st.rerun()

    if st.session_state.segma_sync_error:
        st.error("SEGMA sync 建立失敗")
        st.code(st.session_state.segma_sync_error, language="text")
    if st.session_state.segma_sync_response:
        sync = st.session_state.segma_sync_response
        st.success(f"SEGMA sync 已建立：{sync.get('name', '-')}")
    if st.session_state.campaign_action_warning:
        st.warning(st.session_state.campaign_action_warning)
    if st.session_state.mssql_error:
        st.error(f"MSSQL 連線或查詢失敗：{st.session_state.mssql_error}")
        return
    if not campaigns:
        st.info("目前 MSSQL 中沒有此通路的活動。")
        return

    if channel == "APP":
        ratios = [0.18, 0.11, 0.13, 0.14, 0.15, 0.14, 0.15]
        headers = ["名稱", "狀態", "APP", "客群", "排程時間", "內容摘要", "操作"]
    else:
        ratios = [0.22, 0.11, 0.16, 0.16, 0.19, 0.16]
        headers = ["名稱", "狀態", "客群", "排程時間", "內容摘要", "操作"]

    st.markdown('<div class="campaign-table-head">', unsafe_allow_html=True)
    header_cols = st.columns(ratios, gap="small")
    for col, header in zip(header_cols, headers):
        col.markdown(header)
    st.markdown("</div>", unsafe_allow_html=True)

    for index, campaign in enumerate(campaigns):
        status = campaign_status(campaign)
        selected = campaign["id"] == st.session_state.selected_campaign_id
        row_class = "campaign-table-row"
        if selected:
            row_class += " selected"
        st.markdown(f'<div class="{row_class}">', unsafe_allow_html=True)
        row_cols = st.columns(ratios, gap="small")
        row_cols[0].markdown(
            f"""
            <div class="campaign-table-cell">
              <strong>{campaign['name']}</strong>
              <div class="campaign-table-meta">{campaign['id']} · {channel}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        row_cols[1].markdown(
            f'<span class="status-chip {status_tone(status)}">{status_label(status)}</span>',
            unsafe_allow_html=True,
        )
        next_col = 2
        if channel == "APP":
            row_cols[next_col].markdown(
                f'<div class="campaign-table-cell">{campaign.get("app_name") or campaign.get("app_id") or "-"}</div>',
                unsafe_allow_html=True,
            )
            next_col += 1
        row_cols[next_col].markdown(
            f'<div class="campaign-table-cell">{campaign["segment"]}</div>',
            unsafe_allow_html=True,
        )
        row_cols[next_col + 1].markdown(
            f'<div class="campaign-table-cell">{campaign["schedule"]}</div>',
            unsafe_allow_html=True,
        )
        row_cols[next_col + 2].markdown(
            f'<div class="campaign-table-cell wrap">{campaign["content"]}</div>',
            unsafe_allow_html=True,
        )
        with row_cols[next_col + 3]:
            pending_cancel = st.session_state.pending_cancel_campaign_id == campaign["id"]
            view_col, cancel_col = st.columns([0.52, 0.48], gap="small")
            if view_col.button("檢視", key=f"view_{campaign['id']}", use_container_width=True):
                st.session_state.selected_campaign_id = campaign["id"]
                st.session_state.campaign_view = "detail"
                st.rerun()
            if not pending_cancel:
                if cancel_col.button(
                    "取消",
                    key=f"cancel_{campaign['id']}",
                    use_container_width=True,
                    disabled=status not in CANCELLABLE_STATUSES,
                ):
                    st.session_state.pending_cancel_campaign_id = campaign["id"]
                    st.rerun()
            else:
                confirm_col, keep_col = st.columns([0.58, 0.42], gap="small")
                if confirm_col.button("確認取消", key=f"confirm_cancel_{campaign['id']}", use_container_width=True):
                    st.session_state.selected_campaign_id = campaign["id"]
                    try:
                        st.session_state.mssql_error = ""
                        st.session_state.campaign_action_warning = ""
                        cancel_campaign(campaign)
                        st.session_state.cancelled = True
                        st.session_state.pending_cancel_campaign_id = ""
                        st.session_state.campaign_view = "list"
                        st.rerun()
                    except Exception as exc:
                        st.session_state.mssql_error = str(exc)
                        st.rerun()
                if keep_col.button("保留", key=f"keep_cancel_{campaign['id']}", use_container_width=True):
                    st.session_state.pending_cancel_campaign_id = ""
                    st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


def preview_html_text(value: str) -> str:
    return html.escape(value).replace("\n", "<br>")


def preview_sms_markup(message: str, note: str) -> str:
    return f"""
        <div class="info-banner">ⓘ SMS 內容預覽</div>
        <div class="preview-box">
          <div style="max-width:360px;text-align:left;">
            <div style="background:#007f68;color:white;border-radius:8px;padding:12px 14px;line-height:1.5;">
              {preview_html_text(message)}
            </div>
            <div class="preview-empty-copy" style="margin-top:10px;">{html.escape(note)}</div>
          </div>
        </div>
    """


def preview_app_markup(title: str, body: str, note: str) -> str:
    return f"""
        <div class="info-banner">ⓘ APP 推播內容預覽</div>
        <div class="preview-box">
          <div style="max-width:390px;text-align:left;border:1px solid #d7dee8;border-radius:12px;background:white;padding:14px 16px;box-shadow:0 8px 20px rgba(16,24,40,0.10);">
            <div style="color:#101828;font-weight:780;font-size:15px;margin-bottom:6px;">{html.escape(title)}</div>
            <div style="color:#475467;line-height:1.45;font-size:14px;">{preview_html_text(body)}</div>
            <div class="preview-empty-copy" style="margin-top:10px;">{html.escape(note)}</div>
          </div>
        </div>
    """


def dynamic_template_data_preview_values(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    preview = {}
    for key, value in data.items():
        value_text = str(value)
        if value_text.startswith("[") and value_text.endswith("]"):
            preview[key] = value_text
        else:
            preview[key] = value
    return preview


def render_readonly_campaign_preview(campaign: dict, payload: dict, config: dict, prefix: str) -> None:
    channel = st.session_state.selected_channel
    if channel == "SMS":
        sms_body = payload.get("content") or config.get("content") or campaign.get("content") or "尚未輸入 SMS 簡訊內容"
        st.markdown(preview_sms_markup(str(sms_body), "此預覽使用已儲存的 SMS 內容；實際發送會由 SEGMA 套用個人化欄位。"), unsafe_allow_html=True)
        return

    if channel == "APP":
        title = payload.get("title") or "尚未輸入推播標題"
        body = payload.get("body") or "尚未輸入推播內容"
        st.markdown(preview_app_markup(str(title), str(body), "此預覽使用已儲存的 APP 推播內容。"), unsafe_allow_html=True)
        return

    template_id = edm_template_id_from_payload(payload)
    if not template_id:
        readonly_area("預覽", campaign.get("content"), f"{prefix}_content_preview", height=180)
        return

    try:
        template_detail = load_sendgrid_template(template_id)
        version = active_sendgrid_version(template_detail)
        template_html = version.get("html_content") or ""
    except Exception as exc:
        st.warning(f"SendGrid template 預覽載入失敗：{exc}")
        readonly_area("預覽", campaign.get("content"), f"{prefix}_content_preview", height=180)
        return

    if not template_html:
        st.warning("已儲存 SendGrid template，但 active version 沒有可預覽的 HTML。")
        readonly_area("預覽", campaign.get("content"), f"{prefix}_content_preview", height=180)
        return

    preview_values = dynamic_template_data_preview_values(payload.get("dynamic_template_data_json"))
    preview_html = render_template_sample(template_html, preview_values)
    st.markdown(
        '<div class="info-banner">ⓘ 下方預覽使用已儲存的 SendGrid Template ID 與目前 active version HTML。</div>',
        unsafe_allow_html=True,
    )
    components.html(preview_html, height=420, scrolling=True)


def readonly_text(label: str, value: object, key: str) -> None:
    st.text_input(label, value=str(value or "-"), disabled=True, key=key)


def readonly_area(label: str, value: object, key: str, height: int = 92) -> None:
    st.text_area(label, value=str(value or "-"), disabled=True, key=key, height=height)


def readonly_checkbox(label: str, value: object, key: str) -> None:
    st.checkbox(label, value=bool(value), disabled=True, key=key)


def campaign_payload(campaign: dict) -> dict:
    return campaign.get("config", {}).get("channel_payload", {})


def render_readonly_campaign_configuration(campaign: dict, status: str) -> None:
    channel = st.session_state.selected_channel
    config = campaign.get("config", {})
    payload = campaign_payload(campaign)
    prefix = f"campaign_detail_{campaign['id']}"

    left_col, right_col = st.columns([0.58, 0.42], gap="large")
    with left_col:
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown('<div class="form-section-title">活動基本資訊</div>', unsafe_allow_html=True)
        readonly_text("活動名稱", campaign.get("name"), f"{prefix}_name")
        readonly_area("活動描述", campaign.get("description"), f"{prefix}_description")
        if channel in ("EDM", "SMS"):
            readonly_text("活動寫入模式", config.get("campaign_delivery_mode", CAMPAIGN_DELIVERY_MODES[0]), f"{prefix}_delivery_mode")
        if channel != "APP":
            readonly_text("Sender", campaign.get("sender"), f"{prefix}_sender")
        readonly_text("狀態", status_label(status), f"{prefix}_status")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="form-section compact">', unsafe_allow_html=True)
        st.markdown('<div class="form-section-title">目標受眾</div>', unsafe_allow_html=True)
        readonly_text("選擇客群", campaign.get("segment"), f"{prefix}_segment")
        readonly_checkbox("使用 seed list", config.get("use_seed_list"), f"{prefix}_use_seed")
        if config.get("use_seed_list") or campaign.get("seed_action_dataset_id") or campaign.get("seed_action_dataset_name"):
            readonly_text(
                "選擇種子名單",
                campaign.get("seed_action_dataset_name") or campaign.get("seed_action_dataset_id") or config.get("seed_list"),
                f"{prefix}_seed_list",
            )
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="form-section compact">', unsafe_allow_html=True)
        st.markdown('<div class="form-section-title">同步目的地</div>', unsafe_allow_html=True)
        readonly_text("SEGMA main sync", campaign.get("segma_sync_name") or campaign.get("segma_sync_id"), f"{prefix}_main_sync")
        readonly_text("SEGMA seed sync", campaign.get("seed_segma_sync_name") or campaign.get("seed_segma_sync_id"), f"{prefix}_seed_sync")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="form-section no-line">', unsafe_allow_html=True)
        st.markdown(f'<div class="form-section-title">{channel} 內容</div>', unsafe_allow_html=True)
        if channel == "EDM":
            readonly_text("SendGrid Dynamic Template", payload.get("sendgrid_template_name"), f"{prefix}_edm_template")
            readonly_text("Template ID", edm_template_id_from_payload(payload), f"{prefix}_edm_template_id")
            readonly_text("Active Version", payload.get("sendgrid_template_version_id"), f"{prefix}_edm_version")
            readonly_area("Dynamic Template Data JSON", payload.get("dynamic_template_data_json") or "{}", f"{prefix}_edm_dynamic_data", height=120)
            readonly_checkbox("排除空白 email", config.get("edm_filter_null_email", True), f"{prefix}_edm_filter")
            readonly_checkbox("依 email 去重", config.get("edm_deduplicate_email", True), f"{prefix}_edm_dedup")
        elif channel == "SMS":
            sms_body = payload.get("content") or config.get("content") or campaign.get("content")
            readonly_area("SMS 簡訊內容", sms_body, f"{prefix}_sms_body", height=120)
            placeholders = sms_message_placeholders(str(sms_body or ""))
            readonly_text("使用中的個人化欄位", "、".join(f"[{trait}]" for trait in placeholders) or "-", f"{prefix}_sms_placeholders")
            fallback_values = config.get("sms_seed_fallback_values", {})
            if isinstance(fallback_values, dict) and fallback_values:
                readonly_area(
                    "Seed list fallback values",
                    json.dumps(fallback_values, ensure_ascii=False, indent=2),
                    f"{prefix}_sms_seed_fallback_values",
                    height=120,
                )
            st.caption("產生的 message_body formula")
            st.code(sms_message_body_formula(str(sms_body or "")), language="text")
            if isinstance(fallback_values, dict) and fallback_values:
                st.caption("Seed list message_body formula")
                st.code(sms_message_body_formula(str(sms_body or ""), fallback_values=fallback_values), language="text")
            readonly_checkbox("排除空白手機號碼", config.get("sms_filter_null_phone_number", True), f"{prefix}_sms_filter")
            readonly_checkbox("依手機號碼去重", config.get("sms_deduplicate_phone_number", True), f"{prefix}_sms_dedup")
        else:
            readonly_text("APP", payload.get("app_name") or campaign.get("app_name") or campaign.get("app_id"), f"{prefix}_app")
            readonly_text("APP ID", payload.get("app_id") or campaign.get("app_id"), f"{prefix}_app_id")
            readonly_area("APP 推播標題", payload.get("title"), f"{prefix}_app_title", height=72)
            readonly_area("APP 推播內容", payload.get("body"), f"{prefix}_app_body", height=120)
            readonly_area("APP 附加資料", payload.get("data_json") or "{}", f"{prefix}_app_data", height=120)
        st.markdown("</div>", unsafe_allow_html=True)

    with right_col:
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown('<div class="form-section-title">排程設定</div>', unsafe_allow_html=True)
        schedule_ui = schedule_ui_config_from_campaign_config(config)
        schedule_mode = schedule_ui["mode"]
        schedule_text = (
            f"{schedule_ui['send_date']} {schedule_ui['send_time']}"
            if schedule_ui["send_date"] and schedule_ui["send_time"]
            else "-"
        )
        schedule_time_label = "執行時間" if schedule_mode == "一次性排程" else "開始時間"
        readonly_text(schedule_time_label, schedule_text, f"{prefix}_schedule")
        if schedule_mode != "一次性排程":
            readonly_text("結束時間", schedule_ui["end_date"], f"{prefix}_end_schedule")
        readonly_text("排程模式", schedule_mode, f"{prefix}_schedule_mode")
        if schedule_mode == "每週排程" and schedule_ui["weekday"]:
            readonly_text("每週發送日", schedule_ui["weekday"], f"{prefix}_schedule_weekday")
        if schedule_mode == "每月排程" and schedule_ui["month_day"]:
            readonly_text("每月發送日", schedule_ui["month_day"], f"{prefix}_schedule_month_day")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="form-section compact">', unsafe_allow_html=True)
        st.markdown('<div class="form-section-title">內容預覽</div>', unsafe_allow_html=True)
        render_readonly_campaign_preview(campaign, payload, config, prefix)
        st.markdown("</div>", unsafe_allow_html=True)


def render_campaign_detail() -> None:
    campaign = selected_campaign()
    if not campaign.get("id"):
        st.info("目前沒有可顯示的活動資料。")
        return
    if st.session_state.campaign_action_warning:
        st.warning(st.session_state.campaign_action_warning)
    status = campaign_status(campaign)
    channel = st.session_state.selected_channel
    st.markdown(
        f"""
        <div class="detail-header">
          <div>
            <div class="detail-title">{campaign['name']}</div>
            <div class="detail-subtitle">{campaign['id']} · {channel} 活動</div>
          </div>
          <span class="status-chip {status_tone(status)}">{status_label(status)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_readonly_campaign_configuration(campaign, status)

    action_left, clone_col, trigger_col, cancel_col = st.columns([0.40, 0.20, 0.20, 0.20], gap="medium")
    with action_left:
        if status == "Cancelled":
            st.warning("活動已取消。")
        elif status not in CANCELLABLE_STATUSES:
            st.info("此活動已建立任務或已進入後續流程，不能在此取消。")
        elif status in {"Draft", "Sync Failed"}:
            st.info("此活動尚未完成啟用，可取消以清理未完成的活動。")
        else:
            st.info("此活動尚未執行，可取消排程。")
        st.info(
            "可取消條件：活動狀態必須為草稿、同步失敗、已啟用或等待排程。取消會將活動狀態改為已取消，"
            "並將尚未執行的 NEW 任務改為 CANCELLED，同時嘗試清理 SEGMA main/seed sync。"
            "已進入發送中、已送出、失敗或已被供應商接受的任務會保留作為稽核紀錄。"
        )
    with clone_col:
        if st.button("複製活動", use_container_width=True):
            populate_form_from_campaign(campaign)
            st.rerun()
    with trigger_col:
        trigger_disabled = status == "Cancelled" or not campaign.get("segma_sync_id")
        if st.button("手動觸發 sync", use_container_width=True, disabled=trigger_disabled):
            try:
                trigger_results = trigger_campaign_syncs(campaign)
                st.success(f"已送出 {len(trigger_results)} 個 SEGMA sync trigger。")
            except Exception as exc:
                st.error(f"SEGMA sync trigger 失敗：{exc}")
    with cancel_col:
        pending_cancel = st.session_state.pending_cancel_campaign_id == campaign["id"]
        if not pending_cancel:
            if st.button("取消活動", type="primary", use_container_width=True, disabled=status not in CANCELLABLE_STATUSES):
                st.session_state.pending_cancel_campaign_id = campaign["id"]
                st.rerun()
        else:
            confirm_col, keep_col = st.columns([0.58, 0.42], gap="small")
            if confirm_col.button("確認取消", type="primary", use_container_width=True):
                try:
                    st.session_state.mssql_error = ""
                    st.session_state.campaign_action_warning = ""
                    cancel_campaign(campaign)
                    st.session_state.cancelled = True
                    st.session_state.pending_cancel_campaign_id = ""
                    set_state("activated")
                    st.rerun()
                except Exception as exc:
                    st.session_state.mssql_error = str(exc)
                    st.rerun()
            if keep_col.button("保留", use_container_width=True):
                st.session_state.pending_cancel_campaign_id = ""
                st.rerun()

    st.markdown('<div class="form-section">', unsafe_allow_html=True)
    st.markdown('<div class="form-section-title">刪除活動與任務資料</div>', unsafe_allow_html=True)
    st.warning("刪除會移除此活動與所有相關任務資料，且無法復原。")
    st.info("系統會嘗試清理 SEGMA main/seed sync；若清理失敗，仍會繼續刪除 MSSQL 活動與任務資料。")
    confirm_value = st.text_input(
        "輸入活動名稱以確認刪除",
        key=f"delete_confirm_{campaign['id']}",
        placeholder=f"請輸入：{campaign['name']}",
    )
    delete_disabled = confirm_value != campaign["name"]
    if confirm_value != campaign["name"]:
        st.caption(f"請手動輸入「{campaign['name']}」後才能刪除；placeholder 不會被當作確認文字。")
    if st.button("刪除活動與任務資料", use_container_width=True, disabled=delete_disabled):
        try:
            delete_campaign_segma_syncs(campaign)
            delete_campaign_and_jobs(campaign)
            st.session_state.selected_campaign_id = ""
            st.session_state.campaign_view = "list"
            st.session_state.segma_sync_response = None
            st.session_state.segma_sync_error = ""
            st.session_state.mssql_error = ""
            st.rerun()
        except Exception as exc:
            st.session_state.mssql_error = str(exc)
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def render_create_campaign_form() -> None:
    title_col, back_col = st.columns([0.78, 0.22], gap="medium")
    with title_col:
        st.markdown(
            f'<div class="form-section-title">新增 {st.session_state.selected_channel} 行銷活動</div>',
            unsafe_allow_html=True,
        )
    with back_col:
        if st.button("返回", use_container_width=True):
            st.session_state.campaign_view = "list"
            st.rerun()
    left, right = st.columns([1.02, 0.98], gap="large")
    with left:
        render_left_campaign_settings()
    with right:
        render_right_campaign_settings()
    render_draft_actions()


def render_customer_console() -> None:
    render_draft_header()
    with st.container(border=True):
        side, main = st.columns([0.24, 0.76], gap="large")
        with side:
            render_campaign_side_panel()
        with main:
            if st.session_state.campaign_view == "create":
                render_create_campaign_form()
            elif st.session_state.campaign_view == "detail":
                back_col, _ = st.columns([0.22, 0.78])
                if back_col.button("返回清單", use_container_width=True):
                    st.session_state.campaign_view = "list"
                    st.rerun()
                render_campaign_detail()
            else:
                render_campaign_list()


def render_channel_page(channel: str) -> None:
    st.session_state.locked_channel = channel
    st.session_state.selected_channel = channel
    main()


def main() -> None:
    init_state()
    inject_css()
    render_customer_console()


if __name__ == "__main__":
    st.session_state.locked_channel = None
    main()

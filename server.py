"""
Meta (Facebook) Ads MCP Server - read-only reporting.

One insights tool covers every aggregation level (account / campaign /
adset / ad) through the `level` parameter; there are no separate
per-level performance tools. Entity list tools cover discovery, and the
pixel / custom conversion tools cover tracking diagnostics.

Every tool accepts format='compact' (CSV rows plus terse key=value header
lines, default) or format='json' (minified, null fields stripped).

Transport: stdio.
"""
import csv
import io
import json
from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BeforeValidator

from facebook_client import FacebookAdsClient, FacebookAPIError, _date_to_unix
from data_processor import FacebookDataProcessor

INSTRUCTIONS = """Read-only Meta (Facebook) Marketing API.
ids: account_id comes from meta_ads_list_accounts; the act_ prefix is optional everywhere.
Dates are YYYY-MM-DD; every date range is inclusive on both ends.
format: 'compact' (CSV rows + key=value header lines, default) or 'json' (minified, nulls stripped).
In compact output, lists are joined with ' ¦ '. status columns show Meta's effective_status.
name_contains is a CASE-SENSITIVE server-side substring match (the only name operator Meta
supports); anchor the substring for prefix-style matching. Budgets and spend are in the
account currency; budget columns are converted from cents. Pagination is internal and results
are complete unless a 'truncated:' line says otherwise."""

mcp = FastMCP("meta_ads_mcp", instructions=INSTRUCTIONS, include_fastmcp_meta=False)

READONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

Format = Literal["compact", "json"]
Level = Literal["account", "campaign", "adset", "ad"]
Status = Literal["ACTIVE", "PAUSED", "ARCHIVED", "WITH_ISSUES", "ALL"]
AdsetStatus = Literal["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ARCHIVED", "WITH_ISSUES", "ALL"]
AdStatus = Literal["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "ARCHIVED",
                   "WITH_ISSUES", "ALL"]
Increment = Literal["total", "day", "week", "month"]
Bucket = Literal["hour", "day", "total"]

# Ids are strings in the Graph API, but clients often send them as JSON numbers.
Id = Annotated[str, BeforeValidator(
    lambda v: str(v) if isinstance(v, int) and not isinstance(v, bool) else v)]
# Id-list filters also arrive as a bare scalar now and then.
IdList = Annotated[List[Id], BeforeValidator(
    lambda v: [v] if isinstance(v, (str, int)) and not isinstance(v, bool) else v)]

SEP = " ¦ "
MAX_INSIGHTS_ROWS = 1000
MAX_LIST_ROWS = 500
MAX_CREATIVE_IDS = 50

DEFAULT_METRICS = ["spend", "impressions", "reach", "inline_link_clicks", "actions"]
LEVEL_FIELDS = {
    "account": [],
    "campaign": ["campaign_id", "campaign_name"],
    "adset": ["campaign_id", "adset_id", "adset_name"],
    "ad": ["campaign_id", "adset_id", "ad_id", "ad_name"],
}
INCREMENTS = {"total": None, "day": "1", "week": "7", "month": "monthly"}

ACCOUNT_STATUS = {
    1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED", 7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT", 9: "IN_GRACE_PERIOD", 100: "PENDING_CLOSURE", 101: "CLOSED",
}

_client: Optional[FacebookAdsClient] = None
_processor = FacebookDataProcessor()


def _get_client() -> FacebookAdsClient:
    global _client
    if _client is None:
        _client = FacebookAdsClient()
    return _client


def _error_message(e: Exception) -> str:
    if isinstance(e, FacebookAPIError):
        code = e.code
        if code == 190:
            return (f"Meta access token invalid or expired (code 190): {e.message} "
                    "Regenerate FACEBOOK_ACCESS_TOKEN.")
        if code in (4, 17, 613, 80004):
            return (f"Meta rate limit (code {code}) persisted after retries: {e.message} "
                    "Wait a few minutes before retrying.")
        if code == 100:
            return (f"Meta rejected the request (code 100): {e.message} "
                    "Common causes: invalid field name, unsupported filter operator "
                    "(name supports only CONTAIN; objective only IN), or a malformed parameter.")
        if code in (10, 200, 294):
            return (f"Permission denied (code {code}): {e.message} "
                    "The token lacks a required permission (ads_read; "
                    "business_management for dataset quality).")
        if code == 803:
            return (f"Unknown or inaccessible object id (code 803): {e.message} "
                    "Verify ids with the list tools.")
        if code:
            return f"Meta API error {code}: {e.message}"
        return f"Meta API request failed: {e.message}"
    return str(e)


def _parse_date(value: str, field_name: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format, got {value!r}")


def _validate_range(start_date: str, end_date: str) -> None:
    start_dt = _parse_date(start_date, "start_date")
    end_dt = _parse_date(end_date, "end_date")
    if end_dt < start_dt:
        raise ValueError(f"end_date {end_date} is before start_date {start_date}")


# ============================================================ rendering

def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _json(data: Any) -> str:
    return json.dumps(_strip(data), separators=(",", ":"), default=str, ensure_ascii=False)


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return SEP.join(
            json.dumps(x, separators=(",", ":"), ensure_ascii=False)
            if isinstance(x, (dict, list)) else str(x)
            for x in v
        )
    if isinstance(v, dict):
        return json.dumps(v, separators=(",", ":"), ensure_ascii=False)
    return v


def _csv(rows: List[Dict[str, Any]], keep: Optional[List[str]] = None) -> str:
    """CSV of rows. Columns empty in every row are dropped unless listed in keep."""
    cols: List[str] = list(keep or [])
    for r in rows:
        for k, v in r.items():
            if k not in cols and v not in (None, [], ""):
                cols.append(k)
    if keep is None:
        cols = [c for c in cols if any(r.get(c) not in (None, [], "") for r in rows)]
    if not cols:
        return ""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in cols])
    return buf.getvalue().rstrip("\n")


def _lines(*parts: Optional[str]) -> str:
    return "\n".join(p for p in parts if p)


def _kv(d: Dict[str, Any]) -> str:
    return " ".join(f"{k}={_cell(v)}" for k, v in d.items() if v is not None)


def _day(ts: Any) -> Any:
    return ts[:10] if isinstance(ts, str) else ts


def _cents(v: Any) -> Any:
    """Convert a Meta budget (string, in cents) to account currency units."""
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return v
    return n // 100 if n % 100 == 0 else n / 100


def _act(account_id: str) -> str:
    return account_id if account_id.startswith("act_") else f"act_{account_id}"


def _statuses(status: str) -> Optional[List[str]]:
    return None if status == "ALL" else [status]


def _truncate(rows: List[Dict[str, Any]], cap: int, hint: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if len(rows) <= cap:
        return rows, None
    return rows[:cap], f"truncated: showing first {cap} of {len(rows)} rows; {hint}"


def _pages_note(response: Dict[str, Any]) -> Optional[str]:
    if response.get("pages_truncated"):
        return "note: the API page guard stopped pagination early; narrow the request"
    return None


def _render_rows(
    label: str,
    rows: List[Dict[str, Any]],
    fmt: str,
    note: Optional[str] = None,
    truncated: Optional[str] = None,
    keep: Optional[List[str]] = None,
) -> str:
    if fmt == "json":
        out: Dict[str, Any] = {"count": len(rows), label: rows}
        if note:
            out["note"] = note
        if truncated:
            out["truncated"] = truncated
        return _json(out)
    body = _csv(rows, keep=keep) if rows else f"{label}: none"
    return _lines(note, truncated, body)


# ============================================================ entity tools

@mcp.tool(name="meta_ads_list_accounts",
          annotations={"title": "List Meta Ad Accounts", **READONLY}, output_schema=None)
def meta_ads_list_accounts(
    status: Literal["ACTIVE", "ALL"] = "ACTIVE",
    name_contains: Optional[str] = None,
    format: Format = "compact",
) -> str:
    """List Meta ad accounts accessible to the configured token. Start here.

    Args:
        status: 'ACTIVE' (default) or 'ALL' to include disabled and closed accounts.
        name_contains: Substring filter on account name; applied locally,
            case-insensitive (unlike the other list tools).
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Rows with id (the account_id for every other tool), name, currency,
        timezone, status, business_name.
    """
    try:
        response = _get_client().get_ad_accounts()
        rows = []
        for a in response.get("data", []):
            rows.append({
                "id": a.get("id"),
                "name": a.get("name"),
                "currency": a.get("currency"),
                "timezone": a.get("timezone_name"),
                "status": ACCOUNT_STATUS.get(a.get("account_status"), str(a.get("account_status"))),
                "business_name": (a.get("business") or {}).get("name"),
            })
        if status == "ACTIVE":
            rows = [r for r in rows if r["status"] == "ACTIVE"]
        if name_contains:
            needle = name_contains.lower()
            rows = [r for r in rows if needle in (r["name"] or "").lower()]
        return _render_rows("accounts", rows, format, note=_pages_note(response))
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


@mcp.tool(name="meta_ads_list_campaigns",
          annotations={"title": "List Meta Campaigns", **READONLY}, output_schema=None)
def meta_ads_list_campaigns(
    account_id: Id,
    status: Status = "ACTIVE",
    name_contains: Optional[str] = None,
    objective: Optional[Union[str, List[str]]] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    updated_after: Optional[str] = None,
    extra_filters: Optional[List[dict]] = None,
    format: Format = "compact",
) -> str:
    """List campaigns in a Meta ad account with server-side filtering.

    For campaign performance use meta_ads_get_insights with level='campaign'.

    Args:
        account_id: Ad account id ('act_' prefix optional).
        status: effective_status filter, default 'ACTIVE'; 'ALL' for everything.
        name_contains: CASE-SENSITIVE substring match on name.
        objective: One ODAX objective (e.g. 'OUTCOME_LEADS') or a list. Legacy
            campaigns only match their original stored objective (LINK_CLICKS,
            CONVERSIONS, ...), so pass a union list on mixed-vintage accounts.
        created_after: YYYY-MM-DD floor on created_time.
        created_before: YYYY-MM-DD ceiling on created_time.
        updated_after: YYYY-MM-DD floor on updated_time.
        extra_filters: Raw Meta {field, operator, value} triples AND'd in.
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Rows with id, name, status, objective, daily_budget, lifetime_budget
        (account currency), created, updated.
    """
    try:
        response = _get_client().get_campaigns(
            account_id,
            effective_status=_statuses(status),
            name_contains=name_contains,
            objective=objective,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            extra_filters=extra_filters,
        )
        rows = [{
            "id": c.get("id"),
            "name": c.get("name"),
            "status": c.get("effective_status"),
            "objective": c.get("objective"),
            "daily_budget": _cents(c.get("daily_budget")),
            "lifetime_budget": _cents(c.get("lifetime_budget")),
            "created": _day(c.get("created_time")),
            "updated": _day(c.get("updated_time")),
        } for c in response.get("data", [])]
        rows, truncated = _truncate(rows, MAX_LIST_ROWS,
                                    "narrow with status, name_contains or date filters")
        return _render_rows("campaigns", rows, format,
                            note=_pages_note(response), truncated=truncated)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


_ADSET_BASE_KEYS = ("id", "name", "campaign_id", "effective_status", "daily_budget",
                    "lifetime_budget", "promoted_object", "created_time", "updated_time")


@mcp.tool(name="meta_ads_list_adsets",
          annotations={"title": "List Meta Ad Sets", **READONLY}, output_schema=None)
def meta_ads_list_adsets(
    account_id: Id,
    campaign_id: Optional[Id] = None,
    status: AdsetStatus = "ACTIVE",
    name_contains: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    updated_after: Optional[str] = None,
    extra_fields: Optional[List[str]] = None,
    extra_filters: Optional[List[dict]] = None,
    format: Format = "compact",
) -> str:
    """List ad sets in an account or one campaign.

    The promoted_object is flattened into pixel_id / custom_event_type /
    custom_event_str / custom_conversion_id / page_id columns, which shows
    what each ad set optimizes on (the pixel QA workhorse).

    Args:
        account_id: Ad account id ('act_' prefix optional).
        campaign_id: Scope to one campaign (preferred on large accounts).
        status: effective_status filter, default 'ACTIVE'; 'ALL' for everything.
            'PAUSED' means paused directly - ad sets paused via their campaign
            show as CAMPAIGN_PAUSED.
        name_contains: CASE-SENSITIVE substring match on name.
        created_after / created_before / updated_after: YYYY-MM-DD bounds.
        extra_fields: Extra ad set fields to append, e.g. 'optimization_goal',
            'bid_strategy', 'start_time', 'end_time', 'targeting' (large).
        extra_filters: Raw Meta {field, operator, value} triples AND'd in.
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Rows with id, name, campaign_id, status, budgets (account currency),
        the flattened promoted_object columns, created, updated.
    """
    try:
        response = _get_client().get_ad_sets(
            account_id,
            campaign_id=campaign_id,
            effective_status=_statuses(status),
            extra_fields=extra_fields,
            name_contains=name_contains,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            extra_filters=extra_filters,
        )
        rows = []
        for a in response.get("data", []):
            po = a.get("promoted_object") or {}
            row = {
                "id": a.get("id"),
                "name": a.get("name"),
                "campaign_id": a.get("campaign_id"),
                "status": a.get("effective_status"),
                "daily_budget": _cents(a.get("daily_budget")),
                "lifetime_budget": _cents(a.get("lifetime_budget")),
                "pixel_id": po.get("pixel_id"),
                "custom_event_type": po.get("custom_event_type"),
                "custom_event_str": po.get("custom_event_str"),
                "custom_conversion_id": po.get("custom_conversion_id"),
                "page_id": po.get("page_id"),
                "application_id": po.get("application_id"),
                "created": _day(a.get("created_time")),
                "updated": _day(a.get("updated_time")),
            }
            for k, v in a.items():
                if k not in _ADSET_BASE_KEYS:
                    row[k] = v
            rows.append(row)
        rows, truncated = _truncate(rows, MAX_LIST_ROWS,
                                    "narrow with campaign_id, status or name_contains")
        return _render_rows("adsets", rows, format,
                            note=_pages_note(response), truncated=truncated)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


@mcp.tool(name="meta_ads_list_ads",
          annotations={"title": "List Meta Ads", **READONLY}, output_schema=None)
def meta_ads_list_ads(
    account_id: Id,
    campaign_id: Optional[Id] = None,
    adset_id: Optional[Id] = None,
    status: AdStatus = "ACTIVE",
    name_contains: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    updated_after: Optional[str] = None,
    include_copy: bool = False,
    extra_filters: Optional[List[dict]] = None,
    format: Format = "compact",
) -> str:
    """List ads in an account, campaign, or ad set.

    preview_link is a public fb.me URL that renders the full ad (carousel
    slides included) without login - ideal for sharing with clients. For the
    complete ad copy use meta_ads_get_creatives with the creative_id values.

    Args:
        account_id: Ad account id ('act_' prefix optional).
        campaign_id: Scope to one campaign (preferred on large accounts).
        adset_id: Scope to one ad set (takes precedence over campaign_id).
        status: effective_status filter, default 'ACTIVE'; 'ALL' for everything.
            'PAUSED' means paused directly - ads paused via a parent show as
            CAMPAIGN_PAUSED or ADSET_PAUSED.
        name_contains: CASE-SENSITIVE substring match on name.
        created_after / created_before / updated_after: YYYY-MM-DD bounds.
        include_copy: Also return the creative's title and body columns.
        extra_filters: Raw Meta {field, operator, value} triples AND'd in.
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Rows with id, name, campaign_id, adset_id, status, creative_id,
        preview_link, created, updated (+ title, body when include_copy).
    """
    try:
        response = _get_client().get_ads(
            account_id,
            campaign_id=campaign_id,
            adset_id=adset_id,
            effective_status=_statuses(status),
            include_copy=include_copy,
            name_contains=name_contains,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            extra_filters=extra_filters,
        )
        rows = []
        for a in response.get("data", []):
            cr = a.get("creative") or {}
            row = {
                "id": a.get("id"),
                "name": a.get("name"),
                "campaign_id": a.get("campaign_id"),
                "adset_id": a.get("adset_id"),
                "status": a.get("effective_status"),
                "creative_id": cr.get("id"),
                "preview_link": a.get("preview_shareable_link"),
                "created": _day(a.get("created_time")),
                "updated": _day(a.get("updated_time")),
            }
            if include_copy:
                row["title"] = cr.get("title")
                row["body"] = cr.get("body")
            rows.append(row)
        rows, truncated = _truncate(rows, MAX_LIST_ROWS,
                                    "narrow with campaign_id, adset_id, status or name_contains")
        return _render_rows("ads", rows, format,
                            note=_pages_note(response), truncated=truncated)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


def _creative_rows(cid: str, c: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def add(element: str, text: Any) -> None:
        if text not in (None, ""):
            rows.append({"creative_id": cid, "element": element, "text": text})

    spec = c.get("object_story_spec") or {}
    link = spec.get("link_data") or {}
    video = spec.get("video_data") or {}
    add("body", c.get("body") or link.get("message") or video.get("message"))
    add("title", c.get("title") or link.get("name") or video.get("title"))
    add("description", link.get("description") or video.get("link_description"))
    cta = (link.get("call_to_action") or video.get("call_to_action") or {}).get("type")
    add("cta", cta)
    link_url = link.get("link") or ((link.get("call_to_action") or {}).get("value") or {}).get("link")
    add("link", link_url)
    for i, slide in enumerate(link.get("child_attachments") or [], 1):
        add(f"slide{i}_headline", slide.get("name"))
        add(f"slide{i}_description", slide.get("description"))
    afs = c.get("asset_feed_spec") or {}
    for i, b in enumerate(afs.get("bodies") or [], 1):
        add(f"afs_body{i}", b.get("text"))
    for i, t in enumerate(afs.get("titles") or [], 1):
        add(f"afs_title{i}", t.get("text"))
    for i, d in enumerate(afs.get("descriptions") or [], 1):
        add(f"afs_description{i}", d.get("text"))
    if afs.get("call_to_action_types"):
        add("afs_cta", SEP.join(afs["call_to_action_types"]))
    return rows


@mcp.tool(name="meta_ads_get_creatives",
          annotations={"title": "Get Meta Ad Creatives", **READONLY}, output_schema=None)
def meta_ads_get_creatives(
    creative_ids: IdList,
    format: Format = "compact",
) -> str:
    """Fetch ad copy for up to 50 creatives in one batched call.

    Compact output is one row per text element (body, title, description,
    cta, link, per-slide carousel headlines, dynamic-creative variants).
    json returns the raw specs (object_story_spec, asset_feed_spec,
    image_url) for structural inspection. Carousel slides reference images
    by image_hash; to see the rendered ad use the preview_link from
    meta_ads_list_ads instead.

    Args:
        creative_ids: creative_id values from meta_ads_list_ads (max 50).
            Meta rejects the whole batch if any id is invalid.
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Rows with creative_id, element, text.
    """
    try:
        creative_ids = list(dict.fromkeys(creative_ids))
        if not creative_ids:
            raise ValueError("creative_ids must contain at least one id")
        if len(creative_ids) > MAX_CREATIVE_IDS:
            raise ValueError(f"creative_ids is capped at {MAX_CREATIVE_IDS} per call; split the list")
        response = _get_client().get_ad_creatives(creative_ids)
        if format == "json":
            return _json(response)
        rows: List[Dict[str, Any]] = []
        for cid in creative_ids:
            rows.extend(_creative_rows(cid, response.get(cid) or {}))
        body = _csv(rows, keep=["creative_id", "element", "text"]) if rows else "no text elements found"
        return _lines(f"creatives={len(creative_ids)}", body)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


# ============================================================ insights

def _insights_totals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum additive numeric columns and derive UI-style ctr_pct / cpc."""
    sums: Dict[str, float] = {}
    for row in rows:
        for k, v in row.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            lk = k.lower()
            if lk in ("reach", "frequency") or lk.endswith("_id"):
                continue
            # Ratios and unique counts are not additive across rows.
            if lk.startswith("unique") or "rate" in lk:
                continue
            if any(t in lk for t in ("ctr", "cpc", "cpm", "cpp", "cost_per", "roas")):
                continue
            sums[k] = sums.get(k, 0.0) + v
    totals: Dict[str, Any] = {
        k: (int(v) if float(v).is_integer() else round(v, 2)) for k, v in sums.items()
    }
    spend = totals.get("spend")
    impressions = totals.get("impressions")
    link_clicks = totals.get("inline_link_clicks")
    if impressions and link_clicks is not None:
        totals["ctr_pct"] = round(link_clicks / impressions * 100, 2)
    if link_clicks and spend is not None:
        totals["cpc"] = round(spend / link_clicks, 2)
    return totals


@mcp.tool(name="meta_ads_get_insights",
          annotations={"title": "Get Meta Ads Insights", **READONLY}, output_schema=None)
def meta_ads_get_insights(
    account_id: Id,
    start_date: str,
    end_date: str,
    level: Level = "account",
    metrics: Optional[List[str]] = None,
    breakdowns: Optional[List[str]] = None,
    time_increment: Increment = "total",
    campaign_ids: Optional[IdList] = None,
    adset_ids: Optional[IdList] = None,
    ad_ids: Optional[IdList] = None,
    action_types: Optional[List[str]] = None,
    attribution_windows: Optional[List[str]] = None,
    format: Format = "compact",
) -> str:
    """Performance reporting at any level - the single reporting tool.

    level='campaign' returns one row per campaign ('adset' / 'ad' likewise);
    id and name columns for the chosen level are added automatically, so
    there is no separate campaign or adset performance tool to look for.

    Args:
        account_id: Ad account id ('act_' prefix optional).
        start_date: YYYY-MM-DD, inclusive.
        end_date: YYYY-MM-DD, inclusive.
        level: 'account' (default), 'campaign', 'adset' or 'ad'.
        metrics: Insights fields. Default: spend, impressions, reach,
            inline_link_clicks, actions. Other common ones: clicks, frequency,
            ctr, cpc, cpm, action_values, conversions, conversion_values,
            purchase_roas, cost_per_action_type, video_thruplay_watched_actions.
            'actions' flattens to action_<type> columns; registered custom
            conversions appear as action_offsite_conversion.custom.<id>. Use
            'conversions' instead of 'actions' for unregistered pixel events an
            ad set optimizes on - it matches the UI Results column where
            'actions' over-counts.
        breakdowns: e.g. age, gender, country, region, publisher_platform,
            platform_position, device_platform, impression_device.
        time_increment: 'total' (default, one row per entity), 'day', 'week'
            or 'month' (adds a date column).
        campaign_ids / adset_ids / ad_ids: Filter to specific entities
            (server-side; get ids from the list tools).
        action_types: Keep only these action_types when flattening actions /
            conversions columns (trims very wide rows).
        attribution_windows: e.g. ['7d_click','1d_view'] adds per-window columns.
        format: 'compact' (header + totals + CSV, default) or 'json'.

    Returns:
        A header line, a totals line (additive metrics plus ctr_pct and cpc
        computed UI-style from inline_link_clicks; the API's own ctr/cpc
        count all clicks), then one CSV row per entity and period.
    """
    try:
        _validate_range(start_date, end_date)
        metric_list = list(metrics) if metrics else list(DEFAULT_METRICS)
        level_cols = LEVEL_FIELDS[level]
        api_fields = level_cols + [m for m in metric_list if m not in level_cols]

        response = _get_client().get_insights(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            fields=api_fields,
            level=level,
            breakdowns=breakdowns,
            time_increment=INCREMENTS[time_increment],
            campaign_ids=campaign_ids,
            adset_ids=adset_ids,
            ad_ids=ad_ids,
            attribution_windows=attribution_windows,
        )
        rows = _processor.process_insights(response, action_types=action_types)
        rows = _processor.convert_numeric_fields(rows)
        for row in rows:
            if time_increment == "total":
                row.pop("date_start", None)
            elif "date_start" in row:
                row["date"] = row.pop("date_start")

        totals = _insights_totals(rows) if rows else None
        shown, truncated = _truncate(
            rows, MAX_INSIGHTS_ROWS,
            "narrow the date range, use time_increment='total', or filter ids (totals cover all rows)")
        note = _pages_note(response)
        header = {
            "account": _act(account_id), "level": level,
            "range": f"{start_date}..{end_date}",
            "increment": time_increment, "rows": len(rows),
        }

        if format == "json":
            out: Dict[str, Any] = dict(header)
            out["row_count"] = out.pop("rows")
            if totals:
                out["totals"] = totals
            if note:
                out["note"] = note
            if truncated:
                out["truncated"] = truncated
            out["rows"] = shown
            return _json(out)

        lead = list(level_cols)
        if time_increment != "total":
            lead.append("date")
        if breakdowns:
            lead.extend(breakdowns)
        body = _csv(shown, keep=lead or None) if shown else "rows: none"
        return _lines(
            _kv(header),
            note,
            truncated,
            f"totals: {_kv(totals)}" if totals else None,
            body,
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


# ============================================================ pixels and custom conversions

@mcp.tool(name="meta_ads_list_pixels",
          annotations={"title": "List Meta Pixels", **READONLY}, output_schema=None)
def meta_ads_list_pixels(
    account_id: Id,
    format: Format = "compact",
) -> str:
    """List Meta Pixels (datasets) on an ad account.

    last_fired_time is the cheapest 'is this pixel alive' signal. Use the id
    with meta_ads_get_pixel_stats and meta_ads_get_pixel_health.

    Args:
        account_id: Ad account id ('act_' prefix optional).
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Rows with id, name, last_fired_time, is_unavailable, data_use_setting,
        automatic_matching, first_party_cookies.
    """
    try:
        response = _get_client().get_pixels(account_id)
        rows = [{
            "id": p.get("id"),
            "name": p.get("name"),
            "last_fired_time": p.get("last_fired_time"),
            "is_unavailable": p.get("is_unavailable"),
            "data_use_setting": p.get("data_use_setting"),
            "automatic_matching": p.get("enable_automatic_matching"),
            "first_party_cookies": p.get("first_party_cookie_status"),
        } for p in response.get("data", [])]
        return _render_rows("pixels", rows, format, note=_pages_note(response))
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


_STATS_ROW_META = ("start_time", "timestamp", "end_time", "value", "count", "data", "aggregation")


def _roll_up_stats(raw_rows: List[Dict[str, Any]], bucket: str) -> List[Dict[str, Any]]:
    """Aggregate Meta's hourly stat rows into day / total buckets."""
    agg: Dict[Tuple[str, Tuple], float] = {}
    for row in raw_rows:
        ts = str(row.get("start_time") or row.get("timestamp") or "")
        if bucket == "day":
            label = ts[:10]
        elif bucket == "hour":
            label = ts[:13]
        else:
            label = ""
        # Dimension keys may sit at row level or inside each data entry
        # depending on the aggregation; keep both.
        row_dims = [(k, str(v)) for k, v in row.items() if k not in _STATS_ROW_META]
        entries = row.get("data")
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list) or not entries:
            entries = [{"value": row.get("value", 0)}]
        for entry in entries:
            if not isinstance(entry, dict):
                entry = {"value": entry}
            raw_val = entry.get("value", entry.get("count", 0))
            try:
                val = float(raw_val)
            except (TypeError, ValueError):
                val = 0.0
            dims = tuple(sorted(row_dims + [
                (k, str(v)) for k, v in entry.items() if k not in ("value", "count")
            ]))
            key = (label, dims)
            agg[key] = agg.get(key, 0.0) + val
    rows = []
    for (label, dims), total in sorted(agg.items()):
        row: Dict[str, Any] = {}
        if label:
            row["date" if bucket == "day" else "hour"] = label
        row.update(dict(dims))
        row["count"] = int(total) if total.is_integer() else round(total, 2)
        rows.append(row)
    return rows


def _stats_window(start_date: str, end_date: str) -> Tuple[str, str]:
    """Unix epoch window for the stats endpoints, end date inclusive."""
    _validate_range(start_date, end_date)
    return str(_date_to_unix(start_date)), str(_date_to_unix(end_date) + 86400)


@mcp.tool(name="meta_ads_get_pixel_stats",
          annotations={"title": "Get Meta Pixel Stats", **READONLY}, output_schema=None)
def meta_ads_get_pixel_stats(
    pixel_id: Id,
    start_date: str,
    end_date: str,
    aggregation: str = "event",
    event: Optional[str] = None,
    bucket: Bucket = "day",
    format: Format = "compact",
) -> str:
    """Pixel event volume bucketed by a diagnostic dimension.

    Aggregations answer different questions: 'event' (volume per event type),
    'pixel_fire' (browser vs CAPI split), 'host' (firing domains, spots
    unauthorized fires), 'url', 'browser_type', 'device_os', 'device_type',
    'custom_data_field'. An invalid value makes Meta list the current options.

    Args:
        pixel_id: Pixel id (from meta_ads_list_pixels).
        start_date / end_date: YYYY-MM-DD, inclusive (UTC).
        aggregation: Dimension to bucket by (default 'event').
        event: Optional event name filter (e.g. 'Purchase', 'Lead').
        bucket: 'day' (default; Meta's raw data is hourly and is rolled up),
            'hour' for raw granularity, 'total' for one row per dimension value.
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Rows with date (per bucket), the aggregation dimension columns, count.
    """
    try:
        start_ts, end_ts = _stats_window(start_date, end_date)
        response = _get_client().get_pixel_stats(
            pixel_id=pixel_id, start_time=start_ts, end_time=end_ts,
            aggregation=aggregation, event=event,
        )
        rows = _roll_up_stats(response.get("data", []), bucket)
        header = _kv({"pixel": pixel_id, "aggregation": aggregation,
                      "range": f"{start_date}..{end_date}", "bucket": bucket,
                      "rows": len(rows)})
        shown, truncated = _truncate(rows, MAX_INSIGHTS_ROWS,
                                     "narrow the date range or use bucket='day'/'total'")
        note = _pages_note(response)
        if format == "json":
            out: Dict[str, Any] = {"pixel": pixel_id, "aggregation": aggregation,
                                   "bucket": bucket, "row_count": len(rows)}
            if note:
                out["note"] = note
            if truncated:
                out["truncated"] = truncated
            out["rows"] = shown
            return _json(out)
        return _lines(header, note, truncated,
                      _csv(shown) if shown else "rows: none")
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


@mcp.tool(name="meta_ads_get_pixel_health",
          annotations={"title": "Get Meta Pixel Health", **READONLY}, output_schema=None)
def meta_ads_get_pixel_health(
    pixel_id: Id,
    format: Format = "compact",
) -> str:
    """Composite pixel health snapshot: diagnostic checks plus quality fields.

    Compact output shows a summary line, the PASS/WARN/FAIL counts and only
    the non-PASS checks. The dataset-quality part may need business_management
    permission; when it fails the checks are still returned and the error is
    reported inline (graceful degradation, not a tool error).

    Args:
        pixel_id: Pixel id (from meta_ads_list_pixels).
        format: 'compact' (default) or 'json' (full check list).

    Returns:
        Summary line, check counts, CSV of non-PASS checks, any partial errors.
    """
    try:
        client = _get_client()
        da_checks: Optional[List[Dict[str, Any]]] = None
        da_error: Optional[str] = None
        quality: Optional[Dict[str, Any]] = None
        quality_error: Optional[str] = None
        try:
            da_checks = client.get_pixel_da_checks(pixel_id).get("data", [])
        except Exception as e:
            da_error = _error_message(e)
        try:
            quality = client.get_pixel_dataset_quality(pixel_id)
        except Exception as e:
            quality_error = _error_message(e)

        if format == "json":
            return _json({
                "pixel_id": pixel_id,
                "da_checks": da_checks,
                "da_checks_error": da_error,
                "dataset_quality": quality,
                "dataset_quality_error": quality_error,
            })

        summary: Dict[str, Any] = {"pixel": pixel_id}
        if quality:
            summary.update({
                "name": quality.get("name"),
                "last_fired": quality.get("last_fired_time"),
                "automatic_matching": quality.get("enable_automatic_matching"),
                "matching_fields": quality.get("automatic_matching_fields"),
                "cookies": quality.get("first_party_cookie_status"),
                "data_use": quality.get("data_use_setting"),
            })
        parts: List[Optional[str]] = [_kv(summary)]
        if da_checks is not None:
            counts: Dict[str, int] = {}
            non_pass = []
            for check in da_checks:
                result = str(check.get("result", "UNKNOWN")).upper()
                counts[result] = counts.get(result, 0) + 1
                if result != "PASS":
                    non_pass.append({
                        "result": result,
                        "check": check.get("key"),
                        "message": (check.get("user_message") or check.get("title")
                                    or check.get("description")),
                    })
            parts.append("checks: " + _kv(counts) if counts else "checks: none returned")
            parts.append(_csv(non_pass) if non_pass else
                         ("all checks passed" if counts else None))
        if da_error:
            parts.append(f"da_checks_error: {da_error}")
        if quality_error:
            parts.append(f"dataset_quality_error: {quality_error}")
        return _lines(*parts)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


@mcp.tool(name="meta_ads_list_custom_conversions",
          annotations={"title": "List Meta Custom Conversions", **READONLY}, output_schema=None)
def meta_ads_list_custom_conversions(
    account_id: Id,
    include_archived: bool = False,
    include_rule: bool = False,
    format: Format = "compact",
) -> str:
    """List custom conversions on an ad account (Meta caps them at 100).

    last_fired_time of None usually means a broken setup - this is the
    fastest tracking health check. pixel_id links each conversion to its
    source pixel.

    Args:
        account_id: Ad account id ('act_' prefix optional).
        include_archived: Include archived conversions (default False).
        include_rule: Add the matching rule JSON and description columns.
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Rows with id, name, custom_event_type, pixel_id, first_fired_time,
        last_fired_time, retention_days, default_conversion_value.
    """
    try:
        response = _get_client().get_custom_conversions(account_id)
        rows = []
        for c in response.get("data", []):
            if not include_archived and c.get("is_archived"):
                continue
            row = {
                "id": c.get("id"),
                "name": c.get("name"),
                "custom_event_type": c.get("custom_event_type"),
                "pixel_id": c.get("event_source_id"),
                "first_fired_time": _day(c.get("first_fired_time")),
                "last_fired_time": c.get("last_fired_time"),
                "retention_days": c.get("retention_days"),
                "default_conversion_value": c.get("default_conversion_value"),
                "is_unavailable": c.get("is_unavailable"),
            }
            if include_archived:
                row["is_archived"] = c.get("is_archived")
            if include_rule:
                row["rule"] = c.get("rule")
                row["description"] = c.get("description")
            rows.append(row)
        return _render_rows("custom_conversions", rows, format, note=_pages_note(response))
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


@mcp.tool(name="meta_ads_get_custom_conversion_stats",
          annotations={"title": "Get Meta Custom Conversion Stats", **READONLY}, output_schema=None)
def meta_ads_get_custom_conversion_stats(
    custom_conversion_id: Id,
    start_date: str,
    end_date: str,
    aggregation: str = "count",
    bucket: Bucket = "day",
    format: Format = "compact",
) -> str:
    """Fire-volume stats for one custom conversion, independent of attribution.

    Args:
        custom_conversion_id: Id from meta_ads_list_custom_conversions.
        start_date / end_date: YYYY-MM-DD, inclusive (UTC).
        aggregation: 'count' (default), 'usd_amount', 'unmatched_count',
            'unmatched_usd_amount', 'device_type', 'host', 'pixel_fire', 'url'.
            An invalid value makes Meta list the current options.
        bucket: 'day' (default; raw hourly data is rolled up), 'hour', 'total'.
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Rows with date (per bucket), any dimension columns, count.
    """
    try:
        start_ts, end_ts = _stats_window(start_date, end_date)
        response = _get_client().get_custom_conversion_stats(
            custom_conversion_id=custom_conversion_id,
            start_time=start_ts, end_time=end_ts, aggregation=aggregation,
        )
        rows = _roll_up_stats(response.get("data", []), bucket)
        header = _kv({"custom_conversion": custom_conversion_id,
                      "aggregation": aggregation,
                      "range": f"{start_date}..{end_date}", "bucket": bucket,
                      "rows": len(rows)})
        shown, truncated = _truncate(rows, MAX_INSIGHTS_ROWS,
                                     "narrow the date range or use bucket='day'/'total'")
        note = _pages_note(response)
        if format == "json":
            out: Dict[str, Any] = {"custom_conversion": custom_conversion_id,
                                   "aggregation": aggregation, "bucket": bucket,
                                   "row_count": len(rows)}
            if note:
                out["note"] = note
            if truncated:
                out["truncated"] = truncated
            out["rows"] = shown
            return _json(out)
        return _lines(header, note, truncated,
                      _csv(shown) if shown else "rows: none")
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


if __name__ == "__main__":
    mcp.run(transport="stdio")

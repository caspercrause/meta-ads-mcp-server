"""
Shared rendering helpers, id types, and error mapping for both MCP servers.

server.py (read) and write_server.py (write) are deployed independently;
everything either server needs from the other lives here so neither has to
import the other's FastMCP instance.
"""
import csv
import io
import json
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BeforeValidator

from facebook_client import FacebookAPIError

Format = Literal["compact", "json"]

# Ids are strings in the Graph API, but clients often send them as JSON numbers.
Id = Annotated[str, BeforeValidator(
    lambda v: str(v) if isinstance(v, int) and not isinstance(v, bool) else v)]
# Id-list filters also arrive as a bare scalar now and then.
IdList = Annotated[List[Id], BeforeValidator(
    lambda v: [v] if isinstance(v, (str, int)) and not isinstance(v, bool) else v)]

SEP = " ¦ "


def _error_message(e: Exception) -> str:
    if isinstance(e, FacebookAPIError):
        code = e.code
        if code == 190:
            return (f"Meta access token invalid or expired (code 190): {e.message} "
                    "Regenerate the token (FACEBOOK_ACCESS_TOKEN_R for the read "
                    "server, FACEBOOK_ACCESS_TOKEN_W for the write server).")
        if code in (4, 17, 613, 80004):
            return (f"Meta rate limit (code {code}) persisted after retries: {e.message} "
                    "Wait a few minutes before retrying.")
        if code == 100:
            return (f"Meta rejected the request (code 100): {e.message} "
                    "Common causes: invalid field name, unsupported filter operator "
                    "(name supports only CONTAIN; objective only IN), or a malformed parameter.")
        if code in (10, 200, 294):
            return (f"Permission denied (code {code}): {e.message} "
                    "The token lacks a required permission (ads_read for reads, "
                    "ads_management for budget/end-date writes, "
                    "business_management for dataset quality).")
        if code == 803:
            return (f"Unknown or inaccessible object id (code 803): {e.message} "
                    "Verify ids with the list tools.")
        if code:
            return f"Meta API error {code}: {e.message}"
        return f"Meta API request failed: {e.message}"
    return str(e)


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

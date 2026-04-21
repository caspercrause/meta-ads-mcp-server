"""
Facebook Ads MCP Server

A Model Context Protocol (MCP) server for Facebook Marketing API that provides
entity management and performance reporting capabilities with automatic pagination.

Transport Method:
    This server uses stdio (standard input/output) transport, which is the standard
    method for MCP servers. The server communicates with clients (like Claude Desktop)
    through stdin/stdout, making it compatible with any MCP-compliant client.
"""
from typing import List, Optional
from datetime import datetime, timezone
from fastmcp import FastMCP
from facebook_client import FacebookAdsClient
from data_processor import FacebookDataProcessor

# Pixel stats accepts either ISO 8601 or Unix epoch. We convert YYYY-MM-DD to
# Unix epoch (start-of-day UTC) so the parameter behavior is unambiguous.
# We deliberately do not allowlist aggregation values client-side: Meta keeps
# adding new ones and a hardcoded list drifts. If the value is invalid Meta
# returns a clear error listing the currently valid options.


def _date_to_unix(date_str: str) -> int:
    """Convert a YYYY-MM-DD string to a Unix epoch (start of day, UTC)."""
    dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(dt.timestamp())

# Initialize FastMCP server
# The server will communicate using stdio transport by default
mcp = FastMCP("Facebook Ads MCP Server")

# Initialize client and processor (will be created per request)
def _get_client() -> FacebookAdsClient:
    """Create Facebook Ads client instance."""
    return FacebookAdsClient()

def _get_processor() -> FacebookDataProcessor:
    """Create data processor instance."""
    return FacebookDataProcessor()


@mcp.tool()
def list_ad_accounts() -> List[dict]:
    """
    List all Facebook ad accounts accessible with your access token.

    Returns complete list of all accounts automatically - no pagination needed.
    This tool handles pagination internally and returns ALL accounts in a single call.
    
    **THIS IS THE FIRST TOOL TO CALL** when working with Facebook Ads.
    Use it to find the account_id needed for all other tools.

    Returns:
        List of ad account dictionaries with keys:
        - id: Account ID with 'act_' prefix (e.g., "act_123456789")
              **USE THIS** for account_id parameter in other tools
        - account_id: Numeric account ID (without prefix)
        - name: Account name (use to identify the right account)
        - currency: Account currency code (e.g., "USD", "CHF", "EUR")
        - timezone_name: Account timezone
        - account_status: 1 = Active, 101 = Disabled
        - business: Business object with id and name (if linked)

    Examples:
        **1. Find account by name:**
        accounts = list_ad_accounts()
        # Look for account named "My Company" in the results
        # Use the 'id' field (e.g., "act_123456789") for other tools
        
        **2. Typical workflow:**
        # Step 1: List accounts to find account_id
        accounts = list_ad_accounts()
        
        # Step 2: Use account_id in other tools
        # campaigns = list_campaigns(account_id="act_123456789")
        # insights = get_account_insights(account_id="act_123456789", ...)
    
    Note:
        - Pagination is handled automatically - you get ALL accounts
        - Look for account_status=1 for active accounts
    """
    client = _get_client()
    response = client.get_ad_accounts()
    return response.get('data', [])


@mcp.tool()
def list_campaigns(
    account_id: str,
    status_filter: Optional[str] = None
) -> List[dict]:
    """
    Get all campaigns for a Facebook ad account.

    Automatically fetches all pages of results and returns complete list.
    No manual pagination required.
    
    **USE THIS TOOL TO GET CAMPAIGN IDs FOR FILTERING:**
    When user wants insights for specific campaigns (e.g., "all French campaigns"):
    1. Call this tool to get all campaigns
    2. Filter results by campaign name pattern (e.g., names containing "FR")
    3. Extract the 'id' field from matching campaigns
    4. Pass those IDs to get_account_insights(campaign_ids=[...])

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)
            Example: "act_123456789" or "123456789"
        status_filter: Filter by status (optional):
            - 'ACTIVE': Only active campaigns
            - 'PAUSED': Only paused campaigns
            - 'ARCHIVED': Only archived campaigns
            - None: All campaigns (default)

    Returns:
        List of campaign dictionaries with these keys:
        - id: Campaign ID (USE THIS for filtering in get_account_insights)
        - name: Campaign name (USE THIS for pattern matching)
        - status: Campaign status (ACTIVE, PAUSED, etc.)
        - effective_status: Effective status
        - objective: Campaign objective
        - daily_budget: Daily budget in cents (divide by 100 for currency)
        - lifetime_budget: Lifetime budget in cents
        - created_time: Creation timestamp
        - updated_time: Last update timestamp

    Examples:
        **1. Get all active campaigns:**
        list_campaigns(account_id="act_123456789", status_filter="ACTIVE")
        
        **2. Get all campaigns (any status):**
        list_campaigns(account_id="act_123456789")
        
        **3. Workflow to filter campaigns by name pattern:**
        # Step 1: Get all campaigns
        campaigns = list_campaigns(account_id="act_123456789")
        
        # Step 2: Filter by name (e.g., campaigns containing "CH | FR")
        # fr_campaigns = [c for c in campaigns if "CH | FR" in c["name"]]
        
        # Step 3: Extract IDs
        # fr_campaign_ids = [c["id"] for c in fr_campaigns]
        
        # Step 4: Get insights for those campaigns only
        # get_account_insights(..., campaign_ids=fr_campaign_ids)
    """
    client = _get_client()
    effective_status = [status_filter] if status_filter else None
    response = client.get_campaigns(account_id, effective_status=effective_status)
    return response.get('data', [])


@mcp.tool()
def list_ad_sets(
    account_id: str,
    status_filter: Optional[str] = None
) -> List[dict]:
    """
    Get all ad sets for a Facebook ad account.

    Automatically fetches all pages of results and returns complete list.

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)
        status_filter: Filter by status: 'ACTIVE', 'PAUSED', 'ARCHIVED', or None for all

    Returns:
        Complete list of all ad sets with keys:
        - id: Ad set ID
        - name: Ad set name
        - status: Ad set status
        - effective_status: Effective status
        - daily_budget: Daily budget in cents
        - lifetime_budget: Lifetime budget in cents
        - targeting: Targeting specifications
        - created_time: Creation timestamp
        - updated_time: Last update timestamp

    Example:
        Get all active ad sets for account "123456".
    """
    client = _get_client()
    effective_status = [status_filter] if status_filter else None
    response = client.get_ad_sets(account_id, effective_status=effective_status)
    return response.get('data', [])


@mcp.tool()
def list_ads(
    account_id: str,
    campaign_id: Optional[str] = None,
    adset_id: Optional[str] = None,
    status_filter: Optional[str] = None
) -> List[dict]:
    """
    Get all ads for a Facebook ad account, specific campaign, or specific ad set.

    Automatically fetches all pages of results and returns complete list.
    For large accounts, filter by campaign_id or adset_id to avoid timeouts.

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)
        campaign_id: Optional campaign ID to list ads for a specific campaign only.
            Use list_campaigns() first to find the campaign ID.
        adset_id: Optional ad set ID to list ads for a specific ad set only.
            Use list_ad_sets() first to find the ad set ID.
        status_filter: Filter by status: 'ACTIVE', 'PAUSED', 'ARCHIVED', or None for all

    Returns:
        Complete list of all ads with keys:
        - id: Ad ID
        - name: Ad name
        - status: Ad status
        - effective_status: Effective status
        - creative: Creative object with id, title, body, image_url
        - preview_shareable_link: Public preview URL (fb.me short link) that
              anyone can open without logging in - useful for sharing with clients
        - created_time: Creation timestamp
        - updated_time: Last update timestamp

    Examples:
        **1. Get all active ads for an account:**
        list_ads(account_id="act_123456789", status_filter="ACTIVE")

        **2. Get ads for a specific campaign (recommended for large accounts):**
        list_ads(account_id="act_123456789", campaign_id="120239672137210575")

        **3. Get ads for a specific ad set:**
        list_ads(account_id="act_123456789", adset_id="120239672137220575")

        **4. Workflow to get preview links for a campaign:**
        # Step 1: Find the campaign
        campaigns = list_campaigns(account_id="act_123456789")
        # Step 2: Get ads with preview links
        ads = list_ads(account_id="act_123456789", campaign_id="<campaign_id>")
        # Step 3: Share the preview_shareable_link values with the client
    """
    client = _get_client()
    effective_status = [status_filter] if status_filter else None
    response = client.get_ads(account_id, campaign_id=campaign_id, adset_id=adset_id, effective_status=effective_status)
    return response.get('data', [])


@mcp.tool()
def get_account_insights(
    account_id: str,
    start_date: str,
    end_date: str,
    fields: List[str],
    level: str = "account",
    breakdowns: Optional[List[str]] = None,
    time_increment: Optional[str] = None,
    campaign_ids: Optional[List[str]] = None,
    adset_ids: Optional[List[str]] = None,
    ad_ids: Optional[List[str]] = None,
    flatten_actions: bool = True
) -> List[dict]:
    """
    Get performance insights for a Facebook ad account.

    Automatically fetches all pages of insights data and returns complete results.
    This is the primary tool for retrieving performance metrics.
    
    **WORKFLOW FOR FILTERING BY CAMPAIGN NAME:**
    If user wants data for campaigns matching a pattern (e.g., "CH | FR" campaigns):
    1. First call list_campaigns(account_id) to get all campaigns with their IDs
    2. Filter the results to find campaigns whose names match the pattern
    3. Extract the campaign IDs from matching campaigns
    4. Call get_account_insights with campaign_ids parameter set to those IDs
    
    **CALCULATING CTR AND CPC:**
    The API returns ctr and cpc based on link clicks, which may differ from UI.
    To match Facebook UI exactly, calculate manually:
    - CTR = (inline_link_clicks / impressions) * 100
    - CPC = spend / inline_link_clicks

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)
            Example: "act_123456789" or just "123456789"
        start_date: Start date in YYYY-MM-DD format
            Example: "2025-01-01"
        end_date: End date in YYYY-MM-DD format
            Example: "2025-01-31"
        fields: List of metrics to retrieve. ALWAYS include the fields you need!
            
            **BASIC METRICS (most common):**
            - 'spend': Total cost/spend
            - 'impressions': Number of times ads were shown
            - 'reach': Unique people who saw ads
            - 'inline_link_clicks': Link clicks (use this for CTR/CPC calculations)
            - 'clicks': All clicks (includes likes, comments, shares)
            
            **CALCULATED METRICS (returned by API but may differ from UI):**
            - 'ctr': Click-through rate
            - 'cpc': Cost per click
            - 'cpm': Cost per 1000 impressions
            
            **CAMPAIGN/ADSET/AD INFO (include when level != 'account'):**
            - 'campaign_name', 'campaign_id': Campaign details
            - 'adset_name', 'adset_id': Ad set details
            - 'ad_name', 'ad_id': Ad details
            
            **CONVERSION METRICS:**
            - 'actions': Standard pixel events (purchases, leads, etc.)
              Returns flattened as: action_purchase, action_lead, etc.
            - 'action_values': Monetary values of actions
              Returns flattened as: action_value_purchase, etc.
            - 'conversions': Facebook Conversions API events
              Returns: conversion_schedule_total (appointments), 
                       conversion_find_location_total, etc.
            - 'purchase_roas': Return on ad spend for purchases
            
        level: Aggregation level - MUST match what you're querying:
            - 'account': Total account performance (no breakdown)
            - 'campaign': Broken down by campaign (include campaign_name in fields)
            - 'adset': Broken down by ad set (include adset_name in fields)
            - 'ad': Broken down by individual ad (include ad_name in fields)
            
        breakdowns: Optional list for demographic/platform segmentation:
            - ['age']: By age group
            - ['gender']: By gender
            - ['age', 'gender']: By age AND gender
            - ['country']: By country
            - ['publisher_platform']: By platform (Facebook, Instagram, etc.)

        time_increment: Time granularity for the data:
            - '1': Daily breakdown (one row per day)
            - '7': Weekly breakdown
            - 'monthly': Monthly breakdown
            - 'all_days' or None: Total aggregation (single row, DEFAULT)
            
        campaign_ids: Optional list of campaign IDs to filter results.
            **HOW TO USE:** 
            1. Call list_campaigns() first to get campaign IDs
            2. Filter campaigns by name pattern you need
            3. Pass matching IDs here as a list: ["id1", "id2", "id3"]
            This filters at API level - much more efficient than client-side filtering!
            
        adset_ids: Optional list of ad set IDs to filter results.
            Same workflow as campaign_ids but for ad sets.
            
        ad_ids: Optional list of ad IDs to filter results.
            Same workflow as campaign_ids but for ads.
            
        flatten_actions: If True (default), flattens nested action arrays.
            - True: Returns action_purchase, action_lead as separate fields
            - False: Returns raw actions array (rarely needed)

    Returns:
        List of dictionaries, each containing the requested metrics.
        
        **EXAMPLE RESPONSE (level='campaign', time_increment='1'):**
        [
            {
                "campaign_name": "Summer Sale 2025",
                "spend": 150.50,
                "impressions": 25000,
                "inline_link_clicks": 450,
                "action_purchase": 12,
                "date_start": "2025-01-01"
            },
            ...
        ]

    Examples:
        **1. Get total account performance for a date range:**
        get_account_insights(
            account_id="act_123456789",
            start_date="2025-01-01",
            end_date="2025-01-31",
            fields=["spend", "impressions", "reach", "inline_link_clicks"],
            level="account"
        )
        
        **2. Get daily campaign breakdown:**
        get_account_insights(
            account_id="act_123456789",
            start_date="2025-01-01",
            end_date="2025-01-07",
            fields=["campaign_name", "spend", "impressions", "inline_link_clicks"],
            level="campaign",
            time_increment="1"
        )
        
        **3. Get data for SPECIFIC campaigns only (by ID):**
        get_account_insights(
            account_id="act_123456789",
            start_date="2025-01-01",
            end_date="2025-01-31",
            fields=["campaign_name", "spend", "impressions"],
            level="campaign",
            campaign_ids=["123456789012345678", "234567890123456789"]
        )
        
        **4. Get conversion data with purchases and leads:**
        get_account_insights(
            account_id="act_123456789",
            start_date="2025-01-01",
            end_date="2025-01-31",
            fields=["spend", "impressions", "actions", "action_values", "conversions"],
            level="account"
        )
        # Returns: action_purchase, action_lead, action_value_purchase, 
        #          conversion_schedule_total, etc.

    Note:
        - Pagination is handled automatically - you get ALL results
        - For large date ranges with daily breakdowns, expect many rows
        - To match Facebook UI metrics, calculate CTR/CPC manually from 
          spend, impressions, and inline_link_clicks
    """
    client = _get_client()
    processor = _get_processor()

    response = client.get_account_insights(
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        fields=fields,
        level=level,
        breakdowns=breakdowns,
        time_increment=time_increment,
        campaign_ids=campaign_ids,
        adset_ids=adset_ids,
        ad_ids=ad_ids
    )

    if flatten_actions:
        # Flatten actions and convert numeric fields
        flattened = processor.process_insights(response)
        return processor.convert_numeric_fields(flattened)
    else:
        # Return raw data
        return response.get('data', [])


@mcp.tool()
def get_campaign_insights(
    account_id: str,
    start_date: str,
    end_date: str,
    fields: List[str],
    time_increment: Optional[str] = None,
    flatten_actions: bool = True
) -> List[dict]:
    """
    Get performance insights broken down by campaign.

    Convenience method that calls get_account_insights with level='campaign'.
    Automatically fetches all pages and returns complete results.

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        fields: List of metrics to retrieve (should include 'campaign_name')
        time_increment: Time granularity ('1' for daily, 'all_days' for total)
        flatten_actions: If True, flatten 'actions' array into separate fields

    Returns:
        Complete list of insights by campaign

    Example:
        Get campaign performance with spend and conversions for Q1 2025.
    """
    return get_account_insights(
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        fields=fields,
        level='campaign',
        time_increment=time_increment,
        flatten_actions=flatten_actions
    )


@mcp.tool()
def list_pixels(account_id: str) -> List[dict]:
    """
    List all Meta Pixels (datasets) on a Facebook ad account.

    Use this as the entry point for any pixel-related work. The returned
    `id` is the pixel/dataset ID needed by get_pixel_stats and
    get_pixel_health. The `last_fired_time` field is the cheapest signal
    for "is this pixel still receiving traffic?".

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)
            Example: "act_123456789" or "123456789"

    Returns:
        List of pixel dictionaries with keys:
        - id: Pixel/dataset ID (USE THIS for other pixel tools)
        - name: Pixel name
        - last_fired_time: ISO timestamp of the most recent event received
              (None if the pixel has never fired)
        - creation_time: ISO timestamp when the pixel was created
        - is_unavailable: True if the pixel is no longer accessible
        - is_created_by_business: True if owned by a Business Manager
        - data_use_setting: 'ADVERTISING_AND_ANALYTICS' or 'ANALYTICS_ONLY'
        - enable_automatic_matching: Whether Advanced Matching is enabled
        - first_party_cookie_status: Cookie status string
        - owner_ad_account: {id, name} of the owning ad account if applicable

    Examples:
        **1. Find pixels for an account:**
        pixels = list_pixels(account_id="act_123456789")

        **2. Quick health check across all pixels:**
        pixels = list_pixels(account_id="act_123456789")
        # Sort by last_fired_time to spot pixels that have gone quiet
        for p in pixels:
            print(p["name"], p.get("last_fired_time"))

    Note:
        Requires `ads_read` permission. Pagination handled internally.
    """
    client = _get_client()
    response = client.get_pixels(account_id)
    return response.get('data', [])


@mcp.tool()
def get_pixel_stats(
    pixel_id: str,
    start_date: str,
    end_date: str,
    aggregation: str = "event",
    event: Optional[str] = None
) -> List[dict]:
    """
    Get event activity stats for a Meta Pixel, bucketed by a chosen dimension.

    This is the swiss army knife for pixel diagnostics. Pick an aggregation
    to answer different questions:

    - 'event': Volume per event type (Purchase, Lead, PageView, ...)
    - 'pixel_fire': Browser-vs-server (CAPI) split
    - 'host': Top firing domains - useful for spotting unauthorized fires
    - 'browser_type' / 'device_os' / 'device_type': Traffic mix
    - 'url' / 'url_by_rule': Top firing pages
    - 'custom_data_field': Custom event parameter breakdowns
    - 'match_keys' / 'had_pii': Advanced Matching diagnostics
    - 'event_detection_method' / 'event_source' / 'event_processing_results':
      Pipeline diagnostics
    - 'event_value_count' / 'event_total_counts': Aggregate event volume

    Args:
        pixel_id: Pixel/dataset ID (use list_pixels first to get this)
        start_date: Start date in YYYY-MM-DD format (UTC)
        end_date: End date in YYYY-MM-DD format (UTC)
        aggregation: Dimension to bucket by. Common values: 'event',
            'pixel_fire', 'host', 'browser_type', 'device_os', 'device_type',
            'url', 'custom_data_field' (default: 'event'). If you pass an
            invalid value, Meta returns an error listing the currently
            supported options.
        event: Optional event name to filter to (e.g. 'Purchase', 'Lead').
            Only meaningful when combined with non-event aggregations.

    Returns:
        List of stat-row dictionaries. Each row typically contains:
        - start_time: Bucket start time
        - value: Numeric count for the bucket
        - data: Bucketed dimension breakdown (shape depends on aggregation)

    Note:
        Facebook returns this data at HOURLY granularity by default. A
        7-day window will return ~168 rows per dimension value. For
        wider date ranges, prefer narrow windows or aggregate the
        returned rows by date client-side.

    Examples:
        **1. Event volume for the last 7 days:**
        get_pixel_stats(
            pixel_id="123456789",
            start_date="2026-04-14",
            end_date="2026-04-21",
            aggregation="event"
        )

        **2. CAPI vs browser fire split for purchases:**
        get_pixel_stats(
            pixel_id="123456789",
            start_date="2026-04-01",
            end_date="2026-04-21",
            aggregation="pixel_fire",
            event="Purchase"
        )

        **3. Find unauthorized domains firing the pixel:**
        get_pixel_stats(
            pixel_id="123456789",
            start_date="2026-04-14",
            end_date="2026-04-21",
            aggregation="host"
        )

    """
    client = _get_client()
    response = client.get_pixel_stats(
        pixel_id=pixel_id,
        start_time=str(_date_to_unix(start_date)),
        end_time=str(_date_to_unix(end_date)),
        aggregation=aggregation,
        event=event
    )
    return response.get('data', [])


@mcp.tool()
def list_custom_conversions(account_id: str) -> List[dict]:
    """
    List all custom conversions for a Facebook ad account.

    Custom conversions are what most campaigns optimize against, so the
    `last_fired_time` field is often the fastest way to detect a broken
    tracking setup. The `event_source_id` field tells you which pixel
    each custom conversion is attached to.

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)

    Returns:
        List of custom conversion dictionaries with keys:
        - id: Custom conversion ID (use for get_custom_conversion_stats)
        - name: Display name
        - description: Optional description
        - custom_event_type: PURCHASE, LEAD, ADD_TO_CART, OTHER, ...
        - rule: JSON rule string used to match incoming events
        - event_source_id: Linked pixel ID
        - first_fired_time: First time this conversion fired (ISO timestamp)
        - last_fired_time: Most recent fire (ISO timestamp). None if it has
              never fired - usually indicates a broken setup.
        - is_archived: True if archived
        - is_unavailable: True if no longer accessible
        - retention_days: Days events are retained (typically 28-90)
        - default_conversion_value: Default monetary value
        - creation_time: ISO timestamp when the conversion was created

    Note:
        Facebook caps custom conversions at 100 per ad account.
        Pagination handled internally.
    """
    client = _get_client()
    response = client.get_custom_conversions(account_id)
    return response.get('data', [])


@mcp.tool()
def get_custom_conversion_stats(
    custom_conversion_id: str,
    start_date: str,
    end_date: str,
    aggregation: str = "count"
) -> List[dict]:
    """
    Get fire-volume stats for a single custom conversion over a date range.

    Use this to verify a custom conversion is firing as expected, or to
    chart its volume over time independent of any campaign attribution.

    Args:
        custom_conversion_id: Custom conversion ID (from list_custom_conversions)
        start_date: Start date in YYYY-MM-DD format (UTC)
        end_date: End date in YYYY-MM-DD format (UTC)
        aggregation: Aggregation mode. One of: 'count' (fire count),
            'usd_amount' (monetary value), 'unmatched_count',
            'unmatched_usd_amount', 'device_type', 'host', 'pixel_fire',
            'url' (default: 'count'). If invalid, Meta returns an error
            listing the currently supported options.

    Returns:
        List of stat-row dictionaries. Typical shape:
        - timestamp: Bucket start time
        - data: Aggregated value(s) for the bucket

    Note:
        Facebook returns this data at HOURLY granularity by default. A
        7-day window can return 100+ rows. Aggregate the returned rows
        by date client-side if you need a daily view.
    """
    client = _get_client()
    response = client.get_custom_conversion_stats(
        custom_conversion_id=custom_conversion_id,
        start_time=str(_date_to_unix(start_date)),
        end_time=str(_date_to_unix(end_date)),
        aggregation=aggregation
    )
    return response.get('data', [])


@mcp.tool()
def get_pixel_health(pixel_id: str) -> dict:
    """
    Get a composite health snapshot for a Meta Pixel.

    Combines two diagnostic surfaces:
    - `da_checks`: Per-rule diagnostic checks with PASS/WARN/FAIL results
    - Dataset-quality fields: Event Match Quality summary, automatic
      matching fields, last fired time

    The dataset-quality portion may require additional permissions
    (`business_management`) and a System User token. If that call fails,
    only `da_checks` is returned and `dataset_quality_error` describes the
    failure - the tool degrades gracefully rather than erroring out.

    Args:
        pixel_id: Pixel/dataset ID

    Returns:
        Dictionary with keys:
        - pixel_id: The input pixel ID
        - da_checks: List of DACheck objects (key, description, result, ...).
              None if the diagnostics call failed.
        - da_checks_error: Error string if da_checks failed, else None.
        - dataset_quality: Dict of quality fields (last_fired_time,
              aggregated_event_match_quality_summary, ...).
              None if the quality call failed (often a permissions issue).
        - dataset_quality_error: Error string if quality failed, else None.

    Examples:
        **1. Quick health check on a pixel:**
        health = get_pixel_health(pixel_id="123456789")
        if health["da_checks"]:
            failed = [c for c in health["da_checks"]
                      if c.get("result") == "FAIL"]
            print(f"{len(failed)} failed diagnostic checks")
    """
    client = _get_client()
    result: dict = {
        'pixel_id': pixel_id,
        'da_checks': None,
        'da_checks_error': None,
        'dataset_quality': None,
        'dataset_quality_error': None,
    }

    try:
        da_response = client.get_pixel_da_checks(pixel_id)
        result['da_checks'] = da_response.get('data', [])
    except Exception as e:
        result['da_checks_error'] = str(e)

    try:
        result['dataset_quality'] = client.get_pixel_dataset_quality(pixel_id)
    except Exception as e:
        result['dataset_quality_error'] = str(e)

    return result


# Run the server
if __name__ == "__main__":
    # Run server using stdio transport (standard input/output)
    # This is the standard MCP transport method for Claude Desktop and other clients
    mcp.run(transport="stdio")

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
from fastmcp import FastMCP
from facebook_client import FacebookAdsClient
from data_processor import FacebookDataProcessor

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
    status_filter: Optional[str] = None
) -> List[dict]:
    """
    Get all ads for a Facebook ad account.

    Automatically fetches all pages of results and returns complete list.

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)
        status_filter: Filter by status: 'ACTIVE', 'PAUSED', 'ARCHIVED', or None for all

    Returns:
        Complete list of all ads with keys:
        - id: Ad ID
        - name: Ad name
        - status: Ad status
        - effective_status: Effective status
        - creative: Creative object with id, title, body, image_url
        - created_time: Creation timestamp
        - updated_time: Last update timestamp

    Example:
        Get all ads for account "123456".
    """
    client = _get_client()
    effective_status = [status_filter] if status_filter else None
    response = client.get_ads(account_id, effective_status=effective_status)
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
            campaign_ids=["120235919196570172", "120233299253770172"]
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


# Run the server
if __name__ == "__main__":
    # Run server using stdio transport (standard input/output)
    # This is the standard MCP transport method for Claude Desktop and other clients
    mcp.run(transport="stdio")

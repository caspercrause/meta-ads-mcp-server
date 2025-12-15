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

    Returns:
        List of ad account dictionaries with keys:
        - id: Account ID with 'act_' prefix (e.g., "act_123456")
        - account_id: Numeric account ID
        - name: Account name
        - currency: Account currency code (e.g., "USD")
        - timezone_name: Account timezone
        - account_status: 1 = Active, 101 = Disabled
        - business: Business object (if linked)

    Example:
        List all accessible ad accounts and display their names and IDs.
    
    Note:
        This tool automatically fetches ALL pages of results. You will receive
        the complete list of all accounts in one response - no manual pagination needed.
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

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)
        status_filter: Filter by status: 'ACTIVE', 'PAUSED', 'ARCHIVED', or None for all

    Returns:
        Complete list of all campaigns with keys:
        - id: Campaign ID
        - name: Campaign name
        - status: Campaign status
        - effective_status: Effective status (considers parent statuses)
        - objective: Campaign objective (e.g., "CONVERSIONS", "LINK_CLICKS")
        - daily_budget: Daily budget in cents
        - lifetime_budget: Lifetime budget in cents
        - created_time: Creation timestamp
        - updated_time: Last update timestamp

    Example:
        Get all active campaigns for account "123456".
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
    flatten_actions: bool = True
) -> List[dict]:
    """
    Get performance insights for a Facebook ad account.

    Automatically fetches all pages of insights data and returns complete results.
    This is the primary tool for retrieving performance metrics.

    Args:
        account_id: Ad account ID (with or without 'act_' prefix)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        fields: List of metrics to retrieve
            Common fields: 'spend', 'impressions', 'clicks', 'ctr', 'cpc', 'cpm',
                          'reach', 'frequency', 'actions', 'action_values',
                          'conversions', 'conversion_values',
                          'campaign_name', 'campaign_id', 'adset_name', 'adset_id'
            
            Important: 'conversions' field includes Facebook Conversions API events:
                - conversion_schedule_total: Appointment scheduling events
                - conversion_find_location_total: Find location events
                - Other custom conversion events
            These may differ from 'actions' due to different attribution models.
        level: Aggregation level - one of:
            - 'account': Account-level aggregation
            - 'campaign': Broken down by campaign
            - 'adset': Broken down by ad set
            - 'ad': Broken down by individual ad
        breakdowns: Optional breakdowns for segmentation:
            Demographics: 'age', 'gender'
            Geography: 'country', 'region', 'dma'
            Platform: 'publisher_platform', 'platform_position', 'device_platform'
        time_increment: Time granularity:
            - '1': Daily breakdown
            - '7': Weekly breakdown
            - 'monthly': Monthly breakdown
            - 'all_days': Total aggregation (default)
        flatten_actions: If True, flatten 'actions' array into separate fields
            (e.g., action_purchase, action_lead). Default: True

    Returns:
        Complete list of all insights rows with flattened structure.
        If flatten_actions=True, actions like [{'action_type': 'purchase', 'value': '5'}]
        become flat fields: {'action_purchase': '5'}

    Examples:
        # Get account-level performance with standard actions
        get_account_insights(
            account_id="123456",
            start_date="2025-01-01",
            end_date="2025-01-31",
            fields=["spend", "impressions", "clicks", "actions"],
            level="account"
        )
        
        # Get campaign performance with conversions (for appointment scheduling, etc.)
        get_account_insights(
            account_id="123456",
            start_date="2025-11-24",
            end_date="2025-12-10",
            fields=["campaign_name", "spend", "impressions", "actions", "conversions"],
            level="campaign"
        )
        # Returns: conversion_schedule_total, conversion_find_location_total, etc.

    Note:
        This tool returns ALL results automatically. For large date ranges with
        daily breakdowns, this may return thousands of rows. The pagination is
        handled internally - you always get complete results.
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
        time_increment=time_increment
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

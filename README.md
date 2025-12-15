# Facebook Ads MCP Server

A Model Context Protocol (MCP) server for Facebook Marketing API that provides entity management and performance reporting capabilities with automatic pagination.

## Key Features

**Automatic Pagination**: Unlike other implementations, this server automatically fetches all pages of results internally. When you list campaigns, ad sets, or insights, you get complete results without manual pagination.

**Complete Results**: All list and insights tools return complete datasets. No need to manually follow pagination cursors or make multiple requests.

**Performance Reporting**: Comprehensive insights API with support for breakdowns, time increments, and all Facebook metrics.

**Action Flattening**: Automatically flattens Facebook's complex action structures into simple key-value pairs for easy data processing.

## Architecture

This server integrates with Facebook Marketing API (Graph API) to provide:

- **Entity Management**: List and retrieve campaigns, ad sets, and ads
- **Performance Reporting**: Get insights with flexible date ranges and breakdowns
- **Automatic Pagination**: Internal handling of all pagination - always returns complete results
- **Data Processing**: Flattens complex nested structures into clean JSON

## Prerequisites

### 1. Facebook Marketing API Access

You need a Facebook access token with marketing API permissions:

1. Create a Facebook App in [Facebook Developers](https://developers.facebook.com/)
2. Enable the Marketing API
3. Generate a long-lived access token
4. Grant the token access to your ad accounts

**Required Permissions:**
- `ads_read`: Read ad account data
- `ads_management`: Manage ad campaigns (if needed)

### 2. Find Your Ad Account ID

1. Go to [Facebook Ads Manager](https://business.facebook.com/adsmanager)
2. Look at the URL: `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=123456789`
3. The number after `act=` is your Ad Account ID
4. You can use it with or without the `act_` prefix in this server

## Installation

### Step 1: Set Up Python Environment

This project uses pyenv for Python version management:

```bash
# Navigate to project directory
cd /path/to/your/pc/meta-ads-mcp-server

# Install Python 3.11.1 if not already installed
pyenv install 3.11.1

# Set local Python version
pyenv local 3.11.1

# Create virtual environment
pyenv virtualenv 3.11.1 meta-ads-mcp

# Activate virtual environment
pyenv activate meta-ads-mcp
```

### Step 2: Install Dependencies

```bash
# Upgrade pip
pip install --upgrade pip

# Install required packages
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables

Create a `.env` file with your credentials:

```bash
# Copy the example file
cp .env.example .env
```

Edit the `.env` file:

```bash
# Facebook Marketing API Configuration
FACEBOOK_ACCESS_TOKEN=your_long_lived_token_here
FACEBOOK_API_VERSION=v22.0
```

**Important**: Keep your `.env` file secure and never commit it to version control.

## Local Testing

Test the server locally with FastMCP:

```bash
# Run in development mode
fastmcp dev server.py

# Or run directly
python server.py
```

The server will start and be available for MCP connections.

## Deployment to FastMCP Cloud

```bash
# Login to FastMCP Cloud
fastmcp login

# Deploy your server
fastmcp deploy

# Set environment variables
fastmcp env set FACEBOOK_ACCESS_TOKEN="your_token_here"
fastmcp env set FACEBOOK_API_VERSION="v22.0"
```

## Available Tools

### Account Management

#### list_ad_accounts

List all Facebook ad accounts accessible with your access token.

**Returns all accounts automatically** - pagination handled internally.

```python
list_ad_accounts()
```

**Returns:**
- `id`: Account ID with 'act_' prefix
- `account_id`: Numeric account ID
- `name`: Account name
- `currency`: Currency code (e.g., "USD")
- `timezone_name`: Account timezone
- `account_status`: 1 = Active, 101 = Disabled

### Campaign Management

#### list_campaigns

Get all campaigns for an ad account.

**Returns all campaigns automatically** - no pagination needed.

```python
list_campaigns(
    account_id="123456",
    status_filter="ACTIVE"  # Optional: 'ACTIVE', 'PAUSED', 'ARCHIVED', or None
)
```

**Returns:**
- `id`: Campaign ID
- `name`: Campaign name
- `status`: Campaign status
- `effective_status`: Effective status
- `objective`: Campaign objective
- `daily_budget`: Daily budget in cents
- `lifetime_budget`: Lifetime budget in cents
- `created_time`: Creation timestamp
- `updated_time`: Last update timestamp

### Ad Set Management

#### list_ad_sets

Get all ad sets for an ad account.

**Returns all ad sets automatically** - pagination handled internally.

```python
list_ad_sets(
    account_id="123456",
    status_filter="ACTIVE"  # Optional
)
```

### Ad Management

#### list_ads

Get all ads for an ad account.

**Returns all ads automatically** - pagination handled internally.

```python
list_ads(
    account_id="123456",
    status_filter="ACTIVE"  # Optional
)
```

### Performance Reporting

#### get_account_insights

Get performance insights for an ad account.

**This is the primary reporting tool.** It automatically fetches all pages of results and returns complete data.

```python
get_account_insights(
    account_id="123456",
    start_date="2025-01-01",
    end_date="2025-01-31",
    fields=["spend", "impressions", "clicks", "ctr", "actions"],
    level="campaign",  # 'account', 'campaign', 'adset', or 'ad'
    breakdowns=["age", "gender"],  # Optional
    time_increment="1",  # Optional: '1' for daily, 'all_days' for total
    flatten_actions=True  # Flatten actions array into separate fields
)
```

**Common Fields:**
- **Basic Metrics**: `spend`, `impressions`, `clicks`, `reach`, `frequency`
- **Rates**: `ctr`, `cpc`, `cpm`, `cpp`
- **Conversions**: `actions`, `action_values`, `conversions`, `conversion_values`
- **Entity Names**: `campaign_name`, `campaign_id`, `adset_name`, `ad_name`

**Common Breakdowns:**
- **Demographics**: `age`, `gender`
- **Geography**: `country`, `region`, `dma`, `city`
- **Platform**: `publisher_platform`, `platform_position`, `device_platform`
- **Device**: `impression_device`

#### get_campaign_insights

Convenience method for campaign-level insights.

```python
get_campaign_insights(
    account_id="123456",
    start_date="2025-01-01",
    end_date="2025-01-31",
    fields=["campaign_name", "spend", "impressions", "clicks", "actions"],
    time_increment="1"  # Daily breakdown
)
```

## Action Flattening

Facebook returns actions and conversions as nested arrays:

```json
{
  "campaign_name": "My Campaign",
  "spend": "100.50",
  "actions": [
    {"action_type": "purchase", "value": "5"},
    {"action_type": "lead", "value": "12"}
  ]
}
```

With `flatten_actions=True` (default), this becomes:

```json
{
  "campaign_name": "My Campaign",
  "spend": 100.50,
  "action_purchase": 5.0,
  "action_lead": 12.0
}
```

This makes the data much easier to work with in analysis tools.

## Common Action Types

When requesting `actions` or `action_values` fields, you'll see these common action types:

- `purchase`: Purchases
- `lead`: Leads
- `offsite_conversion.fb_pixel_purchase`: Website purchases (pixel-tracked)
- `offsite_conversion.fb_pixel_lead`: Website leads (pixel-tracked)
- `link_click`: Link clicks
- `landing_page_view`: Landing page views
- `add_to_cart`: Add to cart events
- `initiate_checkout`: Checkout initiated
- `view_content`: Content views

## Example Use Cases

### Get All Active Campaigns

```python
campaigns = list_campaigns(
    account_id="123456",
    status_filter="ACTIVE"
)
```

### Get Daily Campaign Performance for Last Month

```python
insights = get_campaign_insights(
    account_id="123456",
    start_date="2025-11-01",
    end_date="2025-11-30",
    fields=["campaign_name", "spend", "impressions", "clicks", "ctr", "actions"],
    time_increment="1"
)
```

### Get Account Performance with Demographic Breakdown

```python
insights = get_account_insights(
    account_id="123456",
    start_date="2025-01-01",
    end_date="2025-01-31",
    fields=["spend", "impressions", "clicks", "reach", "frequency"],
    level="account",
    breakdowns=["age", "gender"]
)
```

## Response Format

All tools return JSON-serializable data (lists and dictionaries). Lists are used for collections, dictionaries for individual items.

Example response from `list_campaigns`:

```json
[
  {
    "id": "123456789",
    "name": "My Campaign",
    "status": "ACTIVE",
    "effective_status": "ACTIVE",
    "objective": "CONVERSIONS",
    "daily_budget": "10000",
    "created_time": "2025-01-01T00:00:00+0000"
  }
]
```

## Troubleshooting

### Access Token Issues

- Ensure your access token has not expired
- Verify the token has permissions for the ad accounts you're accessing
- Long-lived tokens expire after 60 days - regenerate as needed

### Empty Results

- Check that the account ID is correct (with or without 'act_' prefix works)
- Verify the date range contains data
- Check status filters - inactive campaigns won't appear with `status_filter='ACTIVE'`

### Rate Limiting

Facebook enforces rate limits on API calls. This server includes error handling for rate limits. If you hit limits:

- Reduce query frequency
- Use broader date ranges with `time_increment='all_days'`
- Contact Facebook to request higher limits if needed

## Important Notes

### Automatic Pagination

**This is the key feature of this implementation.** All list and insights methods automatically fetch all pages of results. You never need to manually handle pagination.

When you call `list_campaigns()`, you get ALL campaigns, not just the first 25.
When you call `get_account_insights()`, you get ALL insights rows, even if there are thousands.

This is different from naive implementations that only return the first page and require manual pagination.

### Security

- Never commit your `.env` file or access tokens to version control
- Use environment variables for all sensitive configuration
- Rotate access tokens regularly
- Follow Facebook's security best practices

## Resources

- [Facebook Marketing API Documentation](https://developers.facebook.com/docs/marketing-apis)
- [Graph API Reference](https://developers.facebook.com/docs/graph-api)
- [Marketing API Insights](https://developers.facebook.com/docs/marketing-api/insights)
- [FastMCP Documentation](https://github.com/anthropics/fastmcp)

## License

This project is provided as-is for use with Facebook Marketing API integration.

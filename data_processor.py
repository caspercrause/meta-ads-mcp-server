"""
Facebook Data Processor for transforming API responses.

This module handles flattening of complex Facebook API responses,
particularly actions, action_values, conversions, and nested structures.
"""
from typing import Dict, List, Any, Optional


class FacebookDataProcessor:
    """
    Processes Facebook API responses into clean, flat JSON structures.

    Facebook returns complex nested structures for actions and conversions.
    This processor flattens them into simple key-value pairs suitable for
    JSON serialization and MCP tool responses.
    """

    def flatten_insights(
        self,
        item: Dict[str, Any],
        action_types: Optional[List[str]] = None,
        action_value_types: Optional[List[str]] = None,
        conversion_types: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Flatten Facebook insights data including actions and conversions.

        Transforms nested structures like:
            {'actions': [{'action_type': 'purchase', 'value': '5'}]}
        Into flat structure:
            {'action_purchase': '5'}

        Args:
            item: Single insights data item from Facebook API
            action_types: Specific action types to extract (None = extract all)
            action_value_types: Specific action value types to extract (None = extract all)
            conversion_types: Specific conversion types to extract (None = extract all)

        Returns:
            Flattened dictionary with simple key-value pairs

        Example:
            >>> processor = FacebookDataProcessor()
            >>> raw_item = {
            ...     'campaign_name': 'My Campaign',
            ...     'spend': '100.50',
            ...     'actions': [
            ...         {'action_type': 'purchase', 'value': '5'},
            ...         {'action_type': 'lead', 'value': '12'}
            ...     ]
            ... }
            >>> flat = processor.flatten_insights(raw_item)
            >>> print(flat)
            {'campaign_name': 'My Campaign', 'spend': '100.50',
             'action_purchase': '5', 'action_lead': '12'}
        """
        flat: Dict[str, Any] = {}

        for key, value in item.items():
            if key == 'actions' and isinstance(value, list):
                # Flatten actions array into separate fields
                for action in value:
                    action_type = action.get('action_type', 'unknown')
                    action_val = action.get('value', 0)

                    # Filter if action_types specified
                    if action_types is None or action_type in action_types:
                        flat[f'action_{action_type}'] = action_val

            elif key == 'action_values' and isinstance(value, list):
                # Flatten action values array into separate fields
                for action in value:
                    action_type = action.get('action_type', 'unknown')
                    action_val = action.get('value', 0)

                    # Filter if action_value_types specified
                    if action_value_types is None or action_type in action_value_types:
                        flat[f'action_value_{action_type}'] = action_val

            elif key == 'conversions' and isinstance(value, (list, dict)):
                # Flatten conversions - can be either list or dict depending on API response
                if isinstance(value, list):
                    # List format: [{'action_type': 'schedule_total', 'value': '296'}]
                    for conversion in value:
                        conv_type = conversion.get('action_type', 'unknown')
                        conv_val = conversion.get('value', 0)

                        # Filter if conversion_types specified
                        if conversion_types is None or conv_type in conversion_types:
                            flat[f'conversion_{conv_type}'] = conv_val
                else:
                    # Dict format: {'schedule_total': '296', 'find_location_total': '1449'}
                    # This is the actual format returned by Facebook API
                    for conv_type, conv_val in value.items():
                        # Filter if conversion_types specified
                        if conversion_types is None or conv_type in conversion_types:
                            flat[f'conversion_{conv_type}'] = conv_val

            elif key == 'conversion_values' and isinstance(value, (list, dict)):
                # Flatten conversion values - can be either list or dict
                if isinstance(value, list):
                    # List format
                    for conversion in value:
                        conv_type = conversion.get('action_type', 'unknown')
                        conv_val = conversion.get('value', 0)

                        # Use same filter as conversion_types
                        if conversion_types is None or conv_type in conversion_types:
                            flat[f'conversion_value_{conv_type}'] = conv_val
                else:
                    # Dict format
                    for conv_type, conv_val in value.items():
                        # Use same filter as conversion_types
                        if conversion_types is None or conv_type in conversion_types:
                            flat[f'conversion_value_{conv_type}'] = conv_val

            elif key == 'video_thruplay_watched_actions' and isinstance(value, list):
                # Flatten video metrics
                for video_action in value:
                    video_action_type = video_action.get('action_type', 'unknown')
                    video_val = video_action.get('value', 0)
                    flat[f'video_thruplay_watched_actions_{video_action_type}'] = video_val

            elif isinstance(value, dict):
                # Flatten nested dicts (like creative object)
                for sub_key, sub_value in value.items():
                    flat[f'{key}_{sub_key}'] = sub_value

            else:
                # Skip date_stop as it's redundant
                if key == 'date_stop':
                    continue
                flat[key] = value

        return flat

    def process_insights(
        self,
        response: Dict[str, Any],
        action_types: Optional[List[str]] = None,
        action_value_types: Optional[List[str]] = None,
        conversion_types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Process insights response into list of flattened dicts.

        Args:
            response: Response from Facebook API with 'data' key
            action_types: Specific action types to extract
            action_value_types: Specific action value types to extract
            conversion_types: Specific conversion types to extract

        Returns:
            List of flattened insight dictionaries

        Example:
            >>> processor = FacebookDataProcessor()
            >>> response = {
            ...     'data': [
            ...         {'campaign_name': 'Campaign 1', 'spend': '100',
            ...          'actions': [{'action_type': 'purchase', 'value': '5'}]},
            ...         {'campaign_name': 'Campaign 2', 'spend': '200',
            ...          'actions': [{'action_type': 'purchase', 'value': '8'}]}
            ...     ]
            ... }
            >>> result = processor.process_insights(response)
            >>> print(len(result))
            2
        """
        data = response.get('data', [])

        if not data:
            return []

        # Flatten each item
        flat_data = []
        for item in data:
            flat_item = self.flatten_insights(
                item,
                action_types=action_types,
                action_value_types=action_value_types,
                conversion_types=conversion_types
            )
            flat_data.append(flat_item)

        return flat_data

    def convert_numeric_fields(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert numeric string fields to appropriate types.

        Facebook returns most numeric values as strings. This method
        attempts to convert them to numbers for easier processing.

        Args:
            data: List of dictionaries to process

        Returns:
            List of dictionaries with numeric fields converted

        Note:
            Fields that fail conversion remain as strings.
        """
        numeric_patterns = [
            'spend', 'impressions', 'clicks', 'reach', 'frequency',
            'ctr', 'cpc', 'cpm', 'conversions', 'cost', 'action_',
            'value', 'video', 'budget'
        ]

        result = []
        for item in data:
            converted = {}
            for key, value in item.items():
                # Check if field matches numeric patterns
                if any(pattern in key.lower() for pattern in numeric_patterns):
                    try:
                        # Try to convert to float
                        if isinstance(value, str):
                            converted[key] = float(value)
                        else:
                            converted[key] = value
                    except (ValueError, TypeError):
                        converted[key] = value
                else:
                    converted[key] = value
            result.append(converted)

        return result

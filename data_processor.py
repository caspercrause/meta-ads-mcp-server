"""
Flattens Meta insights responses into flat dicts with proper numeric types.

Meta returns actions, conversions, ROAS and cost-per-action data as nested
lists of {action_type, value} objects and most numbers as strings. This
module flattens those structures into simple columns and converts numeric
strings to int/float while protecting ids, names and dates.
"""
import re
from typing import Any, Dict, List, Optional

# Attribution-window keys Meta attaches to action/conversion entries when
# action_attribution_windows is requested.
ATTRIBUTION_WINDOWS = ('7d_click', '28d_click', '1d_click', '7d_view', '1d_view', '28d_view')

# Column prefixes for the canonical action-shaped fields. Any other field
# whose value is a list of {action_type, value} entries (purchase_roas,
# cost_per_action_type, video_p25_watched_actions, ...) is flattened with
# the field name itself as prefix.
_PREFIX = {
    'actions': 'action',
    'action_values': 'action_value',
    'conversions': 'conversion',
    'conversion_values': 'conversion_value',
}

# Zero-padded strings ('02134') are codes, not numbers; leave them alone.
_INT_RE = re.compile(r'-?(0|[1-9]\d*)')
_FLOAT_RE = re.compile(r'-?\d+\.\d+')


def _is_action_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(e, dict) and 'action_type' in e for e in value)
    )


class FacebookDataProcessor:
    """Processes Meta API insights responses into flat, typed dicts."""

    def flatten_insights(
        self,
        item: Dict[str, Any],
        action_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Flatten one insights row.

        {'actions': [{'action_type': 'lead', 'value': '5'}]} becomes
        {'action_lead': '5'}. The same applies to action_values,
        conversions, conversion_values, and any other list of
        {action_type, value} entries (purchase_roas, cost_per_action_type,
        video metrics, ...). Attribution-window keys present on an entry
        become additional <column>_<window> fields. date_stop is dropped
        as redundant with date_start.

        'actions' vs 'conversions' - when to use which:
        - 'actions' with offsite_conversion.custom.<id> types covers custom
          conversions REGISTERED in Events Manager (exact counts).
        - 'conversions' covers unregistered pixel events an ad set optimizes
          on (e.g. fb_pixel_custom.*). Querying 'actions' for those returns
          ALL custom pixel fires and over-counts versus the UI Results
          column; 'conversions' matches the UI. It can arrive as a list or
          as a dict, so both shapes are handled.

        Args:
            item: Single insights row from the API
            action_types: When given, only these action_types are kept while
                flattening actions / action_values / conversions /
                conversion_values. Other action-shaped fields are unaffected.
        """
        flat: Dict[str, Any] = {}
        for key, value in item.items():
            if key == 'date_stop':
                continue

            if _is_action_list(value):
                prefix = _PREFIX.get(key, key)
                filtered = key in _PREFIX and action_types is not None
                for entry in value:
                    entry_type = entry.get('action_type', 'unknown')
                    if filtered and entry_type not in action_types:
                        continue
                    flat[f'{prefix}_{entry_type}'] = entry.get('value', 0)
                    for window in ATTRIBUTION_WINDOWS:
                        if window in entry:
                            flat[f'{prefix}_{entry_type}_{window}'] = entry[window]

            elif key in ('conversions', 'conversion_values') and isinstance(value, dict):
                prefix = _PREFIX[key]
                for conv_type, conv_val in value.items():
                    if action_types is None or conv_type in action_types:
                        flat[f'{prefix}_{conv_type}'] = conv_val

            elif isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    flat[f'{key}_{sub_key}'] = sub_value

            else:
                flat[key] = value
        return flat

    def process_insights(
        self,
        response: Dict[str, Any],
        action_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Flatten every row of an insights response."""
        return [
            self.flatten_insights(item, action_types=action_types)
            for item in response.get('data', [])
        ]

    @staticmethod
    def _is_textual(key: str) -> bool:
        """Fields that must never be converted to numbers (ids lose precision)."""
        return (
            key == 'id'
            or key.endswith('_id')
            or 'name' in key
            or key.startswith('date')
            or key == 'dma'
        )

    def convert_numeric_fields(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert numeric strings to int/float.

        Integers stay integers (no trailing .0); ids, names, dates and DMA
        codes are left as strings.
        """
        result = []
        for item in data:
            converted: Dict[str, Any] = {}
            for key, value in item.items():
                if isinstance(value, str) and not self._is_textual(key):
                    if _INT_RE.fullmatch(value):
                        converted[key] = int(value)
                    elif _FLOAT_RE.fullmatch(value):
                        converted[key] = float(value)
                    else:
                        converted[key] = value
                else:
                    converted[key] = value
            result.append(converted)
        return result

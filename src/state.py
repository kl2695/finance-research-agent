"""Shim — re-exports from core.state for backwards compatibility."""
from core.state import (  # noqa: F401
    create_empty_state,
    make_data_point,
    get_missing_data,
    get_missing_entities,
    get_unfilled_steps,
    is_data_complete,
    is_calculation_complete,
    render_state_for_prompt,
    validate_state_update,
)

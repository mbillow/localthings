"""Cooking entities whose display names identify their oven cavity."""

CAVITY_KEYS = frozenset(
    {
        "start_cooking",
        "stop",
        "oven_mode",
        "oven_setpoint",
        "cook_time_hours",
        "cook_time_minutes",
        "cook_time",
        "machine_state",
        "cycle_active",
        "progress_percentage",
        "operation_time_minutes",
        "finish_time",
        "oven_state",
        "current_temp_c",
        "alarm_code",
        "diagnosis_status",
    }
)

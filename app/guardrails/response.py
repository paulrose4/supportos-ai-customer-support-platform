_PROHIBITED_UNVERIFIED_PROMISES = ("马上", "很快", "24 小时内")


def validate_safe_response(message: str, *, has_approved_sla: bool = False) -> None:
    if not has_approved_sla and any(term in message for term in _PROHIBITED_UNVERIFIED_PROMISES):
        raise ValueError("response contains an unapproved SLA promise")

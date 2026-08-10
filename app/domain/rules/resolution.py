_RESOLUTION_CONFIRMATIONS = (
    "that solved it",
    "problem solved",
    "issue resolved",
    "thanks, that's all",
    "thank you, that's all",
    "resolved now",
    "问题已解决",
    "已经解决了",
    "解决了，谢谢",
    "可以了，谢谢",
)


def is_resolution_confirmation(message: str) -> bool:
    normalized = " ".join(message.casefold().replace("’", "'").split())
    return any(phrase in normalized for phrase in _RESOLUTION_CONFIRMATIONS)

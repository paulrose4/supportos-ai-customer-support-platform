from dataclasses import dataclass

from app.domain.ports import ChatModelPort, ChatModelRequest


@dataclass(frozen=True, slots=True)
class ResponseRepairResult:
    text: str
    model: str


class ResponseRepairService:
    def __init__(self, model: ChatModelPort) -> None:
        self._model = model

    async def rewrite(
        self,
        *,
        draft: str,
        reason_code: str,
        target_language: str,
        max_words: int,
    ) -> ResponseRepairResult:
        result = await self._model.generate(
            ChatModelRequest(
                messages=(
                    {
                        "role": "system",
                        "content": (
                            "Rewrite the customer-support draft only. Preserve every factual claim "
                            "and do not add facts, prices, inventory, delivery promises, "
                            "discounts, links, contact details, or guarantees. Output only "
                            "the revised customer reply."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Validation issue: {reason_code}\n"
                            f"Target language: {target_language}\n"
                            f"Maximum words: {max_words}\n"
                            f"Draft:\n{draft}"
                        ),
                    },
                ),
                metadata={
                    "task": "response_expression_repair",
                    "validation_reason": reason_code,
                    "target_language": target_language,
                },
                max_output_tokens=500,
            )
        )
        return ResponseRepairResult(result.text.strip(), result.model)

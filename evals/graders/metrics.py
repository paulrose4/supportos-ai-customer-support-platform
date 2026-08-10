from collections.abc import Sequence


def recall(expected: Sequence[bool], predicted: Sequence[bool]) -> float:
    positives = sum(expected)
    if positives == 0:
        return 1.0
    true_positives = sum(
        expected_value and predicted_value
        for expected_value, predicted_value in zip(expected, predicted, strict=True)
    )
    return true_positives / positives

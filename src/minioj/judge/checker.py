from __future__ import annotations


def normalize_output(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line.rstrip(" \t") for line in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def outputs_match(actual: str, expected: str, checker: str = "lines") -> bool:
    if checker == "lines":
        return normalize_output(actual) == normalize_output(expected)
    if checker == "tokens":
        return actual.split() == expected.split()
    if checker == "yesno":
        actual_tokens = actual.upper().split()
        expected_tokens = expected.upper().split()
        return (
            all(token in {"YES", "NO"} for token in expected_tokens)
            and actual_tokens == expected_tokens
        )
    raise ValueError("Unsupported problem checker")

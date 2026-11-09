"""Static rubric configuration for first-stage quality scoring."""

DOMAIN_NAMES = ("runtime", "code_quality", "api_test", "agent")


def grade(score: int) -> str:
    """Map a 0-100 domain score to a compact letter grade."""
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def status(score: int) -> str:
    """Map a domain score to pass, conditional_pass, or fail."""
    if score >= 80:
        return "pass"
    if score >= 60:
        return "conditional_pass"
    return "fail"

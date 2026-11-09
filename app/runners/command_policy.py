import re

from app.core.exceptions import InvalidInputError


class CommandPolicy:
    _blocked_patterns = (
        r"(^|\s)rm\s+-rf\s+/(?:\s|$)",
        r"(^|\s)(shutdown|reboot|halt|poweroff)(\s|$)",
        r"/var/run/docker\.sock",
        r"(^|\s)docker(\s|$)",
        r"(^|\s)mount(\s|$)",
    )

    def validate(self, command: str) -> None:
        if not command or len(command) > 500:
            raise InvalidInputError("command must be between 1 and 500 characters")
        for pattern in self._blocked_patterns:
            if re.search(pattern, command, flags=re.IGNORECASE):
                raise InvalidInputError("command is blocked by policy")


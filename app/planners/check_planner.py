from abc import ABC, abstractmethod

from app.core.exceptions import InvalidInputError
from app.planners.profiles import DEFAULT_PROFILES
from app.runners.command_policy import CommandPolicy


class CheckPlanner(ABC):
    @abstractmethod
    def build_plan(
        self,
        profile: str,
        checks: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        raise NotImplementedError


class ProfileCheckPlanner(CheckPlanner):
    def __init__(self, command_policy: CommandPolicy):
        self.command_policy = command_policy

    def build_plan(
        self,
        profile: str,
        checks: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        if checks:
            plan = checks
        elif profile == "custom":
            raise InvalidInputError("custom profile requires checks")
        else:
            plan = DEFAULT_PROFILES.get(profile)
            if plan is None:
                raise InvalidInputError(f"unsupported profile: {profile}")

        normalized: list[dict[str, str]] = []
        for item in plan:
            name = item["name"].strip()
            command = item["command"].strip()
            self.command_policy.validate(command)
            normalized.append({"name": name, "command": command})
        return normalized

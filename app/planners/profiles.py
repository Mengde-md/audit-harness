DEFAULT_PROFILES: dict[str, list[dict[str, str]]] = {
    "python-basic": [
        {"name": "lint", "command": "ruff check ."},
        {"name": "test", "command": "pytest"},
    ],
}

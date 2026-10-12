from __future__ import annotations

import uvicorn

from orchestrator.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "orchestrator.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()

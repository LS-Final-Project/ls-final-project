"""Start the service selected by SERVICE_NAME; Cloud Run supplies PORT."""

import uvicorn

from backend.shared.logs import configure_logging
from backend.shared.settings import Settings


def main() -> None:
    settings = Settings()
    configure_logging()
    uvicorn.run(
        f"backend.api.{settings.service_name}.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=settings.port,
        # Keep the JSON configuration above; the middleware writes the request lines.
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()

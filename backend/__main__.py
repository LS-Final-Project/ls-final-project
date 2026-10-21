"""Start the service selected by SERVICE_NAME; Cloud Run supplies PORT."""

import uvicorn

from backend.shared.settings import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        f"backend.api.{settings.service_name}.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=settings.port,
    )


if __name__ == "__main__":
    main()

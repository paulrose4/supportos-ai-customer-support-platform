import argparse
import asyncio
import json
from datetime import UTC, datetime

from app.application.dto.resolution import RunAutoResolutionCommand
from app.application.services.resolution import AutoResolutionService
from app.config import get_settings
from app.integrations.postgres.resolution import PostgreSQLAutoResolutionAdapter
from app.integrations.postgres.session import DatabaseSessionManager


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview or execute due AI auto-resolutions.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=500)
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    manager = DatabaseSessionManager(settings.database_url)
    service = AutoResolutionService(
        PostgreSQLAutoResolutionAdapter(manager.session_factory),
        execution_enabled=settings.auto_resolution_execution_enabled,
    )
    try:
        result = await service.run(
            RunAutoResolutionCommand(
                evaluated_at=datetime.now(UTC),
                execute=args.execute,
                limit=args.limit,
            )
        )
        print(
            json.dumps(
                {
                    "evaluated_at": result.evaluated_at.isoformat(),
                    "due_count": result.due_count,
                    "resolved_count": result.resolved_count,
                    "executed": result.executed,
                }
            )
        )
        return 0
    finally:
        await manager.dispose()


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

from anyio.to_thread import run_sync

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.runner import ProcessingRuntime, run_analysis_job  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.persistence.models import JobStatus  # noqa: E402
from app.persistence.repository import (  # noqa: E402
    AnalysisJobNotFoundError,
    AnalysisJobRepository,
)


async def process_job(job_id: UUID) -> int:
    async with ProcessingRuntime(get_settings()) as runtime:
        await run_analysis_job(job_id, runtime=runtime)
        pipeline = await runtime.get_pipeline()

        def read_status():
            with pipeline.session_factory() as session:
                return AnalysisJobRepository(session).get_by_id(job_id).status

        status = await run_sync(read_status)
        print(f"{job_id}: {status}")
        return 0 if status == JobStatus.COMPLETED else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Process an existing analysis job")
    parser.add_argument("job_id", type=UUID)
    args = parser.parse_args()
    try:
        return asyncio.run(process_job(args.job_id))
    except AnalysisJobNotFoundError:
        print(f"{args.job_id}: job not found", file=sys.stderr)
        return 1
    except Exception:
        print(f"{args.job_id}: processing failed; see sanitized logs", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

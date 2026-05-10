"""One-shot CLI for the canonical plan migration.

Usage:
    python -m scripts.migrate_to_canonical_plans

Idempotent — safe to re-run. The same routine fires automatically on
app startup via ``services.plan_bootstrap.run_full_bootstrap`` (wired
from ``main.py`` lifespan); this script exists for ops engineers who
need to apply the migration against a non-running database.

Runs three steps in order:

1. Upsert the four canonical plans (Free / Starter / Premium / Enterprise)
   from ``config.plan_tiers``. Existing canonical plans have their gate
   fields refreshed but admin-tunable cap fields are preserved.
2. Archive any plan whose name is not in the canonical set. Existing
   subscriptions to those legacy plans continue working until they
   expire — we don't yank paying customers off a plan mid-cycle.
3. Backfill: every active tenant without a current ACTIVE/TRIALING
   subscription gets a free-plan subscription provisioned.

Prints a summary and exits 0.
"""

from __future__ import annotations

import asyncio
import json

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from services.plan_bootstrap import run_full_bootstrap

    print("Running canonical plan migration ...")
    summary = await run_full_bootstrap()
    print(json.dumps(summary, indent=2, default=str))
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())

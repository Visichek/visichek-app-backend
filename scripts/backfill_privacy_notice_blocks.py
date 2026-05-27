"""One-shot CLI: migrate every tenant's visitor privacy notice to BlockNote.

Usage:
    python -m scripts.backfill_privacy_notice_blocks            # apply changes
    python -m scripts.backfill_privacy_notice_blocks --dry-run  # preview only

Idempotent — notices already on the block format are skipped, so it is safe to
re-run. Legacy plain-text notices are regenerated as the standardized BlockNote
document (with each tenant's details substituted) and a new consent version is
minted. Active tenants with no notice at all are seeded.

Prints a JSON summary and exits 0.
"""

from __future__ import annotations

import asyncio
import json
import sys

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from services.privacy_notice_backfill import backfill_blocknote_privacy_notices

    dry_run = "--dry-run" in sys.argv
    print(
        f"Backfilling visitor privacy notices to BlockNote (dry_run={dry_run}) ..."
    )
    summary = await backfill_blocknote_privacy_notices(dry_run=dry_run)
    print(json.dumps(summary, indent=2, default=str))
    print("Dry run complete — no changes written." if dry_run else "Done.")


if __name__ == "__main__":
    asyncio.run(main())

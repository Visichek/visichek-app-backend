"""One-shot CLI: create a per-tenant Data Processing Agreement copy for every
existing tenant.

Usage:
    python -m scripts.backfill_tenant_dpa            # apply
    python -m scripts.backfill_tenant_dpa --dry-run  # preview counts only

Requires the DPA template asset (services/dpa_template_blocks.json) to be
present — run `python -m scripts.dump_dpa_template` against production first and
commit the asset. Idempotent: tenants that already have a DPA record are
skipped, so it is safe to re-run. Prints a JSON summary and exits 0.
"""

from __future__ import annotations

import asyncio
import json
import sys

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from services.tenant_agreements.bootstrap import backfill_tenant_agreements

    dry_run = "--dry-run" in sys.argv
    print(f"Backfilling per-tenant agreement copies (dry_run={dry_run}) ...")
    summary = await backfill_tenant_agreements(dry_run=dry_run)
    print(json.dumps(summary, indent=2, default=str))
    print("Dry run complete — no changes written." if dry_run else "Done.")


if __name__ == "__main__":
    asyncio.run(main())

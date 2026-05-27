"""Production extraction: pull the Data Processing Agreement body out of the
``legal_documents`` collection and write it to the committed template asset
``services/dpa_template_blocks.json``.

Why this exists: the DPA body is too large to paste through chat (it truncates),
and it only lives in the production database. This script is the bridge — run it
with access to the production Mongo (either on a prod host, or locally with the
prod connection exported) and commit the resulting asset.

Usage (locally, pointed at production):
    $env:MONGO_URL = "<prod mongo url>"   # PowerShell
    $env:DB_NAME   = "<prod db name>"
    python -m scripts.dump_dpa_template

    # or on a production host where the env is already configured:
    python -m scripts.dump_dpa_template

It is READ-ONLY against the database (a single find_one). It looks the document
up by slug ``data-processing-agreement`` first, then by
``doc_type == "data_processing_agreement"``, prefers the live ``published_body``
and falls back to the working ``body``, and writes the RAW block list (with the
``[Insert Organization's representative ...]`` placeholders intact — the runtime
substitutes them per tenant) to the asset.

After running, commit ``services/dpa_template_blocks.json``. The per-tenant DPA
feature activates automatically once the asset is present.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

_SLUG = "data-processing-agreement"
_DOC_TYPE = "data_processing_agreement"
# The committed asset the runtime loads (services/dpa_template_blocks.json).
_ASSET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "services",
    "dpa_template_blocks.json",
)


async def main() -> None:
    from core.database import db

    doc = await db.legal_documents.find_one({"slug": _SLUG})
    if doc is None:
        doc = await db.legal_documents.find_one({"doc_type": _DOC_TYPE})

    if doc is None:
        print(
            f"No legal document found with slug={_SLUG!r} or "
            f"doc_type={_DOC_TYPE!r} in this database. "
            "Make sure MONGO_URL / DB_NAME point at the environment that has "
            "the DPA (the local dev DB does not)."
        )
        sys.exit(1)

    body = doc.get("published_body") or doc.get("body") or []
    if not body:
        print(
            "Found the DPA document but its body/published_body is empty. "
            "Publish or populate the document, then re-run."
        )
        sys.exit(1)

    asset = {
        "title": doc.get("title") or "Data Processing Agreement",
        "summary": doc.get("summary"),
        "source_slug": doc.get("slug"),
        "source_doc_type": doc.get("doc_type"),
        "source_version": doc.get("current_version"),
        "block_count": len(body),
        "body": body,
    }

    with open(_ASSET_PATH, "w", encoding="utf-8") as fh:
        json.dump(asset, fh, ensure_ascii=False, indent=2)

    print(
        f"Wrote {_ASSET_PATH}\n"
        f"  title={asset['title']!r} blocks={asset['block_count']} "
        f"status={doc.get('status')!r}\n"
        "Commit this file. The per-tenant DPA feature activates once it is present."
    )


if __name__ == "__main__":
    asyncio.run(main())

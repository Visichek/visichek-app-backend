"""A partial update must never blank a field the caller didn't mention.

``AdminUpdate.password`` defaults to None, so ``AdminUpdate()`` — used as a bare
"bump last_updated" when changing an admin's access preset — dumps
``{"password": None}``. A plain ``$set`` of that dict writes the None straight
over the stored bcrypt hash and the admin is locked out of their own account.

It fails silently, which is what makes it dangerous: ``AdminOut.password`` is
Optional so nothing raises, and ``check_password`` treats a missing hash as
"wrong password", so the victim just sees "invalid credentials" forever. There
is no error anywhere to notice. Hence this test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

from schemas.admin_schema import AdminUpdate


class TestAdminUpdateDump:
    def test_empty_update_would_null_the_password(self):
        """The trap itself — documents WHY exclude_none is required below."""
        dumped = AdminUpdate().model_dump()
        assert dumped["password"] is None

    def test_exclude_none_drops_the_password_key_entirely(self):
        """Not 'sets it to empty' — the key must be ABSENT, so $set can't
        touch the stored hash at all."""
        dumped = AdminUpdate().model_dump(exclude_none=True)
        assert "password" not in dumped
        assert "last_updated" in dumped


@pytest.mark.asyncio
class TestUpdateAdminRepo:
    async def test_partial_update_never_sets_password(self):
        """The real guard: whatever update_admin sends to Mongo, a partial
        update must not carry a password key."""
        from repositories import admin_repo

        admin_id = ObjectId()
        stored = {
            "_id": admin_id,
            "full_name": "Ada Okafor",
            "email": "ada@example.com",
            "password": "$2b$12$originalhashmustsurvive",
        }

        fake_collection = AsyncMock()
        fake_collection.find_one_and_update.return_value = stored

        fake_db = AsyncMock()
        fake_db.admins = fake_collection

        with patch.object(admin_repo, "db", fake_db):
            await admin_repo.update_admin({"_id": admin_id}, AdminUpdate())

        sent = fake_collection.find_one_and_update.await_args.args[1]
        assert "password" not in sent["$set"], (
            "update_admin sent a password key on a partial update — this "
            "overwrites the stored hash and silently locks the admin out"
        )

    async def test_an_explicit_password_change_still_goes_through(self):
        """exclude_none must not break the path that legitimately sets one."""
        from repositories import admin_repo

        admin_id = ObjectId()
        fake_collection = AsyncMock()
        fake_collection.find_one_and_update.return_value = {
            "_id": admin_id,
            "full_name": "Ada Okafor",
            "email": "ada@example.com",
            "password": "hashed",
        }
        fake_db = AsyncMock()
        fake_db.admins = fake_collection

        with patch.object(admin_repo, "db", fake_db):
            await admin_repo.update_admin(
                {"_id": admin_id}, AdminUpdate(password="Str0ng!Passw0rd#2026")
            )

        sent = fake_collection.find_one_and_update.await_args.args[1]
        assert "password" in sent["$set"]
        # Stored hashed, never in cleartext.
        assert sent["$set"]["password"] != "Str0ng!Passw0rd#2026"

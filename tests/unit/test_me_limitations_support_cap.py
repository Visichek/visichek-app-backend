from __future__ import annotations

import pytest

from services.support_case_service import MAX_OPEN_CASES_PER_TENANT


@pytest.mark.unit
class TestSupportCapSurfaced:
    def test_caps_block_includes_open_support_case_cap(self):
        from services.me_limitations_service import _support_case_caps

        caps = _support_case_caps()
        assert caps["maxOpenSupportCases"] == MAX_OPEN_CASES_PER_TENANT

    def test_cap_is_ten(self):
        """Guard the documented number — the proposal and the pricing page
        both quote it."""
        assert MAX_OPEN_CASES_PER_TENANT == 10

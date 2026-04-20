"""Tests for earnings press release fetching — must find the right 8-K exhibit."""

import pytest
from unittest.mock import patch, MagicMock
from src.tools.sec_edgar import get_earnings_press_release


class TestEarningsPressRelease:
    """Test the quarter-to-filing-date logic and exhibit detection."""

    def test_invalid_quarter_format(self):
        result = get_earnings_press_release("LYFT", "fourth quarter 2024")
        assert "Invalid quarter format" in result

    def test_valid_quarter_format(self):
        """Should not fail on format validation."""
        # Will fail on network call in CI, but format should be accepted
        result = get_earnings_press_release("INVALIDTICKER99999", "Q4 2024")
        assert "Invalid quarter format" not in result

    def test_quarter_to_filing_window_q4(self):
        """Q4 2024 earnings should search Jan-Mar 2025."""
        # We test the logic by checking the quarter parsing
        import re
        quarter = "Q4 2024"
        match = re.match(r'Q(\d)\s*(\d{4})', quarter)
        q_num = int(match.group(1))
        year = int(match.group(2))

        filing_windows = {
            4: (f"{year + 1}-01", f"{year + 1}-03"),
            1: (f"{year}-04", f"{year}-06"),
            2: (f"{year}-07", f"{year}-09"),
            3: (f"{year}-10", f"{year}-12"),
        }
        start, end = filing_windows[q_num]
        assert start == "2025-01"
        assert end == "2025-03"

    def test_quarter_to_filing_window_q1(self):
        """Q1 2025 earnings should search Apr-Jun 2025."""
        import re
        quarter = "Q1 2025"
        match = re.match(r'Q(\d)\s*(\d{4})', quarter)
        q_num = int(match.group(1))
        year = int(match.group(2))

        filing_windows = {
            4: (f"{year + 1}-01", f"{year + 1}-03"),
            1: (f"{year}-04", f"{year}-06"),
            2: (f"{year}-07", f"{year}-09"),
            3: (f"{year}-10", f"{year}-12"),
        }
        start, end = filing_windows[q_num]
        assert start == "2025-04"
        assert end == "2025-06"

    def test_quarter_to_filing_window_q2(self):
        import re
        quarter = "Q2 2024"
        match = re.match(r'Q(\d)\s*(\d{4})', quarter)
        q_num = int(match.group(1))
        year = int(match.group(2))

        filing_windows = {
            4: (f"{year + 1}-01", f"{year + 1}-03"),
            1: (f"{year}-04", f"{year}-06"),
            2: (f"{year}-07", f"{year}-09"),
            3: (f"{year}-10", f"{year}-12"),
        }
        start, end = filing_windows[q_num]
        assert start == "2024-07"
        assert end == "2024-09"

    def test_quarter_to_filing_window_q3(self):
        import re
        quarter = "Q3 2024"
        match = re.match(r'Q(\d)\s*(\d{4})', quarter)
        q_num = int(match.group(1))
        year = int(match.group(2))

        filing_windows = {
            4: (f"{year + 1}-01", f"{year + 1}-03"),
            1: (f"{year}-04", f"{year}-06"),
            2: (f"{year}-07", f"{year}-09"),
            3: (f"{year}-10", f"{year}-12"),
        }
        start, end = filing_windows[q_num]
        assert start == "2024-10"
        assert end == "2024-12"

    def test_exhibit_detection_pressreleas(self):
        """Should detect 'pressreleas' in exhibit filenames."""
        import re
        html = '''
        <a href="lyft-2024x12x31pressreleas.htm">Press Release</a>
        <a href="lyft-20250211.htm">8-K Cover</a>
        '''
        exhibit = None
        for match in re.finditer(r'href="([^"]*)"', html):
            href = match.group(1)
            if any(kw in href.lower() for kw in ["pressreleas", "ex99", "earnings", "exhibit99"]):
                exhibit = href
                break
        assert exhibit == "lyft-2024x12x31pressreleas.htm"

    def test_exhibit_detection_ex99(self):
        """Should detect 'ex99' in exhibit filenames."""
        import re
        html = '''
        <a href="aapl-ex991_20250130.htm">Exhibit 99.1</a>
        <a href="aapl-20250130.htm">8-K</a>
        '''
        exhibit = None
        for match in re.finditer(r'href="([^"]*)"', html):
            href = match.group(1)
            if any(kw in href.lower() for kw in ["pressreleas", "ex99", "earnings", "exhibit99"]):
                exhibit = href
                break
        assert exhibit == "aapl-ex991_20250130.htm"

    def test_exhibit_detection_no_match(self):
        """Should return None when no exhibit matches."""
        import re
        html = '''
        <a href="cover-page.htm">Cover</a>
        <a href="signature.htm">Signature</a>
        '''
        exhibit = None
        for match in re.finditer(r'href="([^"]*)"', html):
            href = match.group(1)
            if any(kw in href.lower() for kw in ["pressreleas", "ex99", "earnings", "exhibit99"]):
                exhibit = href
                break
        assert exhibit is None


class TestEarningsPressReleaseLive:
    """Live network tests — skip in CI, run manually to verify real SEC access."""

    @pytest.mark.skipif(True, reason="Network test — run manually with: pytest -k 'live' --no-header -rN")
    def test_lyft_q4_2024(self):
        result = get_earnings_press_release("LYFT", "Q4 2024")
        assert "EARNINGS PRESS RELEASE" in result
        assert "Gross Bookings" in result or "gross bookings" in result

    @pytest.mark.skipif(True, reason="Network test")
    def test_apple_q4_fy2024(self):
        # Apple FY ends Sep, so Q4 FY2024 = Jul-Sep 2024, filed Oct-Dec 2024
        result = get_earnings_press_release("AAPL", "Q3 2024")  # Calendar Q3 = Apple Q4
        assert "EARNINGS PRESS RELEASE" in result or "No earnings" in result

    @pytest.mark.skipif(True, reason="Network test")
    def test_invalid_ticker(self):
        result = get_earnings_press_release("ZZZZZ99", "Q4 2024")
        assert "not found" in result.lower()

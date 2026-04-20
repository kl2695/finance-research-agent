"""Tests for ticker extraction — must handle messy planner output."""

import pytest
from src.ticker import extract_ticker


class TestExtractTicker:
    # Basic formats
    def test_ticker_keyword(self):
        assert extract_ticker("Lyft, Inc., ticker LYFT") == "LYFT"

    def test_ticker_with_colon(self):
        assert extract_ticker("ticker: AAPL") == "AAPL"

    def test_exchange_prefix_nasdaq(self):
        assert extract_ticker("NASDAQ: LYFT") == "LYFT"

    def test_exchange_prefix_nyse(self):
        assert extract_ticker("NYSE: X") == "X"

    def test_parenthetical(self):
        assert extract_ticker("Barrett Business Services (BBSI)") == "BBSI"

    def test_parenthetical_with_exchange(self):
        assert extract_ticker("Lyft Inc (NASDAQ: LYFT)") == "LYFT"

    # Messy planner output
    def test_planner_output_with_comma(self):
        assert extract_ticker("Lyft, Inc., ticker LYFT, CIK 0001759509") == "LYFT"

    def test_planner_output_full(self):
        assert extract_ticker("United States Steel Corp, ticker X, CIK 0001163302") == "X"

    def test_planner_output_with_exchange(self):
        assert extract_ticker("Apple Inc. (NASDAQ: AAPL), Cupertino, CA") == "AAPL"

    # Edge cases
    def test_single_letter_ticker(self):
        assert extract_ticker("ticker X") == "X"

    def test_two_letter_ticker(self):
        assert extract_ticker("ticker MU") == "MU"

    def test_ignores_excluded_words(self):
        assert extract_ticker("The SEC filed a FORM for INC") is None

    def test_returns_none_for_empty(self):
        assert extract_ticker("") is None
        assert extract_ticker(None) is None

    def test_ignores_cik(self):
        assert extract_ticker("CIK 0001759509") is None

    # Common FAB companies
    def test_apple(self):
        assert extract_ticker("Apple Inc., ticker AAPL") == "AAPL"

    def test_meta(self):
        assert extract_ticker("Meta Platforms, Inc. (NASDAQ: META)") == "META"

    def test_us_steel(self):
        assert extract_ticker("United States Steel Corporation, ticker X") == "X"

    def test_lyft(self):
        assert extract_ticker("Lyft, Inc. (NASDAQ: LYFT)") == "LYFT"

    def test_netflix(self):
        assert extract_ticker("Netflix, Inc., ticker NFLX") == "NFLX"

    def test_airbnb(self):
        assert extract_ticker("Airbnb, Inc. (NASDAQ: ABNB)") == "ABNB"

    def test_palantir(self):
        assert extract_ticker("Palantir Technologies Inc. (NYSE: PLTR)") == "PLTR"

    def test_tjx(self):
        assert extract_ticker("The TJX Companies, Inc., ticker TJX") == "TJX"

    def test_cloudflare(self):
        assert extract_ticker("Cloudflare, Inc. (NYSE: NET)") == "NET"

    def test_uber(self):
        assert extract_ticker("Uber Technologies, Inc. (NYSE: UBER)") == "UBER"

    def test_amd(self):
        assert extract_ticker("Advanced Micro Devices, Inc. (NASDAQ: AMD)") == "AMD"

    def test_micron(self):
        assert extract_ticker("Micron Technology, Inc., ticker MU") == "MU"

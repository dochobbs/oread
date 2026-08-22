"""
Shared pytest configuration for Oread.

Makes the suite hermetic: all tests run with the LLM disabled unless a test
explicitly opts in, so no unit test ever makes a paid network call or hangs
on API latency.
"""

import os

# Must be set before any src module is imported by test modules.
os.environ.setdefault("OREAD_DISABLE_LLM", "1")

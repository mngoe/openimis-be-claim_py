"""
Tests for backend/features/cache-performance.feature, claim part (open/postponed performance work).
"""
import unittest

from django.test import TestCase


class ClaimPerformanceTest(TestCase):
    # Scenario: invoice_csu_query avoids N+1 queries (@redmine-33807)
    @unittest.skip("postponed: #33807")
    def test_invoice_csu_query_no_n_plus_one(self):
        pass

    # Scenario: The claim count query is fast (@redmine-36724)
    @unittest.skip("postponed: #36724")
    def test_claim_count_query(self):
        pass

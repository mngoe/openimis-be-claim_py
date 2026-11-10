"""
Tests for backend/features/program-cheque-sante.feature, claim module part (pregnancy age query).
"""
import datetime
from unittest import mock

from django.test import TestCase

from insuree.test_helpers import create_test_insuree
from policy.models import Policy
from policy.test_helpers import create_test_policy
from product.test_helpers import create_test_product

GAP = "the pregnancyAge query (#35111, claim PR 154-159) is not in this checkout: it is on origin/dev-csu"


class PregnancyAgeQueryTest(TestCase):
    def _resolve(self, start, recorded, claim_to, status=Policy.STATUS_ACTIVE):
        from claim import schema
        resolver = getattr(schema.Query, "resolve_pregnancy_age", None)
        self.assertIsNotNone(resolver, GAP)
        insuree = create_test_insuree()
        product = create_test_product("FCSPQ")
        policy = create_test_policy(product, insuree)
        Policy.objects.filter(id=policy.id).update(
            status=status, start_date=start, expiry_date=datetime.date(2026, 12, 31), pregnancy_age=recorded)
        info = mock.Mock()
        info.context.user.has_perms = mock.MagicMock(return_value=True)
        result = resolver(None, info, claim_date_to=claim_to, family_id=insuree.family.id, product=product.id)
        return result.pregnancy_age

    # Scenario: The pregnancy age at claim time adds the weeks since the policy start (@redmine-35111 @redmine-35112)
    def test_pregnancy_age_projection(self):
        cases = [
            (datetime.date(2026, 1, 1), 4, datetime.datetime(2026, 1, 19), 7),
            (datetime.date(2026, 1, 1), 10, datetime.datetime(2026, 2, 5), 15),
        ]
        for start, recorded, claim_to, expected in cases:
            with self.subTest(claim_to=claim_to):
                self.assertEqual(self._resolve(start, recorded, claim_to), expected)

    # Scenario: No pregnancy age without an active policy that records it (@redmine-35111)
    def test_no_pregnancy_age_without_policy_value(self):
        self.assertIsNone(self._resolve(datetime.date(2026, 1, 1), None, datetime.datetime(2026, 2, 5)))

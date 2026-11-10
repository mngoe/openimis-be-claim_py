"""
Tests for backend/features/user-rights-locations.feature, claim part (bulk mutations and location scope).
Fixture as in claim/tests/test_security_parity.py: two disjoint branches, user granted district 1 only.
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from claim.apps import ClaimConfig
from claim.gql_queries import ClaimGQLType
from claim.models import Claim
from claim.test_helpers import create_test_claim
from core.gql.gql_mutations import mutation_on_uuids_from_filter
from core.test_helpers import create_security_tree, create_right_only_user, perm_ids
from insuree.test_helpers import create_test_insuree
from medical.test_helpers import create_test_diagnosis
from program.test_helpers import create_test_program


@override_settings(ROW_SECURITY=True)
class BulkClaimLocationScopeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        t = cls.t = create_security_tree("FURL")
        from medical.models import Diagnosis
        icd = Diagnosis.objects.filter(validity_to__isnull=True).first() or create_test_diagnosis({"code": "FURLD"})
        program = create_test_program(code="FURLP", name="FURL program")
        ins_a = create_test_insuree(custom_props={"current_village": t.v1, "health_facility": t.hf1},
                                    family_custom_props={"location": t.v1})
        ins_b = create_test_insuree(custom_props={"current_village": t.v2, "health_facility": t.hf2},
                                    family_custom_props={"location": t.v2})

        def claim(code, hf, ins):
            return create_test_claim(custom_props={
                "code": code, "health_facility_id": hf.id, "insuree_id": ins.id, "icd_id": icd.id,
                "program": program, "status": Claim.STATUS_ENTERED})
        cls.claim_x = claim("FURL-X", t.hf1, ins_a)
        cls.claim_y = claim("FURL-Y", t.hf2, ins_b)
        rights = perm_ids(ClaimConfig.gql_query_claims_perms + ClaimConfig.gql_mutation_submit_claims_perms)
        cls.user_x = create_right_only_user("furlusera", sorted(set(rights)), [t.d1.id])

    def setUp(self):
        cache.clear()

    def _client_mutation_id(self):
        import uuid
        from core.models import MutationLog
        cmid = str(uuid.uuid4())
        MutationLog.objects.create(json_content="{}", client_mutation_id=cmid)
        return cmid

    # Scenario: "Select all" collects only the claims of the user's districts (@redmine-36951 @redmine-36952)
    def test_select_all_collects_only_user_district_claims(self):
        collected = {}

        @mutation_on_uuids_from_filter(Claim, ClaimGQLType, "additional_filters")
        def fake_mutate(cls, user, **data):
            collected["uuids"] = [str(u).lower() for u in data["uuids"]]
            return []

        with mock.patch("core.utils.get_current_user", return_value=self.user_x, create=True):
            fake_mutate(None, self.user_x, additional_filters='{"code_Istartswith": "FURL-"}')
        self.assertIn(str(self.claim_x.uuid).lower(), collected["uuids"])
        self.assertNotIn(str(self.claim_y.uuid).lower(), collected["uuids"],
                         "mutation_on_uuids_from_filter does not apply get_queryset(base_query, user) "
                         "(core PR 62/63 not in this checkout)")

    # Scenario: "Select all" then "Submit" submits only the claims of the user's districts (@redmine-36951 @redmine-36954)
    def test_submit_all_keeps_other_district_claims(self):
        from claim.gql_mutations import SubmitClaimsMutation
        SubmitClaimsMutation.async_mutate(self.user_x, additional_filters='{"code_Istartswith": "FURL-"}',
                                          client_mutation_id=self._client_mutation_id())
        self.claim_y.refresh_from_db()
        self.assertEqual(self.claim_y.status, Claim.STATUS_ENTERED)

    # Scenario: Submitting a single claim by its uuid keeps working (@redmine-36951)
    def test_submit_single_claim_by_uuid(self):
        from claim.gql_mutations import SubmitClaimsMutation
        SubmitClaimsMutation.async_mutate(self.user_x, uuids=[str(self.claim_x.uuid)],
                                          client_mutation_id=self._client_mutation_id())
        current = Claim.objects.filter(code="FURL-X", validity_to__isnull=True).first()
        self.assertNotEqual(current.status, Claim.STATUS_ENTERED)

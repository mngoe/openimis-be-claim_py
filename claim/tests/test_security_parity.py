"""
Security parity tests for claim (row scope + API rights) against the openimis-dev-tools
reference (claim/claim/tests/test_claim_row_security.py, release/26.10).

Each test asserts the REFERENCE behaviour: a FAIL is a gap in the Cameroon code.
Tags: [REF <file>::<test>] ported from the reference, [NEW] no reference equivalent.

Fixture: two disjoint branches (R1>D1>W1>V1 + HF1, R2>D2>W2>V2 + HF2). `user_a` is a
non-superuser holding the claim rights and granted D1 only, assigned to NO program, so
Claim.get_queryset's program filter is inert and any leak is attributable to location
scope. Every *_b object lives on branch 2.
"""
import json
import uuid as uuid_lib

from django.conf import settings
from django.core.cache import cache
from django.test import Client, override_settings

from claim.apps import ClaimConfig
from claim.models import Claim, ClaimAttachment, ClaimAdmin, Feedback, FeedbackPrompt
from claim.test_helpers import create_test_claim
from core.models import Officer
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import (
    create_security_tree,
    create_right_only_user,
    create_test_interactive_user,
    create_test_role,
    get_test_token,
    perm_ids,
)
from insuree.test_helpers import create_test_insuree
from medical.test_helpers import create_test_diagnosis

CLAIM_QUERY = 'query { claim(uuid: "%s") { code } }'
CLAIMS_QUERY = 'query { claims(code_Istartswith: "SPCL-") { edges { node { code } } } }'
ATTACHMENTS_QUERY = 'query { claimAttachments%s { edges { node { title } } } }'


def _claim(code, hf, insuree, icd, **extra):
    return create_test_claim(custom_props={
        "code": code,
        "health_facility_id": hf.id,
        "insuree_id": insuree.id,
        "icd_id": icd.id,
        **extra,
    })


def _attachment(claim, title):
    return ClaimAttachment.objects.create(
        claim=claim, title=title, filename=f"{title}.txt", mime="text/plain",
        document="aGVsbG8=",  # b64 "hello"
    )


@override_settings(ROW_SECURITY=True)
class ClaimRowSecurityParityTests(openIMISGraphQLTestCase):
    @classmethod
    def setUpTestData(cls):
        t = cls.t = create_security_tree("SPCL")
        from medical.models import Diagnosis
        cls.icd = Diagnosis.objects.filter(validity_to__isnull=True).first() or create_test_diagnosis({"code": "SPCLD"})
        cls.ins_a = create_test_insuree(custom_props={"current_village": t.v1, "health_facility": t.hf1},
                                        family_custom_props={"location": t.v1})
        cls.ins_b = create_test_insuree(custom_props={"current_village": t.v2, "health_facility": t.hf2},
                                        family_custom_props={"location": t.v2})
        # claims require a program; it is assigned to NO user, so the program filter of
        # Claim.get_queryset stays inert for user_a and only location scope applies
        from program.test_helpers import create_test_program
        cls.program = create_test_program(code="SPCLP", name="SPCL program")
        cls.claim_a = _claim("SPCL-A", t.hf1, cls.ins_a, cls.icd, program=cls.program)
        cls.claim_b = _claim("SPCL-B", t.hf2, cls.ins_b, cls.icd, program=cls.program)
        cls.att_a = _attachment(cls.claim_a, "SPCL-ATT-A")
        cls.att_b = _attachment(cls.claim_b, "SPCL-ATT-B")

        rights = perm_ids(
            ClaimConfig.gql_query_claims_perms
            + ClaimConfig.gql_mutation_create_claims_perms
            + ClaimConfig.gql_mutation_update_claims_perms
            + ClaimConfig.gql_mutation_delete_claims_perms
            + ClaimConfig.gql_mutation_process_claims_perms
            + ClaimConfig.gql_mutation_submit_claims_perms
            + ClaimConfig.gql_mutation_select_claim_review_perms
            + ClaimConfig.gql_mutation_deliver_claim_review_perms
            + ClaimConfig.gql_query_claim_officers_perms
            + ClaimConfig.gql_query_claim_admins_perms
        )
        cls.user_a = create_right_only_user("spclusera", sorted(set(rights)), [t.d1.id])
        cls.no_right = create_right_only_user("spclnoright", [], [t.d1.id])

        # claim admins / officers on both branches
        cls.ca_a = ClaimAdmin.objects.create(code="SPCLCA1", last_name="CA", other_names="A",
                                             health_facility=t.hf1, audit_user_id=-1,
                                             has_login=False, validity_from="2019-01-01")
        cls.ca_b = ClaimAdmin.objects.create(code="SPCLCA2", last_name="CA", other_names="B",
                                             health_facility=t.hf2, audit_user_id=-1,
                                             has_login=False, validity_from="2019-01-01")
        cls.off_a = Officer.objects.create(code="SPCLO1", last_name="Off", other_names="A",
                                           location=t.d1, audit_user_id=-1, has_login=False,
                                           validity_from="2019-01-01")
        cls.off_b = Officer.objects.create(code="SPCLO2", last_name="Off", other_names="B",
                                           location=t.d2, audit_user_id=-1, has_login=False,
                                           validity_from="2019-01-01")

    def setUp(self):
        cache.clear()

    # ------------------------------------------------------------------ helpers
    def _gql(self, user, query):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {get_test_token(user)}"} if user else {}
        response = self.query(query, headers=headers)
        return json.loads(response.content)

    def _claim_code(self, user, claim):
        content = self._gql(user, CLAIM_QUERY % claim.uuid)
        data = (content.get("data") or {}).get("claim")
        return data["code"] if data else None

    def _attachment_titles(self, user, arguments=""):
        content = self._gql(user, ATTACHMENTS_QUERY % arguments)
        self.assertIsNone(content.get("errors"), content.get("errors"))
        edges = content["data"]["claimAttachments"]["edges"]
        return {e["node"]["title"] for e in edges} & {"SPCL-ATT-A", "SPCL-ATT-B"}

    def _reload_b(self):
        return Claim.objects.get(id=self.claim_b.id)

    def _history_count_b(self):
        return Claim.objects.filter(legacy_id=self.claim_b.id).count()

    # ------------------------------------------------------------------ reads
    def test_claim_by_uuid_keeps_own_district(self):
        """[REF test_claim_row_security.py::test_claim_by_uuid_keeps_own_district]
        claim(uuid) of a claim on a foreign HF reads as null (IDOR)."""
        self.assertEqual(self._claim_code(self.user_a, self.claim_a), "SPCL-A")
        self.assertIsNone(self._claim_code(self.user_a, self.claim_b),
                          "claim(uuid) returned a claim outside the user's districts")

    def test_claim_by_id_keeps_own_district(self):
        """[REF test_claim_row_security.py::test_claim_by_id_keeps_own_district]"""
        content = self._gql(self.user_a, 'query { claim(id: %d) { code } }' % self.claim_b.id)
        self.assertIsNone((content.get("data") or {}).get("claim"),
                          "claim(id) returned a claim outside the user's districts")

    @override_settings(ROW_SECURITY=False)
    def test_claim_requires_right_without_row_security(self):
        """[REF test_claim_row_security.py::test_claim_requires_right_without_row_security]
        H05: the right is required whatever ROW_SECURITY says."""
        content = self._gql(self.no_right, CLAIM_QUERY % self.claim_a.uuid)
        self.assertTrue(content.get("errors"), "claim(uuid) answered a user without the claim right")

    @override_settings(ROW_SECURITY=False)
    def test_claims_require_right_without_row_security(self):
        """[REF test_claim_row_security.py::test_claims_and_history_require_right_without_row_security]"""
        self.assertTrue(self._gql(self.no_right, CLAIMS_QUERY).get("errors"))

    def test_claim_anonymous_refused(self):
        """[NEW] claim(uuid) without any token is refused."""
        content = self._gql(None, CLAIM_QUERY % self.claim_a.uuid)
        self.assertTrue(content.get("errors") or not (content.get("data") or {}).get("claim"))

    def test_claims_keep_own_district(self):
        """[REF test_claim_row_security.py::test_claims_keep_own_district]"""
        content = self._gql(self.user_a, CLAIMS_QUERY)
        self.assertIsNone(content.get("errors"), content.get("errors"))
        codes = {e["node"]["code"] for e in content["data"]["claims"]["edges"]}
        self.assertEqual(codes, {"SPCL-A"})

    def test_attachments_keep_own_district(self):
        """[REF test_claim_row_security.py::test_attachments_keep_own_district]"""
        self.assertEqual(self._attachment_titles(self.user_a), {"SPCL-ATT-A"})

    def test_attachments_filtered_on_foreign_claim_return_nothing(self):
        """[REF test_claim_row_security.py::test_attachments_filtered_on_foreign_claim_return_nothing]"""
        arguments = '(claim_Uuid: "%s")' % self.claim_b.uuid
        self.assertEqual(self._attachment_titles(self.user_a, arguments), set())

    def test_model_scope_follows_the_claim(self):
        """[REF test_claim_row_security.py::test_model_scope_follows_the_claim]
        ClaimAttachment.get_queryset must follow the parent claim's scope
        (reference: row_scope = ParentScope('claim'))."""
        if not callable(getattr(ClaimAttachment, "get_queryset", None)):
            self.fail("ClaimAttachment has no get_queryset: attachments carry no row scope")
        titles = set(
            ClaimAttachment.get_queryset(ClaimAttachment.objects.all(), self.user_a)
            .filter(title__startswith="SPCL-ATT-")
            .values_list("title", flat=True)
        )
        self.assertEqual(titles, {"SPCL-ATT-A"})

    def test_hf_bound_user_sees_only_own_hf(self):
        """[REF core/tests/test_gql_user_hf_location_filters.py] A user bound to a health
        facility only sees that facility's claims even if granted both districts."""
        role = create_test_role(name="SPCL hf role", rights=perm_ids(ClaimConfig.gql_query_claims_perms))
        user = create_test_interactive_user(username="spclhfuser", roles=[role.id],
                                            custom_props={"health_facility_id": self.t.hf1.id})
        from core.services.userServices import create_or_update_user_districts
        create_or_update_user_districts(user.i_user, [self.t.d1.id, self.t.d2.id], -1)
        cache.clear()
        codes = set(Claim.get_queryset(Claim.objects.filter(code__startswith="SPCL-"), user)
                    .values_list("code", flat=True))
        self.assertEqual(codes, {"SPCL-A"})

    def test_feedback_scope_does_not_crash(self):
        """[REF claim/models.py Feedback.get_queryset — reference bug noted too]
        Feedback/FeedbackPrompt get_queryset for a restricted user must return a scoped
        queryset, not raise FieldError (wrong prefix 'health_facility__location')."""
        for model in (Feedback, FeedbackPrompt):
            with self.subTest(model=model.__name__):
                try:
                    list(model.get_queryset(model.objects.all(), self.user_a)[:1])
                except Exception as exc:  # FieldError is the finding
                    self.fail(f"{model.__name__}.get_queryset crashed for a district user: {exc!r}")

    def test_claim_admins_keep_own_district(self):
        """[REF core/tests/test_claim_admin_row_security.py] claimAdmins only within districts."""
        content = self._gql(self.user_a, 'query { claimAdmins(search: "SPCLCA") { edges { node { code } } } }')
        self.assertIsNone(content.get("errors"), content.get("errors"))
        codes = {e["node"]["code"] for e in content["data"]["claimAdmins"]["edges"]}
        self.assertEqual(codes, {"SPCLCA1"})

    def test_claim_officers_keep_own_district(self):
        """[NEW] claimOfficers lists only officers of the user's districts."""
        content = self._gql(self.user_a, 'query { claimOfficers(search: "SPCLO") { edges { node { code } } } }')
        self.assertIsNone(content.get("errors"), content.get("errors"))
        codes = {e["node"]["code"] for e in content["data"]["claimOfficers"]["edges"]}
        self.assertEqual(codes, {"SPCLO1"})

    def test_fsp_from_claim_keeps_own_district(self):
        """[NEW] fspFromClaim must not disclose the FSP of an insuree outside the user's
        districts."""
        q = 'query { fspFromClaim(insureeCode: "%s", dateClaimed: "2020-01-01") { code } }' % self.ins_b.chf_id
        content = self._gql(self.user_a, q)
        data = (content.get("data") or {}).get("fspFromClaim")
        self.assertFalse(data and data.get("code") == "SPCLHF2",
                         "fspFromClaim disclosed the HF of a foreign insuree")

    # ------------------------------------------------------------------ attach view
    def _download(self, attachment, user=None):
        client = Client(raise_request_exception=False)
        if user is not None:
            client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        return client.get(f"/{settings.SITE_ROOT()}claim/attach/", {"id": str(attachment.id)})

    def test_download_anonymous_refused(self):
        """[NEW] claim/attach/ without authentication answers 401/403 (not 200, not 500)."""
        status = self._download(self.att_a).status_code
        self.assertIn(status, (401, 403), f"anonymous attach download answered {status}")

    def test_download_keeps_own_district(self):
        """[REF test_claim_row_security.py::test_download_keeps_own_district]"""
        own = self._download(self.att_a, self.user_a).status_code
        foreign = self._download(self.att_b, self.user_a).status_code
        self.assertEqual((own, foreign), (200, 403),
                         f"attach download own={own} foreign={foreign}")

    def test_download_requires_right(self):
        """[REF claim/views.py attach: checkUserWithRights(gql_query_claims_perms)]"""
        status = self._download(self.att_a, self.no_right).status_code
        self.assertIn(status, (401, 403), f"attach download without right answered {status}")

    # ------------------------------------------------------------------ writes
    def test_delete_refuses_foreign_attachment(self):
        """[REF test_claim_row_security.py::test_delete_refuses_foreign_attachment]"""
        from claim.gql_mutations import DeleteAttachmentMutation
        try:
            DeleteAttachmentMutation.async_mutate(self.user_a, id=str(self.att_b.id))
        except Exception as exc:
            self.fail(f"DeleteAttachment crashed instead of refusing cleanly "
                      f"(scope check broken, also blocks own attachments): {exc!r}"[:300])
        self.assertTrue(ClaimAttachment.objects.filter(id=self.att_b.id, validity_to__isnull=True).exists())

    def test_delete_refuses_foreign_claim(self):
        """[REF test_claim_row_security.py::test_delete_refuses_foreign_claim]"""
        from claim.gql_mutations import DeleteClaimsMutation
        DeleteClaimsMutation.async_mutate(self.user_a, uuids=[self.claim_b.uuid])
        self.assertIsNone(self._reload_b().validity_to, "a claim outside the user's districts was deleted")

    def test_process_refuses_foreign_claim(self):
        """[REF test_claim_row_security.py::test_process_refuses_foreign_claim]
        Outcome: no history row written / status unchanged on the foreign claim."""
        from claim.gql_mutations import ProcessClaimsMutation
        from core.models import MutationLog
        before = (self._reload_b().status, self._history_count_b())
        MutationLog.objects.create(json_content="{}", client_mutation_id="spcl-process")
        try:
            ProcessClaimsMutation.async_mutate(self.user_a, uuids=[self.claim_b.uuid],
                                               client_mutation_id="spcl-process")
        except Exception:
            pass
        after = (self._reload_b().status, self._history_count_b())
        self.assertEqual(before, after, "processing touched a claim outside the user's districts")

    def test_status_change_refuses_foreign_claim(self):
        """[REF test_claim_row_security.py::test_status_change_refuses_foreign_claim]"""
        from claim.gql_mutations import SelectClaimsForReviewMutation
        before = self._reload_b().review_status
        SelectClaimsForReviewMutation.async_mutate(self.user_a, uuids=[self.claim_b.uuid])
        self.assertEqual(self._reload_b().review_status, before)

    def test_save_review_refuses_foreign_claim(self):
        """[REF test_claim_row_security.py::test_save_review_refuses_foreign_claim]"""
        from claim.gql_mutations import SaveClaimReviewMutation
        before = self._history_count_b()
        try:
            SaveClaimReviewMutation.async_mutate(self.user_a, claim_uuid=self.claim_b.uuid,
                                                 items=[], services=[])
        except Exception:
            pass
        self.assertEqual(self._history_count_b(), before, "review saved on a foreign claim")

    def test_update_refuses_foreign_claim(self):
        """[REF test_claim_row_security.py::test_update_refuses_foreign_claim]"""
        from claim.gql_mutations import UpdateClaimMutation
        UpdateClaimMutation.async_mutate(self.user_a, uuid=self.claim_b.uuid, code="SPCL-HACK",
                                         health_facility_id=self.t.hf2.id,
                                         insuree_id=self.ins_b.id, icd_id=self.icd.id,
                                         date_from="2019-06-01", date_claimed="2019-06-01",
                                         program=self.program.idProgram)
        self.assertEqual(self._reload_b().code, "SPCL-B", "a foreign claim was updated")

    def test_update_control_own_claim(self):
        """[CONTROL] the same update call succeeds on the user's own claim, so a PASS of
        test_update_refuses_foreign_claim is attributable to scope, not to bad input."""
        from claim.gql_mutations import UpdateClaimMutation
        errors = UpdateClaimMutation.async_mutate(self.user_a, uuid=self.claim_a.uuid, code="SPCL-A2",
                                                  health_facility_id=self.t.hf1.id,
                                                  insuree_id=self.ins_a.id, icd_id=self.icd.id,
                                                  date_from="2019-06-01", date_claimed="2019-06-01",
                                                  program=self.program.idProgram)
        self.assertEqual(Claim.objects.get(id=self.claim_a.id).code, "SPCL-A2", errors)

    def test_create_control_own_hf(self):
        """[CONTROL] the same create call succeeds on the user's own HF."""
        from claim.gql_mutations import CreateClaimMutation
        errors = CreateClaimMutation.async_mutate(
            self.user_a, code="SPCL-NEWA", health_facility_id=self.t.hf1.id,
            insuree_id=self.ins_a.id, icd_id=self.icd.id,
            date_from="2019-06-01", date_claimed="2019-06-01", program=self.program.idProgram)
        self.assertTrue(Claim.objects.filter(code="SPCL-NEWA").exists(), errors)

    def test_submit_refuses_foreign_claim(self):
        """[NEW] (reference: SubmitClaimsMutation reads Claim.get_queryset)"""
        from claim.gql_mutations import SubmitClaimsMutation
        from core.models import MutationLog
        before = (self._reload_b().status, self._history_count_b())
        MutationLog.objects.create(json_content="{}", client_mutation_id="spcl-submit")
        try:
            SubmitClaimsMutation.async_mutate(self.user_a, uuids=[self.claim_b.uuid],
                                              client_mutation_id="spcl-submit")
        except Exception:
            pass
        self.assertEqual((self._reload_b().status, self._history_count_b()), before,
                         "submit touched a claim outside the user's districts")

    def test_create_refuses_foreign_hf(self):
        """[NEW] (reference: save_claim / _validate_user_hf) creating a claim on a HF
        outside the user's districts is refused."""
        from claim.gql_mutations import CreateClaimMutation
        CreateClaimMutation.async_mutate(
            self.user_a, code="SPCL-NEWB", health_facility_id=self.t.hf2.id,
            insuree_id=self.ins_b.id, icd_id=self.icd.id,
            date_from="2019-06-01", date_claimed="2019-06-01", program=self.program.idProgram)
        self.assertFalse(Claim.objects.filter(code="SPCL-NEWB").exists(),
                         "a claim was created on a HF outside the user's districts")

    def test_mutation_requires_right(self):
        """[REF claim mutations has_perms] a user without rights cannot delete."""
        from claim.gql_mutations import DeleteClaimsMutation
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            DeleteClaimsMutation.async_mutate(self.no_right, uuids=[self.claim_a.uuid])

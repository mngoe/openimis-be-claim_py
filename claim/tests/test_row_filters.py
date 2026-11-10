"""get_queryset honours the OP-2029 validity contract (epic OP-3317, phase 1),
ported from the openIMIS reference (claim feature/OP-3317). Cameroon claims
need a health facility, an insuree, a diagnosis and a program, so the
fixture builds them instead of relying on seeded ids."""

from django.test import TestCase

from core.test_helpers import (
    assert_row_filters,
    create_security_tree,
    create_test_interactive_user,
    mark_deleted,
)
from claim.models import Claim, ClaimAttachment
from claim.test_helpers import create_test_claim
from insuree.test_helpers import create_test_insuree


class ClaimRowFiltersTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from medical.models import Diagnosis
        from program.test_helpers import create_test_program

        cls.user = create_test_interactive_user(username="claimrowfilters")
        tree = create_security_tree("CRF")
        insuree = create_test_insuree(custom_props={"current_village": tree.v1},
                                      family_custom_props={"location": tree.v1})
        cls.props = {
            "health_facility_id": tree.hf1.id,
            "insuree_id": insuree.id,
            "icd_id": Diagnosis.objects.filter(validity_to__isnull=True).first().id,
            "program": create_test_program(code="CRFP", name="row filters"),
        }

    def _claim(self, code):
        return create_test_claim(custom_props={"code": code, **self.props})

    def test_claim(self):
        live = self._claim("CRF-LIVE")
        deleted = mark_deleted(self._claim("CRF-DEL"))
        assert_row_filters(
            self, Claim, Claim.objects.filter(id__in=[live.id, deleted.id]), self.user,
            live.id, deleted.id, default_hides_deleted=True,
        )

    def test_claim_audit_copies_come_with_audits(self):
        from core.models.row_filters import Audits

        claim = self._claim("CRF-AUD")
        copy_id = claim.save_history()
        rows = Claim.objects.filter(code="CRF-AUD")
        self.assertNotIn(copy_id, Claim.get_queryset(rows, self.user).values_list("id", flat=True))
        self.assertIn(copy_id, Claim.get_queryset(rows, self.user, audits=Audits.ALL)
                      .values_list("id", flat=True))

    def test_attachment_follows_the_claim(self):
        live = self._claim("CRF-ATT")
        attachment = ClaimAttachment.objects.create(claim=live, title="CRF", filename="crf.txt")
        self.assertIn(attachment.id, ClaimAttachment.get_queryset(
            ClaimAttachment.objects.all(), self.user).values_list("id", flat=True))

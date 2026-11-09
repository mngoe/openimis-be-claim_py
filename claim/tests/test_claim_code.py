import datetime
from unittest import mock
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase

from claim.apps import ClaimConfig
from claim.models import Claim
from claim.services import (
    assign_claim_code,
    claim_create,
    generate_claim_code,
    is_cheque_sante_program,
    should_autogenerate_claim_code,
)
from claim.test_helpers import DummyUser, create_test_claim
from insuree.test_helpers import create_test_insuree
from location.test_helpers import create_test_health_facility, create_test_location
from medical.test_helpers import create_test_diagnosis
from program.test_helpers import create_test_program


class ClaimCodeNumberingTestCase(TestCase):
    """D34 claim numbering: `<HF code>.<program code>.<6-digit sequence>` for claims outside Chèque Santé."""

    @classmethod
    def setUpTestData(cls):
        location = create_test_location('D')
        cls.hf = create_test_health_facility('HFAAA', location.id, valid=True)
        cls.other_hf = create_test_health_facility('HFBBB', location.id, valid=True)
        cls.program = create_test_program(code="VIH", name="VIH")
        cls.cs_program = create_test_program(code="CS", name="Programme CS")
        cls.cs_named_program = create_test_program(code="CHQ", name="Chèque Santé")
        cls.insuree = create_test_insuree()
        cls.icd = create_test_diagnosis()
        cls.user = DummyUser()

    def _existing_claim(self, code, hf=None, valid=True):
        claim = create_test_claim({
            "code": code,
            "health_facility_id": (hf or self.hf).id,
            "insuree": self.insuree,
            "icd": self.icd,
        })
        if not valid:
            Claim.objects.filter(id=claim.id).update(validity_to=datetime.datetime.now())
        return claim

    def _new_claim_data(self, program=None, code="AUTO", **extra):
        now = datetime.datetime.now()
        return {
            "code": code,
            "date_from": now - datetime.timedelta(days=2),
            "date_claimed": now - datetime.timedelta(days=1),
            "date_to": None,
            "status": Claim.STATUS_ENTERED,
            "validity_from": now,
            "program": program or self.program,
            "insuree": self.insuree,
            "health_facility": self.hf,
            "icd": self.icd,
            **extra,
        }

    def test_is_cheque_sante_program(self):
        self.assertTrue(is_cheque_sante_program(self.cs_program))
        self.assertTrue(is_cheque_sante_program(self.cs_named_program))
        self.assertTrue(is_cheque_sante_program(self.cs_named_program.idProgram))
        self.assertFalse(is_cheque_sante_program(self.program))
        self.assertFalse(is_cheque_sante_program(self.program.idProgram))
        self.assertFalse(is_cheque_sante_program(None))

    def test_should_autogenerate_claim_code(self):
        data = {"autogenerate": True, "source": Claim.SOURCE_WEB, "program": self.program}
        self.assertTrue(should_autogenerate_claim_code(data))
        self.assertTrue(should_autogenerate_claim_code({**data, "program": self.program.idProgram}))
        self.assertFalse(should_autogenerate_claim_code({**data, "autogenerate": False}))
        self.assertFalse(should_autogenerate_claim_code({**data, "source": Claim.SOURCE_MOBILE}))
        self.assertFalse(should_autogenerate_claim_code({**data, "restore": uuid4()}))
        self.assertFalse(should_autogenerate_claim_code({**data, "program": self.cs_program}))

    def test_generate_first_claim_code(self):
        self.assertEqual(generate_claim_code(self.hf, self.program), "HFAAA.VIH.000001")
        self.assertEqual(generate_claim_code(self.hf.id, self.program.idProgram), "HFAAA.VIH.000001")

    def test_generate_next_claim_code_per_facility_and_program(self):
        self._existing_claim("HFAAA.VIH.000007")
        self._existing_claim("HFAAA.VIH.12")  # not a generated number, ignored
        self._existing_claim("HFBBB.VIH.000050", hf=self.other_hf)  # other facility
        self._existing_claim("HFAAA.VIH.000099", valid=False)  # historical row
        self.assertEqual(generate_claim_code(self.hf, self.program), "HFAAA.VIH.000008")
        self.assertEqual(generate_claim_code(self.other_hf, self.program), "HFBBB.VIH.000051")

    def test_generate_claim_code_respects_max_length(self):
        with mock.patch.object(ClaimConfig, "max_claim_length", 10):
            with self.assertRaises(ValidationError):
                generate_claim_code(self.hf, self.program)

    def test_assign_claim_code_requires_manual_code_for_cheque_sante(self):
        with self.assertRaises(ValidationError):
            assign_claim_code({"program": self.cs_program, "health_facility": self.hf}, autogenerate=True)
        data = {"program": self.cs_program, "health_facility": self.hf, "code": "CS-MANUAL"}
        assign_claim_code(data, autogenerate=True)
        self.assertEqual(data["code"], "CS-MANUAL")

    def test_assign_claim_code_only_when_requested(self):
        data = {"program": self.program, "health_facility": self.hf, "code": "MANUAL"}
        assign_claim_code(data, autogenerate=False)
        self.assertEqual(data["code"], "MANUAL")
        assign_claim_code(data, autogenerate=True)
        self.assertEqual(data["code"], "HFAAA.VIH.000001")

    def test_claim_create_generates_code_for_direct_callers(self):
        claim = claim_create(self._new_claim_data(), self.user, autogenerate_code=True)
        self.assertEqual(claim.code, "HFAAA.VIH.000001")
        claim = claim_create(self._new_claim_data(autogenerate=True), self.user)
        self.assertEqual(claim.code, "HFAAA.VIH.000002")

    def test_claim_create_keeps_code_without_flag(self):
        claim = claim_create(self._new_claim_data(code="MANUAL-1"), self.user)
        self.assertEqual(claim.code, "MANUAL-1")

from django.test import TestCase
from unittest import mock

import datetime
from .services import *
import core


class ClaimSubmitServiceTestCase(TestCase):

    def test_minimal_item_claim_submit_xml(self):
        items = [
            ClaimItemSubmit(code='aa', quantity=2),
        ]
        item = "<Item><ItemCode>aa</ItemCode><ItemQuantity>2</ItemQuantity></Item>"

        claim = ClaimSubmit(
            date=core.datetime.date(2020, 1, 9),
            code="code_ABVC",
            icd_code="ICD_CODE_WWQ",
            total=334,
            start_date=core.datetime.date(2020, 1, 13),
            claim_admin_code='ADM_CODE_ADKJ',
            insuree_chf_id='CHFID_UUZIS',
            health_facility_code="HFCode_JQL",
            item_submits=items,
        )
        details = "<Details>"
        details = details + "<ClaimDate>09/01/2020</ClaimDate>"
        details = details + "<HFCode>HFCode_JQL</HFCode>"
        details = details + "<ClaimAdmin>ADM_CODE_ADKJ</ClaimAdmin>"
        details = details + "<ClaimCode>code_ABVC</ClaimCode>"
        details = details + "<CHFID>CHFID_UUZIS</CHFID>"
        details = details + "<StartDate>13/01/2020</StartDate>"
        details = details + "<ICDCode>ICD_CODE_WWQ</ICDCode>"
        details = details + "<Total>334</Total>"
        details = details + "</Details>"
        expected = "<Claim>%s<Items>%s</Items></Claim>" % (details, item)
        self.assertEquals(expected, claim.to_xml())

    def test_minimal_service_claim_submit_xml(self):
        services = [
            ClaimServiceSubmit(code='aa', quantity=2),
        ]
        service = "<Service><ServiceCode>aa</ServiceCode><ServiceQuantity>2</ServiceQuantity></Service>"

        claim = ClaimSubmit(
            date=core.datetime.date(2020, 1, 9),
            code="code_ABVC",
            icd_code="ICD_CODE_WWQ",
            total=334,
            start_date=core.datetime.date(2020, 1, 13),
            claim_admin_code='ADM_CODE_ADKJ',
            insuree_chf_id='CHFID_UUZIS',
            health_facility_code="HFCode_JQL",
            service_submits=services,
        )
        details = "<Details>"
        details = details + "<ClaimDate>09/01/2020</ClaimDate>"
        details = details + "<HFCode>HFCode_JQL</HFCode>"
        details = details + "<ClaimAdmin>ADM_CODE_ADKJ</ClaimAdmin>"
        details = details + "<ClaimCode>code_ABVC</ClaimCode>"
        details = details + "<CHFID>CHFID_UUZIS</CHFID>"
        details = details + "<StartDate>13/01/2020</StartDate>"
        details = details + "<ICDCode>ICD_CODE_WWQ</ICDCode>"
        details = details + "<Total>334</Total>"
        details = details + "</Details>"
        expected = "<Claim>%s<Services>%s</Services></Claim>" % (
            details, service)
        self.assertEquals(expected, claim.to_xml())

    def test_extended_claim_submit_xml(self):
        items = [
            ClaimItemSubmit(code='aa', quantity=2),
            ClaimItemSubmit(code='bb', quantity=1, price=12.3),
        ]
        item_a = "<Item><ItemCode>aa</ItemCode><ItemQuantity>2</ItemQuantity></Item>"
        item_b = "<Item><ItemCode>bb</ItemCode><ItemPrice>12.3</ItemPrice><ItemQuantity>1</ItemQuantity></Item>"

        services = [
            ClaimServiceSubmit(code='aa-serv', quantity=2),
            ClaimServiceSubmit(code='a<a\'-serv', quantity=1, price=35),
        ]
        service_a = "<Service><ServiceCode>aa-serv</ServiceCode><ServiceQuantity>2</ServiceQuantity></Service>"
        service_b = "<Service><ServiceCode>a&lt;a\'-serv</ServiceCode><ServicePrice>35</ServicePrice><ServiceQuantity>1</ServiceQuantity></Service>"

        claim = ClaimSubmit(
            date=core.datetime.date(2020, 1, 9),
            code="code_ABVC",
            icd_code="ICD_CODE_WWQ",
            total=334,
            start_date=core.datetime.date(2020, 1, 13),
            claim_admin_code='ADM_CODE_ADKJ',
            insuree_chf_id='CHFID_UUZIS',
            health_facility_code="HFCode_JQL",
            item_submits=items,
            service_submits=services
        )
        details = "<Details>"
        details = details + "<ClaimDate>09/01/2020</ClaimDate>"
        details = details + "<HFCode>HFCode_JQL</HFCode>"
        details = details + "<ClaimAdmin>ADM_CODE_ADKJ</ClaimAdmin>"
        details = details + "<ClaimCode>code_ABVC</ClaimCode>"
        details = details + "<CHFID>CHFID_UUZIS</CHFID>"
        details = details + "<StartDate>13/01/2020</StartDate>"
        details = details + "<ICDCode>ICD_CODE_WWQ</ICDCode>"
        details = details + "<Total>334</Total>"
        details = details + "</Details>"
        expected = "<Claim>%s" % details
        expected = expected + "<Items>%s%s</Items>" % (item_a, item_b)
        expected = expected + \
                   "<Services>%s%s</Services>" % (service_a, service_b)
        expected = expected + "</Claim>"
        self.assertEquals(expected, claim.to_xml())

    @mock.patch('django.db.connections')
    def test_claim_submit_error(self, mock_connections):
        if connection.vendor != 'mssql':
            self.skipTest("This test can only be executed for MSSQL database")
        with mock.patch("claim.services.ClaimSubmitService.hf_scope_check") as mock_security:
            mock_security.return_value = None
            query_result = [2]
            mock_connections.__getitem__.return_value.cursor.return_value \
                .__enter__.return_value.fetchone.return_value = query_result
            mock_user = mock.Mock(is_anonymous=False)
            mock_user.has_perm = mock.MagicMock(return_value=True)
            claim = ClaimSubmit(
                date=core.datetime.date(2020, 1, 9),
                code="code_ABVC",
                icd_code="ICD_CODE_WWQ",
                total=334,
                start_date=core.datetime.date(2020, 1, 13),
                claim_admin_code='ADM_CODE_ADKJ',
                insuree_chf_id='CHFID_UUZIS',
                health_facility_code="HFCode_JQL"
            )
            service = ClaimSubmitService(user=mock_user)
            with self.assertRaises(ClaimSubmitError) as cm:
                service.submit(claim)
            self.assertNotEqual(cm.exception.code, 0)

    def test_claim_submit_allgood_xml(self):
        with mock.patch("django.db.backends.utils.CursorWrapper") as mock_cursor:
            # required for all modules tests
            mock_cursor.return_value.description = None
            # required for claim module tests
            mock_cursor.return_value.__enter__.return_value.description = None
            with mock.patch("claim.services.ClaimSubmitService.hf_scope_check") as mock_security:
                mock_security.return_value = None
                mock_user = mock.Mock(is_anonymous=False)
                mock_user.has_perm = mock.MagicMock(return_value=True)
                claim = ClaimSubmit(
                    date=core.datetime.date(2020, 1, 9),
                    code="code_ABVC",
                    icd_code="ICD_CODE_WWQ",
                    total=334,
                    start_date=core.datetime.date(2020, 1, 13),
                    claim_admin_code='ADM_CODE_ADKJ',
                    insuree_chf_id='CHFID_UUZIS',
                    health_facility_code="HFCode_JQL"
                )
                service = ClaimSubmitService(user=mock_user)
                service.submit(claim)  # doesn't raise an error

    @mock.patch("claim.services.ClaimSubmitService._validate_user_hf")
    @mock.patch("claim.services.ClaimCreateService._validate_user_hf")
    def test_claim_enter_and_submit(self, check_hf_submit, check_hf_enter):
        check_hf_submit.return_value, check_hf_enter.return_value = True, True
        mock_user = mock.Mock(is_anonymous=False)
        mock_user.has_perm = mock.MagicMock(return_value=True)
        mock_user.id_for_audit = -1

        claim = self._get_test_dict()
        service = ClaimSubmitService(user=mock_user)
        submitted_claim = service.enter_and_submit(claim, False)
        expected_claimed = 2 * 7 * 11  # 2 provisions, both qty = 7, price asked == 11

        self.assertEqual(submitted_claim.status, Claim.STATUS_CHECKED)
        self.assertEqual(submitted_claim.approved, expected_claimed)
        self.assertEqual(submitted_claim.claimed, expected_claimed)
        self.assertEqual(submitted_claim.health_facility.id, 18)
        self.assertEqual(len(submitted_claim.items.all()), 1)
        self.assertEqual(len(submitted_claim.services.all()), 1)
        self.assertEqual(submitted_claim.audit_user_id, -1)
        self.assertTrue(submitted_claim.id is not None)

    @mock.patch("claim.services.ClaimSubmitService._validate_user_hf")
    @mock.patch("claim.services.ClaimCreateService._validate_user_hf")
    def test_claim_enter_duplicate_exception(self, check_hf_submit, check_hf_enter):
        check_hf_submit.return_value, check_hf_enter.return_value = True, True
        mock_user = mock.Mock(is_anonymous=False)
        mock_user.has_perm = mock.MagicMock(return_value=True)
        mock_user.id_for_audit = -1

        claim = self._get_test_dict()
        service = ClaimSubmitService(user=mock_user)

        service.enter_and_submit(claim, False)

        with self.assertRaises(ValidationError):
            service.enter_and_submit(claim, False)

    def _get_test_dict(self):
        return {
            "health_facility_id": 18, "icd_id": 116, "date_from": datetime.datetime(2019, 6, 1), "code": "CLCODE1",
            "date_claimed": datetime.datetime(2019, 6, 1), "date_to": datetime.datetime(2019, 6, 1),
            "audit_user_id": 1, "insuree_id": 2, "status": 2, "validity_from": datetime.datetime(2019, 6, 1),
            "items": [{
                "qty_provided": 7, "price_asked": 11, "item_id": 23, "status": 1, "availability": True,
                "validity_from": "2019-06-01", "validity_to": None, "audit_user_id": -1
            }],
            "services": [{
                "qty_provided": 7, "price_asked": 11, "service_id": 23,  # Skin graft, no cat
                "status": 1, "validity_from": "2019-06-01", "validity_to": None, "audit_user_id": -1
            }]
        }

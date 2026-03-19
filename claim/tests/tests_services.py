from django.test import TestCase
from django.core.exceptions import ValidationError
from unittest import mock
from location.test_helpers import (
    create_test_health_facility,
    create_test_village,
)
from insuree.test_helpers import create_test_insuree
from policy.test_helpers import create_test_policy2
from product.test_helpers import (
    create_test_product,
    create_test_product_service,
    create_test_product_item,
)
from claim.test_helpers import create_test_claim_admin, create_test_claim
from claim.models import Claim, ClaimItem, ClaimService, ClaimDetail
from medical.models import Diagnosis
from medical.test_helpers import create_test_item, create_test_service
from medical_pricelist.test_helpers import (
    add_service_to_hf_pricelist,
    add_item_to_hf_pricelist,
)
from claim.services import (
    ClaimSubmitService,
    ClaimSubmitError,
    ClaimItemSubmit,
    ClaimSubmit,
    processing_claim,
    ClaimServiceSubmit
)
import core
from claim.utils import service_create_hook, calcul_amount_service, service_update_hook
from claim.models import ClaimServiceItem, ClaimServiceService
from django.db import connection

from datetime import datetime, timedelta


class ClaimSubmitServiceTestCase(TestCase):
    test_hf = None

    test_insuree = None
    test_claim_admin = None
    test_icd = None
    test_claim = None
    test_claim_item = None
    test_claim_service = None
    test_region = None
    test_district = None
    test_village = None
    test_ward = None
    test_policy = None

    @classmethod
    def setUpTestData(cls):
        if cls.test_region is None:
            cls.test_village = create_test_village()
            cls.test_ward = cls.test_village.parent
            cls.test_region = cls.test_village.parent.parent.parent
            cls.test_district = cls.test_village.parent.parent

        cls.test_hf = create_test_health_facility("1", cls.test_district.id, valid=True)
        props = dict(
            last_name="name",
            other_names="surname",
            dob=core.datetime.date(2000, 1, 13),
            chf_id="884930485",
        )
        family_props = dict(
            location=cls.test_village,
        )
        cls.test_insuree = create_test_insuree(
            is_head=True, custom_props=props, family_custom_props=family_props
        )
        product = create_test_product(
            "BCUL0001",
            custom_props={
                "name": "simplebatch",
                "lump_sum": 10_000,
                "location_id": cls.test_region.id,
            },
        )

        cls.test_policy = create_test_policy2(
            product,
            cls.test_insuree,
        )
        cls.test_claim_admin = create_test_claim_admin()
        cls.test_icd = Diagnosis(code="ICD00I", name="diag test", audit_user_id=-1)
        cls.test_icd.save()
        cls.test_claim = Claim.objects.create(
            date_claimed=core.datetime.date(2020, 1, 9),
            code="code_ABVC",
            icd=cls.test_icd,
            claimed=2000,
            date_from=core.datetime.date(2020, 1, 13),
            admin=cls.test_claim_admin,
            insuree=cls.test_insuree,
            health_facility=cls.test_hf,
            status=Claim.STATUS_ENTERED,
            audit_user_id=-1,
        )

        cls.test_claim_item = ClaimItem.objects.create(
            claim=cls.test_claim,
            item=create_test_item("D", custom_props={"code": "cCode", "price": 1000}),
            price_asked=1000,
            qty_provided=1,
            audit_user_id=-1,
            status=ClaimDetail.STATUS_PASSED,
            availability=True,
        )
        add_item_to_hf_pricelist(
            cls.test_claim_item.item,
            cls.test_claim.health_facility,
            custom_props={},
        )
        create_test_product_item(product, cls.test_claim_item.item, valid=True)

        cls.test_claim_service = ClaimService.objects.create(
            claim=cls.test_claim,
            service=create_test_service(
                "D", custom_props={"code": "sCode", "price": 1000}
            ),
            price_asked=1000,
            qty_provided=1,
            audit_user_id=-1,
            status=ClaimDetail.STATUS_PASSED,
        )
        add_service_to_hf_pricelist(
            cls.test_claim_service.service,
            cls.test_claim.health_facility,
            custom_props={},
        )
        create_test_product_service(product, cls.test_claim_service.service, valid=True)

    def test_minimal_item_claim_submit_xml(self):
        items = [
            ClaimItemSubmit(code="aa", quantity=2),
        ]
        item = "<Item><ItemCode>aa</ItemCode><ItemQuantity>2</ItemQuantity></Item>"

        claim = ClaimSubmit(
            date=core.datetime.date(2020, 1, 9),
            code="code_ABVC",
            icd_code=self.test_icd.code,
            total=334,
            start_date=core.datetime.date(2020, 1, 13),
            claim_admin_code=self.test_claim_admin.code,
            insuree_chf_id=self.test_insuree.chf_id,
            health_facility_code=self.test_hf.code,
            item_submits=items,
        )
        details = "<Details>"
        details = details + "<ClaimDate>09/01/2020</ClaimDate>"
        details = details + f"<HFCode>{self.test_hf.code}</HFCode>"
        details = details + f"<ClaimAdmin>{self.test_claim_admin.code}</ClaimAdmin>"
        details = details + "<ClaimCode>code_ABVC</ClaimCode>"
        details = details + f"<CHFID>{self.test_insuree.chf_id}</CHFID>"
        details = details + "<StartDate>13/01/2020</StartDate>"
        details = details + f"<ICDCode>{self.test_icd.code}</ICDCode>"
        details = details + "<Total>334</Total>"
        details = details + "</Details>"
        expected = "<Claim>%s<Items>%s</Items></Claim>" % (details, item)
        self.assertEqual(expected, claim.to_xml())

    def test_minimal_service_claim_submit_xml(self):
        services = [
            ClaimServiceSubmit(code="aa", quantity=2),
        ]
        service = "<Service><ServiceCode>aa</ServiceCode><ServiceQuantity>2</ServiceQuantity></Service>"

        claim = ClaimSubmit(
            date=core.datetime.date(2020, 1, 9),
            code="code_ABVC",
            icd_code=self.test_icd.code,
            total=334,
            start_date=core.datetime.date(2020, 1, 13),
            claim_admin_code=self.test_claim_admin.code,
            insuree_chf_id=self.test_insuree.chf_id,
            health_facility_code=self.test_hf.code,
            service_submits=services,
        )
        details = "<Details>"
        details = details + "<ClaimDate>09/01/2020</ClaimDate>"
        details = details + f"<HFCode>{self.test_hf.code}</HFCode>"
        details = details + f"<ClaimAdmin>{self.test_claim_admin.code}</ClaimAdmin>"
        details = details + "<ClaimCode>code_ABVC</ClaimCode>"
        details = details + f"<CHFID>{self.test_insuree.chf_id}</CHFID>"
        details = details + "<StartDate>13/01/2020</StartDate>"
        details = details + f"<ICDCode>{self.test_icd.code}</ICDCode>"
        details = details + "<Total>334</Total>"
        details = details + "</Details>"
        expected = "<Claim>%s<Services>%s</Services></Claim>" % (
            details, service)
        self.assertEqual(expected, claim.to_xml())

    def test_extended_claim_submit_xml(self):
        items = [
            ClaimItemSubmit(code="aa", quantity=2),
            ClaimItemSubmit(code="bb", quantity=1, price=12.3),
        ]
        item_a = "<Item><ItemCode>aa</ItemCode><ItemQuantity>2</ItemQuantity></Item>"
        item_b = "<Item><ItemCode>bb</ItemCode><ItemPrice>12.3</ItemPrice><ItemQuantity>1</ItemQuantity></Item>"

        services = [
            ClaimServiceSubmit(code="aa-serv", quantity=2),
            ClaimServiceSubmit(code="a<a'-serv", quantity=1, price=35),
        ]
        service_a = "<Service><ServiceCode>aa-serv</ServiceCode><ServiceQuantity>2</ServiceQuantity></Service>"
        service_b = "<Service><ServiceCode>a&lt;a'-serv</ServiceCode><ServicePrice>35</ServicePrice><ServiceQuantity>1</ServiceQuantity></Service>"  # noqa: E501

        claim = ClaimSubmit(
            date=core.datetime.date(2020, 1, 9),
            code="code_ABVC",
            icd_code=self.test_icd.code,
            total=334,
            start_date=core.datetime.date(2020, 1, 13),
            claim_admin_code=self.test_claim_admin.code,
            insuree_chf_id=self.test_insuree.chf_id,
            health_facility_code=self.test_hf.code,
            item_submits=items,
            service_submits=services,
        )

        details = "<Details>"
        details = details + "<ClaimDate>09/01/2020</ClaimDate>"
        details = details + f"<HFCode>{self.test_hf.code}</HFCode>"
        details = details + f"<ClaimAdmin>{self.test_claim_admin.code}</ClaimAdmin>"
        details = details + "<ClaimCode>code_ABVC</ClaimCode>"
        details = details + f"<CHFID>{self.test_insuree.chf_id}</CHFID>"
        details = details + "<StartDate>13/01/2020</StartDate>"
        details = details + f"<ICDCode>{self.test_icd.code}</ICDCode>"
        details = details + "<Total>334</Total>"
        details = details + "</Details>"
        expected = "<Claim>%s" % details
        expected = expected + "<Items>%s%s</Items>" % (item_a, item_b)
        expected = expected + "<Services>%s%s</Services>" % (service_a, service_b)
        expected = expected + "</Claim>"
        self.assertEqual(expected, claim.to_xml())

    @mock.patch("django.db.connections")
    def test_claim_submit_error(self, mock_connections):
        if connection.vendor != "mssql":
            self.skipTest("This test can only be executed for MSSQL database")
        with mock.patch(
            "claim.services.ClaimSubmitService.hf_scope_check"
        ) as mock_security:
            mock_security.return_value = None
            query_result = [2]
            mock_connections.__getitem__.return_value.cursor.return_value\
                .__enter__.return_value.fetchone.return_value = (query_result)
            mock_user = mock.Mock(is_anonymous=False)
            mock_user.has_perm = mock.MagicMock(return_value=True)
            claim = ClaimSubmit(
                date=core.datetime.date(2020, 1, 9),
                code="code_ABVC",
                icd_code=self.test_icd.code,
                total=334,
                start_date=core.datetime.date(2020, 1, 13),
                claim_admin_code=self.test_claim_admin.code,
                insuree_chf_id=self.test_insuree.chf_id,
                health_facility_code=self.test_hf.code,
            )
            service = ClaimSubmitService(user=mock_user)
            with self.assertRaises(ClaimSubmitError) as cm:
                service.submit(claim)
            self.assertNotEqual(cm.exception.code, 0)

    @mock.patch("django.db.connections")
    def test_claim_submit_allgood_xml(self, mock_connections):
        if connection.vendor != "mssql":
            self.skipTest("This test can only be executed for MSSQL database")
        with mock.patch("django.db.backends.utils.CursorWrapper") as mock_cursor:
            # required for all modules tests
            mock_cursor.return_value.description = None
            # required for claim module tests
            mock_cursor.return_value.__enter__.return_value.description = None
            with mock.patch(
                "claim.services.ClaimSubmitService.hf_scope_check"
            ) as mock_security:
                mock_security.return_value = None
                mock_user = mock.Mock(is_anonymous=False)
                mock_user.has_perm = mock.MagicMock(return_value=True)

                _to = datetime.now() - timedelta(days=1)
                claim = ClaimSubmit(
                    date=_to,
                    code="code_ABVCD",
                    icd_code=self.test_icd.code,
                    total=334,
                    start_date=datetime.now() - timedelta(days=2),
                    claim_admin_code=self.test_claim_admin.code,
                    insuree_chf_id=self.test_insuree.chf_id,
                    health_facility_code=self.test_hf.code,
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
        from datetime import date, timedelta

        start_date = date.today() - timedelta(days=2)
        end_date = date.today() - timedelta(days=1)
        claim = self._get_test_dict(
            code="e_n_s", start_date=start_date, end_date=end_date
        )
        service = ClaimSubmitService(user=mock_user)
        claim = service.enter_and_submit(claim, True)
        expected_claimed = (
            1000 + 1000
        )  # 2 provisions, both qty = 1, price asked == 1000

        self.assertEqual(claim.status, Claim.STATUS_CHECKED)
        errors = processing_claim(claim, mock_user, True)
        self.assertEqual(errors, [])
        self.assertEqual(claim.status, Claim.STATUS_VALUATED)
        self.assertEqual(claim.approved, expected_claimed)
        self.assertEqual(claim.claimed, expected_claimed)
        self.assertEqual(claim.health_facility.id, self.test_hf.id)
        self.assertEqual(claim.items.all().count(), 1)
        self.assertEqual(claim.services.all().count(), 1)
        self.assertEqual(claim.audit_user_id, -1)
        self.assertTrue(claim.id is not None)

    @mock.patch("claim.services.ClaimSubmitService._validate_user_hf")
    @mock.patch("claim.services.ClaimCreateService._validate_user_hf")
    def test_claim_enter_duplicate_exception(self, check_hf_submit, check_hf_enter):
        check_hf_submit.return_value, check_hf_enter.return_value = True, True
        mock_user = mock.Mock(is_anonymous=False)
        mock_user.has_perm = mock.MagicMock(return_value=True)
        mock_user.id_for_audit = -1
        from datetime import date, timedelta

        start_date = date.today() - timedelta(days=2)
        end_date = date.today() - timedelta(days=1)
        claim = self._get_test_dict(
            code="dup", start_date=start_date, end_date=end_date
        )
        service = ClaimSubmitService(user=mock_user)

        service.enter_and_submit(claim, True)

        with self.assertRaises(ValidationError):
            service.enter_and_submit(claim, False)

    def test_service_create_hook(self):
        item = create_test_item("D", custom_props={})
        service = create_test_service("V")
        service_super = create_test_service("V", custom_props={'packagetype': 'F'})
        claim = create_test_claim()
        service_items_dict = {
            "qty_provided": 7,
            "price_asked": 11,
            "service_id": service_super.id,
            "status": 1,
            "validity_from": "2019-06-01",
            "validity_to": None,
            "audit_user_id": 1,
            "service_item_set": [
                {
                    "sub_item_code": item.code,
                    "qty_asked": 1,
                    "qty_provided": 7,
                    "price_asked": 11,
                }
            ],
            "service_service_set": [
                {
                    "sub_service_code": service.code,
                    "qty_asked": 2,
                    "qty_provided": 3,
                    "price_asked": 20,
                }
            ],
        }
        service_create_hook(claim.id, service_items_dict)
        claim_service_item = ClaimServiceItem.objects.filter(item=item.id)
        self.assertTrue(len(claim_service_item) > 0)
        claim_service_item = claim_service_item.first()
        self.assertEqual(claim_service_item.qty_displayed, 1)
        self.assertEqual(claim_service_item.qty_provided, 7)
        self.assertEqual(claim_service_item.price_asked, 11)

        claim_service_service = ClaimServiceService.objects.filter(service=service.id)
        self.assertTrue(len(claim_service_service) > 0)
        claim_service_service = claim_service_service.first()
        self.assertEqual(claim_service_service.qty_displayed, 2)
        self.assertEqual(claim_service_service.qty_provided, 3)
        self.assertEqual(claim_service_service.price_asked, 20)

        service_items_dict["service_item_set"] = [
            {"qty_asked": 90, "sub_item_code": item.code}
        ]
        service_items_dict["service_service_set"] = [
            {"qty_asked": 100, "sub_service_code": service.code}
        ]

        service_update_hook(claim.id, service_items_dict)
        claim_service_service.refresh_from_db()
        self.assertEqual(100, claim_service_service.qty_displayed)

        claim_service_item.refresh_from_db()
        self.assertEqual(90, claim_service_item.qty_displayed)

    def test_calcul_amount_service(self):
        item = create_test_item("D", custom_props={})
        service = create_test_service("V")
        service_super = create_test_service("V", custom_props={'packagetype': 'F'})
        service_items_dict = {
            "qty_provided": 5,
            "price_asked": 50,
            "service_id": service_super.id,
            "status": 1,
            "validity_from": "2019-06-01",
            "validity_to": None,
            "audit_user_id": 1,
            "service_item_set": [
                {
                    "sub_item_code": item.code,
                    "qty_asked": 1,
                    "qty_provided": 7,
                    "price_asked": 11,
                }
            ],
            "service_service_set": [
                {
                    "sub_service_code": service.code,
                    "qty_asked": 2,
                    "qty_provided": 3,
                    "price_asked": 20,
                }
            ],
        }
        result = calcul_amount_service(service_items_dict)
        self.assertEqual(result, 137)

    def _get_test_dict(self, code=None, start_date=None, end_date=None):
        return {
            "health_facility_id": self.test_claim.health_facility_id,
            "icd_id": self.test_icd.id,
            "date_from": start_date or self.test_claim.date_from,
            "code": self.test_claim.code if code is None else code,
            "date_claimed": end_date or self.test_claim.date_claimed,
            "date_to": end_date or self.test_claim.date_to,
            "audit_user_id": self.test_claim.audit_user_id,
            "insuree_id": self.test_claim.insuree_id,
            "status": self.test_claim.status,
            "validity_from": start_date or self.test_claim.validity_from,
            "items": [
                {
                    "qty_provided": self.test_claim_item.qty_provided,
                    "price_asked": self.test_claim_item.price_asked,
                    "item_id": self.test_claim_item.item_id,
                    "status": self.test_claim_item.status,
                    "availability": self.test_claim_item.availability,
                    "validity_from": self.test_claim_item.validity_from,
                    "validity_to": self.test_claim_item.validity_to,
                    "audit_user_id": self.test_claim_item.audit_user_id,
                }
            ],
            "services": [
                {
                    "qty_provided": self.test_claim_service.qty_provided,
                    "price_asked": self.test_claim_service.price_asked,
                    "service_id": self.test_claim_service.service_id,
                    "status": self.test_claim_service.status,
                    "validity_from": self.test_claim_service.validity_from,
                    "validity_to": self.test_claim_service.validity_to,
                    "audit_user_id": self.test_claim_service.audit_user_id,
                }
            ],
        }

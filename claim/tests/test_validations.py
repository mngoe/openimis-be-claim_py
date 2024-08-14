from claim.services import update_claims_dedrems, set_claims_status
from claim.models import Claim, ClaimDedRem, ClaimItem, ClaimDetail, ClaimService, ClaimServiceItem, ClaimServiceService
from claim.test_helpers import create_test_claim, create_test_claimservice, create_test_claimitem, \
    mark_test_claim_as_processed, delete_claim_with_itemsvc_dedrem_and_history
from core.test_helpers import create_test_officer


from claim.validations import get_claim_category, validate_claim, validate_assign_prod_to_claimitems_and_services, \
    process_dedrem, REJECTION_REASON_WAITING_PERIOD_FAIL, REJECTION_REASON_INVALID_ITEM_OR_SERVICE
from core.models import User, InteractiveUser
from django.test import TestCase
from insuree.models import Family, Insuree
from insuree.test_helpers import create_test_insuree
from location.models import HealthFacility
from medical_pricelist.test_helpers import add_service_to_hf_pricelist, add_item_to_hf_pricelist
from medical.models import ServiceItem, ServiceService
from product.models import ProductItemOrService

from medical.test_helpers import create_test_service, create_test_item
from medical_pricelist.test_helpers import add_service_to_hf_pricelist, add_item_to_hf_pricelist, \
    update_pricelist_service_detail_in_hf_pricelist, update_pricelist_item_detail_in_hf_pricelist
from policy.test_helpers import create_test_policy


# default arguments should not pass a list or a dict because they're mutable but we don't risk mutating them here:
# noinspection PyDefaultArgument,DuplicatedCode
from product.test_helpers import create_test_product, create_test_product_service, create_test_product_item


class ValidationTest(TestCase):
    service_H = None
    service_O = None
    service_D = None
    service_A = None
    service_A_invalid = None

    def setUp(self) -> None:
        super(ValidationTest, self).setUp()
        self.i_user = InteractiveUser(
            login_name="test_batch_run", audit_user_id=978911, id=97891
        )
        self.user = User(i_user=self.i_user)

        self.service_H = create_test_service("H")
        self.service_O = create_test_service("O")
        self.service_D = create_test_service("D")
        self.service_A = create_test_service("A")
        self.service_A_invalid = create_test_service("A", False)

        self.item_1 = create_test_item("D")

    def test_get_claim_category_S(self):
        # Given
        claim = create_test_claim()
        service1 = create_test_claimservice(claim, "S")

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "S")

        # tearDown
        service1.delete()
        claim.delete()

    def test_get_claim_category_D(self):
        # Given
        claim = create_test_claim()
        service1 = create_test_claimservice(claim, "D")

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "D")

        # tearDown
        service1.delete()
        claim.delete()

    def test_get_claim_category_mix(self):
        # Given
        claim = create_test_claim()
        services = [
            create_test_claimservice(claim, "H"),
            create_test_claimservice(claim, "O"),
            create_test_claimservice(claim, None),
            create_test_claimservice(claim, "A"),
            create_test_claimservice(claim, "S"),
        ]

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "S")

        # tearDown
        for service in services:
            service.delete()
        claim.delete()

    def test_get_claim_category_some_invalid(self):
        # Given
        claim = create_test_claim()
        services = [
            create_test_claimservice(claim, "H", False),
            create_test_claimservice(claim, "A", False),
            create_test_claimservice(claim, "S"),
        ]

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "S")

        # tearDown
        for service in services:
            service.delete()
        claim.delete()

    def test_get_claim_category_null(self):
        # Given
        claim = create_test_claim()
        service1 = create_test_claimservice(claim, None)

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "V")

        # tearDown
        service1.delete()
        claim.delete()

    # This test cannot be performed because the database constraints don't allow a null date_from.
    # def test_validate_claim_target_date(self):
    #     # Given
    #     claim = create_test_claim(custom_props={"date_from": None, "date_to": None})
    #     service1 = create_test_claimservice(claim, "S")
    #
    #     # When
    #     errors = validate_claim(claim)
    #
    #     # Then
    #     self.assertEquals(len(errors), 1, "The claim should fail the target date validation")
    #     self.assertEquals(errors[0].code, 9, "The claim should fail the target date validation with code 9")
    #     self.assertTrue("date" in errors[0].message.lower())
    #
    #     # tearDown
    #     service1.delete()
    #     claim.delete()

    def test_validate_pricelist_hf1(self):
        # When the claimitem points to a pricelist that doesn't correspond to the claim HF
        hf_without_pricelist = HealthFacility.objects.filter(items_pricelist__id__isnull=True).first()
        self.assertIsNotNone(hf_without_pricelist, "This test requires a health facility without a price list item")
        # Given
        claim = create_test_claim({"health_facility_id": hf_without_pricelist.id})
        service1 = create_test_claimservice(claim, "S")
        item1 = create_test_claimitem(claim, "D")

        # When
        errors = validate_claim(claim, True)

        # Then
        claim.refresh_from_db()
        service1.refresh_from_db()
        item1.refresh_from_db()
        self.assertGreaterEqual(len(errors), 1, "Should raise at least one error")
        error1 = [e for e in errors if e['code'] == 1]  # all services rejected
        self.assertGreaterEqual(len(error1), 1, "There should be an error code 1")
        self.assertEquals(item1.rejection_reason, 2, "Database was updated with rejection reason")

        # tearDown
        service1.delete()
        item1.delete()
        claim.delete()

    def test_validate_family(self):
        # When the insuree family is invalid
        # Given
        invalid_insuree = Insuree.objects.filter(family__in=Family.objects.filter(validity_to__isnull=False)).first()
        self.assertIsNotNone(invalid_insuree)
        claim = create_test_claim({"insuree_id": invalid_insuree.id})
        service1 = create_test_claimservice(claim, "S")
        item1 = create_test_claimitem(claim, "D")

        # When
        errors = validate_claim(claim, True)

        # Then
        claim.refresh_from_db()
        item1.refresh_from_db()
        self.assertGreaterEqual(len(errors), 1, "Should raise at least one error")
        error7 = [e for e in errors if e['code'] == 7]
        self.assertGreaterEqual(len(error7), 1, "There should be 1 error code 7: invalid insuree")
        self.assertEquals(item1.rejection_reason, 7, "Database was updated with rejection reason")

        # tearDown
        service1.delete()
        item1.delete()
        claim.delete()

    def test_validate_max_visits(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={"max_no_visits": 1})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("V")
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a visit should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)
        self.assertEquals(len(errors), 0, "The first visit should be accepted")

        # a second visit should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        service2 = create_test_claimservice(claim2, "V")
        errors = validate_claim(claim2, True)
        # TODO Temporarily disabled
        # self.assertGreater(len(errors), 0, "The second visit should be refused")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

        # tearDown
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_validate_patient_category(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={"max_no_visits": 1})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("V", custom_props={"patient_category": 1})
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)

        # Then
        claim1.refresh_from_db()
        self.assertEquals(len(errors), 2)
        self.assertEquals(errors[0]['code'], 1)  # claimed rejected because all services are rejected
        self.assertEquals(claim1.services.first().rejection_reason, 4)  # reason is wrong insuree mask

        # tearDown
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_frequency(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("CSECT")
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("C", custom_props={"code": "G34B", "frequency": 180})
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a visit should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEquals(len(errors), 0, "The first visit should be accepted")

        # a second visit should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        service2 = create_test_claimservice(claim2, custom_props={"service_id": service.id})
        errors = validate_claim(claim2, True)
        self.assertGreater(len(errors), 0, "The second service should be refused as it is withing 180 days")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

        # tearDown
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_limit_no(self):
        # When the insuree already reaches his limit number
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("CSECT")
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("C", custom_props={"code": "G34B"})
        product_service = create_test_product_service(product, service, custom_props={"limit_no_adult": 1})
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a visit should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id, "qty_provided": 1})
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEquals(len(errors), 0, "The first visit should be accepted")

        # a second visit should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        service2 = create_test_claimservice(claim2, custom_props={"service_id": service.id})
        errors = validate_claim(claim2, True)
        self.assertGreater(len(errors), 0, "The second service should be refused")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

        # tearDown
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_limit_delivery(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("DELIV", custom_props={"max_no_delivery": 1})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("D", custom_props={"code": "G34C"})
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a delivery should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEquals(len(errors), 0, "The first visit should be accepted")

        # a second delivery should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        service2 = create_test_claimservice(claim2, custom_props={"service_id": service.id})
        errors = validate_claim(claim2, True)
        self.assertGreater(len(errors), 0, "The second service should fail because there is already one delivery")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

        # tearDown
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_limit_hospital(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("DELIV", custom_props={"max_no_hospitalization": 1})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("H", custom_props={"code": "HHHH"})
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a delivery should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEquals(len(errors), 0, "The first hospitalization should be accepted")

        # a second delivery should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        service2 = create_test_claimservice(claim2, custom_props={"service_id": service.id})
        errors = validate_claim(claim2, True)
        self.assertGreater(len(errors), 0,
                           "The second service should fail because there is already one hospitalization")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

        # tearDown
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_limit_surgery(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("DELIV", custom_props={"max_no_surgery": 1})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("S", custom_props={"code": "SSSS"})
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a delivery should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEquals(len(errors), 0, "The first surgery should be accepted")

        # a second delivery should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        service2 = create_test_claimservice(claim2, custom_props={"service_id": service.id})
        errors = validate_claim(claim2, True)
        self.assertGreater(len(errors), 0, "The second service should fail because there is already one surgery")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

        # tearDown
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_limit_visit(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("DELIV", custom_props={"max_no_visits": 1})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("V", custom_props={"code": "VVVV"})
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a delivery should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEquals(len(errors), 0, "The first visit should be accepted")

        # a second delivery should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        service2 = create_test_claimservice(claim2, custom_props={"service_id": service.id})
        errors = validate_claim(claim2, True)
        self.assertGreater(len(errors), 0, "The second service should fail because there is already one visit")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

        # tearDown
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_waiting_period(self):
        # When the insuree already reaches his limit of visits
        # Given
        from core import datetime
        insuree = create_test_insuree()
        child_insuree = create_test_insuree( with_family = False,
            custom_props={
            "dob": datetime.datetime(2020, 1, 1),
            "family": insuree.family
        })
        self.assertIsNotNone(insuree)
        product = create_test_product("CSECT")
        policy = create_test_policy(product, insuree, link=True)
        policy_child = create_test_policy(product, child_insuree, link=True)
        service = create_test_service("C")
        product_service = create_test_product_service(
            product, service, custom_props={"waiting_period_adult": 6, "waiting_period_child": 0})
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a visit within the waiting period should be refused
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)

        self.assertEqual(len(errors), 2, "An adult visit within the waiting period should be refused")
        self.assertEqual(claim1.services.first().rejection_reason, REJECTION_REASON_WAITING_PERIOD_FAIL)

        # a visit after the waiting period should be fine
        claim2 = create_test_claim({
            "insuree_id": insuree.id,
            "date_from": datetime.datetime(2020, 2, 1),
            "date_to": datetime.datetime(2020, 2, 1),
            "date_claimed": datetime.datetime(2020, 2, 1),
        })
        service2 = create_test_claimservice(claim2, custom_props={"service_id": service.id})
        errors = validate_claim(claim2, True)
        self.assertEqual(len(errors), 0, "This one should be accepted as after the waiting period")

        # a child should not have the waiting period
        claim3 = create_test_claim({"insuree_id": child_insuree.id})
        service3 = create_test_claimservice(claim3, custom_props={"service_id": service.id})
        errors = validate_claim(claim3, True)
        self.assertEqual(len(errors), 0, "The child has no waiting period")

        # tearDown
        service3.delete()
        claim3.delete()
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.all().delete()
        policy.delete()
        policy_child.insuree_policies.all().delete()
        policy_child.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_submit_claim_dedrem(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("V", custom_props={})
        item = create_test_item("D", custom_props={})
        product_service = create_test_product_service(product, service)
        product_item = create_test_product_item(product, item)
        pricelist_detail1 = add_service_to_hf_pricelist(service)
        pricelist_detail2 = add_item_to_hf_pricelist(item)

        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id})
        item1 = create_test_claimitem(
            claim1, "D", custom_props={"item_id": item.id})
        errors = validate_claim(claim1, True)
        errors += validate_assign_prod_to_claimitems_and_services(claim1)
        errors += process_dedrem(claim1, -1, True)
        self.assertEqual(len(errors), 0)

        # Then
        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()
        self.assertEqual(len(errors), 0)
        self.assertEqual(item1.price_adjusted, 100)
        self.assertEqual(item1.price_valuated, 700)
        self.assertEqual(item1.deductable_amount, 0)
        self.assertEqual(item1.exceed_ceiling_amount, 0)
        self.assertIsNone(item1.exceed_ceiling_amount_category)
        self.assertEqual(item1.remunerated_amount, 700)
        self.assertEqual(claim1.status, Claim.STATUS_VALUATED)
        self.assertEqual(claim1.audit_user_id_process, -1)
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1)
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.audit_user_id, -1)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 1400)
        self.assertEqual(dedrem1.rem_op, 1400)
        self.assertIsNone(dedrem1.rem_ip)
        self.assertEqual(dedrem1.rem_surgery, 0)
        self.assertEqual(dedrem1.rem_consult, 0)
        self.assertEqual(dedrem1.rem_hospitalization, 0)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

        # tearDown
        dedrem_qs.delete()
        service1.delete()
        item1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_item.delete()
        product_service.delete()
        pricelist_detail1.delete()
        pricelist_detail2.delete()
        service.delete()
        item.delete()
        product.delete()

    def test_submit_claim_dedrem_limit_delivery(self):
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service("D", custom_props={})
        item = create_test_item("D", custom_props={})

        product = create_test_product("BCUL0001", custom_props={
            "name": "Basic Cover Ultha",
            "lump_sum": 10_000,
            "max_amount_delivery": 55,
        })
        product_service = create_test_product_service(product, service)
        product_item = create_test_product_item(product, item)
        policy = create_test_policy(product, insuree, link=True)
        pricelist_detail1 = add_service_to_hf_pricelist(service)
        pricelist_detail2 = add_item_to_hf_pricelist(item)

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id})
        item1 = create_test_claimitem(
            claim1, "D", custom_props={"item_id": item.id})
        errors = validate_claim(claim1, True)
        errors += validate_assign_prod_to_claimitems_and_services(claim1)
        errors += process_dedrem(claim1, -1, True)
        self.assertEqual(len(errors), 0)

        # Then
        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()
        self.assertEqual(len(errors), 0)
        self.assertEqual(item1.price_adjusted, 100)
        self.assertEqual(item1.price_valuated, 55)
        self.assertEqual(item1.deductable_amount, 0)
        self.assertEqual(item1.exceed_ceiling_amount, 0)
        self.assertIsNone(item1.exceed_ceiling_amount_category)
        self.assertEqual(item1.remunerated_amount, 55)
        self.assertEqual(claim1.status, Claim.STATUS_VALUATED)
        self.assertEqual(claim1.audit_user_id_process, -1)
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1)
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.audit_user_id, -1)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 55)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

        # tearDown
        dedrem_qs.delete()
        service1.delete()
        item1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_item.delete()
        product_service.delete()
        pricelist_detail1.delete()
        pricelist_detail2.delete()
        service.delete()
        item.delete()
        product.delete()

    def test_submit_claim_dedrem_limit_consultation(self):
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service("C", custom_props={})
        item = create_test_item("C", custom_props={})

        product = create_test_product("BCUL0001", custom_props={
            "name": "Basic Cover Ultha",
            "lump_sum": 10_000,
            "max_amount_consultation": 55,
        })
        product_service = create_test_product_service(product, service)
        product_item = create_test_product_item(product, item)
        policy = create_test_policy(product, insuree, link=True)
        pricelist_detail1 = add_service_to_hf_pricelist(service)
        pricelist_detail2 = add_item_to_hf_pricelist(item)

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id})
        item1 = create_test_claimitem(
            claim1, "C", custom_props={"item_id": item.id})
        errors = validate_claim(claim1, True)
        errors += validate_assign_prod_to_claimitems_and_services(claim1)
        errors += process_dedrem(claim1, -1, True)
        self.assertEqual(len(errors), 0)

        # Then
        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()
        self.assertEqual(len(errors), 0)
        self.assertEqual(item1.price_adjusted, 100)
        self.assertEqual(item1.price_valuated, 55)
        self.assertEqual(item1.deductable_amount, 0)
        self.assertEqual(item1.exceed_ceiling_amount, 0)
        self.assertIsNone(item1.exceed_ceiling_amount_category)
        self.assertEqual(item1.remunerated_amount, 55)
        self.assertEqual(claim1.status, Claim.STATUS_VALUATED)
        self.assertEqual(claim1.audit_user_id_process, -1)
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1)
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.audit_user_id, -1)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 55)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

        # tearDown
        dedrem_qs.delete()
        service1.delete()
        item1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_item.delete()
        product_service.delete()
        pricelist_detail1.delete()
        pricelist_detail2.delete()
        service.delete()
        item.delete()
        product.delete()

    def test_submit_claim_dedrem_limit_antenatal(self):
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service("A", custom_props={})
        item = create_test_item("A", custom_props={})

        product = create_test_product("BCUL0001", custom_props={
            "name": "Basic Cover Ultha",
            "lump_sum": 10_000,
            "max_amount_antenatal": 55,
        })
        product_service = create_test_product_service(product, service)
        product_item = create_test_product_item(product, item)
        policy = create_test_policy(product, insuree, link=True)
        pricelist_detail1 = add_service_to_hf_pricelist(service)
        pricelist_detail2 = add_item_to_hf_pricelist(item)

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id})
        item1 = create_test_claimitem(
            claim1, "A", custom_props={"item_id": item.id})
        errors = validate_claim(claim1, True)
        errors += validate_assign_prod_to_claimitems_and_services(claim1)
        errors += process_dedrem(claim1, -1, True)
        self.assertEqual(len(errors), 0)

        # Then
        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()
        self.assertEqual(len(errors), 0)
        self.assertEqual(item1.price_adjusted, 100)
        self.assertEqual(item1.price_valuated, 55)
        self.assertEqual(item1.deductable_amount, 0)
        self.assertEqual(item1.exceed_ceiling_amount, 0)
        self.assertIsNone(item1.exceed_ceiling_amount_category)
        self.assertEqual(item1.remunerated_amount, 55)
        self.assertEqual(claim1.status, Claim.STATUS_VALUATED)
        self.assertEqual(claim1.audit_user_id_process, -1)
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1)
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.audit_user_id, -1)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 55)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

        # tearDown
        dedrem_qs.delete()
        service1.delete()
        item1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_item.delete()
        product_service.delete()
        pricelist_detail1.delete()
        pricelist_detail2.delete()
        service.delete()
        item.delete()
        product.delete()

    def test_review_reject_update_dedrem(self):
        """
        This test creates a claim, submits it so that it gets dedrem entries,
        then submits a review rejecting part of it, then process the claim.
        It should not be processed (which was ok) but the dedrem should be deleted.
        """
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service("A", custom_props={"name": "test_review_reject_delete_dedrem"})
        item = create_test_item("A", custom_props={"name": "test_review_reject_delete_dedrem"})

        product = create_test_product("BCUL0001", custom_props={
            "name": "Basic Cover Ultha deldedrem",
            "lump_sum": 10_000,
        })
        product_service = create_test_product_service(product, service)
        product_item = create_test_product_item(product, item)
        policy = create_test_policy(product, insuree, link=True)
        pricelist_detail1 = add_service_to_hf_pricelist(service)
        pricelist_detail2 = add_item_to_hf_pricelist(item)

        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id, "qty_provided": 2})
        item1 = create_test_claimitem(
            claim1, "A", custom_props={"item_id": item.id, "qty_provided": 3})
        errors = validate_claim(claim1, True)
        errors += validate_assign_prod_to_claimitems_and_services(claim1)
        errors += process_dedrem(claim1, -1, False)

        self.assertEqual(len(errors), 0)
        # Make sure that the dedrem was generated
        dedrem = ClaimDedRem.objects.filter(claim=claim1).first()
        self.assertIsNotNone(dedrem)
        self.assertEquals(dedrem.rem_g, 500)  # 100*2 + 100*3

        # Review the claim and reject all of it
        # A partial rejection would still trigger the process_dedrem and be fine
        item1.qty_approved = 1
        item1.price_approved = 37
        item1.status = ClaimItem.STATUS_PASSED
        item1.audit_user_id_review = -1
        item1.justification = "Review comment item"
        item1.save()

        service1.qty_approved = 1
        service1.price_approved = 53
        service1.status = ClaimItem.STATUS_PASSED
        service1.audit_user_id_review = -1
        service1.justification = "Review comment svc"
        service1.save()

        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()

        set_claims_status([claim1.uuid], "review_status", Claim.REVIEW_DELIVERED)
        update_claims_dedrems([claim1.uuid], self.user)

        # Then dedrem should have been updated
        dedrem = ClaimDedRem.objects.filter(claim=claim1).first()
        self.assertIsNotNone(dedrem)
        self.assertEquals(dedrem.rem_g, 200)  # 100*1 + 100*1
        # tearDown
        # dedrem.delete() # already done if the test passed
        delete_claim_with_itemsvc_dedrem_and_history(claim1)
        policy.insuree_policies.first().delete()
        policy.delete()
        product_item.delete()
        product_service.delete()
        pricelist_detail1.delete()
        pricelist_detail2.delete()
        service.delete()
        item.delete()
        product.delete()

    def test_review_reject_delete_dedrem(self):
        """
        This test creates a claim, submits it so that it gets dedrem entries,
        then submits a review rejecting part of it, then process the claim.
        It should not be processed (which was ok) but the dedrem should be deleted.
        """
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service("A", custom_props={"name": "test_review_reject_delete_dedrem"})
        item = create_test_item("A", custom_props={"name": "test_review_reject_delete_dedrem"})

        product = create_test_product("BCUL0001", custom_props={
            "name": "Basic Cover Ultha deldedrem",
            "lump_sum": 10_000,
        })
        product_service = create_test_product_service(product, service)
        product_item = create_test_product_item(product, item)
        policy = create_test_policy(product, insuree, link=True)
        pricelist_detail1 = add_service_to_hf_pricelist(service)
        pricelist_detail2 = add_item_to_hf_pricelist(item)

        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id, "qty_provided": 2})
        item1 = create_test_claimitem(
            claim1, "A", custom_props={"item_id": item.id, "qty_provided": 3})
        errors = validate_claim(claim1, True)
        errors += validate_assign_prod_to_claimitems_and_services(claim1)
        errors += process_dedrem(claim1, -1, False)

        self.assertEqual(len(errors), 0)
        # Make sure that the dedrem was generated
        dedrem = ClaimDedRem.objects.filter(claim=claim1).first()
        self.assertIsNotNone(dedrem)
        self.assertEquals(dedrem.rem_g, 500)  # 100*2 + 100*3

        # Review the claim and reject all of it
        # A partial rejection would still trigger the process_dedrem and be fine
        item1.qty_approved = 0
        item1.price_approved = 0
        item1.status = ClaimItem.STATUS_REJECTED
        item1.rejection_reason = -1
        item1.audit_user_id_review = -1
        item1.justification = "Review comment item"
        item1.save()

        service1.qty_approved = 0
        service1.price_approved = 0
        service1.status = ClaimService.STATUS_REJECTED
        service1.rejection_reason = -1
        service1.audit_user_id_review = -1
        service1.justification = "Review comment svc"
        service1.save()

        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()

        set_claims_status([claim1.uuid], "review_status", Claim.REVIEW_DELIVERED)
        update_claims_dedrems([claim1.uuid], self.user)

        errors = validate_claim(claim1, True)
        if len(errors) == 0:
            errors += validate_assign_prod_to_claimitems_and_services(claim1)
            errors += process_dedrem(claim1, -1, False)

        # The claim should be globally rejected since the review rejected all items/svc
        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()
        self.assertEquals(claim1.status, Claim.STATUS_REJECTED)
        self.assertEquals(claim1.rejection_reason, REJECTION_REASON_INVALID_ITEM_OR_SERVICE)
        self.assertEquals(item1.status, ClaimDetail.STATUS_REJECTED)
        self.assertEquals(item1.rejection_reason, -1)
        self.assertEquals(service1.status, ClaimDetail.STATUS_REJECTED)
        self.assertEquals(service1.rejection_reason, -1)

        # Then dedrem should have been deleted
        dedrem = ClaimDedRem.objects.filter(claim=claim1).first()
        self.assertIsNone(dedrem)

        # tearDown
        # dedrem.delete() # already done if the test passed
        delete_claim_with_itemsvc_dedrem_and_history(claim1)
        policy.insuree_policies.first().delete()
        policy.delete()
        product_item.delete()
        product_service.delete()
        pricelist_detail1.delete()
        pricelist_detail2.delete()
        service.delete()
        item.delete()
        product.delete()

    def test_submit_claim_dedrem_update_pricelist_detail(self):
        '''
        This test replicates the functionality of test_submit_claim_dedrem,
        with the additional step of updating items and services prior to dedrem calculation.
        Despite the updates, the results should remain unaffected as the prices
        for these services/items are sourced from the pricelist detail's state at the time of
        coalse(claim.dateto, claim.datefrom).
        '''
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("V", custom_props={})
        item = create_test_item("D", custom_props={})
        product_service = create_test_product_service(product, service)
        product_item = create_test_product_item(product, item)
        pricelist_detail1 = add_service_to_hf_pricelist(service)
        pricelist_detail2 = add_item_to_hf_pricelist(item)

        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id})
        item1 = create_test_claimitem(
            claim1, "D", custom_props={"item_id": item.id})
        errors = validate_claim(claim1, True)
        errors += validate_assign_prod_to_claimitems_and_services(claim1)
        update_pricelist_service_detail_in_hf_pricelist(pricelist_detail1, custom_props={"price_overrule": 21})
        update_pricelist_item_detail_in_hf_pricelist(pricelist_detail1, custom_props={"price_overrule": 37})
        errors += process_dedrem(claim1, -1, True)
        self.assertEqual(len(errors), 0)

        # Then
        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()
        self.assertEqual(len(errors), 0)
        self.assertEqual(item1.price_adjusted, 100)
        self.assertEqual(item1.price_valuated, 700)
        self.assertEqual(item1.deductable_amount, 0)
        self.assertEqual(item1.exceed_ceiling_amount, 0)
        self.assertIsNone(item1.exceed_ceiling_amount_category)
        self.assertEqual(item1.remunerated_amount, 700)
        self.assertEqual(claim1.status, Claim.STATUS_VALUATED)
        self.assertEqual(claim1.audit_user_id_process, -1)
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1)
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.audit_user_id, -1)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 1400)
        self.assertEqual(dedrem1.rem_op, 1400)
        self.assertIsNone(dedrem1.rem_ip)
        self.assertEqual(dedrem1.rem_surgery, 0)
        self.assertEqual(dedrem1.rem_consult, 0)
        self.assertEqual(dedrem1.rem_hospitalization, 0)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

        # tearDown
        dedrem_qs.delete()
        service1.delete()
        item1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_item.delete()
        product_service.delete()
        pricelist_detail1.delete()
        pricelist_detail2.delete()
        service.delete()
        item.delete()
        product.delete()

    def test_set_status(self):
        class DummyUser:
            id_for_audit=-1
        insuree = create_test_insuree()
        officer = create_test_officer(villages=[insuree.current_village or insuree.family.location])
        claim = create_test_claim(custom_props={'status':Claim.STATUS_CHECKED, 
                                                'insuree':insuree})
        restult =set_claims_status([claim.uuid], 'feedback_status', Claim.FEEDBACK_SELECTED, user = DummyUser())
        claim.refresh_from_db()
        self.assertEqual(claim.feedback_status, Claim.FEEDBACK_SELECTED)
    
    def test_submit_claim_with_different_packatypes(self):
        from claim.apps import ClaimConfig
        ClaimConfig.native_code_for_services=False
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("V", custom_props={"packagetype": "F"})
        item = create_test_item("D", custom_props={})
        product_service = create_test_product_service(product, service)
        product_service.price_origin = ProductItemOrService.ORIGIN_CLAIM
        product_service.save()
        product_item = create_test_product_item(product, item)
        product_item.price_origin = ProductItemOrService.ORIGIN_CLAIM
        product_item.save()
        pricelist_detail1 = add_service_to_hf_pricelist(service)
        pricelist_detail2 = add_item_to_hf_pricelist(item)

        claim1 = create_test_claim({"insuree_id": insuree.id})
        claimservice1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id})
        clalimitem1 = create_test_claimitem(
            claim1, "D", custom_props={"item_id": item.id})
        # Set the price_adjusted to 4000
        claimservice1.price_adjusted = 4000
        claimservice1.save()
        # set the service price to 1000 lower than the price_adjusted
        service.price = 1000
        service.save()
        service.refresh_from_db()
        errors = validate_claim(claim1, True)
        errors = validate_assign_prod_to_claimitems_and_services(claim1)
        errors += process_dedrem(claim1, -1, True)
        self.assertEqual(len(errors), 0)
        # The claimservice1's price_adjusted should be the service price
        # because the price_adjusted is greater than the service (4000 > 1000)
        claimservice1.refresh_from_db()
        self.assertEqual(claimservice1.price_adjusted, 1000)

        # Set the price_adjusted to None
        claimservice1.price_adjusted = None
        claimservice1.price_asked = 6000
        service.price = 750
        service.save()
        claimservice1.save()
        validate_claim(claim1, True)
        validate_assign_prod_to_claimitems_and_services(claim1)
        process_dedrem(claim1, -1, True)
        claimservice1.refresh_from_db()
        service.refresh_from_db()
        # The claimservice1's price_adjusted should also be the service's price
        # because the price_asked is now also greater than the service (6000 > 750)
        self.assertEqual(claimservice1.price_adjusted, 750)

        # Change the price_origin
        product_service.price_origin = ProductItemOrService.ORIGIN_PRICELIST
        product_service.save()
        product_item.price_origin = ProductItemOrService.ORIGIN_PRICELIST
        product_item.save()
        service.packagetype = "P"
        service.save()
        # serviceservice = ServiceService.objects.create(
        #     servicelinkedService=service,
        #     service_id = claimservice1.service_id,
        #     price_asked = 3,
        #     qty_provided = 2
        # )
        claimserviceservice = ClaimServiceService.objects.create(
            service = service,
            claim_service = claimservice1,
            qty_displayed = 5,
            qty_provided = 4,
            price_asked = 300,
        )
        claimserviceitem = ClaimServiceItem.objects.create(
            item = item,
            claim_service = claimservice1,
            qty_displayed = 2,
            qty_provided = 3,
            price_asked = 500,
        )
        serviceitem = ServiceItem.objects.create(
            servicelinkedItem=service,
            item = clalimitem1.item,
            price_asked = 12,
            qty_provided = 11
        )

        validate_claim(claim1, True)
        validate_assign_prod_to_claimitems_and_services(claim1)
        process_dedrem(claim1, -1, True)
        claimservice1.refresh_from_db()
        # claimservicesitem.qty_provided 2 is not equal to qty_displayed on the claimserviceservice
        # so the price_adjusted is set to 0
        self.assertEqual(claimservice1.price_adjusted, 0)

        claimserviceservice.qty_displayed = 2
        claimserviceservice.save()
        claimserviceservice.refresh_from_db
        validate_claim(claim1, True)
        validate_assign_prod_to_claimitems_and_services(claim1)
        process_dedrem(claim1, -1, True)
        claimservice1.refresh_from_db()
        # service item.qty_provided 11 is not equal to qty_displayed on the claimserviceitem
        # so the price_adjusted is set to 0
        self.assertEqual(claimservice1.price_adjusted, 0)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1)
        # tearDown
        dedrem_qs.delete()
        # serviceservice.delete()
        claimserviceservice.delete()
        claimserviceitem.delete()
        claimservice1.delete()
        serviceitem.delete()
        clalimitem1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_item.delete()
        product_service.delete()
        pricelist_detail1.delete()
        pricelist_detail2.delete()
        service.delete()
        item.delete()
        product.delete()
        ClaimConfig.native_code_for_services=True

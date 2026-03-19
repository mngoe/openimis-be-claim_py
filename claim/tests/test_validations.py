from claim.services import (
    update_claims_dedrems,
    set_claims_status,
    ClaimSubmitService,
    processing_claim,
)
from claim.models import (
    Claim,
    ClaimDedRem,
    ClaimItem,
    ClaimDetail,
    ClaimService,
    ClaimServiceItem,
    ClaimServiceService,
)
from claim.test_helpers import (
    create_test_claim,
    create_test_claimservice,
    create_test_claimitem,
    mark_test_claim_as_processed,
)
from core.test_helpers import create_test_officer, create_test_interactive_user
from datetime import date, timedelta, datetime
from claim.validations import (
    get_claim_category,
    validate_claim,
    REJECTION_REASON_WAITING_PERIOD_FAIL,
    REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
    REJECTION_REASON_TARGET_DATE,
)
from django.test import TestCase
from insuree.test_helpers import create_test_insuree
from location.test_helpers import create_test_health_facility

from product.models import ProductItemOrService
from medical.test_helpers import create_test_service, create_test_item
from medical_pricelist.test_helpers import (
    add_service_to_hf_pricelist,
    add_item_to_hf_pricelist,
    update_pricelist_service_detail_in_hf_pricelist,
    update_pricelist_item_detail_in_hf_pricelist,
)
from policy.test_helpers import create_test_policy2

# default arguments should not pass a list or a dict because they're mutable but we don't risk mutating them here:
# noinspection PyDefaultArgument,DuplicatedCode
from product.test_helpers import (
    create_test_product,
    create_test_product_service,
    create_test_product_item,
)
from copy import copy

import uuid


class ValidationTest(TestCase):
    service_H = None
    service_O = None
    service_D = None
    service_A = None
    service_A_invalid = None

    def setUp(self) -> None:
        super(ValidationTest, self).setUp()

        self.user = create_test_interactive_user()

        self.service_H = create_test_service("H")
        self.service_O = create_test_service("O")
        self.service_D = create_test_service("D")
        self.service_A = create_test_service("A")
        self.service_A_invalid = create_test_service("A", False)
        self.product = create_test_product("Valitst")
        self.item_1 = create_test_item("D")

    def test_get_claim_category_S(self):
        # Given
        claim = create_test_claim(product=self.product)
        service1 = create_test_claimservice(claim, "S", product=self.product)

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEqual(category, "S")

        # tearDown
        service1.delete()
        claim.delete()

    def test_get_claim_category_D(self):
        # Given
        claim = create_test_claim(product=self.product)
        service1 = create_test_claimservice(claim, "D", product=self.product)

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEqual(category, "D")

        # tearDown
        service1.delete()
        claim.delete()

    def test_get_claim_category_mix(self):
        # Given
        claim = create_test_claim(product=self.product)
        services = [
            create_test_claimservice(claim, "H", product=self.product),
            create_test_claimservice(claim, "O", product=self.product),
            create_test_claimservice(claim, None, product=self.product),
            create_test_claimservice(claim, "A", product=self.product),
            create_test_claimservice(claim, "S", product=self.product),
        ]

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEqual(category, "S")

        # tearDown
        for service in services:
            service.delete()
        claim.delete()

    def test_get_claim_category_some_invalid(self):
        # Given
        claim = create_test_claim(product=self.product)
        services = [
            create_test_claimservice(claim, "H", False, product=self.product),
            create_test_claimservice(claim, "A", False, product=self.product),
            create_test_claimservice(claim, "S", product=self.product),
        ]

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEqual(category, "S")

        # tearDown
        for service in services:
            service.delete()
        claim.delete()

    def test_get_claim_category_null(self):
        # Given
        claim = create_test_claim(product=self.product)
        claim.date_to = None
        claim.save()
        create_test_claimservice(claim, None, product=self.product)

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEqual(category, "V")

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
    #     self.assertEqual(len(errors), 1, "The claim should fail the target date validation")
    #     self.assertEqual(errors[0].code, 9, "The claim should fail the target date validation with code 9")
    #     self.assertTrue("date" in errors[0].message.lower())
    #
    #     # tearDown
    #     service1.delete()
    #     claim.delete()

    def test_validate_pricelist_hf1(self):
        # When the claimitem points to a pricelist that doesn't correspond to the claim HF
        hf_without_pricelist = create_test_health_facility(code="HF_NP")
        hf_without_pricelist.items_pricelist = None
        hf_without_pricelist.services_pricelist = None
        self.assertIsNotNone(
            hf_without_pricelist,
            "This test requires a health facility without a price list item",
        )
        # Given

        claim = create_test_claim(
            {"health_facility_id": hf_without_pricelist.id}, product=self.product
        )

        service1 = create_test_claimservice(claim, "S", custom_props={})

        item1 = create_test_claimitem(claim, "D", True, custom_props={})
        # When
        errors = validate_claim(claim, True)
        # Then
        claim.refresh_from_db()
        service1.refresh_from_db()
        item1.refresh_from_db()
        self.assertGreaterEqual(len(errors), 1, "Should raise at least one error")
        error1 = [e for e in errors if e["code"] == 1]  # all services rejected
        self.assertGreaterEqual(len(error1), 1, "There should be an error code 1")
        self.assertEqual(item1.rejection_reason, 2, "Database was updated with rejection reason")

    def test_validate_policy(self):
        # When the insuree family is invalid
        # Given
        invalid_insuree = create_test_insuree(family_custom_props={"validity_to": "2019-01-01"})
        self.assertIsNotNone(invalid_insuree)
        claim = create_test_claim(
            {"insuree_id": invalid_insuree.id}, product=self.product
        )
        service1 = create_test_claimservice(claim, "S", product=self.product)
        item1 = create_test_claimitem(claim, "D", product=self.product)

        # When
        errors = validate_claim(claim, True)

        # Then
        claim.refresh_from_db()
        item1.refresh_from_db()
        self.assertGreaterEqual(len(errors), 1, "Should raise at least one error")
        error7 = [e for e in errors if e['code'] == 7]
        self.assertGreaterEqual(len(error7), 1, "There should be 1 error code 7: invalid insuree")
        self.assertEqual(item1.rejection_reason, 7, "Database was updated with rejection reason")

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
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("V")
        # A first claim for a visit should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id}, product=self.product)
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=self.product
        )
        errors = validate_claim(claim1, True)
        self.assertEqual(len(errors), 0, "The first visit should be accepted")

        # a second visit should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id}, product=self.product)
        create_test_claimservice(claim2, "V", product=self.product)
        errors = validate_claim(claim2, True)
        # TODO Temporarily disabled
        # self.assertGreater(len(errors), 0, "The second visit should be refused")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

    def test_validate_patient_category(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={"max_no_visits": 1})
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("V", custom_props={"patient_category": 1})

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id}, product=self.product)
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=self.product
        )
        errors = validate_claim(claim1, True)

        # Then
        claim1.refresh_from_db()
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[0]['code'], 1)  # claimed rejected because all services are rejected
        self.assertEqual(claim1.services.first().rejection_reason, 4)  # reason is wrong insuree mask

    def test_validate_late_policy(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree(custom_props={'chfid': "testlatepol"})
        self.assertIsNotNone(insuree)
        product = create_test_product("LTEPOL")
        late_insert = datetime.now()
        policy, insuree_policy = create_test_policy2(
            product, insuree, link=True, custom_props={
                "validity_from": late_insert
            })
        service = create_test_service("V")

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id}, product=self.product)
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=self.product
        )
        errors = validate_claim(claim1, True)

        # Then
        self.assertEqual(len(errors), 0)

    def test_frequency(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("CSECT")
        service = create_test_service(
            "C", custom_props={"code": "G34B", "frequency": 180}
        )

        # A first claim for a visit should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id}, product=product)
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=product
        )
        claim_service = ClaimSubmitService(self.user)
        claim, errors = claim_service.submit_claim(claim1, True)

        self.assertEqual(len(errors), 0, "The first visit should be accepted")

        # a second visit should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        claim2.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim2.health_facility.save()
        create_test_claimservice(
            claim2, custom_props={"service_id": service.id}
        )
        claim, errors = claim_service.submit_claim(claim2, True)
        self.assertGreater(
            len(errors),
            0,
            "The second service should be refused as it is withing 180 days",
        )

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

    def test_limit_no(self):
        # When the insuree already reaches his limit number
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("CSECT")
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("C", custom_props={"code": "G34B"})
        create_test_product_service(
            product, service, custom_props={"limit_no_adult": 1}
        )

        # A first claim for a visit should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id}, product=product)
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        add_service_to_hf_pricelist(
            service, claim1.health_facility
        )
        claim1.refresh_from_db()
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id, "qty_provided": 1}
        )
        claim1.refresh_from_db()
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEqual(len(errors), 0, "The first visit should be accepted")

        # a second visit should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        create_test_claimservice(
            claim2, custom_props={"service_id": service.id}
        )
        claim2.refresh_from_db()
        errors = validate_claim(claim2, True)
        self.assertGreater(len(errors), 0, "The second service should be refused")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

    def test_limit_delivery(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("DELIV", custom_props={"max_no_delivery": 1})
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("D", custom_props={"code": "G34C"})
        create_test_product_service(product, service)

        # A first claim for a delivery should be accepted
        start_date = date.today() - timedelta(days=12)
        # A first claim for a visit within the waiting period should be refused
        claim1 = create_test_claim(
            {
                "insuree_id": insuree.id,
                "date_from": start_date,
                "date_to": start_date,
                "date_claimed": start_date,
            },
            product=product,
        )
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=product
        )

        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEqual(len(errors), 0, "The first visit should be accepted")

        # a second delivery should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id}, product=product)
        claim2.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim2.health_facility.save()
        create_test_claimservice(
            claim2, custom_props={"service_id": service.id}, product=product
        )
        errors = validate_claim(claim2, True)
        self.assertGreater(
            len(errors),
            0,
            "The second service should fail because there is already one delivery",
        )

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

    def test_limit_hospital(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product(
            "DELIV", custom_props={"max_no_hospitalization": 1}
        )
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("H", custom_props={"code": "HHHH"})

        # A first claim for a delivery should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id}, product=product)
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=product
        )
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEqual(len(errors), 0, "The first hospitalization should be accepted")

        # a second delivery should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id}, product=product)
        create_test_claimservice(
            claim2, custom_props={"service_id": service.id}, product=product
        )
        errors = validate_claim(claim2, True)
        self.assertGreater(
            len(errors),
            0,
            "The second service should fail because there is already one hospitalization",
        )

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

    def test_limit_surgery(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("DELIV", custom_props={"max_no_surgery": 1})
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("S", custom_props={"code": "SSSS"})

        # A first claim for a delivery should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id}, product=product)
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=product
        )
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEqual(len(errors), 0, "The first surgery should be accepted")

        # a second delivery should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id}, product=product)
        create_test_claimservice(
            claim2, custom_props={"service_id": service.id}, product=product
        )
        errors = validate_claim(claim2, True)
        self.assertGreater(
            len(errors),
            0,
            "The second service should fail because there is already one surgery",
        )

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

    def test_limit_visit(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("DELIV", custom_props={"max_no_visits": 1})
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("V", custom_props={"code": "VVVV"})

        # A first claim for a delivery should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id}, product=product)
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        claim1.date_to = None
        claim1.save()
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=product
        )
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEqual(len(errors), 0, "The first visit should be accepted")

        # a second delivery should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        claim2.date_to = None
        claim2.save()
        create_test_claimservice(
            claim2, custom_props={"service_id": service.id}
        )
        errors = validate_claim(claim2, True)
        self.assertGreater(
            len(errors),
            0,
            "The second service should fail because there is already one visit",
        )

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

    def test_waiting_period(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        child_insuree = create_test_insuree(
            with_family=False,
            custom_props={
                "dob": date.today() - timedelta(hours=24 * 365 * 2),
                "family": insuree.family,
            },
        )
        self.assertIsNotNone(insuree)
        product = create_test_product("CSECT")
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        policy_child, insuree_policy_child = create_test_policy2(
            product, child_insuree, link=True
        )
        service = create_test_service("C")
        create_test_product_service(
            product,
            service,
            custom_props={"waiting_period_adult": 6, "waiting_period_child": 0},
        )

        start_date = policy.effective_date + timedelta(days=2)
        end_date = policy.effective_date + timedelta(days=3)
        # A first claim for a visit within the waiting period should be refused

        claim1 = create_test_claim(
            {
                "insuree_id": insuree.id,
                "date_from": start_date,
                "date_to": None,
                "date_claimed": end_date,
            }
        )
        add_service_to_hf_pricelist(
            service, claim1.health_facility
        )
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}
        )
        claim1.refresh_from_db()
        errors = validate_claim(claim1, True)
        self.assertEqual(
            len(errors), 2, "An adult visit within the waiting period should be refused"
        )
        self.assertEqual(
            claim1.services.first().rejection_reason,
            REJECTION_REASON_WAITING_PERIOD_FAIL,
        )
        start_date = policy.effective_date + timedelta(days=183)
        end_date = policy.effective_date + timedelta(days=184)
        # A 2nd claim for a visit within the waiting period should be refused
        claim2 = create_test_claim(
            {
                "insuree_id": insuree.id,
                "date_from": start_date,
                "date_to": None,
                "date_claimed": end_date,
            }
        )
        create_test_claimservice(
            claim2, custom_props={"service_id": service.id}
        )
        errors = validate_claim(claim2, True)
        self.assertEqual(
            len(errors), 0, "This one should be accepted as after the waiting period"
        )

        # a child should not have the waiting period
        claim3 = create_test_claim(
            {
                "insuree_id": child_insuree.id,
                "date_from": start_date,
                "date_to": None,
                "date_claimed": end_date,
            }
        )

        create_test_claimservice(
            claim3, custom_props={"service_id": service.id}
        )
        errors = validate_claim(claim3, True)
        self.assertEqual(len(errors), 0, "The child has no waiting period")

    def __make_history(self, obj, pivot_date):
        histo = copy(obj)
        histo.id = None
        if hasattr(histo, "uuid"):
            setattr(histo, "uuid", uuid.uuid4())
        histo.validity_to = pivot_date
        histo.legacy_id = obj.id
        histo.save()
        obj.validity_from = pivot_date
        obj.save()

    def test_product_time_correlation(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={})

        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("V", custom_props={})
        item = create_test_item("D", custom_props={})

        claim1 = create_test_claim({"insuree_id": insuree.id})
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=product
        )
        create_test_claimitem(
            claim1, "D", custom_props={"item_id": item.id}, product=product
        )
        errors = validate_claim(claim1, True)
        self.__make_history(
            product, (claim1.validity_to or claim1.validity_from) + timedelta(days=1)
        )
        self.assertEqual(len(errors), 0)

    def test_submit_claim_dedrem(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={})
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("V", custom_props={})
        item = create_test_item("D", custom_props={})

        claim1 = create_test_claim({"insuree_id": insuree.id})
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=product
        )
        item1 = create_test_claimitem(
            claim1, "D", custom_props={"item_id": item.id}, product=product
        )
        errors = validate_claim(claim1, True)
        errors = processing_claim(claim1, self.user, is_process=True)
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
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1, *ClaimDedRem.filter_validity())
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 1400)
        self.assertEqual(dedrem1.rem_op, 1400)
        self.assertIsNone(dedrem1.rem_ip)
        self.assertEqual(dedrem1.rem_surgery, 0)
        self.assertEqual(dedrem1.rem_consult, 0)
        self.assertEqual(dedrem1.rem_hospitalization, 0)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

    def test_submit_claim_dedrem_limit_delivery(self):
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service("D", custom_props={})
        item = create_test_item("D", custom_props={})

        product = create_test_product(
            "BCUL0001",
            custom_props={
                "name": "Basic Cover Ultha",
                "lump_sum": 10_000,
                "max_amount_delivery": 55,
            },
        )

        policy, insuree_policy = create_test_policy2(product, insuree, link=True)

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id})
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=product
        )
        item1 = create_test_claimitem(
            claim1, "D", custom_props={"item_id": item.id}, product=product
        )
        errors = processing_claim(claim1, self.user, is_process=True)

        self.assertEqual(len(errors), 0)
        # Then
        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()

        self.assertEqual(item1.price_adjusted, 100)
        self.assertEqual(item1.price_valuated, 55)
        self.assertEqual(item1.deductable_amount, 0)
        self.assertEqual(item1.exceed_ceiling_amount, 0)
        self.assertIsNone(item1.exceed_ceiling_amount_category)
        self.assertEqual(item1.remunerated_amount, 55)
        self.assertEqual(claim1.status, Claim.STATUS_VALUATED)
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1, *ClaimDedRem.filter_validity())
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 55)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

    def test_submit_claim_dedrem_limit_consultation(self):
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service("C", custom_props={})
        item = create_test_item("C", custom_props={})

        product = create_test_product(
            "BCUL0001",
            custom_props={
                "name": "Basic Cover Ultha",
                "lump_sum": 10000,
                "max_amount_consultation": 55,
                "ceiling_interpretation": "I",
            },
        )

        policy, insuree_policy = create_test_policy2(product, insuree, link=True)

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id})
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        claim1.date_to = None
        claim1.save()
        service1 = create_test_claimservice(
            claim1,
            custom_props={"service_id": service.id, "price_asked": 100},
            product=product,
        )
        item1 = create_test_claimitem(
            claim1,
            "C",
            custom_props={"item_id": item.id, "price_asked": 100},
            product=product,
        )
        errors = processing_claim(claim1, self.user, True)

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
        self.assertEqual(claim1.audit_user_id_process, self.user.id_for_audit)
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1)
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.audit_user_id, self.user.id_for_audit)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 55)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

    def test_submit_claim_dedrem_limit_antenatal(self):
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service("A", custom_props={})
        item = create_test_item("A", custom_props={})

        product = create_test_product(
            "BCUL0001",
            custom_props={
                "name": "Basic Cover Ultha",
                "lump_sum": 10_000,
                "max_amount_antenatal": 55,
            },
        )

        policy, insuree_policy = create_test_policy2(product, insuree, link=True)

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id})
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        service1 = create_test_claimservice(
            claim1,
            custom_props={
                "service_id": service.id,
            },
            product=product,
        )
        item1 = create_test_claimitem(
            claim1,
            "A",
            custom_props={
                "item_id": item.id,
            },
            product=product,
        )
        # errors = validate_claim(claim1, True)
        errors = processing_claim(claim1, self.user, True)
        self.assertEqual(len(errors), 0)

        # Then
        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()
        self.assertEqual(len(errors), 0)
        self.assertEqual(item1.price_adjusted, 100)
        self.assertEqual(item1.price_valuated, 55)  # limit antenatal
        self.assertEqual(item1.deductable_amount, 0)
        self.assertEqual(item1.exceed_ceiling_amount, 0)
        self.assertIsNone(item1.exceed_ceiling_amount_category)
        self.assertEqual(item1.remunerated_amount, 55)
        self.assertEqual(claim1.status, Claim.STATUS_VALUATED)
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1, *ClaimDedRem.filter_validity())
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 55)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

    def test_review_reject_update_dedrem(self):
        """
        This test creates a claim, submits it so that it gets dedrem entries,
        then submits a review rejecting part of it, then process the claim.
        It should not be processed (which was ok) but the dedrem should be deleted.
        """
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service(
            "A", custom_props={"name": "test_review_reject_delete_dedrem"}
        )
        item = create_test_item(
            "A", custom_props={"name": "test_review_reject_delete_dedrem"}
        )

        product = create_test_product(
            "BCUL0001",
            custom_props={
                "name": "Basic Cover Ultha deldedrem",
                "lump_sum": 10_000,
            },
        )

        policy, insuree_policy = create_test_policy2(product, insuree, link=True)

        claim1 = create_test_claim({"insuree_id": insuree.id}, product=product)
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        service1 = create_test_claimservice(
            claim1,
            custom_props={
                "service_id": service.id,
                "qty_provided": 2,
                "origin": ProductItemOrService.ORIGIN_PRICELIST,
            },
            product=product,
        )
        item1 = create_test_claimitem(
            claim1,
            "A",
            custom_props={
                "item_id": item.id,
                "qty_provided": 3,
                "origin": ProductItemOrService.ORIGIN_PRICELIST,
            },
            product=product,
        )
        errors = validate_claim(claim1, True)

        self.assertEqual(len(errors), 0)
        # Make sure that the dedrem was generated
        dedrem = ClaimDedRem.objects.filter(claim=claim1).first()
        self.assertIsNotNone(dedrem)
        self.assertEqual(dedrem.rem_g, 500)  # 100*2 + 100*3 (pricelist origin)

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
        update_claims_dedrems(None, self.user, [claim1])

        # Then dedrem should have been updated
        dedrem = ClaimDedRem.objects.filter(claim=claim1, *ClaimDedRem.filter_validity()).first()
        self.assertIsNotNone(dedrem)
        self.assertEqual(dedrem.rem_g, 90)  # 37*1 + 53*1

    def test_review_reject_delete_dedrem(self):
        """
        This test creates a claim, submits it so that it gets dedrem entries,
        then submits a review rejecting part of it, then process the claim.
        It should not be processed (which was ok) but the dedrem should be deleted.
        """
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        service = create_test_service(
            "A", custom_props={"name": "test_review_reject_delete_dedrem"}
        )
        item = create_test_item(
            "A", custom_props={"name": "test_review_reject_delete_dedrem"}
        )

        product = create_test_product(
            "BCUL0001",
            custom_props={
                "name": "Basic Cover Ultha deldedrem",
                "lump_sum": 10000,
            },
        )

        policy, insuree_policy = create_test_policy2(product, insuree, link=True)

        claim1 = create_test_claim({"insuree_id": insuree.id})
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        service1 = create_test_claimservice(
            claim1,
            custom_props={"service_id": service.id, "qty_provided": 2},
            product=product,
        )
        item1 = create_test_claimitem(
            claim1,
            "A",
            custom_props={"item_id": item.id, "qty_provided": 3},
            product=product,
        )
        errors = processing_claim(claim1, self.user, True)

        self.assertEqual(len(errors), 0)
        # Make sure that the dedrem was generated
        dedrem = ClaimDedRem.objects.filter(claim=claim1).first()
        self.assertIsNotNone(dedrem)
        self.assertEqual(dedrem.rem_g, 500)  # 100*2 + 100*3 (pricelist origin)

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

        # The claim should be globally rejected since the review rejected all items/svc
        claim1.refresh_from_db()
        item1.refresh_from_db()
        service1.refresh_from_db()
        self.assertEqual(claim1.status, Claim.STATUS_REJECTED)
        self.assertEqual(claim1.rejection_reason, REJECTION_REASON_INVALID_ITEM_OR_SERVICE)
        self.assertEqual(item1.status, ClaimDetail.STATUS_REJECTED)
        self.assertEqual(item1.rejection_reason, -1)
        self.assertEqual(service1.status, ClaimDetail.STATUS_REJECTED)
        self.assertEqual(service1.rejection_reason, -1)

        # Then dedrem should have been deleted
        dedrem = ClaimDedRem.objects.filter(claim=claim1).first()
        self.assertIsNone(dedrem)

    def test_submit_claim_dedrem_update_pricelist_detail(self):
        """
        This test replicates the functionality of test_submit_claim_dedrem,
        with the additional step of updating items and services prior to dedrem calculation.
        Despite the updates, the results should remain unaffected as the prices
        for these services/items are sourced from the pricelist detail's state at the time of
        coalse(claim.dateto, claim.datefrom).
        """
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={})
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service("V", custom_props={})
        item = create_test_item("D", custom_props={})

        claim1 = create_test_claim({"insuree_id": insuree.id})
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        service1 = create_test_claimservice(
            claim1, custom_props={"service_id": service.id}, product=product
        )
        item1 = create_test_claimitem(
            claim1, "D", custom_props={"item_id": item.id}, product=product
        )
        errors = validate_claim(claim1, True)
        pricelist_detail1 = claim1.health_facility.services_pricelist.details.filter(
            service=service
        ).first()
        update_pricelist_service_detail_in_hf_pricelist(
            pricelist_detail1, custom_props={"price_overrule": 21}
        )
        pricelist_detail2 = claim1.health_facility.items_pricelist.details.filter(
            item=item
        ).first()
        update_pricelist_item_detail_in_hf_pricelist(
            pricelist_detail2, custom_props={"price_overrule": 37}
        )
        claim1.refresh_from_db()
        errors += processing_claim(claim1, self.user, True)
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
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1, *ClaimDedRem.filter_validity())
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 1400)
        self.assertEqual(dedrem1.rem_op, 1400)
        self.assertIsNone(dedrem1.rem_ip)
        self.assertEqual(dedrem1.rem_surgery, 0)
        self.assertEqual(dedrem1.rem_consult, 0)
        self.assertEqual(dedrem1.rem_hospitalization, 0)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

    def test_set_status(self):
        class DummyUser:
            id_for_audit = -1
            id = 1

        insuree = create_test_insuree()
        create_test_officer(
            villages=[insuree.current_village or insuree.family.location]
        )
        claim = create_test_claim(
            custom_props={"status": Claim.STATUS_CHECKED, "insuree": insuree},
            product=self.product,
        )
        set_claims_status(
            [claim.uuid], "feedback_status", Claim.FEEDBACK_SELECTED, user=DummyUser()
        )
        claim.refresh_from_db()
        self.assertEqual(claim.feedback_status, Claim.FEEDBACK_SELECTED)

    def test_submit_claim_with_different_packatypes(self):
        from claim.apps import ClaimConfig

        ClaimConfig.verify_quantities = True
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={})
        policy, insuree_policy = create_test_policy2(product, insuree, link=True)
        service = create_test_service(
            "V",
            custom_props={
                "code": "V-DP",
                "packagetype": "F",
                "price": 1000,
                "validity_from": datetime(2000, 1, 1),
            },
        )
        service2 = create_test_service(
            "V",
            custom_props={
                "code": "V-DP2",
                "packagetype": "F",
                "price": 750,
                "validity_from": datetime(2000, 1, 1),
            },
        )
        service3 = create_test_service(
            "V",
            custom_props={
                "code": "V-DP3",
                "packagetype": "P",
                "price": 750,
                "validity_from": datetime(2000, 1, 1),
            },
        )
        item = create_test_item("D", custom_props={})
        create_test_product_service(
            product,
            service,
            custom_props={
                "validity_from": datetime(2000, 1, 1),
                "price_origin": ProductItemOrService.ORIGIN_CLAIM,
            },
        )
        create_test_product_service(
            product,
            service2,
            custom_props={
                "validity_from": datetime(2000, 1, 1),
                "price_origin": ProductItemOrService.ORIGIN_CLAIM,
            },
        )
        create_test_product_service(
            product,
            service2,
            custom_props={
                "validity_from": datetime(2000, 1, 1),
                "price_origin": ProductItemOrService.ORIGIN_CLAIM,
            },
        )
        create_test_product_item(
            product,
            item,
            custom_props={
                "validity_from": datetime(2000, 1, 1),
                "price_origin": ProductItemOrService.ORIGIN_PRICELIST,
            },
        )

        claim1 = create_test_claim({"insuree_id": insuree.id})
        add_service_to_hf_pricelist(
            service, claim1.health_facility
        )
        add_service_to_hf_pricelist(
            service2, claim1.health_facility
        )
        add_service_to_hf_pricelist(
            service3, claim1.health_facility
        )
        add_item_to_hf_pricelist(item, claim1.health_facility)

        claim1 = create_test_claim({"insuree_id": insuree.id})
        claim1.health_facility.care_type = claim1.health_facility.CARE_TYPE_BOTH
        claim1.health_facility.save()
        claimservice1 = create_test_claimservice(
            claim1,
            custom_props={
                "service_id": service.id,
                "price_asked": 4000,
                "price_valuated": 4000,
            },
        )

        claimservice2 = create_test_claimservice(
            claim1,
            custom_props={
                "service_id": service2.id,
                "price_asked": 4000,
                "price_adjusted": None,
            },
        )
        claimservice3 = create_test_claimservice(
            claim1,
            custom_props={
                "service_id": service3.id,
                "price_asked": 4000,
                "price_adjusted": None,
            },
        )

        clalimitem1 = create_test_claimitem(
            claim1, "D", custom_props={"item_id": item.id, "price_asked": 4000}
        )
        ClaimServiceService.objects.create(
            service=service,
            claim_service=claimservice3,
            qty_displayed=5,
            qty_provided=4,
            price_asked=300,
        )
        ClaimServiceItem.objects.create(
            item=item,
            claim_service=claimservice3,
            qty_displayed=2,
            qty_provided=3,
            price_asked=500,
        )
        # set the service price to 1000 lower than the price_adjusted
        errors = validate_claim(claim1, True)

        self.assertEqual(len(errors), 0)
        # The claimservice1's price_adjusted should be the service price
        # because the price_adjusted is greater than the service (4000 > 1000)
        claimservice1.refresh_from_db()
        self.assertEqual(claimservice1.price_adjusted, 1000)
        clalimitem1.refresh_from_db()
        self.assertEqual(clalimitem1.price_adjusted, 100)
        claimservice2.refresh_from_db()
        self.assertEqual(claimservice2.price_adjusted, 750)
        claimservice3.refresh_from_db()
        self.assertIsNone(claimservice3.price_adjusted)

    def test_validate_claim_before_policy_start(self):
        # When the policy validity starts after the claim date
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)

        future_validity_start = datetime.today() + timedelta(days=10)
        future_validity_end = future_validity_start + timedelta(days=365)

        product = create_test_product("LATE_P")
        create_test_policy2(
            product,
            insuree,
            custom_props={
                "validity_from": future_validity_start,
                "validity_to": future_validity_end + timedelta(days=1)
            },
            link=True
        )
        service = create_test_service("V")

        # Create a claim dated before the policy validity_from
        claim = create_test_claim(
            {"insuree_id": insuree.id},
            product=self.product
        )
        claim.health_facility.care_type = claim.health_facility.CARE_TYPE_BOTH
        claim.health_facility.save()
        create_test_claimservice(
            claim, custom_props={"service_id": service.id},
            product=self.product
        )

        # When validating the claim
        errors = validate_claim(claim, True)

        # Then: Expect success
        claim.refresh_from_db()
        print(f"There is the error: {errors}")
        self.assertEqual(len(errors), 0)

    def test_validate_claimitem_future_validity(self):
        # Given
        future_date = date.today() + timedelta(days=30)
        item_future = create_test_item("D", valid=True)
        item_future.validity_from = future_date
        item_future.save()

        claim = create_test_claim({"date_from": date.today(), "date_to": date.today()})
        claim_item = create_test_claimitem(
            claim, custom_props={"item_id": item_future.id}
        )

        # When
        errors = validate_claim(claim, check_max=True)

        # Then
        self.assertTrue(
            any(e["code"] == REJECTION_REASON_TARGET_DATE for e in errors),
            "Item validity should cause rejection",
        )
        claim_item.refresh_from_db()
        self.assertEqual(
            claim_item.rejection_reason,
            REJECTION_REASON_TARGET_DATE,
            "Claim item should be rejected for future validity",
        )

        # tearDown
        claim_item.delete()
        claim.delete()
        item_future.delete()

    def test_validate_claimservice_future_validity(self):
        # Given
        future_date = date.today() + timedelta(days=30)
        service_future = create_test_service("D", valid=True)
        service_future.validity_from = future_date
        service_future.save()
        insuree = create_test_insuree()
        claim = create_test_claim(
            {"insuree": insuree, "date_from": date.today(), "date_to": date.today()}
        )
        claim_service = create_test_claimservice(
            claim, custom_props={"service_id": service_future.id}
        )
        # When
        errors = validate_claim(claim, check_max=True)
        # Then
        self.assertTrue(
            any(e["code"] == REJECTION_REASON_TARGET_DATE for e in errors),
            "Service validity should cause rejection",
        )
        claim_service.refresh_from_db()
        self.assertEqual(
            claim_service.rejection_reason,
            REJECTION_REASON_TARGET_DATE,
            "Claim service should be rejected for future validity",
        )
        # tearDown
        claim_service.delete()
        claim.delete()
        service_future.delete()

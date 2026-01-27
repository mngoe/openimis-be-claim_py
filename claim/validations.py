
import logging
from collections import namedtuple, defaultdict
from decimal import Decimal
from claim.models import (
    ClaimItem,
    Claim,
    ClaimService,
    ClaimDedRem,
    ClaimDetail,
    ClaimServiceService,
    ClaimServiceItem,
)
from core import utils
from datetime import datetime
from core.datetimes.shared import datetimedelta
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _
from medical.models import Service, ServiceService, ServiceItem
from medical_pricelist.models import ItemsPricelistDetail, ServicesPricelistDetail
from policy.models import Policy
from product.models import Product, ProductItem, ProductService, ProductItemOrService
from .apps import ClaimConfig
from .utils import (
    get_queryset_valid_at_date,
    get_valid_policies_qs,
    get_claim_target_date,
)

logger = logging.getLogger(__name__)

REJECTION_REASON_INVALID_ITEM_OR_SERVICE = 1
REJECTION_REASON_NOT_IN_PRICE_LIST = 2
REJECTION_REASON_NO_PRODUCT_FOUND = 3
REJECTION_REASON_CATEGORY_LIMITATION = 4
REJECTION_REASON_FREQUENCY_FAILURE = 5
# REJECTION_REASON_DUPLICATED = 6
REJECTION_REASON_FAMILY = 7
# REJECTION_REASON_ICD_NOT_IN_LIST = 8
REJECTION_REASON_TARGET_DATE = 9
REJECTION_REASON_CARE_TYPE = 10
REJECTION_REASON_MAX_HOSPITAL_ADMISSIONS = 11
REJECTION_REASON_MAX_VISITS = 12
REJECTION_REASON_MAX_CONSULTATIONS = 13
REJECTION_REASON_MAX_SURGERIES = 14
REJECTION_REASON_MAX_DELIVERIES = 15
REJECTION_REASON_QTY_OVER_LIMIT = 16
REJECTION_REASON_WAITING_PERIOD_FAIL = 17
REJECTION_REASON_MAX_ANTENATAL = 19
REJECTION_REASON_INVALID_CLAIM = 20
REJECTION_REASON_NO_COVERAGE = 21

Deductible = namedtuple("Deductible", ["amount", "type", "prev"])


def initialize_dedrem_processing(claim, services):
    """Initialize basic claim processing parameters."""
    errors = []
    logger.debug(f"processing dedrem for claim {claim.uuid}")
    target_date = get_claim_target_date(claim)
    category = get_claim_category(claim, services)
    hospitalization = claim.date_from != target_date
    hf_level = claim.health_facility.level
    return errors, target_date, category, hospitalization, hf_level


def archive_old_dedrems(claim):
    """Archive existing dedrems for the claim."""
    ClaimDedRem.objects.filter(claim_id=claim.id, *ClaimDedRem.filter_validity()).update(
        validity_to=datetime.now()
    )


def fetch_policies(claim, target_date, policies=None):
    """Retrieve valid policies if not provided."""
    if not policies:
        policies = list(
            get_valid_policies_qs(claim.insuree.id, target_date).prefetch_related(
                "product"
            )
        )
    if not policies:
        logger.warning(f"No valid policies found for claim {claim.uuid}")
        claim.status = Claim.STATUS_REJECTED
        claim.rejection_reason = REJECTION_REASON_NO_COVERAGE
        claim.save()
        return None
    return policies


def fetch_items_and_services(claim, items=None, services=None):
    """Retrieve claim items and services if not provided."""
    if items is None:
        items = list(
            claim.items.filter(
                item__isnull=False,
                validity_to__isnull=True,
            ).filter(Q(Q(rejection_reason=0) | Q(rejection_reason__isnull=True)))
        )
    if services is None:
        services = list(
            claim.services.filter(
                service__isnull=False,
                validity_to__isnull=True,
            ).filter(Q(Q(rejection_reason=0) | Q(rejection_reason__isnull=True)))
        )
    return items, services


def get_policy_and_product_info(policies, items, services):
    """Extract policy and product information from provided items and services."""

    policies_id = list(
        set(
            (
                *[s.policy_id for s in services if s.policy_id is not None],
                *[i.policy_id for i in items if i.policy_id is not None],
            )
        )
    )
    products_id = list(
        set(
            (
                *[s.product_id for s in services if s.product_id is not None],
                *[i.product_id for i in items if i.product_id is not None],
            )
        )
    )
    if policies and not policies_id:
        policies_id = [p.id for p in policies]
        products_id = [p.product_id for p in policies]

    return policies_id, products_id


def calculate_hospital_visit(product, hospitalization, hf_level):
    return (
        product.ceiling_interpretation == Product.CEILING_INTERPRETATION_IN_PATIENT
        and hospitalization
    ) or (
        product.ceiling_interpretation == Product.CEILING_INTERPRETATION_HOSPITAL
        and hf_level == "H"
    )


def get_policy_members(policy_id, target_date):
    """Count policy members."""
    return Policy.objects.filter(
        id=policy_id,
        effective_date__lte=target_date,
        expiry_date__gte=target_date,
        validity_to__isnull=True,
    ).count()


def initialize_deductibles_and_ceilings():
    """Initialize deductible and ceiling tracking variables."""
    return {
        "deductible": None,
        "ceiling": None,
        "prev_deductible": None,
        "prev_remunerated": 0,
        "prev_remunerated_consult": 0,
        "prev_remunerated_surgery": 0,
        "prev_remunerated_hospitalization": 0,
        "prev_remunerated_delivery": 0,
        "prev_remunerated_antenatal": 0,
        "remunerated_consultation": 0,
        "remunerated_surgery": 0,
        "remunerated_hospitalization": 0,
        "remunerated_delivery": 0,
        "remunerated_antenatal": 0,
        "relative_prices": False,
        "deducted": 0,
        "remunerated": 0,
    }


def fetch_previous_dedrems(claim, policy_id):
    """Retrieve previous dedrems excluding current claim."""
    return list(
        ClaimDedRem.objects.filter(policy_id=policy_id).exclude(claim_id=claim.id)
    )


def calculate_deductibles_and_ceilings(
    product, claim, demrems, hospital_visit, policy_members
):
    deductibles = initialize_deductibles_and_ceilings()
    ded_g = _get_dedrem("ded", "G", "ded_g", product, claim.insuree, demrems)
    if ded_g:
        deductibles["deductible"] = ded_g
        deductibles["prev_deductible"] = ded_g.prev
    rem_g = _get_dedrem("max", "G", "rem_g", product, claim.insuree, demrems)
    if rem_g:
        deductibles["ceiling"] = rem_g
        deductibles["prev_remunerated"] = rem_g.prev
    if product.max_policy:
        if policy_members > product.threshold:
            if product.max_policy_extra_member:
                deductibles["ceiling"] = Deductible(
                    product.max_policy
                    + (policy_members - product.threshold)
                    * product.max_policy_extra_member,
                    deductibles["ceiling"].type,
                    deductibles["ceiling"].prev,
                )
            if (
                product.max_ceiling_policy
                and deductibles["ceiling"].amount > product.max_ceiling_policy
            ):
                deductibles["ceiling"] = Deductible(
                    product.max_ceiling_policy,
                    deductibles["ceiling"].type,
                    deductibles["ceiling"].prev,
                )
        else:
            deductibles["ceiling"] = Deductible(
                product.max_policy,
                deductibles["ceiling"].type,
                deductibles["ceiling"].prev,
            )
    if not deductibles["deductible"]:
        if hospital_visit:
            ded_ip = _get_dedrem(
                "ded_ip", "I", "ded_ip", product, claim.insuree, demrems
            )
            if ded_ip:
                deductibles["deductible"] = ded_ip
                deductibles["prev_deductible"] = ded_ip.prev
        else:
            ded_op = _get_dedrem(
                "ded_op", "O", "ded_op", product, claim.insuree, demrems
            )
            if ded_op:
                deductibles["deductible"] = ded_op
                deductibles["prev_deductible"] = ded_op.prev
    if not deductibles["ceiling"]:
        if hospital_visit:
            max_ip = _get_dedrem(
                "max_ip", "I", "rem_ip", product, claim.insuree, demrems
            )
            if max_ip:
                deductibles["ceiling"] = max_ip
                deductibles["prev_remunerated"] = max_ip.prev
            if product.max_ip_policy:
                if policy_members > product.threshold:
                    if product.max_policy_extra_member_ip:
                        deductibles["ceiling"] = Deductible(
                            product.max_ip_policy
                            + (policy_members - product.threshold)
                            * product.max_policy_extra_member_ip,
                            deductibles["ceiling"].type,
                            deductibles["ceiling"].prev,
                        )
                    if (
                        product.max_ceiling_policy_ip
                        and deductibles["ceiling"].amount
                        > product.max_ceiling_policy_ip
                    ):
                        deductibles["ceiling"] = Deductible(
                            product.max_ceiling_policy_ip,
                            deductibles["ceiling"].type,
                            deductibles["ceiling"].prev,
                        )
                else:
                    deductibles["ceiling"] = Deductible(
                        product.max_ip_policy,
                        deductibles["ceiling"].type,
                        deductibles["ceiling"].prev,
                    )
        else:
            max_op = _get_dedrem(
                "max_op", "O", "rem_op", product, claim.insuree, demrems
            )
            if max_op:
                deductibles["ceiling"] = max_op
                deductibles["prev_remunerated"] = max_op.prev
            if product.max_op_policy:
                if product.threshold and policy_members > product.threshold:
                    if product.max_policy_extra_member_op:
                        deductibles["ceiling"] = Deductible(
                            product.max_op_policy
                            + (policy_members - product.threshold)
                            * product.max_policy_extra_member_op,
                            deductibles["ceiling"].type,
                            deductibles["ceiling"].prev,
                        )
                    if (
                        product.max_ceiling_policy_op
                        and deductibles["ceiling"].amount
                        > product.max_ceiling_policy_op
                    ):
                        deductibles["ceiling"] = Deductible(
                            product.max_ceiling_policy_op,
                            deductibles["ceiling"].type,
                            deductibles["ceiling"].prev,
                        )
                else:
                    deductibles["ceiling"] = Deductible(
                        product.max_op_policy,
                        deductibles["ceiling"].type,
                        deductibles["ceiling"].prev,
                    )
    return deductibles


def get_pricelist_detail(claim, claim_detail, target_date, detail_is_item):
    """Fetch pricelist detail for item or service."""
    pricelist_detail_qs = (
        ItemsPricelistDetail if detail_is_item else ServicesPricelistDetail
    ).objects.filter(
        itemsvcs_pricelist=(
            claim.health_facility.items_pricelist
            if detail_is_item
            else claim.health_facility.services_pricelist
        ),
        itemsvc=claim_detail.itemsvc,
        itemsvcs_pricelist__validity_to__isnull=True,
    )
    return get_queryset_valid_at_date(pricelist_detail_qs, target_date).first()


def get_product_itemsvc(claim_detail, detail_is_item, tuple_dict=None):
    if tuple_dict is None:
        if detail_is_item:
            product_itemsvc = ProductItem.objects.filter(
                product_id=claim_detail.product_id,
                item_id=claim_detail.item_id,
                validity_to__isnull=True,
            ).first()
        else:
            product_itemsvc = ProductService.objects.filter(
                product_id=claim_detail.product_id,
                service_id=claim_detail.service_id,
                validity_to__isnull=True,
            ).first()
    else:
        product_itemsvc = tuple_dict.get(
            (
                claim_detail.product_id,
                claim_detail.item_id if detail_is_item else claim_detail.service_id,
            )
        )
    if product_itemsvc is None:
        raise ValueError(f"Product {'Item' if detail_is_item else 'Service'} not found")
    return product_itemsvc


def calculate_price_adjusted(
    claim, claim_detail, itemsvc_pricelist_detail, detail_is_item
):
    """Calculate adjusted price for claim detail."""
    pl_price = (
        itemsvc_pricelist_detail.price_overrule
        if itemsvc_pricelist_detail.price_overrule
        else claim_detail.itemsvc.price
    )
    if claim_detail.price_approved is not None:
        return claim_detail.price_approved
    if claim_detail.price_origin == ProductItemOrService.ORIGIN_CLAIM:
        set_price_adjusted = claim_detail.price_asked
        if ClaimConfig.verify_quantities and not detail_is_item:
            service_price = None
            if claim_detail.service.packagetype == "F":
                service_price = claim_detail.service.price
            if (
                service_price
                and (claim_detail.price_adjusted or claim_detail.price_asked)
                > service_price
            ):
                return service_price
        return set_price_adjusted
    set_price_adjusted = pl_price
    if ClaimConfig.verify_quantities and not detail_is_item:
        set_price_adjusted = verify_service_quantities(claim_detail, set_price_adjusted)
    return set_price_adjusted


def verify_service_quantities(claim_detail, set_price_adjusted):
    """Verify service quantities for package services."""
    continue_service_check = True
    if claim_detail.service.packagetype == "P":
        service_services = ServiceService.objects.filter(
            parent=claim_detail.service.id
        ).all()
        claim_service_services = ClaimServiceService.objects.filter(
            claim_service=claim_detail.id
        ).all()
        if len(service_services) == len(claim_service_services):
            for servservice in service_services:
                for claimserviceservice in claim_service_services:
                    if servservice.service.id == claimserviceservice.service.id:
                        if (
                            servservice.qty_provided
                            != claimserviceservice.qty_displayed
                        ):
                            return 0
                if not continue_service_check:
                    break
        else:
            return 0
        continue_item_check = True
        service_items = ServiceItem.objects.filter(parent=claim_detail.service.id).all()
        claim_service_items = ClaimServiceItem.objects.filter(
            claim_service=claim_detail.id
        ).all()
        if len(service_items) == len(claim_service_items):
            for serviceitem in service_items:
                for claimservicesitem in claim_service_items:
                    if serviceitem.item.id == claimservicesitem.item.id:
                        if serviceitem.qty_provided != claimservicesitem.qty_displayed:
                            return 0
                if not continue_item_check:
                    break
        else:
            return 0
    return set_price_adjusted


def process_claim_detail(
    claim,
    claim_detail,
    product_data,
    deductibles,
    category,
    hospital_visit,
    product_itemsvc,
    set_price_adjusted,
    itemsvc_quantity,
):
    """Process individual claim item or service."""
    work_value = int(itemsvc_quantity * set_price_adjusted)
    set_unit_price_adjusted = set_price_adjusted
    set_price_deducted = 0
    exceed_ceiling_amount = 0
    exceed_ceiling_amount_category = 0
    if (
        claim_detail.limitation == ProductItemOrService.LIMIT_FIXED_AMOUNT
        and claim_detail.limitation_value
        and (itemsvc_quantity * claim_detail.limitation_value) < work_value
    ):
        work_value = itemsvc_quantity * claim_detail.limitation_value
    if (
        deductibles["deductible"]
        and deductibles["deductible"].amount
        - deductibles["prev_deductible"]
        - deductibles["deducted"]
        > 0
    ):
        if (
            deductibles["deductible"].amount
            - deductibles["deductible"].prev
            - deductibles["deducted"]
            >= work_value
        ):
            set_price_deducted = work_value
            deductibles["deducted"] += work_value
            set_price_approved = 0
            set_price_remunerated = 0
        else:
            set_price_deducted = (
                deductibles["deductible"].amount
                - deductibles["deductible"].prev
                - deductibles["deducted"]
            )
            work_value -= set_price_deducted
            deductibles["deducted"] += (
                deductibles["deductible"].amount
                - deductibles["deductible"].prev
                - deductibles["deducted"]
            )
    if (
        claim_detail.limitation == ProductItemOrService.LIMIT_CO_INSURANCE
        and claim_detail.limitation_value
    ):
        work_value = claim_detail.limitation_value / 100 * work_value
    work_value, exceed_ceiling_amount_category = apply_category_ceilings(
        product_data, category, work_value, deductibles
    )
    set_price_approved, set_price_remunerated, exceed_ceiling_amount = (
        apply_ceiling_exclusions(
            claim,
            claim_detail,
            product_itemsvc,
            hospital_visit,
            work_value,
            deductibles,
        )
    )
    return {
        "set_price_deducted": set_price_deducted,
        "set_price_approved": set_price_approved,
        "set_price_remunerated": set_price_remunerated,
        "exceed_ceiling_amount": exceed_ceiling_amount,
        "exceed_ceiling_amount_category": exceed_ceiling_amount_category,
        "set_unit_price_adjusted": set_unit_price_adjusted,
        "work_value": work_value,
    }


def apply_category_ceilings(product, category, work_value, deductibles):
    """Apply category-specific ceilings."""
    exceed_ceiling_amount_category = 0
    category_checks = {
        Service.CATEGORY_SURGERY: (
            product.max_amount_surgery,
            "remunerated_surgery",
            "prev_remunerated_surgery",
        ),
        Service.CATEGORY_DELIVERY: (
            product.max_amount_delivery,
            "remunerated_delivery",
            "prev_remunerated_delivery",
        ),
        Service.CATEGORY_ANTENATAL: (
            product.max_amount_antenatal,
            "remunerated_antenatal",
            "prev_remunerated_antenatal",
        ),
        Service.CATEGORY_HOSPITALIZATION: (
            product.max_amount_hospitalization,
            "remunerated_hospitalization",
            "prev_remunerated_hospitalization",
        ),
        Service.CATEGORY_CONSULTATION: (
            product.max_amount_consultation,
            "remunerated_consultation",
            "prev_remunerated_consult",
        ),
    }
    if category != Service.CATEGORY_VISIT and category in category_checks:
        max_amount, remunerated_key, prev_remunerated_key = category_checks[category]
        if max_amount:
            total_remunerated = (
                work_value
                + deductibles[prev_remunerated_key]
                + deductibles[remunerated_key]
            )
            if total_remunerated <= max_amount:
                deductibles[remunerated_key] += work_value
            else:
                if (
                    deductibles[prev_remunerated_key] + deductibles[remunerated_key]
                    >= max_amount
                ):
                    exceed_ceiling_amount_category = work_value
                    work_value = 0
                else:
                    exceed_ceiling_amount_category = total_remunerated - max_amount
                    work_value -= exceed_ceiling_amount_category
                    deductibles[remunerated_key] += work_value
    return work_value, exceed_ceiling_amount_category


def apply_ceiling_exclusions(
    claim, claim_detail, product_itemsvc, hospital_visit, work_value, deductibles
):
    """Apply ceiling exclusions based on patient type and visit type."""
    exceed_ceiling_amount = 0
    set_price_approved = work_value
    set_price_remunerated = work_value
    if product_itemsvc and (
        (
            claim.insuree.is_adult
            and hospital_visit
            and product_itemsvc.ceiling_exclusion_adult in ("B", "H")
        )
        or (
            claim.insuree.is_adult
            and not hospital_visit
            and product_itemsvc.ceiling_exclusion_adult in ("B", "N")
        )
        or (
            not claim.insuree.is_adult
            and hospital_visit
            and product_itemsvc.ceiling_exclusion_child in ("B", "H")
        )
        or (
            not claim.insuree.is_adult
            and not hospital_visit
            and product_itemsvc.ceiling_exclusion_child in ("B", "N")
        )
    ):
        exceed_ceiling_amount = 0
    else:
        if deductibles["ceiling"] and deductibles["ceiling"].amount > 0:
            remaining_ceiling = (
                deductibles["ceiling"].amount
                - deductibles["prev_remunerated"]
                - deductibles["remunerated"]
            )
            if remaining_ceiling > 0:
                if remaining_ceiling >= work_value:
                    deductibles["remunerated"] += work_value
                else:
                    exceed_ceiling_amount = work_value - remaining_ceiling
                    set_price_approved = remaining_ceiling
                    set_price_remunerated = remaining_ceiling
                    deductibles["remunerated"] += remaining_ceiling
            else:
                exceed_ceiling_amount = work_value
                set_price_approved = 0
                set_price_remunerated = 0
        else:
            deductibles["remunerated"] += work_value
    return set_price_approved, set_price_remunerated, exceed_ceiling_amount


def update_claim_detail(claim_detail, is_process, result, relative_prices):
    """Update claim detail with processed values."""
    if claim_detail.price_approved is None:
        claim_detail.price_adjusted = result["set_unit_price_adjusted"]
    if is_process:
        if claim_detail.price_origin == ProductItemOrService.ORIGIN_RELATIVE:
            claim_detail.price_valuated = None
            claim_detail.deductable_amount = result["set_price_deducted"]
            claim_detail.exceed_ceiling_amount = result["exceed_ceiling_amount"]
            relative_prices = True
        else:
            claim_detail.price_valuated = result["set_price_approved"]
            claim_detail.deductable_amount = result["set_price_deducted"]
            claim_detail.exceed_ceiling_amount = result["exceed_ceiling_amount"]
            claim_detail.remunerated_amount = result["set_price_remunerated"]
    claim_detail.save()
    return relative_prices


def create_claim_dedrem(claim, policy, user, deductibles, hospital_visit):
    """Create new ClaimDedRem record."""
    now = datetime.now()
    claim_ded_rem_to_create = {
        "policy": policy,
        "insuree": claim.insuree,
        "claim": claim,
        "ded_g": deductibles["deducted"],
        "rem_g": deductibles["remunerated"],
        "rem_consult": deductibles["remunerated_consultation"],
        "rem_hospitalization": deductibles["remunerated_hospitalization"],
        "rem_delivery": deductibles["remunerated_delivery"],
        "rem_antenatal": deductibles["remunerated_antenatal"],
        "rem_surgery": deductibles["remunerated_surgery"],
        "audit_user_id": getattr(user, "id_for_audit", -1),
        "validity_from": now,
    }
    if hospital_visit:
        claim_ded_rem_to_create["ded_ip"] = deductibles["deducted"]
        claim_ded_rem_to_create["rem_ip"] = deductibles["remunerated"]
    else:
        claim_ded_rem_to_create["ded_op"] = deductibles["deducted"]
        claim_ded_rem_to_create["rem_op"] = deductibles["remunerated"]
    ClaimDedRem.objects.create(**claim_ded_rem_to_create)


def update_claim_status(claim, is_process, deductibles, user, products_id):
    """Update final claim status and related fields."""
    now = datetime.now()
    if not deductibles:
        logger.warning(
            f"claim {claim.uuid} did not have any item or service to valuate."
        )
        claim.status = Claim.STATUS_REJECTED
        return [
            {
                "code": REJECTION_REASON_NO_PRODUCT_FOUND,
                "message": _("claim.validation.assign_prod.elt.no_product_code")
                % {"code": claim.code, "element": "all"},
                "detail": claim.uuid,
            }
        ]
    elif is_process:
        claim.approved = deductibles["remunerated"]
        if deductibles["relative_prices"]:
            claim.status = Claim.STATUS_PROCESSED
            claim.remunerated = None
        else:
            claim.status = Claim.STATUS_VALUATED
            claim.remunerated = deductibles["remunerated"]
        claim.audit_user_id_process = getattr(user, "id_for_audit", -1)
        claim.process_stamp = now
        claim.date_processed = now
        if claim.feedback_status == Claim.FEEDBACK_SELECTED:
            claim.feedback_status = Claim.FEEDBACK_BYPASSED
        if claim.review_status == Claim.REVIEW_SELECTED:
            claim.review_status = Claim.REVIEW_BYPASSED
    if not products_id:
        logger.warning(f"claim {claim.uuid} is not covered by any product.")
        claim.status = Claim.STATUS_REJECTED
        return [
            {
                "code": REJECTION_REASON_NO_PRODUCT_FOUND,
                "message": _("claim.validation.product_family.no_item_or_service")
                % {"code": claim.code, "element": "all"},
                "detail": claim.uuid,
            }
        ]
    claim.save()
    return []


def validate_assign_prod_to_claimitems_and_services(
    claim,
    policies=None,
    services=None,
    items=None,
    product_items_by_item_id=None,
    product_services_by_service_id=None,
    target_date=None,
):
    errors = []
    if not policies:
        policies = get_valid_policies_qs(claim.insuree.id, target_date)
    logger.debug(
        "[claim: %s] validate_assign_prod_to_claimitems_and_services", claim.uuid
    )
    if items is None:
        items = list(
            claim.items.filter(validity_to__isnull=True).filter(
                Q(rejection_reason=0) | Q(rejection_reason__isnull=True)
            )
        )
    if services is None:
        services = list(
            claim.services.filter(validity_to__isnull=True).filter(
                Q(rejection_reason=0) | Q(rejection_reason__isnull=True)
            )
        )
    for claimitem in [i for i in items if not i.rejection_reason]:
        logger.debug("[claim: %s] validating item %s", claim.uuid, claimitem.id)
        elt_qs = product_items_by_item_id.get(
            claimitem.item_id,
            ProductItem.objects.filter(
                item_id=claimitem.item_id, product__in=[p.product for p in policies]
            ),
        )
        errors += validate_assign_prod_elt(
            claim,
            claimitem,
            claimitem.item,
            elt_qs,
            target_date=target_date,
            policies=policies,
        )
    for claimservice in [s for s in services if not s.rejection_reason]:
        logger.debug("[claim: %s] validating service %s", claim.uuid, claimservice.id)
        elt_qs = product_services_by_service_id.get(
            claimservice.service_id,
            ProductService.objects.filter(
                service_id=claimservice.service_id,
                product__in=[p.product for p in policies],
            ),
        )
        errors += validate_assign_prod_elt(
            claim,
            claimservice,
            claimservice.service,
            elt_qs,
            target_date=target_date,
            policies=policies,
        )
    logger.debug(
        "[claim: %s] validate_assign_prod_to_claimitems_and_services nb of errors %s",
        claim.uuid,
        len(errors),
    )
    return errors


def validate_assign_prod_elt(claim, elt, elt_ref, elt_qs, target_date, policies=None):
    visit_type_field = {
        "O": ("limitation_type", "limit_adult", "limit_child"),
        "E": ("limitation_type_e", "limit_adult_e", "limit_child_e"),
        "R": ("limitation_type_r", "limit_adult_r", "limit_child_r"),
    }
    logger.debug(
        "[claim: %s] Assigning product for %s %s", claim.uuid, type(elt), elt.id
    )
    visit_type = (
        claim.visit_type
        if claim.visit_type and claim.visit_type in visit_type_field
        else "O"
    )
    adult = claim.insuree.is_adult(target_date)
    (limitation_type_field, limit_adult, limit_child) = visit_type_field[visit_type]
    claim_price = elt.price_approved or elt.price_adjusted or elt.price_asked or 0
    logger.debug("[claim: %s] claim_price: %s", claim.uuid, claim_price)
    logger.debug(
        "[claim: %s] Checking product itemsvc limit at date %s with field %s C for adult: %s",
        claim.uuid,
        target_date,
        limitation_type_field,
        adult,
    )
    limit_ordering = limit_adult if adult else limit_child
    product_elt_c = _query_product_item_service_limit(
        target_date, elt_qs, limitation_type_field, "C", limit_ordering
    )
    product_elt_f = _query_product_item_service_limit(
        target_date, elt_qs, limitation_type_field, "F", limit_ordering
    )
    if not product_elt_c and not product_elt_f:
        elt.rejection_reason = REJECTION_REASON_NO_PRODUCT_FOUND
        elt.save()
        return [
            {
                "code": REJECTION_REASON_NO_PRODUCT_FOUND,
                "message": _("claim.validation.assign_prod.elt.no_product_code")
                % {"code": claim.code, "elt": str(elt_ref)},
                "detail": claim.uuid,
            }
        ]
    if product_elt_f:
        fixed_limit = getattr(product_elt_f, limit_ordering)
        logger.debug("[claim: %s] fixed_limit: %s", claim.uuid, fixed_limit)
    else:
        fixed_limit = None
    if product_elt_c:
        co_sharing_percent = getattr(product_elt_c, limit_ordering)
        logger.debug(
            "[claim: %s] co_sharing_percent: %s", claim.uuid, co_sharing_percent
        )
    else:
        co_sharing_percent = None
    product_elt = find_best_product_etl(
        product_elt_c, product_elt_f, fixed_limit, claim_price, co_sharing_percent
    )
    if product_elt is None:
        logger.warning(f"Could not find a suitable product from {type(elt)} {elt.id}")
    if product_elt.product is None:
        logger.warning(
            f"Found a productItem/Service for {type(elt)} {elt.id} but it does not have a product"
        )
    logger.debug("[claim: %s] product_id found: %s", claim.uuid, product_elt.product.id)
    elt.product = product_elt.product
    logger.debug(
        "[claim: %s] fetching policy for family %s", claim.uuid, claim.insuree.family_id
    )
    elt.policy = next(
        iter([p for p in policies if p.product == product_elt.product]), None
    )
    if elt.policy is None:
        logger.warning(
            f"{type(elt)} id {elt.id} doesn't seem to have a valid policy with product"
            f" {product_elt.product.id}"
        )
    logger.debug(
        "[claim: %s] setting policy %s",
        claim.uuid,
        elt.policy.id if elt.policy else None,
    )
    elt.price_origin = product_elt.price_origin
    if product_elt_c:
        elt.limitation = "C"
        elt.limitation_value = co_sharing_percent
    else:
        elt.limitation = "F"
        elt.limitation_value = fixed_limit
    logger.debug(
        "[claim: %s] setting limitation %s to %s",
        claim.uuid,
        elt.limitation,
        elt.limitation_value,
    )
    elt.save()
    return []


def _query_product_item_service_limit(
    target_date, elt_qs, limitation_field, limitation_type, limit_ordering
):
    pdt_elt = max(
        (pd for pd in elt_qs if getattr(pd, limitation_field) == limitation_type),
        key=lambda pd: getattr(pd, limit_ordering) or Decimal(0),
        default=None,
    )
    logger.debug(
        "product found: %s, checking product itemsvc limit at date %s "
        "with field %s (%s)",
        pdt_elt is not None,
        target_date,
        limitation_field,
        limitation_type,
    )
    return pdt_elt


def find_best_product_etl(
    product_elt_c, product_elt_f, fixed_limit, claim_price, co_sharing_percent
):
    if product_elt_c and product_elt_f:
        if fixed_limit == 0 or fixed_limit > claim_price:
            product_elt = product_elt_f
            product_elt_c = None
        else:
            if 100 - co_sharing_percent > 0:
                product_amount_own_f = claim_price - fixed_limit
                product_amount_own_c = (1 - co_sharing_percent / 100) * claim_price
                if product_amount_own_c > product_amount_own_f:
                    product_elt = product_elt_f
                    product_elt_c = None
                else:
                    product_elt = product_elt_c
            else:
                product_elt = product_elt_c
    else:
        if product_elt_c:
            product_elt = product_elt_c
        else:
            product_elt = product_elt_f
            product_elt_c = None
    return product_elt


def get_product_items_services(
    target_date, elt_ids, insuree_id, adult, item_or_service, product_ids, policies
):
    if not elt_ids:
        return {}
    if item_or_service == "Item":
        model = ProductItem
        field = "item"
    else:
        model = ProductService
        field = "service"
    qs = model.objects.filter(
        validity_to__isnull=True,
        product_id__in=product_ids,
        **{f"{field}_id__in": elt_ids},
    ).select_related("product", f"{field}")
    data_by_elt = defaultdict(list)
    policies_by_product = defaultdict(list)
    for p in policies:
        policies_by_product[p.product_id].append(p)
    for obj in qs:
        elt_id = getattr(obj, f"{field}_id")
        for policy in policies_by_product[obj.product_id]:
            data_by_elt[elt_id].append(
                {
                    "prod_item_svc": obj,
                    "insuree_policy_effective_date": policy.effective_date,
                    "policy_effective_date": policy.effective_date,
                    "expiry_date": policy.expiry_date,
                    "policy_stage": policy.stage,
                    "waiting_period": getattr(
                        obj, "waiting_period_adult" if adult else "waiting_period_child"
                    )
                    or 0,
                }
            )
    for elt_id in data_by_elt:
        data_by_elt[elt_id].sort(
            key=lambda d: d["policy_effective_date"]
            + datetimedelta(months=d["waiting_period"])
        )
    return data_by_elt


def process_dedrem(
    claim,
    user=None,
    is_process=False,
    policies=None,
    items=None,
    services=None,
    item_product_data=None,
    service_product_data=None,
    product_item_tuple_dict=None,
    product_service_tuple_dict=None,
    root_services=None,
):
    errors, target_date, category, hospitalization, hf_level = (
        initialize_dedrem_processing(claim, root_services)
    )
    archive_old_dedrems(claim)
    policies = fetch_policies(claim, target_date, policies)
    if not policies:
        return [
            {
                "code": REJECTION_REASON_NO_COVERAGE,
                "message": _("claim.validation.family.no_policy")
                % {"code": claim.code, "insuree": str(claim.insuree)},
                "detail": claim.uuid,
            }
        ]
    items, services = fetch_items_and_services(claim, items, services)
    policies_id, products_id = get_policy_and_product_info(policies, items, services)
    claim_deductibles = {}
    if item_product_data is None:
        item_ids = [item.item_id for item in items]
        item_product_data = get_product_items_services(
            target_date,
            item_ids,
            claim.insuree_id,
            claim.insuree.is_adult(target_date),
            "Item",
            set(p.product_id for p in policies),
            policies,
        )
    if service_product_data is None:
        service_ids = [service.service_id for service in services]
        service_product_data = get_product_items_services(
            target_date,
            service_ids,
            claim.insuree_id,
            claim.insuree.is_adult(target_date),
            "Service",
            set(p.product_id for p in policies),
            policies,
        )

    if product_item_tuple_dict is None:
        product_item_tuple_dict = {}
        for data in item_product_data.values():
            for d in data:
                pi = d["prod_item_svc"]
                product_item_tuple_dict[(pi.product.id, pi.item.id)] = pi
    if product_service_tuple_dict is None:
        product_service_tuple_dict = {}
        for data in service_product_data.values():
            for d in data:
                ps = d["prod_item_svc"]
                product_service_tuple_dict[(ps.product.id, ps.service.id)] = ps
    for policy_id in policies_id:
        policy = next((p for p in policies if p.id == policy_id), None)
        if not policy:
            continue
        product = policy.product
        hospital_visit = calculate_hospital_visit(product, hospitalization, hf_level)
        policy_members = get_policy_members(policy_id, target_date)
        demrems = fetch_previous_dedrems(claim, policy_id)
        deductibles = calculate_deductibles_and_ceilings(
            product, claim, demrems, hospital_visit, policy_members
        )
        itmsrv = [*items, *services]
        for claim_detail in itmsrv:
            if claim_detail.status not in [
                ClaimItem.STATUS_PASSED,
                ClaimService.STATUS_PASSED,
            ]:
                continue
            detail_is_item = isinstance(claim_detail, ClaimItem)
            itemsvc_quantity = claim_detail.qty_approved or claim_detail.qty_provided
            itemsvc_pricelist_detail = get_pricelist_detail(
                claim, claim_detail, target_date, detail_is_item
            )
            product_itemsvc = get_product_itemsvc(
                claim_detail,
                detail_is_item,
                (
                    product_item_tuple_dict
                    if detail_is_item
                    else product_service_tuple_dict
                ),
            )
            set_price_adjusted = calculate_price_adjusted(
                claim, claim_detail, itemsvc_pricelist_detail, detail_is_item
            )
            result = process_claim_detail(
                claim,
                claim_detail,
                product,
                deductibles,
                category,
                hospital_visit,
                product_itemsvc,
                set_price_adjusted,
                itemsvc_quantity,
            )
            deductibles["relative_prices"] = update_claim_detail(
                claim_detail, is_process, result, deductibles["relative_prices"]
            )
        create_claim_dedrem(claim, policy, user, deductibles, hospital_visit)
        merge_deductible(claim_deductibles, deductibles)
    errors.extend(
        update_claim_status(claim, is_process, claim_deductibles, user, products_id)
    )
    return errors


def merge_deductible(claim_deductibles, deductibles):
    for k in deductibles.keys():
        data = deductibles[k]
        if k in claim_deductibles:
            if isinstance(data, bool):
                claim_deductibles[k] = claim_deductibles[k] & deductibles[k]
            elif isinstance(data, (int, float, Decimal)):
                claim_deductibles[k] = claim_deductibles[k] + deductibles[k]
            else:
                claim_deductibles[k].append(deductibles[k])
        else:
            if isinstance(data, (bool, int, float, Decimal)):
                claim_deductibles[k] = deductibles[k]
            else:
                claim_deductibles[k] = [deductibles[k]]


def validate_claim(claim, check_max, process_dedrem_opt=True, policies=None, is_process=None, user=None):
    logger.debug(f"Validating claim {claim.uuid}")
    if ClaimConfig.default_validations_disabled:
        return []
    errors = []
    detail_errors = []
    errors += validate_target_date(claim)
    if len(errors) == 0:
        target_date = get_claim_target_date(claim)
        if not policies:
            policies = list(get_valid_policies_qs(claim.insuree_id, target_date))
        items, services = fetch_items_and_services(claim)
        errors += validate_insuree(claim, claim.insuree, policies)
        item_ids = [item.item_id for item in items]
        service_ids = [service.service_id for service in services]
        item_pricelist_dict = {
            pd.item_id: pd
            for pd in ItemsPricelistDetail.objects.filter(
                item_id__in=item_ids,
                items_pricelist=claim.health_facility.items_pricelist,
                items_pricelist__validity_to__isnull=True,
                *ItemsPricelistDetail.filter_validity(validity=target_date),
            ).prefetch_related("item")
        }
        # root_items = set(i.item for i in item_pricelist_dict.values())

        service_pricelist_dict = {
            pd.service_id: pd
            for pd in ServicesPricelistDetail.objects.filter(
                service_id__in=service_ids,
                services_pricelist=claim.health_facility.services_pricelist,
                services_pricelist__validity_to__isnull=True,
                *ServicesPricelistDetail.filter_validity(validity=target_date),
            ).prefetch_related("service")
        }
        root_services = set(s.service for s in service_pricelist_dict.values())
    if len(errors) == 0:
        base_category = get_claim_category(claim, root_services)
        for plc in policies:
            policy_errors = check_claim_max_no_category(
                base_category,
                plc.product,
                plc.expiry_date,
                claim.insuree_id,
                plc.effective_date,
                claim,
            )
            if len(policy_errors) > 0:
                if len(policies) == 1:
                    errors += policy_errors
                policies.remove(plc)
    else:
        claim.status = Claim.STATUS_REJECTED
        claim.rejection_reason = REJECTION_REASON_NO_COVERAGE
        claim.save()
        return errors
    if len(errors) == 0:
        adult = claim.insuree.is_adult(target_date)

        product_ids = set(p.product_id for p in policies)
        item_product_data = get_product_items_services(
            target_date,
            item_ids,
            claim.insuree_id,
            adult,
            "Item",
            product_ids,
            policies,
        )
        service_product_data = get_product_items_services(
            target_date,
            service_ids,
            claim.insuree_id,
            adult,
            "Service",
            product_ids,
            policies,
        )
        product_ids = set()

        product_item_tuple_dict = {}
        for data in item_product_data.values():
            for d in data:
                pi = d["prod_item_svc"]
                product_item_tuple_dict[(pi.product.id, pi.item.id)] = pi
        product_service_tuple_dict = {}
        for data in service_product_data.values():
            for d in data:
                ps = d["prod_item_svc"]
                product_service_tuple_dict[(ps.product.id, ps.service.id)] = ps
        product_items_by_item_id = defaultdict(list)
        for item_id, data in item_product_data.items():
            seen = set()
            for d in data:
                pi = d["prod_item_svc"]
                if pi.id not in seen:
                    product_items_by_item_id[item_id].append(pi)
                    seen.add(pi.id)
        product_services_by_service_id = defaultdict(list)
        for service_id, data in service_product_data.items():
            seen = set()
            for d in data:
                ps = d["prod_item_svc"]
                if ps.id not in seen:
                    product_services_by_service_id[service_id].append(ps)
                    seen.add(ps.id)
        if policies:
            min_effective = min(
                (p.effective_date for p in policies if p.effective_date),
                default=target_date,
            )
            max_expiry = max(
                (p.expiry_date for p in policies if p.expiry_date), default=target_date
            )
        else:
            min_effective = target_date
            max_expiry = target_date
        # Identify items and services requiring historical data
        item_ids_with_limits = []
        service_ids_with_limits = []
        for item in items:
            pi_data = product_items_by_item_id.get(item.item_id, [])
            for pi in pi_data:
                if (
                    pi.waiting_period_adult
                    or pi.waiting_period_child
                    or pi.limit_no_adult is not None
                    or pi.limit_no_child is not None
                    or item.item.frequency
                ):
                    item_ids_with_limits.append(item.item_id)

        for service in services:
            ps_data = product_services_by_service_id.get(service.service_id, [])
            for ps in ps_data:
                if (
                    ps.waiting_period_adult
                    or ps.waiting_period_child
                    or ps.limit_no_adult is not None
                    or ps.limit_no_child is not None
                    or service.service.frequency
                ):
                    service_ids_with_limits.append(service.service_id)

        # Fetch historical quantities only for items/services with limits
        if item_ids_with_limits:
            historical_item_qtys = (
                ClaimItem.objects.filter(
                    item_id__in=item_ids_with_limits,
                    claim__insuree_id=claim.insuree_id,
                    claim__status__gt=Claim.STATUS_ENTERED,
                    claim__validity_to__isnull=True,
                    validity_to__isnull=True,
                    status=ClaimDetail.STATUS_PASSED,
                )
                .filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True))
                .annotate(target_date=Coalesce("claim__date_to", "claim__date_from"))
                .filter(target_date__gte=min_effective, target_date__lte=max_expiry)
                .exclude(claim__uuid=claim.uuid)
                .values(
                    "item_id",
                    "target_date",
                    qty=Coalesce("qty_approved", "qty_provided"),
                )
            )
        else:
            historical_item_qtys = []
        item_history_by_id = {}
        # recent_item_dates_by_id = {}
        for h in historical_item_qtys:
            item_id = h["item_id"]
            item_history_by_id.setdefault(item_id, []).append(
                (h["target_date"], h["qty"])
            )

        if service_ids_with_limits:
            historical_service_qtys = (
                ClaimService.objects.filter(
                    service_id__in=service_ids_with_limits,
                    claim__insuree_id=claim.insuree_id,
                    claim__status__gt=Claim.STATUS_ENTERED,
                    claim__validity_to__isnull=True,
                    validity_to__isnull=True,
                    status=ClaimDetail.STATUS_PASSED,
                )
                .filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True))
                .annotate(target_date=Coalesce("claim__date_to", "claim__date_from"))
                .filter(target_date__gte=min_effective, target_date__lte=max_expiry)
                .exclude(claim__uuid=claim.uuid)
                .values(
                    "service_id",
                    "target_date",
                    qty=Coalesce("qty_approved", "qty_provided"),
                )
            )
        else:
            historical_service_qtys = []
        service_history_by_id = {}
        for h in historical_service_qtys:
            service_id = h["service_id"]
            service_history_by_id.setdefault(service_id, []).append(
                (h["target_date"], h["qty"])
            )
        detail_errors += validate_claimitems(
            claim,
            target_date,
            adult,
            items,
            item_pricelist_dict,
            item_product_data,
            item_history_by_id,
        )
        detail_errors += validate_claimservices(
            claim,
            target_date,
            adult,
            services,
            service_pricelist_dict,
            service_product_data,
            service_history_by_id,
            base_category,
        )
        errors += validate_assign_prod_to_claimitems_and_services(
            claim,
            policies=policies,
            services=services,
            items=items,
            product_items_by_item_id=product_items_by_item_id,
            product_services_by_service_id=product_services_by_service_id,
            target_date=target_date,
        )
        if len(errors) == 0 and check_max:
            over_category_errors = [
                x
                for x in detail_errors
                if x["code"]
                in [
                    REJECTION_REASON_MAX_HOSPITAL_ADMISSIONS,
                    REJECTION_REASON_MAX_VISITS,
                    REJECTION_REASON_MAX_CONSULTATIONS,
                    REJECTION_REASON_MAX_SURGERIES,
                    REJECTION_REASON_MAX_DELIVERIES,
                    REJECTION_REASON_MAX_ANTENATAL,
                ]
            ]
            if len(over_category_errors) > 0:
                claim.items.filter(validity_to__isnull=True).update(
                    status=ClaimItem.STATUS_REJECTED,
                    qty_approved=0,
                    rejection_reason=over_category_errors[0]["code"],
                )
                claim.services.filter(validity_to__isnull=True).update(
                    status=ClaimService.STATUS_REJECTED,
                    qty_approved=0,
                    rejection_reason=over_category_errors[0]["code"],
                )
            else:
                for item in items:
                    if item.rejection_reason:
                        item.status = ClaimItem.STATUS_REJECTED
                        item.qty_approved = 0
                        item.product_item = None
                    else:
                        item.status = ClaimItem.STATUS_PASSED
                    item.save()
                for service in services:
                    if service.rejection_reason:
                        service.status = ClaimService.STATUS_REJECTED
                        service.qty_approved = 0
                        service.product_service = None
                    else:
                        service.status = ClaimService.STATUS_PASSED
                    service.save()
        if all(
            item.status == ClaimItem.STATUS_REJECTED
            for item in claim.items.filter(validity_to__isnull=True)
        ) and all(
            service.status == ClaimService.STATUS_REJECTED
            for service in claim.services.filter(validity_to__isnull=True)
        ):
            errors += [
                {
                    "code": REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
                    "message": _("claim.validation.all_items_and_services_rejected")
                    % {"code": claim.code},
                    "detail": claim.uuid,
                }
            ]
            if len(detail_errors) > 0:
                errors += detail_errors
            claim.status = Claim.STATUS_REJECTED
            claim.rejection_reason = REJECTION_REASON_INVALID_ITEM_OR_SERVICE
            claim.save()
        if process_dedrem_opt and len(errors) == 0:
            dedrem_errors = process_dedrem(
                claim,
                user,
                is_process=is_process,
                policies=policies,
                items=items,
                services=services,
                item_product_data=item_product_data,
                service_product_data=service_product_data,
                product_item_tuple_dict=product_item_tuple_dict,
                product_service_tuple_dict=product_service_tuple_dict,
                root_services=root_services,
            )
            errors.extend(dedrem_errors)
    logger.debug(f"Validation found {len(errors)} error(s)")
    return errors


def validate_claimitems(
    claim, target_date, adult, items, pricelist_dict, product_data_by_id, history_by_id
):
    errors = []
    for claimitem in items:
        if claimitem.rejection_reason:
            continue
        errors += validate_claimitem_validity(claim, claimitem)
        if not claimitem.rejection_reason:
            errors += validate_claimitem_in_price_list(claim, claimitem, pricelist_dict)
        if not claimitem.rejection_reason:
            errors += validate_claimdetail_care_type(claim, claimitem)
        if not claimitem.rejection_reason:
            errors += validate_claimdetail_limitation_fail(claim, claimitem)
        if not claimitem.rejection_reason:
            errors += validate_claimitem_frequency(
                claim, claimitem, target_date, history_by_id.get(claimitem.item_id, [])
            )
        if not claimitem.rejection_reason:
            errors += validate_item_product_family(
                claimitem=claimitem,
                target_date=target_date,
                item=claimitem.item,
                insuree_id=claim.insuree_id,
                adult=adult,
                products_data=product_data_by_id.get(claimitem.item_id, []),
                history=history_by_id.get(claimitem.item_id, []),
            )
        if claimitem.rejection_reason:
            claimitem.status = ClaimItem.STATUS_REJECTED
        else:
            claimitem.rejection_reason = 0
            claimitem.status = ClaimItem.STATUS_PASSED
    return errors


def validate_claimservices(
    claim,
    target_date,
    adult,
    services,
    pricelist_dict,
    product_data_by_id,
    history_by_id,
    base_category,
):
    errors = []
    for claimservice in services:
        if claimservice.rejection_reason:
            continue
        errors += validate_claimservice_validity(claim, claimservice)
        if not claimservice.rejection_reason:
            errors += validate_claimservice_in_price_list(
                claim, claimservice, pricelist_dict
            )
        if not claimservice.rejection_reason:
            errors += validate_claimdetail_care_type(claim, claimservice)
        if not claimservice.rejection_reason:
            errors += validate_claimdetail_limitation_fail(claim, claimservice)
        if not claimservice.rejection_reason:
            errors += validate_claimservice_frequency(
                claim,
                claimservice,
                target_date,
                history_by_id.get(claimservice.service_id, []),
            )
        if not claimservice.rejection_reason:
            errors += validate_service_product_family(
                claimservice=claimservice,
                target_date=target_date,
                service=claimservice.service,
                insuree_id=claim.insuree_id,
                adult=adult,
                claim=claim,
                products_data=product_data_by_id.get(claimservice.service_id, []),
                history=history_by_id.get(claimservice.service_id, []),
            )
        if claimservice.rejection_reason:
            claimservice.status = ClaimService.STATUS_REJECTED
        else:
            claimservice.rejection_reason = 0
            claimservice.status = ClaimService.STATUS_PASSED
    return errors


def validate_claimitem_validity(claim, claimitem):
    errors = []
    target_date = get_claim_target_date(claim)
    if claimitem.validity_to is None and claimitem.item.validity_to is not None:
        claimitem.rejection_reason = REJECTION_REASON_INVALID_ITEM_OR_SERVICE
        errors += [
            {
                "code": REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
                "message": _("claim.validation.claimitem_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    elif claimitem.item.validity_from and claimitem.item.validity_from > target_date:
        claimitem.rejection_reason = REJECTION_REASON_TARGET_DATE
        errors += [
            {
                "code": REJECTION_REASON_TARGET_DATE,
                "message": _("claim.validation.item_future_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    return errors


def validate_claimservice_validity(claim, claimservice):
    errors = []
    target_date = get_claim_target_date(claim)
    if (
        claimservice.validity_to is None
        and claimservice.service.validity_to is not None
    ):
        claimservice.rejection_reason = REJECTION_REASON_INVALID_ITEM_OR_SERVICE
        errors += [
            {
                "code": REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
                "message": _("claim.validation.claimservice_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    elif (
        claimservice.service.validity_from
        and claimservice.service.validity_from > target_date
    ):
        claimservice.rejection_reason = REJECTION_REASON_TARGET_DATE
        errors += [
            {
                "code": REJECTION_REASON_TARGET_DATE,
                "message": _("claim.validation.service_future_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    return errors


def validate_claimitem_in_price_list(claim, claimitem, pricelist_dict=None):
    errors = []
    if claimitem.item_id not in pricelist_dict:
        claimitem.rejection_reason = REJECTION_REASON_NOT_IN_PRICE_LIST
        errors += [
            {
                "code": REJECTION_REASON_NOT_IN_PRICE_LIST,
                "message": _("claim.validation.claimitem_in_price_list_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    return errors


def validate_claimservice_in_price_list(claim, claimservice, pricelist_dict=None):
    errors = []
    if claimservice.service_id not in pricelist_dict:
        claimservice.rejection_reason = REJECTION_REASON_NOT_IN_PRICE_LIST
        errors += [
            {
                "code": REJECTION_REASON_NOT_IN_PRICE_LIST,
                "message": _("claim.validation.claimservice_in_price_list_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    return errors


def validate_claimdetail_care_type(claim, claimdetail):
    errors = []
    care_type = claimdetail.itemsvc.care_type
    hf_care_type = (
        claim.health_facility.care_type if claim.health_facility.care_type else "B"
    )
    target_date = get_claim_target_date(claim)
    inpatient = target_date != claim.date_from
    if (
        (hf_care_type == "O" and inpatient)
        or (hf_care_type == "O" and care_type == "I")
        or (hf_care_type == "I" and care_type == "O")
    ):
        claimdetail.rejection_reason = REJECTION_REASON_CARE_TYPE
        errors += [
            {
                "code": REJECTION_REASON_CARE_TYPE,
                "message": _("claim.validation.claimdetail_care_type_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    return errors


def validate_claimdetail_limitation_fail(claim, claimdetail):
    if claimdetail.itemsvc.patient_category == 0:
        return []
    errors = []
    target_date = get_claim_target_date(claim)
    patient_category_mask = utils.patient_category_mask(claim.insuree, target_date)
    if (
        claimdetail.itemsvc.patient_category & patient_category_mask
        != patient_category_mask
    ):
        claimdetail.rejection_reason = REJECTION_REASON_CATEGORY_LIMITATION
        errors += [
            {
                "code": REJECTION_REASON_CATEGORY_LIMITATION,
                "message": _("claim.validation.claimdetail_limitation_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    return errors


def validate_claimitem_frequency(claim, claimitem, target_date, history):
    errors = []
    if claimitem.item.frequency and any(
        d >= (target_date - datetimedelta(days=claimitem.item.frequency))
        for d in [entry[0] for entry in history]
    ):
        claimitem.rejection_reason = REJECTION_REASON_FREQUENCY_FAILURE
        errors += [
            {
                "code": REJECTION_REASON_FREQUENCY_FAILURE,
                "message": _("claim.validation.claimitem_frequency_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    return errors


def validate_claimservice_frequency(claim, claimservice, target_date, history):
    errors = []
    if claimservice.service.frequency and any(
        d >= (target_date - datetimedelta(days=claimservice.service.frequency))
        for d in [entry[0] for entry in history]
    ):
        claimservice.rejection_reason = REJECTION_REASON_FREQUENCY_FAILURE
        errors += [
            {
                "code": REJECTION_REASON_FREQUENCY_FAILURE,
                "message": _("claim.validation.claimservice_frequency_validity")
                % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    return errors


def validate_target_date(claim):
    errors = []
    if (
        claim.date_from is None and claim.date_to is None
    ) or claim.date_claimed < claim.date_from:
        claim.reject(REJECTION_REASON_TARGET_DATE)
        errors += [
            {
                "code": REJECTION_REASON_TARGET_DATE,
                "message": _("claim.validation.target_date") % {"code": claim.code},
                "detail": claim.uuid,
            }
        ]
    return errors


def validate_insuree(claim, insuree, policies=None):
    errors = []
    if insuree.validity_to is not None:
        errors += [
            {
                "code": REJECTION_REASON_FAMILY,
                "message": _("claim.validation.family.insuree_validity")
                % {"code": claim.code, "insuree": str(insuree)},
                "detail": claim.uuid,
            }
        ]
    if not insuree.family or insuree.family.validity_to is not None:
        errors += [
            {
                "code": REJECTION_REASON_FAMILY,
                "message": _("claim.validation.family.family_validity")
                % {"code": claim.code, "insuree": str(insuree)},
                "detail": claim.uuid,
            }
        ]
    if not policies:
        errors += [
            {
                "code": REJECTION_REASON_NO_COVERAGE,
                "message": _("claim.validation.family.no_policy")
                % {"code": claim.code, "insuree": str(insuree)},
                "detail": claim.uuid,
            }
        ]

    if len(errors) > 0:
        claim.reject(REJECTION_REASON_FAMILY)
    return errors


def validate_item_product_family(
    claimitem, target_date, item, insuree_id, adult, products_data, history
):
    errors = []
    found = False
    for data in products_data:
        product_item = data["prod_item_svc"]
        insuree_policy_effective_date = data["insuree_policy_effective_date"]
        policy_effective_date = data["policy_effective_date"]
        expiry_date = data["expiry_date"]
        policy_stage = data["policy_stage"]
        found = True
        core = __import__("core")
        insuree_policy_effective_date = core.datetime.date.from_ad_date(
            insuree_policy_effective_date
        )
        expiry_date = core.datetime.date.from_ad_date(expiry_date)
        errors += check_service_item_waiting_period(
            policy_stage,
            policy_effective_date,
            insuree_policy_effective_date,
            item,
            adult,
            product_item,
            target_date,
            claimitem,
        )
        errors += check_service_item_max_provision(
            adult,
            product_item,
            item,
            insuree_policy_effective_date,
            expiry_date,
            insuree_id,
            claimitem,
            history,
        )
    if not found:
        claimitem.rejection_reason = REJECTION_REASON_NO_PRODUCT_FOUND
        errors += [
            {
                "code": REJECTION_REASON_NO_PRODUCT_FOUND,
                "message": _("claim.validation.product_family.no_product_found")
                % {"code": claimitem.claim.code, "element": str(item)},
                "detail": claimitem.claim.uuid,
            }
        ]
    return errors


def validate_service_product_family(
    claimservice, target_date, service, insuree_id, adult, claim, products_data, history
):
    errors = []
    found = False
    for data in products_data:
        product_service = data["prod_item_svc"]
        insuree_policy_effective_date = data["insuree_policy_effective_date"]
        policy_effective_date = data["policy_effective_date"]
        expiry_date = data["expiry_date"]
        policy_stage = data["policy_stage"]
        found = True
        core = __import__("core")
        insuree_policy_effective_date = core.datetime.date.from_ad_date(
            insuree_policy_effective_date
        )
        policy_effective_date = core.datetime.date.from_ad_date(policy_effective_date)
        expiry_date = core.datetime.date.from_ad_date(expiry_date)
        errors += check_service_item_waiting_period(
            policy_stage,
            policy_effective_date,
            insuree_policy_effective_date,
            service,
            adult,
            product_service,
            target_date,
            claimservice,
        )
        errors += check_service_item_max_provision(
            adult,
            product_service,
            service,
            insuree_policy_effective_date,
            expiry_date,
            insuree_id,
            claimservice,
            history,
        )
        # error_len = len(errors)
        # product = product_service.product

    if not found:
        claimservice.rejection_reason = REJECTION_REASON_NO_PRODUCT_FOUND
        errors += [
            {
                "code": REJECTION_REASON_NO_PRODUCT_FOUND,
                "message": _("claim.validation.product_family.no_product_found")
                % {"code": claimservice.claim.code, "element": str(service)},
                "detail": claimservice.claim.uuid,
            }
        ]
    return errors


def check_service_item_waiting_period(
    policy_stage,
    policy_effective_date,
    insuree_policy_effective_date,
    service_or_item,
    adult,
    product_service_item,
    target_date,
    claim_service_item,
):
    errors = []
    waiting_period = None
    if policy_stage == "N" or policy_effective_date < insuree_policy_effective_date:
        if adult:
            waiting_period = product_service_item.waiting_period_adult
        else:
            waiting_period = product_service_item.waiting_period_child
    if waiting_period and target_date < (
        insuree_policy_effective_date + datetimedelta(months=waiting_period)
    ):
        claim_service_item.rejection_reason = REJECTION_REASON_WAITING_PERIOD_FAIL
        errors += [
            {
                "code": REJECTION_REASON_WAITING_PERIOD_FAIL,
                "message": _("claim.validation.product_family.waiting_period")
                % {
                    "code": claim_service_item.claim.code,
                    "element": str(service_or_item),
                },
                "detail": claim_service_item.claim.uuid,
            }
        ]
    return errors


def check_service_item_max_provision(
    adult,
    product_service_item,
    service_or_item,
    insuree_policy_effective_date,
    expiry_date,
    insuree_id,
    claim_service_item,
    history,
):
    errors = []
    if adult:
        limit_no = product_service_item.limit_no_adult
    else:
        limit_no = product_service_item.limit_no_child
    if limit_no is not None and limit_no >= 0:
        total_qty_provided = sum(
            qty
            for date, qty in history
            if insuree_policy_effective_date <= date <= expiry_date
        )
        qty = total_qty_provided + (
            claim_service_item.qty_provided
            if claim_service_item.qty_approved is None
            else claim_service_item.qty_approved
        )
        if qty > limit_no:
            if total_qty_provided < limit_no:
                remaining_qty = limit_no - total_qty_provided
                if claim_service_item.qty_approved is None:
                    claim_service_item.qty_provided = remaining_qty
                else:
                    claim_service_item.qty_approved = remaining_qty
            else:
                claim_service_item.rejection_reason = REJECTION_REASON_QTY_OVER_LIMIT
                errors += [
                    {
                        "code": REJECTION_REASON_QTY_OVER_LIMIT,
                        "message": _("claim.validation.product_family.max_nb_allowed"),
                    }
                ]
    return errors


def check_claim_max_no_category(
    base_category, product_data, expiry_date, insuree_id, policy_effective_date, claim
):
    errors = []
    category_dict = {
        "C": {
            "field": "max_no_consultation",
            "reason": REJECTION_REASON_MAX_CONSULTATIONS,
            "message": "claim.validation.product_family.max_nb_consultation",
        },
        "S": {
            "field": "max_no_surgery",
            "reason": REJECTION_REASON_MAX_SURGERIES,
            "message": "claim.validation.product_family.max_nb_surgeries",
        },
        "D": {
            "field": "max_no_delivery",
            "reason": REJECTION_REASON_MAX_DELIVERIES,
            "message": "claim.validation.product_family.max_nb_deliveries",
        },
        "A": {
            "field": "max_no_antenatal",
            "reason": REJECTION_REASON_MAX_ANTENATAL,
            "message": "claim.validation.product_family.max_nb_antenatal",
        },
        "H": {
            "field": "max_no_hospitalization",
            "reason": REJECTION_REASON_MAX_HOSPITAL_ADMISSIONS,
            "message": "claim.validation.product_family.max_nb_hospitalizations",
        },
        "V": {
            "field": "max_no_visits",
            "reason": REJECTION_REASON_MAX_VISITS,
            "message": "claim.validation.product_family.max_nb_visits",
        },
    }.get(base_category)
    if (
        category_dict
        and getattr(product_data, category_dict["field"], None) is not None
        and getattr(product_data, category_dict["field"], None) >= 0
    ):
        max_value = getattr(product_data, category_dict["field"])
        historical_claims = (
            Claim.objects.filter(
                insuree_id=claim.insuree_id,
                validity_to__isnull=True,
                status__gt=Claim.STATUS_ENTERED,
                category=base_category,
            )
            .annotate(target_date=Coalesce("date_to", "date_from"))
            .filter(
                target_date__gte=policy_effective_date,
                target_date__lte=expiry_date,
            )
            .exclude(uuid=claim.uuid)
            .values("target_date", "category")
        )
        claims_by_category = defaultdict(list)
        for hc in historical_claims:
            cat = hc["category"]
            claims_by_category[cat].append(hc["target_date"])

        dates = claims_by_category.get(base_category, [])
        if base_category == "V":
            dates += claims_by_category.get(None, [])
        count = len([d for d in dates if policy_effective_date <= d <= expiry_date])
        if count >= max_value:
            claim.rejection_reason = category_dict["reason"]
            errors += [
                {
                    "code": category_dict["reason"],
                    "message": _(category_dict["message"])
                    % {"code": claim.code, "count": count, "max": max_value},
                    "detail": claim.uuid,
                }
            ]
    return errors


def get_claim_category(claim, services=None):
    """
    Determine the claim category based on its services.
    """
    if claim.category:
        return claim.category
    service_categories = [
        Service.CATEGORY_SURGERY,
        Service.CATEGORY_DELIVERY,
        Service.CATEGORY_ANTENATAL,
        Service.CATEGORY_HOSPITALIZATION,
        Service.CATEGORY_CONSULTATION,
        Service.CATEGORY_OTHER,
        Service.CATEGORY_VISIT,
    ]
    target_date = get_claim_target_date(claim)
    if services is None:
        services = Service.objects.filter(
            claimservice__claim=claim,
            *Service.filter_validity(validity=target_date),
            *ClaimService.filter_validity(validity=target_date, prefix="claimservice__"),
        )

    claim_service_categories = [service.category for service in services]
    if claim.date_from != target_date:
        claim_service_categories.append(Service.CATEGORY_HOSPITALIZATION)
    for category in service_categories:
        if category in claim_service_categories:
            claim_category = category
            break
    else:
        claim_category = Service.CATEGORY_VISIT
    return claim_category


def _get_dedrem(prefix, dedrem_type, field, product, insuree, demrems):
    if getattr(product, prefix + "_treatment", None):
        return Deductible(getattr(product, prefix + "_treatment", None), dedrem_type, 0)
    if getattr(product, prefix + "_insuree", None):
        prev = sum(
            [getattr(dr, field, 0) for dr in demrems if dr.insuree_id == insuree.id]
        )
        return Deductible(
            getattr(product, prefix + "_insuree", None),
            dedrem_type,
            prev if prev else 0,
        )
    if getattr(product, prefix + "_policy", None):
        prev = sum([getattr(dr, field, 0) for dr in demrems])
        return Deductible(
            getattr(product, prefix + "_policy", None), dedrem_type, prev if prev else 0
        )
    return None

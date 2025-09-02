import itertools
import logging
from collections import namedtuple
from decimal import Decimal
from claim.models import ClaimItem, Claim, ClaimService, ClaimDedRem, ClaimDetail, ClaimServiceService, ClaimServiceItem
from core import utils
from datetime import datetime
from core.datetimes.shared import datetimedelta
from core.utils import filter_validity
from django.db import connection
from django.db.models import Sum, Q, ExpressionWrapper, DecimalField, Case, When, F, Value, CharField
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _
from insuree.models import InsureePolicy
from medical.models import Service, ServiceService, ServiceItem, Item
from medical_pricelist.models import ItemsPricelistDetail, ServicesPricelistDetail
from policy.models import Policy
from product.models import Product, ProductItem, ProductService, ProductItemOrService
from .apps import ClaimConfig
from .utils import get_queryset_valid_at_date, get_valid_policies_qs, get_claim_target_date
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

Deductible = namedtuple('Deductible', ['amount', 'type', 'prev'])


def check_claim_max_no_category(base_category, product_data, expiry_date, insuree_id,
                               insuree_policy_effective_date, claim, claimservice, claims_by_category):
    errors = []
    category_dict = {
        'C': {'field': 'max_no_consultation', 'reason': REJECTION_REASON_MAX_CONSULTATIONS,
              'message': 'claim.validation.product_family.max_nb_consultation'},
        'S': {'field': 'max_no_surgery', 'reason': REJECTION_REASON_MAX_SURGERIES,
              'message': 'claim.validation.product_family.max_nb_surgeries'},
        'D': {'field': 'max_no_delivery', 'reason': REJECTION_REASON_MAX_DELIVERIES,
              'message': 'claim.validation.product_family.max_nb_deliveries'},
        'A': {'field': 'max_no_antenatal', 'reason': REJECTION_REASON_MAX_ANTENATAL,
              'message': 'claim.validation.product_family.max_nb_antenatal'},
        'H': {'field': 'max_no_hospitalization', 'reason': REJECTION_REASON_MAX_HOSPITAL_ADMISSIONS,
              'message': 'claim.validation.product_family.max_nb_hospitalizations'},
        'V': {'field': 'max_no_visits', 'reason': REJECTION_REASON_MAX_VISITS,
              'message': 'claim.validation.product_family.max_nb_visits'},
    }.get(base_category)
    if category_dict and product_data[category_dict['field']] is not None and product_data[category_dict['field']] >= 0:
        max_value = product_data[category_dict['field']]
        dates = claims_by_category.get(base_category, [])
        if base_category == 'V':
            dates += claims_by_category.get(None, [])
        count = len([d for d in dates if insuree_policy_effective_date <= d <= expiry_date])
        if count >= max_value:
            claimservice.rejection_reason = category_dict['reason']
            errors += [{
                'code': category_dict['reason'],
                'message': _(category_dict['message']) % {
                    'code': claimservice.claim.code,
                    'count': count,
                    'max': max_value},
                'detail': claimservice.claim.uuid
            }]
    return errors

def get_products(target_date, elt_ids, insuree_id, adult, item_or_service):
    if not elt_ids:
        return {}
    if item_or_service == 'Item':
        model = ProductItem
        field = 'item'
    else:
        model = ProductService
        field = 'service'
    waiting_field = 'waiting_period_adult' if adult else 'waiting_period_child'
    qs = model.objects.filter(
        validity_to__isnull=True,
        **{f'{field}_id__in': elt_ids},
        product__validity_to__isnull=True,
        product__policies__validity_to__isnull=True,
        product__policies__effective_date__lte=target_date,
        product__policies__expiry_date__gte=target_date,
        product__policies__status__in=(Policy.STATUS_ACTIVE, Policy.STATUS_EXPIRED),
        product__policies__insuree_policies__insuree_id=insuree_id,
        product__policies__insuree_policies__validity_to__isnull=True,
        product__policies__insuree_policies__effective_date__lte=target_date,
        product__policies__insuree_policies__expiry_date__gte=target_date,
    ).annotate(
        itemservice=Value(field, output_field=CharField()),
        expiry_date=F('product__policies__insuree_policies__expiry_date'),
        policy_effective_date=F('product__policies__effective_date'),
        insuree_policy_effective_date=F('product__policies__insuree_policies__effective_date'),
        policy_stage=F('product__policies__stage'),
        policy_id=F('product__policies__id'),
        prod_elt_id=F('id'),
        waiting_period=Coalesce(F(waiting_field), Value(0)),
        max_no_consultation=F('product__max_no_consultation'),
        max_no_surgery=F('product__max_no_surgery'),
        max_no_delivery=F('product__max_no_delivery'),
        max_no_antenatal=F('product__max_no_antenatal'),
        max_no_hospitalization=F('product__max_no_hospitalization'),
        max_no_visits=F('product__max_no_visits'),
        itemservice_id=F(f'{field}_id')
    ).values_list(
        'itemservice', 'itemservice_id', 'product_id', 'prod_elt_id', 'insuree_policy_effective_date', 'policy_effective_date',
        'expiry_date', 'policy_stage', 'waiting_period', 'max_no_consultation', 'max_no_surgery',
        'max_no_delivery', 'max_no_antenatal', 'max_no_hospitalization', 'max_no_visits', 'id', 'policy_id'
    )
    
    data_by_elt = {}
    for row in qs:
        elt_id = row[1]
        data_by_elt.setdefault(elt_id, []).append(row)
 
    return data_by_elt

def get_claim_category(claim):
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
    services = claim.services \
        .filter(validity_to__isnull=True, service__validity_to__isnull=True) \
        .values("service__category").distinct()
    claim_service_categories = [
        service["service__category"]
        for service in services
    ]
    if claim.date_from != target_date:
        claim_service_categories.append(Service.CATEGORY_HOSPITALIZATION)
    for category in service_categories:
        if category in claim_service_categories:
            claim_category = category
            break
    else:
        claim_category = Service.CATEGORY_VISIT
    return claim_category


def initialize_dedrem_processing(claim):
    """Initialize basic claim processing parameters."""
    errors = []
    logger.debug(f"processing dedrem for claim {claim.uuid}")
    target_date = get_claim_target_date(claim)
    category = get_claim_category(claim)
    hospitalization = claim.date_from != target_date
    hf_level = claim.health_facility.level
    return errors, target_date, category, hospitalization, hf_level

def archive_old_dedrems(claim):
    """Archive existing dedrems for the claim."""
    ClaimDedRem.objects.filter(claim_id=claim.id, *filter_validity()).update(
        validity_to=datetime.now()
    )

def fetch_policies(claim, target_date, policies=None):
    """Retrieve valid policies if not provided."""
    if not policies:
        policies = get_valid_policies_qs(claim.insuree.id, target_date)
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
        items = list(claim.items.filter(
            item__isnull=False,
            product__isnull=False,
            validity_to__isnull=True,
        ).filter(
            Q(Q(rejection_reason=0) | Q(rejection_reason__isnull=True))
        ))
    if services is None:
        services = list(claim.services.filter(
            service__isnull=False,
            product__isnull=False,
            validity_to__isnull=True,
        ).filter(
            Q(Q(rejection_reason=0) | Q(rejection_reason__isnull=True))
        ))
    return items, services

def get_policy_and_product_info(policies, items, services, target_date,product_data_by_item=None,product_data_by_service=None):
    """Extract policy and product information."""
    products_id = set(p.product_id for p in policies) if policies else set()
    policies_id = set(p.id for p in policies ) if policies else set()
    item_ids = [item.item_id for item in items]
    service_ids = [service.service_id for service in services]
    if product_data_by_item is None and item_ids:
        product_data_by_item = get_products(target_date, item_ids, items[0].claim.insuree_id if items else None, items[0].claim.insuree.is_adult(target_date) if items else False, 'Item')
    if product_data_by_service is None and service_ids:
        product_data_by_service = get_products(target_date, service_ids, services[0].claim.insuree_id if services else None, services[0].claim.insuree.is_adult(target_date) if services else False, 'Service')
    products_id_from_items_services = set()
    for pd in product_data_by_item.values():
        for row in pd:
            products_id_from_items_services.add(row[3])
            policies_id.add(row[-1])
    for pd in product_data_by_service.values():
        for row in pd:
            products_id_from_items_services.add(row[3])
            policies_id.add(row[-1])
    products_id = list(products_id | products_id_from_items_services)
    return list(policies_id), products_id

def calculate_hospital_visit(product_data, hospitalization, hf_level):
    """Determine if the claim is a hospital visit."""
    return (
        (product_data.get('ceiling_interpretation') == Product.CEILING_INTERPRETATION_IN_PATIENT
         and hospitalization)
        or (product_data.get('ceiling_interpretation') == Product.CEILING_INTERPRETATION_HOSPITAL
            and hf_level == "H")
    )

def get_policy_members(policy_id, target_date):
    """Count policy members."""
    return InsureePolicy.objects.filter(
        policy_id=policy_id,
        effective_date__isnull=False,
        effective_date__lte=target_date,
        expiry_date__gte=target_date,
        validity_to__isnull=True
    ).count()

def initialize_deductibles_and_ceilings():
    """Initialize deductible and ceiling tracking variables."""
    return {
        'deductible': None,
        'ceiling': None,
        'prev_deductible': None,
        'prev_remunerated': 0,
        'prev_remunerated_consult': 0,
        'prev_remunerated_surgery': 0,
        'prev_remunerated_hospitalization': 0,
        'prev_remunerated_delivery': 0,
        'prev_remunerated_antenatal': 0,
        'remunerated_consultation': 0,
        'remunerated_surgery': 0,
        'remunerated_hospitalization': 0,
        'remunerated_delivery': 0,
        'remunerated_antenatal': 0,
        'relative_prices': False,
        'deducted': 0,
        'remunerated': 0
    }

def fetch_previous_dedrems(claim, policy_id):
    """Retrieve previous dedrems excluding current claim."""
    return list(
        ClaimDedRem.objects.filter(
            policy_id=policy_id
        ).exclude(
            claim_id=claim.id
        )
    )

def calculate_deductibles_and_ceilings(product, product_data, claim, demrems, hospital_visit, policy_members):
    """Calculate deductibles and ceilings based on product and policy."""
    deductibles = initialize_deductibles_and_ceilings()
    ded_g = _get_dedrem("ded", "G", "ded_g", product, claim.insuree, demrems)
    if ded_g:
        deductibles['deductible'] = ded_g
        deductibles['prev_deductible'] = ded_g.prev
    rem_g = _get_dedrem("max", "G", "rem_g", product, claim.insuree, demrems)
    if rem_g:
        deductibles['ceiling'] = rem_g
        deductibles['prev_remunerated'] = rem_g.prev
    if product_data.get('max_policy'):
        if policy_members > product_data.get('threshold', 0):
            if product_data.get('max_policy_extra_member'):
                deductibles['ceiling'] = Deductible(
                    product_data['max_policy'] + (policy_members - product_data['threshold']) * product_data['max_policy_extra_member'],
                    deductibles['ceiling'].type,
                    deductibles['ceiling'].prev
                )
            if product_data.get('max_ceiling_policy') and deductibles['ceiling'].amount > product_data['max_ceiling_policy']:
                deductibles['ceiling'] = Deductible(
                    product_data['max_ceiling_policy'],
                    deductibles['ceiling'].type,
                    deductibles['ceiling'].prev
                )
        else:
            deductibles['ceiling'] = Deductible(
                product_data['max_policy'],
                deductibles['ceiling'].type,
                deductibles['ceiling'].prev
            )
    if not deductibles['deductible']:
        if hospital_visit:
            ded_ip = _get_dedrem("ded_ip", "I", "ded_ip", product, claim.insuree, demrems)
            if ded_ip:
                deductibles['deductible'] = ded_ip
                deductibles['prev_deductible'] = ded_ip.prev
        else:
            ded_op = _get_dedrem("ded_op", "O", "ded_op", product, claim.insuree, demrems)
            if ded_op:
                deductibles['deductible'] = ded_op
                deductibles['prev_deductible'] = ded_op.prev
    if not deductibles['ceiling']:
        if hospital_visit:
            max_ip = _get_dedrem("max_ip", "I", "rem_ip", product, claim.insuree, demrems)
            if max_ip:
                deductibles['ceiling'] = max_ip
                deductibles['prev_remunerated'] = max_ip.prev
            if product_data.get('max_ip_policy'):
                if policy_members > product_data.get('threshold', 0):
                    if product_data.get('max_policy_extra_member_ip'):
                        deductibles['ceiling'] = Deductible(
                            product_data['max_ip_policy'] + (
                                policy_members - product_data['threshold']) * product_data['max_policy_extra_member_ip'],
                            deductibles['ceiling'].type,
                            deductibles['ceiling'].prev
                        )
                    if product_data.get('max_ceiling_policy_ip') and deductibles['ceiling'].amount > product_data['max_ceiling_policy_ip']:
                        deductibles['ceiling'] = Deductible(
                            product_data['max_ceiling_policy_ip'],
                            deductibles['ceiling'].type,
                            deductibles['ceiling'].prev
                        )
                else:
                    deductibles['ceiling'] = Deductible(
                        product_data['max_ip_policy'],
                        deductibles['ceiling'].type,
                        deductibles['ceiling'].prev
                    )
        else:
            max_op = _get_dedrem("max_op", "O", "rem_op", product, claim.insuree, demrems)
            if max_op:
                deductibles['ceiling'] = max_op
                deductibles['prev_remunerated'] = max_op.prev
            if product_data.get('max_op_policy'):
                if product_data.get('threshold') and policy_members > product_data['threshold']:
                    if product_data.get('max_policy_extra_member_op'):
                        deductibles['ceiling'] = Deductible(
                            product_data['max_op_policy'] + (
                                policy_members - product_data['threshold']) * product_data['max_policy_extra_member_op'],
                            deductibles['ceiling'].type,
                            deductibles['ceiling'].prev
                        )
                    if product_data.get('max_ceiling_policy_op') and deductibles['ceiling'].amount > product_data['max_ceiling_policy_op']:
                        deductibles['ceiling'] = Deductible(
                            product_data['max_ceiling_policy_op'],
                            deductibles['ceiling'].type,
                            deductibles['ceiling'].prev
                        )
                else:
                    deductibles['ceiling'] = Deductible(
                        product_data['max_op_policy'],
                        deductibles['ceiling'].type,
                        deductibles['ceiling'].prev
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

def get_product_itemsvc(claim_detail, detail_is_item):
    """Fetch product item or service."""
    if detail_is_item:
        product_itemsvc = ProductItem.objects.filter(
            product_id=claim_detail.product_id,
            item_id=claim_detail.item_id,
            validity_to__isnull=True
        ).first()
    else:
        product_itemsvc = ProductService.objects.filter(
            product=claim_detail.product_id,
            service_id=claim_detail.service_id,
            validity_to__isnull=True
        ).first()
    if product_itemsvc is None:
        raise ValueError(f"Product {'Item' if detail_is_item else 'Service'} not found")
    return product_itemsvc

def calculate_price_adjusted(claim, claim_detail, itemsvc_pricelist_detail, detail_is_item):
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
            if claim_detail.service.packagetype == 'F':
                service_price = claim_detail.service.price
            if service_price and (claim_detail.price_adjusted or claim_detail.price_asked) > service_price:
                return service_price
        return set_price_adjusted
    set_price_adjusted = pl_price
    if ClaimConfig.verify_quantities and not detail_is_item:
        set_price_adjusted = verify_service_quantities(claim_detail, set_price_adjusted)
    return set_price_adjusted

def verify_service_quantities(claim_detail, set_price_adjusted):
    """Verify service quantities for package services."""
    continue_service_check = True
    if claim_detail.service.packagetype == 'P':
        service_services = ServiceService.objects.filter(parent=claim_detail.service.id).all()
        claim_service_services = ClaimServiceService.objects.filter(claim_service=claim_detail.id).all()
        if len(service_services) == len(claim_service_services):
            for servservice in service_services:
                for claimserviceservice in claim_service_services:
                    if servservice.service.id == claimserviceservice.service.id:
                        if servservice.qty_provided != claimserviceservice.qty_displayed:
                            return 0
                if not continue_service_check:
                    break
        else:
            return 0
        continue_item_check = True
        service_items = ServiceItem.objects.filter(parent=claim_detail.service.id).all()
        claim_service_items = ClaimServiceItem.objects.filter(claim_service=claim_detail.id).all()
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

def process_claim_detail(claim, claim_detail, product_data, deductibles, category, hospital_visit, product_itemsvc, set_price_adjusted, itemsvc_quantity):
    """Process individual claim item or service."""
    work_value = int(itemsvc_quantity * set_price_adjusted)
    set_unit_price_adjusted = set_price_adjusted
    set_price_deducted = 0
    exceed_ceiling_amount = 0
    exceed_ceiling_amount_category = 0
    if (claim_detail.limitation == ProductItemOrService.LIMIT_FIXED_AMOUNT
        and claim_detail.limitation_value
        and (itemsvc_quantity * claim_detail.limitation_value) < work_value):
        work_value = itemsvc_quantity * claim_detail.limitation_value
    if deductibles['deductible'] and deductibles['deductible'].amount - deductibles['prev_deductible'] - deductibles['deducted'] > 0:
        if deductibles['deductible'].amount - deductibles['deductible'].prev - deductibles['deducted'] >= work_value:
            set_price_deducted = work_value
            deductibles['deducted'] += work_value
            set_price_approved = 0
            set_price_remunerated = 0
        else:
            set_price_deducted = deductibles['deductible'].amount - deductibles['deductible'].prev - deductibles['deducted']
            work_value -= set_price_deducted
            deductibles['deducted'] += deductibles['deductible'].amount - deductibles['deductible'].prev - deductibles['deducted']
    if claim_detail.limitation == ProductItemOrService.LIMIT_CO_INSURANCE and claim_detail.limitation_value:
        work_value = claim_detail.limitation_value / 100 * work_value
    work_value, exceed_ceiling_amount_category = apply_category_ceilings(
        product_data, category, work_value, deductibles
    )
    set_price_approved, set_price_remunerated, exceed_ceiling_amount = apply_ceiling_exclusions(
        claim, claim_detail, product_itemsvc, hospital_visit, work_value, deductibles
    )
    return {
        'set_price_deducted': set_price_deducted,
        'set_price_approved': set_price_approved,
        'set_price_remunerated': set_price_remunerated,
        'exceed_ceiling_amount': exceed_ceiling_amount,
        'exceed_ceiling_amount_category': exceed_ceiling_amount_category,
        'set_unit_price_adjusted': set_unit_price_adjusted,
        'work_value': work_value
    }

def apply_category_ceilings(product_data, category, work_value, deductibles):
    """Apply category-specific ceilings."""
    exceed_ceiling_amount_category = 0
    category_checks = {
        Service.CATEGORY_SURGERY: (
            product_data.get('max_amount_surgery'),
            'remunerated_surgery',
            'prev_remunerated_surgery'
        ),
        Service.CATEGORY_DELIVERY: (
            product_data.get('max_amount_delivery'),
            'remunerated_delivery',
            'prev_remunerated_delivery'
        ),
        Service.CATEGORY_ANTENATAL: (
            product_data.get('max_amount_antenatal'),
            'remunerated_antenatal',
            'prev_remunerated_antenatal'
        ),
        Service.CATEGORY_HOSPITALIZATION: (
            product_data.get('max_amount_hospitalization'),
            'remunerated_hospitalization',
            'prev_remunerated_hospitalization'
        ),
        Service.CATEGORY_CONSULTATION: (
            product_data.get('max_amount_consultation'),
            'remunerated_consultation',
            'prev_remunerated_consult'
        )
    }
    if category != Service.CATEGORY_VISIT and category in category_checks:
        max_amount, remunerated_key, prev_remunerated_key = category_checks[category]
        if max_amount:
            total_remunerated = (
                work_value +
                deductibles[prev_remunerated_key] +
                deductibles[remunerated_key]
            )
            if total_remunerated <= max_amount:
                deductibles[remunerated_key] += work_value
            else:
                if deductibles[prev_remunerated_key] + deductibles[remunerated_key] >= max_amount:
                    exceed_ceiling_amount_category = work_value
                    work_value = 0
                else:
                    exceed_ceiling_amount_category = (
                        total_remunerated - max_amount
                    )
                    work_value -= exceed_ceiling_amount_category
                    deductibles[remunerated_key] += work_value
    return work_value, exceed_ceiling_amount_category

def apply_ceiling_exclusions(claim, claim_detail, product_itemsvc, hospital_visit, work_value, deductibles):
    """Apply ceiling exclusions based on patient type and visit type."""
    exceed_ceiling_amount = 0
    set_price_approved = work_value
    set_price_remunerated = work_value
    if product_itemsvc and (
        (claim.insuree.is_adult and hospital_visit
         and product_itemsvc.ceiling_exclusion_adult in ("B", "H"))
        or (claim.insuree.is_adult and not hospital_visit
            and product_itemsvc.ceiling_exclusion_adult in ("B", "N"))
        or (not claim.insuree.is_adult and hospital_visit
            and product_itemsvc.ceiling_exclusion_child in ("B", "H"))
        or (not claim.insuree.is_adult and not hospital_visit
            and product_itemsvc.ceiling_exclusion_child in ("B", "N"))
    ):
        exceed_ceiling_amount = 0
    else:
        if deductibles['ceiling'] and deductibles['ceiling'].amount > 0:
            remaining_ceiling = (
                deductibles['ceiling'].amount -
                deductibles['prev_remunerated'] -
                deductibles['remunerated']
            )
            if remaining_ceiling > 0:
                if remaining_ceiling >= work_value:
                    deductibles['remunerated'] += work_value
                else:
                    exceed_ceiling_amount = work_value - remaining_ceiling
                    set_price_approved = remaining_ceiling
                    set_price_remunerated = remaining_ceiling
                    deductibles['remunerated'] += remaining_ceiling
            else:
                exceed_ceiling_amount = work_value
                set_price_approved = 0
                set_price_remunerated = 0
        else:
            deductibles['remunerated'] += work_value
    return set_price_approved, set_price_remunerated, exceed_ceiling_amount

def update_claim_detail(claim_detail, is_process, result, relative_prices):
    """Update claim detail with processed values."""
    if claim_detail.price_approved is None:
        claim_detail.price_adjusted = result['set_unit_price_adjusted']
    if is_process:
        if claim_detail.price_origin == ProductItemOrService.ORIGIN_RELATIVE:
            claim_detail.price_valuated = None
            claim_detail.deductable_amount = result['set_price_deducted']
            claim_detail.exceed_ceiling_amount = result['exceed_ceiling_amount']
            relative_prices = True
        else:
            claim_detail.price_valuated = result['set_price_approved']
            claim_detail.deductable_amount = result['set_price_deducted']
            claim_detail.exceed_ceiling_amount = result['exceed_ceiling_amount']
            claim_detail.remunerated_amount = result['set_price_remunerated']
    claim_detail.save()
    return relative_prices

def create_claim_dedrem(claim, policy, user, deductibles, hospital_visit):
    """Create new ClaimDedRem record."""
    now = datetime.now()
    claim_ded_rem_to_create = {
        "policy": policy,
        "insuree": claim.insuree,
        "claim": claim,
        "ded_g": deductibles['deducted'],
        "rem_g": deductibles['remunerated'],
        "rem_consult": deductibles['remunerated_consultation'],
        "rem_hospitalization": deductibles['remunerated_hospitalization'],
        "rem_delivery": deductibles['remunerated_delivery'],
        "rem_antenatal": deductibles['remunerated_antenatal'],
        "rem_surgery": deductibles['remunerated_surgery'],
        "audit_user_id": user.id_for_audit if user else -1,
        "validity_from": now
    }
    if hospital_visit:
        claim_ded_rem_to_create["ded_ip"] = deductibles['deducted']
        claim_ded_rem_to_create["rem_ip"] = deductibles['remunerated']
    else:
        claim_ded_rem_to_create["ded_op"] = deductibles['deducted']
        claim_ded_rem_to_create["rem_op"] = deductibles['remunerated']
    ClaimDedRem.objects.create(**claim_ded_rem_to_create)

def update_claim_status(claim, is_process, deductibles, user, products_id):
    """Update final claim status and related fields."""
    now = datetime.now()
    if not deductibles:
        logger.warning(f"claim {claim.uuid} did not have any item or service to valuate.")
        claim.status = Claim.STATUS_REJECTED
        return [{
            'code': REJECTION_REASON_NO_PRODUCT_FOUND,
            'message': _("claim.validation.product_family.no_product_found") % {
                'code': claim.code,
                'element': 'all'
            },
            'detail': claim.uuid
        }]
    elif is_process:
        claim.approved = deductibles['remunerated']
        if deductibles['relative_prices']:
            claim.status = Claim.STATUS_PROCESSED
            claim.remunerated = None
        else:
            claim.status = Claim.STATUS_VALUATED
            claim.remunerated = deductibles['remunerated']
        claim.audit_user_id_process = user.id_for_audit if user else -1
        claim.process_stamp = now
        claim.date_processed = now
        if claim.feedback_status == Claim.FEEDBACK_SELECTED:
            claim.feedback_status = Claim.FEEDBACK_BYPASSED
        if claim.review_status == Claim.REVIEW_SELECTED:
            claim.review_status = Claim.REVIEW_BYPASSED
    if not products_id:
        logger.warning(f"claim {claim.uuid} is not covered by any product.")
        claim.status = Claim.STATUS_REJECTED
        return [{
            'code': REJECTION_REASON_NO_PRODUCT_FOUND,
            'message': _("claim.validation.product_family.no_item_or_service") % {
                'code': claim.code,
                'element': 'all'
            },
            'detail': claim.uuid
        }]
    claim.save()
    return []

def process_dedrem(claim, user=None, is_process=False, policies=None, items=None, services=None, item_product_data = None, service_product_data = None):
    """Main function to process claim deductions and remunerations."""
    errors, target_date, category, hospitalization, hf_level = initialize_dedrem_processing(claim)
    archive_old_dedrems(claim)
    if not policies:
        policies = fetch_policies(claim, target_date, policies)
    if not policies:
        return [{
            'code': REJECTION_REASON_NO_COVERAGE,
            'message': _("claim.validation.family.no_policy") % {
                'code': claim.code,
                'insuree': str(claim.insuree)},
            'detail': claim.uuid
        }]
    items, services = fetch_items_and_services(claim, items, services)
    
    policies_id, products_id = get_policy_and_product_info(policies, items, services, target_date, item_product_data, service_product_data)
    claim_deductibles = {}
    item_ids = [item.item_id for item in items]
    service_ids = [service.service_id for service in services]
    if item_product_data is None:
        item_product_data = get_products(target_date, item_ids, claim.insuree_id, claim.insuree.is_adult(target_date), 'Item')
    if service_product_data is None:
        service_product_data = get_products(target_date, service_ids, claim.insuree_id, claim.insuree.is_adult(target_date), 'Service')
    product_data = {}

    for policy_id in policies_id:
        policy = next((p for p in policies if p.id == policy_id), None)
        if not policy:
            continue
        product_id = policy.product_id
        product_data = {
            'id': policy.product_id,
            'ceiling_interpretation': policy.product.ceiling_interpretation,
            'max_policy': policy.product.max_policy,
            'threshold': policy.product.threshold,
            'max_policy_extra_member': policy.product.max_policy_extra_member,
            'max_ceiling_policy':  policy.product.max_ceiling_policy,
            'max_ip_policy':  policy.product.max_ip_policy,
            'max_policy_extra_member_ip':  policy.product.max_policy_extra_member_ip,
            'max_ceiling_policy_ip':  policy.product.max_ceiling_policy_ip,
            'max_op_policy':  policy.product.max_op_policy,
            'max_policy_extra_member_op':  policy.product.max_policy_extra_member_op,
            'max_ceiling_policy_op':  policy.product.max_ceiling_policy_op,
            'max_amount_surgery':  policy.product.max_amount_surgery,
            'max_amount_delivery':  policy.product.max_amount_delivery,
            'max_amount_antenatal':  policy.product.max_amount_antenatal,
            'max_amount_hospitalization':  policy.product.max_amount_hospitalization,
            'max_amount_consultation':  policy.product.max_amount_consultation
        }
        hospital_visit = calculate_hospital_visit(product_data, hospitalization, hf_level)
        policy_members = get_policy_members(policy_id, target_date)
        demrems = fetch_previous_dedrems(claim, policy_id)
        deductibles = calculate_deductibles_and_ceilings(policy.product, product_data, claim, demrems, hospital_visit, policy_members)
        itmsrv = [
            *items,
            *services
        ]
        for claim_detail in itmsrv:
            if claim_detail.status not in [ClaimItem.STATUS_PASSED, ClaimService.STATUS_PASSED]:
                continue
            detail_is_item = isinstance(claim_detail, ClaimItem)
            itemsvc_quantity = claim_detail.qty_approved or claim_detail.qty_provided
            itemsvc_pricelist_detail = get_pricelist_detail(claim, claim_detail, target_date, detail_is_item)
            product_itemsvc = get_product_itemsvc(claim_detail, detail_is_item)
            set_price_adjusted = calculate_price_adjusted(claim, claim_detail, itemsvc_pricelist_detail, detail_is_item)
            result = process_claim_detail(
                claim, claim_detail, product_data, deductibles, category,
                hospital_visit, product_itemsvc, set_price_adjusted, itemsvc_quantity
            )
            deductibles['relative_prices'] = update_claim_detail(
                claim_detail, is_process, result, deductibles['relative_prices']
            )
        create_claim_dedrem(claim, policy, user, deductibles, hospital_visit)
        merge_deductible(claim_deductibles, deductibles)
    errors.extend(update_claim_status(claim, is_process, claim_deductibles, user, products_id))
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

def validate_claimitems(claim, target_date, adult, items, pricelist_dict, product_data_by_id, history_by_id, recent_dates_by_id):
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
            errors += validate_claimitem_frequency(claim, claimitem, target_date, recent_dates_by_id.get(claimitem.item_id, []))
        if not claimitem.rejection_reason:
            errors += validate_item_product_family(
                claimitem=claimitem,
                target_date=target_date,
                item=claimitem.item,
                insuree_id=claim.insuree_id,
                adult=adult,
                products_data=product_data_by_id.get(claimitem.item_id, []),
                history=history_by_id.get(claimitem.item_id, [])
            )
        if claimitem.rejection_reason:
            claimitem.status = ClaimItem.STATUS_REJECTED
        else:
            claimitem.rejection_reason = 0
            claimitem.status = ClaimItem.STATUS_PASSED
    return errors

def validate_claimservices(claim, target_date, adult, services, pricelist_dict, product_data_by_id, history_by_id, recent_dates_by_id, base_category, claims_by_category):
    errors = []
    for claimservice in services:
        if claimservice.rejection_reason:
            continue
        errors += validate_claimservice_validity(claim, claimservice)
        if not claimservice.rejection_reason:
            errors += validate_claimservice_in_price_list(claim, claimservice, pricelist_dict)
        if not claimservice.rejection_reason:
            errors += validate_claimdetail_care_type(claim, claimservice)
        if not claimservice.rejection_reason:
            errors += validate_claimdetail_limitation_fail(claim, claimservice)
        if not claimservice.rejection_reason:
            errors += validate_claimservice_frequency(claim, claimservice, target_date, recent_dates_by_id.get(claimservice.service_id, []))
        if not claimservice.rejection_reason:
            errors += validate_service_product_family(
                claimservice=claimservice,
                target_date=target_date,
                service=claimservice.service,
                insuree_id=claim.insuree_id,
                adult=adult,
                base_category=base_category,
                claim=claim,
                products_data=product_data_by_id.get(claimservice.service_id, []),
                history=history_by_id.get(claimservice.service_id, []),
                claims_by_category=claims_by_category            )
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
        errors += [{'code': REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
                    'message': _("claim.validation.claimitem_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    elif claimitem.item.validity_from and claimitem.item.validity_from > target_date:
        claimitem.rejection_reason = REJECTION_REASON_TARGET_DATE
        errors += [{
            'code': REJECTION_REASON_TARGET_DATE,
            'message': _("claim.validation.item_future_validity") % {
                'code': claim.code
            },
            'detail': claim.uuid
        }]
    return errors

def validate_claimservice_validity(claim, claimservice):
    errors = []
    target_date = get_claim_target_date(claim)
    if claimservice.validity_to is None and claimservice.service.validity_to is not None:
        claimservice.rejection_reason = REJECTION_REASON_INVALID_ITEM_OR_SERVICE
        errors += [{'code': REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
                    'message': _("claim.validation.claimservice_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    elif claimservice.service.validity_from and claimservice.service.validity_from > target_date:
        claimservice.rejection_reason = REJECTION_REASON_TARGET_DATE
        errors += [{
            'code': REJECTION_REASON_TARGET_DATE,
            'message': _("claim.validation.service_future_validity") % {
                'code': claim.code
            },
            'detail': claim.uuid}]
    return errors
def validate_claimitem_in_price_list(claim, claimitem, pricelist_dict=None):
    errors = []
    if claimitem.item_id not in pricelist_dict:
        claimitem.rejection_reason = REJECTION_REASON_NOT_IN_PRICE_LIST
        errors += [{'code': REJECTION_REASON_NOT_IN_PRICE_LIST,
                    'message': _("claim.validation.claimitem_in_price_list_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors
def validate_claimservice_in_price_list(claim, claimservice, pricelist_dict=None):
    errors = []
    if claimservice.service_id not in pricelist_dict:
        claimservice.rejection_reason = REJECTION_REASON_NOT_IN_PRICE_LIST
        errors += [{'code': REJECTION_REASON_NOT_IN_PRICE_LIST,
                    'message': _("claim.validation.claimservice_in_price_list_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors
def validate_claimdetail_care_type(claim, claimdetail):
    errors = []
    care_type = claimdetail.itemsvc.care_type
    hf_care_type = claim.health_facility.care_type if claim.health_facility.care_type else 'B'
    target_date = get_claim_target_date(claim)
    inpatient = target_date != claim.date_from
    if (
        (hf_care_type == 'O' and inpatient) or
        (hf_care_type == 'O' and care_type == 'I') or
        (hf_care_type == 'I' and care_type == 'O')
    ):
        claimdetail.rejection_reason = REJECTION_REASON_CARE_TYPE
        errors += [{'code': REJECTION_REASON_CARE_TYPE,
                    'message': _("claim.validation.claimdetail_care_type_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors
def validate_claimdetail_limitation_fail(claim, claimdetail):
    if claimdetail.itemsvc.patient_category == 0:
        return []
    errors = []
    target_date = get_claim_target_date(claim)
    patient_category_mask = utils.patient_category_mask(
        claim.insuree, target_date)
    if claimdetail.itemsvc.patient_category & patient_category_mask != patient_category_mask:
        claimdetail.rejection_reason = REJECTION_REASON_CATEGORY_LIMITATION
        errors += [{'code': REJECTION_REASON_CATEGORY_LIMITATION,
                    'message': _("claim.validation.claimdetail_limitation_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors
def validate_target_date(claim):
    errors = []
    if (claim.date_from is None and claim.date_to is None) \
            or claim.date_claimed < claim.date_from:
        claim.reject(REJECTION_REASON_TARGET_DATE)
        errors += [{'code': REJECTION_REASON_TARGET_DATE,
                    'message': _("claim.validation.target_date") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors
def validate_insuree(claim, insuree, policies=None):
    errors = []
    if insuree.validity_to is not None:
        errors += [{'code': REJECTION_REASON_FAMILY,
                    'message': _("claim.validation.family.insuree_validity") % {
                        'code': claim.code,
                        'insuree': str(insuree)},
                    'detail': claim.uuid}]
    if not policies and not InsureePolicy.objects.filter(
        insuree=insuree,
        effective_date__lte=claim.date_from,
        expiry_date__gte=claim.date_to or claim.date_from,
        *filter_validity()):
        errors += [{'code': REJECTION_REASON_NO_COVERAGE,
                    'message': _("claim.validation.family.no_policy") % {
                        'code': claim.code,
                        'insuree': str(insuree)},
                    'detail': claim.uuid}]
    if len(errors) > 0:
        claim.reject(REJECTION_REASON_FAMILY)
    return errors

def validate_item_product_family(claimitem, target_date, item, insuree_id, adult, products_data, history):
    errors = []
    found = False
    for data in products_data:
        (itemservice, itemservice_id, product_id, prod_elt_id, insuree_policy_effective_date, policy_effective_date,
        expiry_date, policy_stage, waiting_period, max_no_consultation, max_no_surgery,
        max_no_delivery, max_no_antenatal, max_no_hospitalization, max_no_visits, product_itmsrv_id, policy_id) = data
        if itemservice == 'item' and itemservice_id ==  claimitem.itemsvc.id:
            found = True
            core = __import__("core")
            insuree_policy_effective_date = core.datetime.date.from_ad_date(
                insuree_policy_effective_date)
            expiry_date = core.datetime.date.from_ad_date(expiry_date)
            claimitem.product_id = product_id
            claimitem.policy_id = policy_id
            claimitem.product_item = ProductItem.objects.get(id=product_itmsrv_id)
            errors += check_service_item_waiting_period(policy_stage, policy_effective_date,
                                                    insuree_policy_effective_date,
                                                    item, adult, claimitem.product_item, target_date, claimitem)
            errors += check_service_item_max_provision(adult, claimitem.product_item, item, insuree_policy_effective_date,
                                                    expiry_date, insuree_id, claimitem, history)
    if not found:
        claimitem.rejection_reason = REJECTION_REASON_NO_PRODUCT_FOUND
        errors += [{'code': REJECTION_REASON_NO_PRODUCT_FOUND,
                    'message': _("claim.validation.product_family.no_product_found") % {
                        'code': claimitem.claim.code,
                        'element': str(item)},
                    'detail': claimitem.claim.uuid}]
    return errors

def check_service_item_waiting_period(policy_stage, policy_effective_date, insuree_policy_effective_date, service_or_item,
                                 adult, product_service_item, target_date, claim_service_item):
    errors = []
    waiting_period = None
    if policy_stage == 'N' or policy_effective_date < insuree_policy_effective_date:
        if adult:
            waiting_period = product_service_item.waiting_period_adult
        else:
            waiting_period = product_service_item.waiting_period_child
    if waiting_period and target_date < \
            (insuree_policy_effective_date + datetimedelta(months=waiting_period)):
        claim_service_item.rejection_reason = REJECTION_REASON_WAITING_PERIOD_FAIL
        errors += [{'code': REJECTION_REASON_WAITING_PERIOD_FAIL,
                    'message': _("claim.validation.product_family.waiting_period") % {
                        'code': claim_service_item.claim.code,
                        'element': str(service_or_item)},
                    'detail': claim_service_item.claim.uuid}]
    return errors
def check_service_item_max_provision(adult, product_service_item, service_or_item, insuree_policy_effective_date,
                                     expiry_date, insuree_id, claim_service_item, history):
    errors = []
    if adult:
        limit_no = product_service_item.limit_no_adult
    else:
        limit_no = product_service_item.limit_no_child
    if limit_no is not None and limit_no >= 0:
        total_qty_provided = sum(qty for date, qty in history if insuree_policy_effective_date <= date <= expiry_date)
        qty = total_qty_provided + (claim_service_item.qty_provided if claim_service_item.qty_approved is None else claim_service_item.qty_approved)
        if qty > limit_no:
            if total_qty_provided < limit_no:
                remaining_qty = limit_no - total_qty_provided
                if claim_service_item.qty_approved is None:
                    claim_service_item.qty_provided = remaining_qty
                else:
                    claim_service_item.qty_approved = remaining_qty
            else:
                claim_service_item.rejection_reason = REJECTION_REASON_QTY_OVER_LIMIT
                errors += [{'code': REJECTION_REASON_QTY_OVER_LIMIT,
                            'message': _("claim.validation.product_family.max_nb_allowed") % {
                                'code': claim_service_item.claim.code,
                                'element': str(service_or_item),
                                'provided': total_qty_provided,
                                'max': limit_no},
                            'detail': claim_service_item.claim.uuid}]
    return errors



def validate_claim(claim, check_max, policies=None, user=None, process_dedrem_opt=True):
    """
    Based on the legacy validation, this method returns standard codes along with details
    :param claim: claim to be verified
    :param check_max: max amount to validate. Everything above will be rejected
    :return: (result_code, error_details)
    """
    logger.debug(f"Validating claim {claim.uuid}")
    if ClaimConfig.default_validations_disabled:
        return []
    errors = []
    detail_errors = []
    errors += validate_target_date(claim)
    if len(errors) == 0:
        errors += validate_insuree(claim, claim.insuree, policies)
    if len(errors) == 0:
        target_date = get_claim_target_date(claim)
        if not policies:
            policies = list(get_valid_policies_qs(claim.insuree.id, target_date))
        if policies:
            min_effective = min((p.effective_date for p in policies if p.effective_date), default=target_date)
            max_expiry = max((p.expiry_date for p in policies if p.expiry_date), default=target_date)
        else:
            errors += [{
                'code': REJECTION_REASON_NO_COVERAGE,
                'message': _("claim.validation.family.no_policy") % {
                    'code': claim.code,
                    'insuree': str(claim.insuree)},
                'detail': claim.uuid
            }]
        
    if len(errors) == 0:    
        base_category = get_claim_category(claim)
        adult = claim.insuree.is_adult(target_date)
        items = list(claim.items.filter(validity_to__isnull=True))
        services = list(claim.services.filter(validity_to__isnull=True))
        item_ids = [item.item_id for item in items]
        service_ids = [service.service_id for service in services]
        item_pricelist_dict = {pd.item_id: pd for pd in ItemsPricelistDetail.objects.filter(
            item_id__in=item_ids,
            items_pricelist=claim.health_facility.items_pricelist,
            items_pricelist__validity_to__isnull=True,
            *filter_validity(validity=target_date)
        )}
        service_pricelist_dict = {pd.service_id: pd for pd in ServicesPricelistDetail.objects.filter(
            service_id__in=service_ids,
            services_pricelist=claim.health_facility.services_pricelist,
            services_pricelist__validity_to__isnull=True,
            *filter_validity(validity=target_date)
        )}

    
        historical_item_qtys = ClaimItem.objects.filter(
            item_id__in=item_ids,
            claim__insuree_id=claim.insuree_id,
            claim__status__gt=Claim.STATUS_ENTERED,
            claim__validity_to__isnull=True,
            validity_to__isnull=True,
            status=ClaimDetail.STATUS_PASSED,
        ).filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True)) \
        .annotate(target_date=Coalesce("claim__date_to", "claim__date_from")) \
        .filter(target_date__gte=min_effective, target_date__lte=max_expiry) \
        .exclude(claim__uuid=claim.uuid) \
        .values('item_id', 'target_date', qty=Coalesce('qty_approved', 'qty_provided'))
        item_history_by_id = {}
        for h in historical_item_qtys:
            item_history_by_id.setdefault(h['item_id'], []).append((h['target_date'], h['qty']))
        historical_service_qtys = ClaimService.objects.filter(
            service_id__in=service_ids,
            claim__insuree_id=claim.insuree_id,
            claim__status__gt=Claim.STATUS_ENTERED,
            claim__validity_to__isnull=True,
            validity_to__isnull=True,
            status=ClaimDetail.STATUS_PASSED,
        ).filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True)) \
        .annotate(target_date=Coalesce("claim__date_to", "claim__date_from")) \
        .filter(target_date__gte=min_effective, target_date__lte=max_expiry) \
        .exclude(claim__uuid=claim.uuid) \
        .values('service_id', 'target_date', qty=Coalesce('qty_approved', 'qty_provided'))
        service_history_by_id = {}
        for h in historical_service_qtys:
            service_history_by_id.setdefault(h['service_id'], []).append((h['target_date'], h['qty']))
        historical_claims = Claim.objects \
            .filter(
                insuree_id=claim.insuree_id,
                validity_to__isnull=True,
                status__gt=Claim.STATUS_ENTERED,
            ).annotate(
                target_date=Coalesce("date_to", "date_from")
            ) \
            .filter(
                target_date__gte=min_effective,
                target_date__lte=max_expiry,
            ).exclude(uuid=claim.uuid) \
            .values('target_date', 'category')
        claims_by_category = {}
        for hc in historical_claims:
            cat = hc['category']
            claims_by_category.setdefault(cat, []).append(hc['target_date'])
        max_freq_days = max(
            *[ (item.item.frequency or 0) for item in items ],
            *[ (service.service.frequency or 0) for service in services ],
            0
        )
        freq_start_date = target_date - datetimedelta(days=max_freq_days)
        historical_frequency_items = ClaimItem.objects.filter(
            item_id__in=item_ids,
            claim__insuree_id=claim.insuree_id,
            claim__status__gt=Claim.STATUS_ENTERED,
            validity_to__isnull=True,
            claim__validity_to__isnull=True,
            status=ClaimDetail.STATUS_PASSED,
        ).filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True)) \
        .annotate(target_date=Coalesce("claim__date_to", "claim__date_from")) \
        .filter(target_date__gte=freq_start_date, target_date__lte=target_date) \
        .exclude(claim__uuid=claim.uuid) \
        .values('item_id', 'target_date')
        recent_item_dates_by_id = {}
        for hfi in historical_frequency_items:
            recent_item_dates_by_id.setdefault(hfi['item_id'], []).append(hfi['target_date'])
        historical_frequency_services = ClaimService.objects.filter(
            service_id__in=service_ids,
            claim__insuree_id=claim.insuree_id,
            claim__status__gt=Claim.STATUS_ENTERED,
            validity_to__isnull=True,
            claim__validity_to__isnull=True,
            status=ClaimDetail.STATUS_PASSED,
        ).filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True)) \
        .annotate(target_date=Coalesce("claim__date_to", "claim__date_from")) \
        .filter(target_date__gte=freq_start_date, target_date__lte=target_date) \
        .exclude(claim__uuid=claim.uuid) \
        .values('service_id', 'target_date')
        recent_service_dates_by_id = {}
        for hfs in historical_frequency_services:
            recent_service_dates_by_id.setdefault(hfs['service_id'], []).append(hfs['target_date'])
        item_product_data = get_products(target_date, item_ids, claim.insuree_id, adult, 'Item')
        service_product_data = get_products(target_date, service_ids, claim.insuree_id, adult, 'Service')
        product_ids = set()
        for pd in item_product_data.values():
            for row in pd:
                product_ids.add(row[3])
        for pd in service_product_data.values():
            for row in pd:
                product_ids.add(row[3])
        detail_errors += validate_claimitems(claim, target_date, adult, items, item_pricelist_dict, item_product_data, item_history_by_id, recent_item_dates_by_id)
        detail_errors += validate_claimservices(claim, target_date, adult, services, service_pricelist_dict, service_product_data, service_history_by_id, recent_service_dates_by_id, base_category, claims_by_category)
    if len(errors) == 0 and check_max:
        over_category_errors = [
            x for x in detail_errors if x['code'] in [REJECTION_REASON_MAX_HOSPITAL_ADMISSIONS,
                                                      REJECTION_REASON_MAX_VISITS,
                                                      REJECTION_REASON_MAX_CONSULTATIONS,
                                                      REJECTION_REASON_MAX_SURGERIES,
                                                      REJECTION_REASON_MAX_DELIVERIES,
                                                      REJECTION_REASON_MAX_ANTENATAL]]
        if len(over_category_errors) > 0:
            claim.items.filter(validity_to__isnull=True) \
                .update(status=ClaimItem.STATUS_REJECTED,
                        qty_approved=0,
                        rejection_reason=over_category_errors[0]['code'])
            claim.services.filter(validity_to__isnull=True) \
                .update(status=ClaimService.STATUS_REJECTED,
                        qty_approved=0,
                        rejection_reason=over_category_errors[0]['code'])
        else:
            for item in items:
                if item.rejection_reason:
                    item.status = ClaimItem.STATUS_REJECTED
                    item.qty_approved=0
                    item.product_item=None
                else:
                    item.status = ClaimItem.STATUS_PASSED
                item.save()
            for service in services:
                if service.rejection_reason:
                    service.status = ClaimService.STATUS_REJECTED
                    service.qty_approved=0
                    service.product_service=None
                else:
                    service.status = ClaimService.STATUS_PASSED
                service.save()
        if all(item.status == ClaimItem.STATUS_REJECTED for item in claim.items.filter(validity_to__isnull=True)) and \
           all(service.status == ClaimService.STATUS_REJECTED for service in claim.services.filter(validity_to__isnull=True)):
            errors += [{'code': REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
                        'message': _("claim.validation.all_items_and_services_rejected") % {
                            'code': claim.code},
                        'detail': claim.uuid}]
            if len(detail_errors) > 0:
                errors += detail_errors
            claim.status = Claim.STATUS_REJECTED
            claim.rejection_reason = REJECTION_REASON_INVALID_ITEM_OR_SERVICE
            claim.save()
        if process_dedrem_opt and len(errors) == 0:
            
            dedrem_errors = process_dedrem(
                claim, user, is_process=True, policies=policies, items=items, 
                services=services, item_product_data=item_product_data, 
                service_product_data=service_product_data
            )
            errors.extend(dedrem_errors)
    logger.debug(f"Validation found {len(errors)} error(s)")
    return errors

def validate_claimitem_frequency(claim, claimitem, target_date, recent_dates):
    errors = []
    if claimitem.item.frequency and any(
        d >= (target_date - datetimedelta(days=claimitem.item.frequency)) for d in recent_dates
    ):
        claimitem.rejection_reason = REJECTION_REASON_FREQUENCY_FAILURE
        errors += [{
            'code': REJECTION_REASON_FREQUENCY_FAILURE,
            'message': _("claim.validation.claimitem_frequency_validity") % {
                'code': claim.code
            },
            'detail': claim.uuid
        }]
    return errors

def validate_claimservice_frequency(claim, claimservice, target_date, recent_dates):
    errors = []
    if claimservice.service.frequency and any(
        d >= (target_date - datetimedelta(days=claimservice.service.frequency)) for d in recent_dates
    ):
        claimservice.rejection_reason = REJECTION_REASON_FREQUENCY_FAILURE
        errors += [{
            'code': REJECTION_REASON_FREQUENCY_FAILURE,
            'message': _("claim.validation.claimservice_frequency_validity") % {
                'code': claim.code
            },
            'detail': claim.uuid
        }]
    return errors

def validate_service_product_family(claimservice, target_date, service, insuree_id, adult, base_category, claim, products_data, history, claims_by_category):
    errors = []
    found = False
    for data in products_data:
        (itemservice, itemservice_id, product_id, prod_elt_id, insuree_policy_effective_date, policy_effective_date,
        expiry_date, policy_stage, waiting_period, max_no_consultation, max_no_surgery,
        max_no_delivery, max_no_antenatal, max_no_hospitalization, max_no_visits, product_itmsrv_id, policy_id) = data
        if itemservice == 'service' and itemservice_id == claimservice.itemsvc.id:
            
            product_data = {
                'max_no_consultation': max_no_consultation,
                'max_no_surgery': max_no_surgery,
                'max_no_delivery': max_no_delivery,
                'max_no_antenatal': max_no_antenatal,
                'max_no_hospitalization': max_no_hospitalization,
                'max_no_visits': max_no_visits
            }
            found = True
            core = __import__("core")
            insuree_policy_effective_date = core.datetime.date.from_ad_date(
                insuree_policy_effective_date)
            expiry_date = core.datetime.date.from_ad_date(expiry_date)
            claimservice.product_id = product_id
            claimservice.policy_id = policy_id
            claimservice.product_service = ProductService.objects.get(id=product_itmsrv_id)
            errors += check_service_item_waiting_period(policy_stage, policy_effective_date,
                                                    insuree_policy_effective_date, service, adult,
                                                    claimservice.product_service, target_date, claimservice)
            errors += check_service_item_max_provision(adult, claimservice.product_service, service, insuree_policy_effective_date,
                                                    expiry_date, insuree_id, claimservice, history)
            error_len = len(errors)
            if base_category != 'O':
                errors += check_claim_max_no_category(base_category, product_data, expiry_date, insuree_id,
                                                    insuree_policy_effective_date, claim, claimservice, claims_by_category)
                if error_len != len(errors):
                    break
    if not found:
        claimservice.rejection_reason = REJECTION_REASON_NO_PRODUCT_FOUND
        errors += [{
            'code': REJECTION_REASON_NO_PRODUCT_FOUND,
            'message': _("claim.validation.product_family.no_product_found") % {
                'code': claimservice.claim.code,
                'element': str(service)},
            'detail': claimservice.claim.uuid
        }]
    return errors

def _get_dedrem(prefix, dedrem_type, field, product, insuree, demrems):
    if getattr(product, prefix + "_treatment", None):
        return Deductible(
            getattr(product, prefix + "_treatment", None),
            dedrem_type,
            0
        )
    if getattr(product, prefix + "_insuree", None):
        prev = sum([getattr(dr, field, 0)
            for dr in demrems if dr.insuree_id == insuree.id])
        return Deductible(
            getattr(product, prefix + "_insuree", None),
            dedrem_type,
            prev if prev else 0
        )
    if getattr(product, prefix + "_policy", None):
        prev = sum([getattr(dr, field, 0) for dr in demrems])
        return Deductible(
            getattr(product, prefix + "_policy", None),
            dedrem_type,
            prev if prev else 0
        )
    return None
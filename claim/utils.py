import math
from claim.models import (
    Claim,
    ClaimItem,
    ClaimService,
    ClaimDetail,
    ClaimServiceItem,
    ClaimServiceService,
)
from medical.models import Item, Service
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from policy.models import Policy
from insuree.models import InsureePolicy
from claim.subqueries import (
    total_elm_approved_exp,
)
from product.models import Product, ProductItem, ProductService
from django.db.models import (
    Q,
    Prefetch,
)

from django.db.models import DecimalField, ExpressionWrapper


def process_child_relation(user, data_children, claim_id, children, create_hook):
    claimed = 0
    from core.utils import TimeUtils

    if __check_if_maximum_amount_overshoot(data_children, children):
        raise ValidationError(_("mutation.claim_item_service_maximum_amount_overshoot"))
    for data_elt in data_children:
        use_sub = create_hook == service_create_hook

        claimed += calcul_amount_service(data_elt, use_sub)

        elt_id = data_elt.pop("id") if "id" in data_elt else None
        if elt_id:
            # elt has been historized along with claim historization
            elt = children.get(id=elt_id)
            [setattr(elt, k, v) for k, v in data_elt.items()]
            elt.validity_from = TimeUtils.now()
            elt.audit_user_id = user.id_for_audit
            elt.claim_id = claim_id
            elt.validity_to = None
            if create_hook == service_create_hook:
                service_update_hook(elt.claim_id, data_elt)

            elt.save()
        else:
            data_elt["validity_from"] = TimeUtils.now()
            data_elt["audit_user_id"] = user.id_for_audit
            # Ensure claim id from func argument will be assigned
            data_elt.pop("claim_id", None)
            # Should entered claim items/services have status passed assigned?
            # Status is mandatory field, and it doesn't have default value in model
            data_elt["status"] = ClaimDetail.STATUS_PASSED
            create_hook(claim_id, data_elt)

    return claimed


def get_claim_target_date(claim):
    return claim.date_to if claim.date_to else claim.date_from


def generic_amount_claimdetail(elt):
    return (
        elt.get("price_approved") or elt.get("price_adjusted") or elt.get("price_asked")
    ) * (elt.get("qty_approved") or elt.get("qty_provided")) or 0


def get_valid_policies_qs(insuree_id, target_date):
    return Policy.objects.filter(
        insuree_policies__insuree_id=insuree_id,
        *Policy.filter_validity(validity=target_date),
        *InsureePolicy.filter_validity(validity=target_date, prefix="insuree_policies__"),
        effective_date__lte=target_date,
        expiry_date__gte=target_date,
        status__in=[Policy.STATUS_ACTIVE, Policy.STATUS_EXPIRED],
        insuree_policies__effective_date__lte=target_date,
        insuree_policies__expiry_date__gte=target_date,
    )


def calcul_amount_service(elt, use_sub=True):
    if "service_id" in elt and use_sub:
        service = Service.objects.get(id=elt["service_id"], validity_to__isnull=True)
        if service.manualPrice:
            total_claimed = service.price
            return total_claimed
    total_claimed = generic_amount_claimdetail(elt)
    total_claimed_sub = 0
    sub_found = False
    if "service_item_set" in elt and isinstance(elt["service_item_set"], list):
        for service_item in elt["service_item_set"]:
            sub_found = True
            total_claimed_sub += generic_amount_claimdetail(service_item)
    if "service_service_set" in elt and isinstance(elt["service_service_set"], list):
        for service_service in elt["service_service_set"]:
            sub_found = True
            total_claimed_sub += generic_amount_claimdetail(service_service)
    if use_sub and sub_found:
        return total_claimed_sub
    return total_claimed


def approved_amount(claim):
    if claim.status != Claim.STATUS_REJECTED:
        return (
            Claim.objects.filter(id=claim.id).aggregate(
                value=ExpressionWrapper(
                    total_elm_approved_exp("items__")
                    + total_elm_approved_exp("services__"),
                    output_field=DecimalField(),
                )
            )["value"]
            or 0
        )
    else:
        return 0


def get_claim_product(
    claim, adult, target_date=None, items=None, services=None, assigned=False
):
    if not target_date:
        target_date = get_claim_target_date(claim)
    if items is None:
        items = claim.items.filter(*ClaimItem.filter_validity(validity=target_date))
    if services is None:
        services = claim.services.filter(*ClaimService.filter_validity(validity=target_date))

    qs = Product.objects
    if assigned:
        qs = qs.filter(
            Q(
                Q(n=[i.product_id for i in items])
                | Q(id__in=[s.product_id for s in services])
            )
        )
    else:
        qs = qs.filter(
            policies__effective_date__lte=target_date,
            policies__expiry_date__gte=target_date,
            policies__status__in=[Policy.STATUS_ACTIVE, Policy.STATUS_EXPIRED],
            policies__insuree_policies__insuree_id=claim.insuree.id,
            policies__insuree_policies__effective_date__lte=target_date,
            policies__insuree_policies__expiry_date__gte=target_date,
            *Policy.filter_validity(validity=target_date, prefix="policies__"),
            *InsureePolicy.filter_validity(
                validity=target_date, prefix="policies__insuree_policies__"
            ),
        ).filter(
            Q(
                Q(items__item__in=[i.item_id for i in items])
                | Q(services__service__in=[s.service_id for s in services])
            )
        )
    return list(
        qs.prefetch_related(
            Prefetch(
                "items",
                queryset=ProductItem.objects.filter(
                    *ProductItem.filter_validity(validity=target_date)
                ).prefetch_related("item"),
            )
        ).prefetch_related(
            Prefetch(
                "services",
                queryset=ProductService.objects.filter(
                    *ProductService.filter_validity(validity=target_date)
                ).prefetch_related("service"),
            )
        )
    )


def __check_if_maximum_amount_overshoot(data_children, children):
    is_overshoot = False
    for entity in data_children:
        quantity = entity.get("qty_provided")
        maximum_amount = None

        if children.model == ClaimItem:
            current_item = Item.objects.get(
                id=entity["item_id"], validity_to__isnull=True
            )
            maximum_amount = (
                int(current_item.maximum_amount)
                if current_item.maximum_amount
                else None
            )
        elif children.model == ClaimService:
            current_service = Service.objects.get(
                id=entity["service_id"], validity_to__isnull=True
            )
            maximum_amount = (
                int(current_service.maximum_amount)
                if current_service.maximum_amount
                else None
            )

        if maximum_amount is not None and (quantity > maximum_amount):
            is_overshoot = True
            break

    return is_overshoot


def item_create_hook(claim_id, item):
    # TODO: investigate 'availability' is mandatory,
    # but not in UI > always true?
    item["availability"] = True
    ClaimItem.objects.create(claim_id=claim_id, **item)


def service_create_hook(claim_id, service):
    service_item_set = service.pop("service_item_set", [])
    service_service_set = service.pop("service_service_set", [])
    ClaimServiceId = ClaimService.objects.create(claim_id=claim_id, **service)

    for service_item in service_item_set:
        if "qty_asked" in service_item:
            if math.isnan(service_item["qty_asked"]):
                service_item["qty_asked"] = 0
        itemId = Item.objects.filter(code=service_item["sub_item_code"]).first()
        ClaimServiceItem.objects.create(
            item=itemId,
            claim_service=ClaimServiceId,
            qty_displayed=service_item["qty_asked"],
            qty_provided=service_item["qty_provided"],
            price_asked=service_item["price_asked"],
        )

    for service_service in service_service_set:
        if "qty_asked" in service_service:
            if math.isnan(service_service["qty_asked"]):
                service_service["qty_asked"] = 0
        serviceId = Service.objects.filter(
            code=service_service["sub_service_code"]
        ).first()
        ClaimServiceService.objects.create(
            service=serviceId,
            claim_service=ClaimServiceId,
            qty_displayed=service_service["qty_asked"],
            qty_provided=service_service["qty_provided"],
            price_asked=service_service["price_asked"],
        )


def service_update_hook(claim_id, service):
    service_item_set = service.pop("service_item_set", [])
    service_service_set = service.pop("service_service_set", [])
    ClaimServiceId = ClaimService.objects.filter(
        claim=claim_id, service=service["service_id"]
    ).first()

    for service_item in service_item_set:
        if "qty_asked" in service_item:
            if math.isnan(service_item["qty_asked"]):
                service_item["qty_asked"] = 0
        itemId = Item.objects.filter(code=service_item["sub_item_code"]).first()
        claimServiceItemId = ClaimServiceItem.objects.filter(
            item=itemId, claim_service=ClaimServiceId
        ).first()
        claimServiceItemId.qty_displayed = service_item["qty_asked"]
        claimServiceItemId.save()

    for service_service in service_service_set:
        if "qty_asked" in service_service:
            if math.isnan(service_service["qty_asked"]):
                service_service["qty_asked"] = 0
        serviceId = Service.objects.filter(
            code=service_service["sub_service_code"]
        ).first()
        claimServiceServiceId = ClaimServiceService.objects.filter(
            service=serviceId, claim_service=ClaimServiceId
        ).first()
        claimServiceServiceId.qty_displayed = service_service["qty_asked"]
        claimServiceServiceId.save()


def process_items_relations(user, claim, items):
    return process_child_relation(user, items, claim.id, claim.items, item_create_hook)


def process_services_relations(user, claim, services):
    return process_child_relation(
        user, services, claim.id, claim.services, service_create_hook
    )


def autogenerate_nepali_claim_code(config):
    code_length = config.get("code_length")
    if not code_length and type(code_length) is not int:
        raise ValueError(
            "Invalid config for `autogenerate_nepali_claim_code`, expected `code_length` value"
        )
    prefix = __get_current_nepali_fiscal_year_code()
    last_claim = Claim.objects.filter(validity_to__isnull=True, code__icontains=prefix)
    code = 0
    if last_claim:
        code = int(last_claim.latest("code").code[-code_length:])
    return prefix + str(code + 1).zfill(code_length)


def __get_current_nepali_fiscal_year_code():
    import nepali_datetime

    current_date = nepali_datetime.date.today()

    if current_date.month < 4:
        current_year = nepali_datetime.date.today().year - 1
    else:
        current_year = nepali_datetime.date.today().year

    year_code = str(current_year) + "-" + str(current_year + 1)[-3:] + "-"
    return year_code


def get_queryset_valid_at_date(queryset, date):
    filtered_qs = queryset.filter(validity_to__gte=date, validity_from__lte=date)
    if filtered_qs.exists():
        return filtered_qs
    return queryset.filter(validity_from__lte=date, validity_to__isnull=True)

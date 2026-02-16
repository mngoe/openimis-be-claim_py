from claim.models import Claim, ClaimService, ClaimItem, ClaimDedRem
from core.models.user import ClaimAdmin
from claim.validations import get_claim_category
from claim.utils import approved_amount
from claim.services import claim_create, update_sum_claims
from medical.test_helpers import (
    get_item_of_type,
    get_service_of_category,
    create_test_diagnosis,
)
from uuid import uuid4
from location.models import HealthFacility, Location
from product.models import ProductItem, ProductService, Product
from product.test_helpers import (
    create_test_product_service,
    create_test_product_item,
    create_test_product,
)
from medical_pricelist.test_helpers import (
    add_service_to_hf_pricelist,
    add_item_to_hf_pricelist,
)
from insuree.test_helpers import create_test_insuree
from policy.test_helpers import create_test_policy2
from insuree.models import Insuree
from location.test_helpers import create_test_health_facility
from medical.test_helpers import create_test_item, create_test_service


class DummyUser:
    def __init__(self):
        self.id_for_audit = 1
        self.id = 1


def create_test_claim(custom_props=None, user=DummyUser(), product=None):
    if custom_props is None:
        custom_props = {}
    else:
        custom_props = {k: v for k, v in custom_props.items() if hasattr(Claim, k)}
    from datetime import datetime, timedelta

    insuree = None
    insuree_in_props = False
    if "insuree" in custom_props:
        insuree = custom_props["insuree"]
        insuree_in_props = True
    elif "insuree_id" in custom_props:
        insuree = Insuree.objects.filter(id=custom_props["insuree_id"]).first()
        insuree_in_props = True
    else:
        insuree = create_test_insuree()
        custom_props["insuree"] = insuree

    if not insuree_in_props and not product:
        product = create_test_product()
    if product:
        create_test_policy2(product, insuree)

    _to = datetime.now() - timedelta(days=1)

    if "icd" not in custom_props and "icd_id" not in custom_props:
        custom_props["icd"] = create_test_diagnosis()
    elif "icd" in custom_props and isinstance(custom_props["icd"], dict):
        custom_props["icd"] = create_test_diagnosis(custom_props=custom_props["icd"])
    if (
        "health_facility_id" not in custom_props
        and "health_facility" not in custom_props
    ):
        custom_props["health_facility"] = create_test_health_facility()
    if "claim_admin" not in custom_props and "claim_admin_id" not in custom_props:
        if "health_facility" in custom_props:
            custom_props_ca = {"health_facility": custom_props["health_facility"]}
        else:
            custom_props_ca = {"health_facility_id": custom_props["health_facility_id"]}
        custom_props["claim_admin"] = create_test_claim_admin(
            custom_props=custom_props_ca
        )

    claim = claim_create(
        {
            "date_from": datetime.now() - timedelta(days=2),
            "date_claimed": _to,
            "date_to": None,
            "status": 2,
            "validity_from": _to,
            "code": str(uuid4()),
            **custom_props,
        },
        user,
    )
    return claim


def create_test_claimitem(
    claim, item_type="D", valid=True, custom_props=None, product=None
):
    if custom_props is None:
        custom_props = {}
    item = None
    if "item" not in custom_props and "item_id" not in custom_props:
        if item_type:
            item = get_item_of_type(item_type)
        if not item:
            item = create_test_item(item_type, custom_props=custom_props)
        custom_props["item"] = item

    custom_props_item = {k: v for k, v in custom_props.items() if hasattr(ClaimItem, k)}
    item = ClaimItem.objects.create(
        **{
            "claim": claim,
            "qty_provided": 7,
            "price_asked": 11,
            "status": 1,
            "availability": True,
            "validity_from": "2019-06-01",
            "validity_to": None if valid else "2019-06-01",
            "audit_user_id": -1,
            **custom_props_item,
        }
    )
    update_sum_claims(claim)
    if product:
        custom_props_item = {
            k: v for k, v in custom_props.items() if hasattr(ProductItem, k)
        }
        create_test_product_item(
            product,
            item.item,
            custom_props=custom_props_item,
        )
        add_item_to_hf_pricelist(
            item.item, claim.health_facility
        )
        claim.refresh_from_db()
    return item


def create_test_claimservice(
    claim, category="V", valid=True, custom_props=None, product=None
):
    if custom_props is None:
        custom_props = {}
    service = None
    if "service" not in custom_props and "service_id" not in custom_props:
        if category:
            service = get_service_of_category(category)
        if not service:
            service = create_test_service(category, custom_props=custom_props)
        custom_props["service"] = service
    custom_props_service = {
        k: v for k, v in custom_props.items() if hasattr(ClaimService, k)
    }
    service = ClaimService.objects.create(
        **{
            "claim": claim,
            "qty_provided": 7,
            "price_asked": 11,
            "status": 1,
            "validity_from": "2019-06-01",
            "validity_to": None if valid else "2019-06-01",
            "audit_user_id": -1,
            **custom_props_service,
        }
    )
    update_sum_claims(claim)
    if product:
        custom_props_prod_service = {
            k: v for k, v in custom_props.items() if hasattr(ProductService, k)
        }
        create_test_product_service(
            product,
            service.service,
            custom_props=custom_props_prod_service,
        )
        add_service_to_hf_pricelist(service.service, claim.health_facility)
        claim.refresh_from_db()
    return service


def mark_test_claim_as_processed(claim, status=Claim.STATUS_CHECKED, audit_user_id=-1):
    claim.approved = approved_amount(claim)
    claim.status = status
    claim.audit_user_id_submit = audit_user_id
    from core.utils import TimeUtils

    claim.submit_stamp = TimeUtils.now()
    claim.category = get_claim_category(claim)
    claim.save()


def delete_claim_with_itemsvc_dedrem_and_history(claim):
    # first delete old versions of the claim
    ClaimDedRem.objects.filter(claim=claim).delete()
    old_claims = Claim.objects.filter(legacy_id=claim.id)
    ClaimItem.objects.filter(claim__in=old_claims).delete()
    ClaimService.objects.filter(claim__in=old_claims).delete()
    old_claims.delete()
    claim.items.all().delete()
    claim.services.all().delete()
    claim.delete()


def create_test_claim_admin(custom_props=None):
    if custom_props is None:
        custom_props = {}
    from core import datetime

    custom_props = {k: v for k, v in custom_props.items() if hasattr(ClaimAdmin, k)}
    if (
        "health_facility" not in custom_props
        and "health_facility_id" not in custom_props
    ):
        custom_props["health_facility"] = create_test_health_facility(
            code=None, location_id=None
        )

    code = custom_props.pop("code", "TST-CA")
    uuid = custom_props.pop("uuid", uuid4())
    ca = None
    qs_ca = ClaimAdmin.objects
    data = {
        "code": code,
        "uuid": uuid,
        "last_name": "LastAdmin",
        "other_names": "JoeAdmin",
        "email_id": "joeadmin@lastadmin.com",
        "phone": "+12027621401",
        "has_login": False,
        "audit_user_id": 1,
        "validity_from": datetime.datetime(2019, 6, 1),
        **custom_props,
    }
    if code:
        qs_ca = qs_ca.filter(code=code)
    if uuid:
        qs_ca = qs_ca.filter(uuid=uuid)

    if code or uuid:
        ca = qs_ca.first()
    if ca:
        data["uuid"] = ca.uuid
        ca.objects.update(**data)
        return ca
    else:
        return ClaimAdmin.objects.create(**data)


def create_test_claim_context(
    claim=None,
    claim_admin=None,
    insuree=None,
    product=None,
    hf=None,
    items=None,
    services=None,
):
    if claim is None:
        claim = {}
    if claim_admin is None:
        claim_admin = {}
    if insuree is None:
        insuree = {}
    if product is None:
        product = {}
    if hf is None:
        hf = {}
    if items is None:
        items = []
    if services is None:
        services = []

    if claim and isinstance(claim, Claim):
        if claim.insuree:
            insuree = claim.insuree
        if claim.health_facility:
            hf = claim.health_facility
    if isinstance(insuree, dict):
        prop = insuree if isinstance(insuree, dict) else {}
        insuree = create_test_insuree(with_family=True, is_head=True, custom_props=prop)

    if not isinstance(hf, HealthFacility):
        hf_props = hf if isinstance(hf, dict) else {}
        code = hf_props["code"] if "code" in hf_props else "HFH"
        if "location_id" in hf_props and hf_props["location_id"]:
            location_id = hf_props["location_id"]
        elif (
            "location" in hf_props
            and hf_props["location"]
            and isinstance(hf["location"], Location)
        ):
            location_id = hf_props["location"].id
        else:
            location_id = insuree.current_village.id or insuree.family.location.id
        hf = create_test_health_facility(code, location_id, custom_props={})
        product_code = (
            product["code"]
            if isinstance(product, dict) and "code" in product
            else "TPDT"
        )
        if not isinstance(product, Product):
            product = create_test_product(product_code, custom_props=product)

    if insuree and product:
        policy, insuree_policy = create_test_policy2(product, insuree)
    else:
        raise Exception("insuree or product not created")

    claim_admin_props = claim_admin if isinstance(claim_admin, dict) else {}
    claim_admin_props["health_facility_id"] = hf.id
    if not isinstance(claim_admin, ClaimAdmin):
        claim_admin = create_test_claim_admin(custom_props=claim_admin_props)
    if not isinstance(claim, Claim):
        claim_props = claim if isinstance(claim, dict) else {}
        claim_props["insuree"] = insuree
        claim_props["health_facility_id"] = hf.id
        claim_props["admin"] = claim_admin
        claim = create_test_claim(custom_props=claim_props)
    if isinstance(claim, object):
        if not items:
            items = list(claim.items.all())
        if not services:
            services = list(claim.services.all())
    items_source = None
    if all([isinstance(i, dict) for i in items]):
        items_source = items.copy()
        items = []
        for item in items_source:
            if isinstance(item, dict):
                item_type = item.pop("type", "V")
                it = create_test_item(item_type, custom_props=item)
                items.append(it)
    for item in items:
        custom_props = items_source.pop(0) if items_source else {}
        create_test_product_item(
            product,
            item,
            custom_props=custom_props if isinstance(custom_props, dict) else {},
        )
        if items_source is not None:
            custom_props["item"] = item
            create_test_claimitem(claim=claim, custom_props=custom_props)
    services_source = None
    if all([isinstance(s, dict) for s in services]):
        services_source = services.copy()
        services = []
        for service in services_source:
            if isinstance(service, dict):
                service_type = service.pop("type", "V")
                it = create_test_service(service_type, custom_props=service)
            services.append(it)
    for service in services:
        custom_props = services_source.pop(0) if services_source else {}
        create_test_product_service(product, service, custom_props=custom_props)
        if services_source is not None:
            custom_props["service"] = service
            create_test_claimservice(claim=claim, custom_props=custom_props)
    return claim, insuree, policy, hf


def full_delete_claim(claim_id):
    ClaimItem.objects.filter(claim_id=claim_id).delete()
    ClaimService.objects.filter(claim_id=claim_id).delete()
    Claim.objects.filter(id=claim_id).delete()

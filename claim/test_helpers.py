from claim.models import Claim, ClaimService, ClaimItem, ClaimDedRem, ClaimAdmin
from claim.validations import get_claim_category
from claim.utils import approved_amount
from claim.services import claim_create, update_sum_claims
from medical.test_helpers import get_item_of_type, get_service_of_category 
from uuid import uuid4
from product.models import ProductItem, ProductService, ProductItemOrService
from product.test_helpers import create_test_product_service, create_test_product_item
from medical_pricelist.test_helpers import add_service_to_hf_pricelist, add_item_to_hf_pricelist
from insuree.test_helpers import create_test_insuree
from policy.test_helpers import create_test_policy2
from insuree.models import Insuree

class DummyUser:
    def __init__(self):
      self.id_for_audit = 1  

def create_test_claim(custom_props={}, user = DummyUser(), product=None):
    from datetime import datetime, timedelta
    insuree = None
    if 'insuree' in custom_props:
        insuree = custom_props['insuree']
    if 'insuree_id' in custom_props:
        insuree = Insuree.objects.filter(id=custom_props['insuree_id']).first()
    else:
        insuree = create_test_insuree()
        custom_props["insuree"]= insuree
        
    _to = datetime.now() - timedelta(days=1)
    if product:
        create_test_policy2(product, insuree)
    
    return claim_create(
        {
            "health_facility_id": 18,
            "icd_id": 116,
            "date_from": datetime.now() - timedelta(days=2),
            "date_claimed": _to,
            "date_to": None,
            "status": 2,
            "validity_from": _to,
            "code": str(uuid4()),
            **custom_props
        }, user
    )


def create_test_claimitem(claim, item_type, valid=True, custom_props={}, product=None):
    item_id = custom_props.pop(
        'item_id',
        get_item_of_type(item_type).id if item_type and get_item_of_type(item_type) else 23)# Atropine
    item = ClaimItem.objects.create(
        **{
            "claim": claim,
            "qty_provided": 7,
            "price_asked": 11,
            "item_id": item_id,  
            "status": 1,
            "availability": True,
            "validity_from": "2019-06-01",
            "validity_to": None if valid else "2019-06-01",
            "audit_user_id": -1,
            **custom_props
           }
    )
    update_sum_claims(claim)
    if product:
        product_item = create_test_product_item(
            product,
            item.item,
            custom_props={"price_origin": ProductItemOrService.ORIGIN_RELATIVE},
        )
        pricelist_detail = add_item_to_hf_pricelist(
            item.item,
            hf_id=claim.health_facility.id
        )

    
    return item



def create_test_claimservice(claim, category=None, valid=True, custom_props={}, product=None):
    service_id = custom_props.pop(
        'service_id',
        get_service_of_category(category).id if category and get_service_of_category(category) else 23)# Atropine
    
    service = ClaimService.objects.create(
        **{
            "claim": claim,
            "qty_provided": 7,
            "price_asked": 11,
            "service_id": service_id,  # Skin graft, no cat
            "status": 1,
            "validity_from": "2019-06-01",
            "validity_to": None if valid else "2019-06-01",
            "audit_user_id": -1,
            **custom_props
        }
    )    
    update_sum_claims(claim)
    if product:
        create_test_product_service(
            product,
            service.service,
            custom_props={"price_origin": ProductItemOrService.ORIGIN_RELATIVE},
        )
        add_service_to_hf_pricelist(
            service.service,
            hf_id=claim.health_facility.id
        )

    
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


def create_test_claim_admin(custom_props={}):
    from core import datetime
    code = custom_props.pop('code','TST-CA')
    uuid = custom_props.pop('uuid',None)
    ca = None
    qs_ca = ClaimAdmin.objects
    data = {
        "code": code,
        "uuid": uuid,
        "last_name": "LastAdmin",
        "other_names": "JoeAdmin",
        "email_id": "joeadmin@lastadmin.com",
        "phone": "+12027621401",
        "health_facility_id": 1,
        "has_login": False,
        "audit_user_id": 1,
        "validity_from": datetime.datetime(2019, 6, 1),
        **custom_props
    }
    if code:
        qs_ca = qs_ca.filter(code=code)
    if uuid:
        qs_ca = qs_ca.filter(uuid=uuid)
        
    if code or uuid:
        ca = qs_ca.first()
    if ca:
        data['uuid']=ca.uuid
        ca.update(data)
        return ca
    else:
        data['uuid']=uuid4()
        return ClaimAdmin.objects.create( **data)

from product.test_helpers import create_test_product
from location.test_helpers import create_test_health_facility
from medical.test_helpers import create_test_item, create_test_service
from medical_pricelist.test_helpers import (
    create_test_item_pricelist,
    create_test_service_pricelist,
    add_service_to_hf_pricelist,
    add_item_to_hf_pricelist,
)

def create_claim_context(claim = None, insuree={}, product={}, hf={}, items=[], services=[]):
    if isinstance(claim, object):
        if claim.insuree:
            insuree = claim.insuree
        if claim.health_facility:
            hf = claim.health_facility
    if not isinstance(insuree, object):
        prop = insuree if isinstance(insuree, dict) else {}
        insuree = create_test_insuree(
            with_family=True, 
            is_head=True, 
            custom_props=prop)
    if not isinstance(hf, object):
        code = hf['code'] if 'code' in hf else 'HFH'
        prop = hf if isinstance(hf, dict) else {}
        if hf['location_id'] in 'location_id' in hf:
            location_id = hf['location_id']
        elif hf['location'] in 'location' in hf and isinstance(hf['location'], object):
            location_id = hf['location'].id
        else:
            location_id = insuree.location.id
        hf = create_test_health_facility(code, location_id, custom_props={})
    elif isinstance(product, dict):
        code = product['code'] if 'code' in product else 'TPDT'
        product = create_test_product(code, custom_props=product)
    
    if insuree and product:
        policy, insuree_policy = create_test_policy2(product, insuree)
    else:
        raise Exception("insuree or product not created")
    
    if isinstance(claim, object):
        if not items:
            items = list(claim.items.all())
        if not services:
            services = list(claim.services.all())        
        
    if all([isinstance(i, dict) for i in items]):
        items_source = items.copy()
        items = []
        for item in items_source:
            custom_items = {k: v for k, v in item.items() if hasattr(Item, k)} 
            it = create_test_item('V', custom_props=custom_items)
            item.append(it)
    for item in items:    
        create_test_product_item(product, it, custom_props=custom_pitems)
    if all([isinstance(s, dict) for s in services]):
        services_source = services.copy()
        services = []
        for service in services_source:
            it = create_test_service('V', custom_props=custom_items)
            item.append(it)
    for item in items:    
        create_test_product_service(product, it, custom_props=custom_pitems)
                                


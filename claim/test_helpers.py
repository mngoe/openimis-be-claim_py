from claim.validations import get_claim_category, approved_amount 
from program.test_helpers import create_test_program
from claim.models import Claim, ClaimService, ClaimItem, ClaimDedRem, ClaimAdmin 
from claim.services import claim_create, update_sum_claims
from medical.test_helpers import get_item_of_type, get_service_of_category, create_test_diagnosis
from uuid import uuid4
from location.models import HealthFacility
from insuree.test_helpers import create_test_insuree
from policy.test_helpers import create_test_policy2
from insuree.models import Insuree
from location.test_helpers import create_test_health_facility, create_test_location
from medical.test_helpers import create_test_item, create_test_service

class DummyUser:
    def __init__(self):
      self.id_for_audit = 1  

def create_test_claim(custom_props=None, user=DummyUser(), product=None):
    test_program = create_test_program(code="CCS", name="Chêque Santé")
    location = create_test_location('D')
    if custom_props is None:
        custom_props = {}
    else:
        custom_props = {k: v for k, v in custom_props.items() if hasattr(Claim, k)} 
    from datetime import datetime, timedelta
    insuree = None
    if 'insuree' in custom_props:
        insuree = custom_props['insuree']
    elif 'insuree_id' in custom_props:
        insuree = Insuree.objects.filter(id=custom_props['insuree_id']).first()
    else:
        insuree = create_test_insuree()
        custom_props["insuree"] = insuree
        
    test_hf = None
    if 'health_facility_id' in custom_props:
        test_hf = HealthFacility.objects.filter(id=custom_props['health_facility_id']).first()
    else:
        test_hf = create_test_health_facility('HF1', location.id)
        custom_props["health_facility"] = test_hf
        
    _to = datetime.now() - timedelta(days=1)
    if product:
        create_test_policy2(product, insuree)
    
    if 'icd' not in custom_props and 'icd_id' not in custom_props:
        custom_props['icd'] = create_test_diagnosis()
    elif 'icd' in custom_props and isinstance(custom_props['icd'], dict):
        custom_props['icd'] = create_test_diagnosis(
            custom_props=custom_props['icd']
        )
    claim = claim_create(
        {
            "date_from": datetime.now() - timedelta(days=2),
            "date_claimed": _to,
            "date_to": None,
            "status": 2,
            "validity_from": _to,
            "code": str(uuid4()),
            "program": test_program,
            **custom_props
        }, user
    )

    return claim

def create_test_claimitem(claim, item_type='D', valid=True, custom_props=None, product=None):
    if custom_props is None:
        custom_props = {}
    item = None
    if 'item' not in custom_props and 'item_id' not in custom_props:
        if item_type:
            item = get_item_of_type(item_type)
        if not item:
            item = create_test_item(item_type, custom_props=custom_props)
        custom_props['item'] = item
    
    custom_props = {k: v for k, v in custom_props.items() if hasattr(ClaimItem, k)} 
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
            **custom_props
           }
    )
    update_sum_claims(claim)
    return item



def create_test_claimservice(claim, category='V', valid=True, custom_props=None, product=None):
    if custom_props is None:
        custom_props = {}
    service = None
    if 'service' not in custom_props and 'service_id' not in custom_props:
        if category:
            service = get_service_of_category(category)
        if not service:
            service = create_test_service(category, custom_props=custom_props)
        custom_props['service'] = service
    
    custom_props = {k: v for k, v in custom_props.items() if hasattr(ClaimService, k)}
    service = ClaimService.objects.create(
        **{
            "claim": claim,
            "qty_provided": 7,
            "price_asked": 11,
            "status": 1,
            "validity_from": "2019-06-01",
            "validity_to": None if valid else "2019-06-01",
            "audit_user_id": -1,
            **custom_props
        }
    )    
    update_sum_claims(claim)
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
    location = create_test_location('D') 
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

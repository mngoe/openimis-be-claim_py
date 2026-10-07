from claim.models import Claim, ClaimService, ClaimItem, ClaimDedRem, ClaimAdmin
from claim.validations import get_claim_category, approved_amount
from claim.services import claim_create, update_sum_claims
from medical.test_helpers import get_item_of_type, get_service_of_category
from uuid import uuid4

class DummyUser:
    def __init__(self):
      self.id_for_audit = 1  

def get_test_program():
    """
    Claims are mandatorily attached to a program: reuse the program created along
    with the test products, if any, rather than creating one per claim.
    """
    from program.models import Program
    from program.test_helpers import create_test_program
    program = Program.objects.filter(validityDateTo__isnull=True).first()
    return program if program else create_test_program(code="CCS", name="Chêque Santé")


def get_test_health_facility():
    """A health facility in a test district, created along with its locations."""
    from location.test_helpers import create_test_health_facility, create_test_village
    village = create_test_village({"name": "Claim test"})
    return create_test_health_facility("CLHF", village.parent.parent.id)


def create_test_claim(custom_props={}, user = DummyUser() ):
    from core import datetime
    # never mutate the default dict: it is shared between the calls and would keep
    # objects of an already rolled back test transaction
    custom_props = dict(custom_props) if custom_props else {}
    # build what the claim refers to rather than relying on the ids of a seeded database
    if 'insuree' not in custom_props and 'insuree_id' not in custom_props:
        from insuree.test_helpers import create_test_insuree
        custom_props["insuree_id"] = create_test_insuree().id
    if 'program' not in custom_props and 'program_id' not in custom_props:
        custom_props["program"] = get_test_program()
    if 'health_facility' not in custom_props and 'health_facility_id' not in custom_props:
        custom_props["health_facility_id"] = get_test_health_facility().id
    if 'icd' not in custom_props and 'icd_id' not in custom_props:
        from medical.test_helpers import create_test_diagnosis
        custom_props["icd_id"] = create_test_diagnosis().id

    return claim_create(
        {
            "date_from": datetime.datetime(2019, 6, 1),
            "date_claimed": datetime.datetime(2019, 6, 1),
            "date_to": datetime.datetime(2019, 6, 1),
            "status": 2,
            "validity_from": datetime.datetime(2019, 6, 1),
            "code":  str(uuid4()),
            **custom_props
        }, user
    )


def create_test_claimitem(claim, item_type, valid=True, custom_props={}):
    item =  ClaimItem.objects.create(
        **{
            "claim": claim,
            "qty_provided": 7,
            "price_asked": 11,
            "item_id": get_item_of_type(item_type).id if item_type else 23,  # Atropine
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



def create_test_claimservice(claim, category=None, valid=True, custom_props={}):
    service =  ClaimService.objects.create(
        **{
            "claim": claim,
            "qty_provided": 7,
            "price_asked": 11,
            "service_id": get_service_of_category(category).id if category else 23,  # Skin graft, no cat
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

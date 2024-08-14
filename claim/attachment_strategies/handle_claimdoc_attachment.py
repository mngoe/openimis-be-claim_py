import os
import requests
from claim.models import Claim


def handler(data):
    url_template = 'https://claimdoc.hib.gov.np/upload_documents?'
    token = os.environ.get("CLAIMDOC_TOKEN", default='testToken')
    hf_id = None
    if "claim_id" in data:
        hf_id = Claim.objects.get(validity_to__isnull=True, id=data["claim_id"]).health_facility.id
    else:
        claim = Claim.objects.get(validity_to__isnull=True, uuid=data["claim_uuid"])
        data["claim_id"] = claim.id
        hf_id = claim.health_facility.id
    url_params = {'claim_code': data["claim_id"], 'token': token, 'hf_id': hf_id}
    return requests.get(url_template, url_params).url

import base64
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes

from report.services import ReportService
from tools.views import checkUserWithRights
from .services import ClaimReportService
from .reports import claim
from .apps import ClaimConfig
from .models import ClaimAttachment
from django.utils.translation import gettext as _
import core


@api_view(['GET'])
def print(request):
    if not request.user.has_perms(ClaimConfig.claim_print_perms):
        raise PermissionDenied(_("unauthorized"))
    report_service = ReportService(request.user)
    report_data_service = ClaimReportService(request.user)
    data = report_data_service.fetch(request.GET['uuid'])
    return report_service.process('claim_claim', data, claim.template)


@api_view(["GET", "POST"])
@permission_classes(
    [
        checkUserWithRights(
            ClaimConfig.gql_query_claims_perms,
        )
    ]
)
def attach(request):
    queryset = ClaimAttachment.objects.filter(*core.filter_validity())
    if settings.ROW_SECURITY:
        from location.models import UserDistrict
        dist = UserDistrict.get_user_districts(request.user._u)
        queryset = queryset.select_related("claim")\
            .filter(
            claim__health_facility__location__id__in=[
                loc.location_id for loc in dist]
        )
    attachment = queryset\
        .filter(id=request.GET['id'])\
        .first()
    if not attachment:
        raise PermissionDenied(_("unauthorized"))

    if ClaimConfig.claim_attachments_root_path and attachment.url is None:
        response = HttpResponse(status=404)
        return response

    if not ClaimConfig.claim_attachments_root_path and attachment.document is None:
        response = HttpResponse(status=404)
        return response

    response = HttpResponse(content_type=("application/x-binary" if attachment.mime is None else attachment.mime))
    response['Content-Disposition'] = 'attachment; filename=%s' % attachment.filename
    if ClaimConfig.claim_attachments_root_path:
        f = open('%s/%s' % (ClaimConfig.claim_attachments_root_path, attachment.url), "rb")
        response.write(f.read())
        f.close()
    else:
        response.write(base64.b64decode(attachment.document))
    return response

import random
import string

from claim.models import ClaimAdmin
from claim.test_helpers import create_test_claim, create_test_claimservice, create_test_claimitem
from django.core.management.base import BaseCommand
from insuree.models import Insuree
from medical.models import Service, Item


class Command(BaseCommand):
    help = "This command will generate test Claims with some optional parameters. It is intended to simulate larger" \
           "databases for performance testing"
    insurees = None
    services = None
    items = None
    claim_admins = None
    hfs = None

    def add_arguments(self, parser):
        parser.add_argument("nb_claims", nargs=1, type=int)
        parser.add_argument("nb_services", nargs=1, type=int, help="number of services per claim, with 10% randomness")
        parser.add_argument("nb_items", nargs=1, type=int, help="number of items per claim, with 10% randomness")
        parser.add_argument(
            '--verbose',
            action='store_true',
            dest='verbose',
            help='Be verbose about what it is doing',
        )

    def handle(self, *args, **options):
        nb_claims = options["nb_claims"][0]
        nb_services = options["nb_services"][0]
        nb_items = options["nb_items"][0]
        verbose = options["verbose"]
        for claim_num in range(1, nb_claims + 1):
            insuree = self.get_random_insuree()
            claim_admin = self.get_random_claim_admin()
            hf = self.get_random_hf()
            claim = create_test_claim({"insuree_id": insuree,
                                       "code": ''.join(random.choices(string.ascii_uppercase + string.digits, k=8)),
                                       "admin_id": claim_admin,
                                       "health_facility_id": hf})
            if verbose:
                logger.debug(f"{claim_num} created claim {claim} for insuree {insuree} with code {claim.code}")
            for svc_num in range(1, nb_services + 1):
                service = self.get_random_service()
                claim_service = create_test_claimservice(claim, custom_props={
                    "service_id": service,
                    "qty_provided": random.randint(1, 10),
                    "price_asked": random.randint(1, 1000),
                })
                if verbose:
                    logger.debug(f"{claim_num} {svc_num} Created claim service {claim_service} for service {service}")
            for item_num in range(1, nb_items + 1):
                item = self.get_random_item()
                claim_item = create_test_claimitem(claim, "D", custom_props=
                {
                    "item_id": item,
                    "qty_provided": random.randint(1, 10),
                    "price_asked": random.randint(1, 1000),
                })
                if verbose:
                    logger.debug(f"{claim_num} {item_num} Created claim item {claim_item} for item {item}")

    def get_random_insuree(self):
        if not self.insurees:
            self.insurees = Insuree.objects.filter(validity_to__isnull=True).values_list("pk", flat=True)
        return random.choice(self.insurees)

    def get_random_service(self):
        if not self.services:
            self.services = Service.objects.filter(validity_to__isnull=True).values_list("pk", flat=True)
        return random.choice(self.services)

    def get_random_item(self):
        if not self.items:
            self.items = Item.objects.filter(validity_to__isnull=True).values_list("pk", flat=True)
        return random.choice(self.items)

    def get_random_claim_admin(self):
        if not self.claim_admins:
            self.claim_admins = ClaimAdmin.objects.filter(validity_to__isnull=True).values_list("pk", flat=True)
        return random.choice(self.claim_admins)

    def get_random_hf(self):
        if not self.hfs:
            from location.models import HealthFacility
            self.hfs = HealthFacility.objects.filter(validity_to__isnull=True).values_list("pk", flat=True)
        return random.choice(self.hfs)

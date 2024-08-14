import random
import string

from claim.test_helpers import create_test_claim_admin
from django.core.management.base import BaseCommand
from faker import Faker


class Command(BaseCommand):
    help = "This command will generate test Claims Administrators. It is intended to simulate larger" \
           "databases for performance testing. Some locations have almost 1000 of them."
    insurees = None
    services = None
    items = None
    claim_admins = None
    hfs = None

    def add_arguments(self, parser):
        parser.add_argument("nb_admins", nargs=1, type=int)
        parser.add_argument(
            '--verbose',
            action='store_true',
            dest='verbose',
            help='Be verbose about what it is doing',
        )
        parser.add_argument(
            '--locale',
            default="fr_FR",
            help="Used to adapt the fake names generation to the locale, using Faker, by default fr_FR",
        )

    def handle(self, *args, **options):

        nb_admins = options["nb_admins"][0]
        verbose = options["verbose"]
        fake = Faker(options["locale"])
        for admin_num in range(1, nb_admins + 1):
            hf = self.get_random_hf()
            claim_admin = create_test_claim_admin({
                "code": ''.join(random.choices(string.ascii_uppercase + string.digits, k=8)),
                "last_name": fake.last_name(),
                "other_names": fake.first_name(),
                "email_id": fake.ascii_email(),
                "phone": "+" + fake.msisdn(),
                "health_facility_id": hf})
            if verbose:
                logger.debug(f"{admin_num} created claim admin {claim_admin} for HF {hf} \
                    with code {claim_admin.code}")

    def get_random_hf(self):
        if not self.hfs:
            from location.models import HealthFacility
            self.hfs = HealthFacility.objects.filter(validity_to__isnull=True).values_list("pk", flat=True)
        return random.choice(self.hfs)

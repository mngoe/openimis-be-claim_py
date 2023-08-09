from claim.reports import claim_percentage_referrals
from claim.reports.claim_percentage_referrals import claim_percentage_referrals_query

report_definitions = [
    {
        "name": "claim_percentage_referrals",
        "engine": 0,
        "default_report": claim_percentage_referrals.template,
        "description": "Percentage of referrals in claims",
        "module": "claim",
        "python_query": claim_percentage_referrals_query,
        "permission": ["131214"],
    },
]

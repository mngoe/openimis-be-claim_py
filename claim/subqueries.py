from claim.models import Claim, ClaimDetail
from django.db.models import (
    OuterRef,
    Subquery,
    Q,
    Sum,
    FloatField,
    ExpressionWrapper,
    DecimalField,
    Case,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from claim.models import ClaimItem, ClaimService

# row_id = models.BinaryField(db_column='RowID', blank=True, null=True)

# subqueries
# Subquery for total_itm_adjusted


def elm_qty_exp(prefix=""):
    return Coalesce(f"{prefix}qty_approved", f"{prefix}qty_provided", Value(0.0))


def elm_price_exp(prefix=""):
    return Coalesce(
        f"{prefix}price_approved",
        f"{prefix}price_adjusted",
        f"{prefix}price_asked",
        Value(0.0),
    )


def elm_approved_exp(prefix=""):
    return ExpressionWrapper(
        elm_price_exp(prefix) * elm_qty_exp(prefix), output_field=DecimalField()
    )


def elm_adjusted_exp(prefix=""):
    return ExpressionWrapper(
        Coalesce(f"{prefix}qty_provided", Value(0.0))
        * Coalesce(f"{prefix}price_adjusted", f"{prefix}price_asked", Value(0.0)),
        output_field=DecimalField(),
    )


def elm_valuate_exp(prefix=""):
    return ExpressionWrapper(
        Coalesce(f"{prefix}price_valuated", Value(0.0)), output_field=DecimalField()
    )


def total_elm_adjusted_exp(prefix=""):
    return ExpressionWrapper(
        Coalesce(Sum(elm_adjusted_exp(prefix=prefix)), 0), output_field=DecimalField()
    )


def total_elm_approved_exp(prefix=""):
    return ExpressionWrapper(
        Coalesce(Sum(elm_approved_exp(prefix=prefix)), 0), output_field=DecimalField()
    )


# Subquery for total_srv_adjusted
total_srv_adjusted_exp = total_elm_adjusted_exp(prefix="services__")


total_itm_adjusted_exp = total_elm_adjusted_exp(prefix="items__")


# Subquery for total_itm_approved

total_itm_approved_exp = Coalesce(
    Sum(
        Case(
            When(
                Q(
                    Q(status=Claim.STATUS_REJECTED)
                    | Q(items__status=ClaimDetail.STATUS_REJECTED)
                ),
                then=Value(0.0),
            ),
            default=elm_approved_exp(prefix="items__"),
            output_field=DecimalField(),
        )
    ),
    Value(0.0),
    output_field=DecimalField(),
)


# Subquery for total_srv_approved

total_srv_approved_exp = Coalesce(
    Sum(
        Case(
            When(
                Q(
                    Q(status=Claim.STATUS_REJECTED)
                    | Q(services__status=ClaimDetail.STATUS_REJECTED)
                ),
                then=Value(0.0),
            ),
            default=elm_approved_exp(prefix="services__"),
            output_field=DecimalField(),
        )
    ),
    Value(0.0),
    output_field=DecimalField(),
)


def update_claim_remunerated(claims_qs, ratio=1, updates={}):
    ClaimItem.objects.filter(claim__in=claims_qs, *ClaimItem.filter_validity()).filter(
        Q(Q(rejection_reason__isnull=True) | Q(rejection_reason=0))
    ).update(
        remunerated_amount=ExpressionWrapper(
            ratio * elm_approved_exp(), output_field=DecimalField()
        )
    )
    ClaimService.objects.filter(claim__in=claims_qs, *ClaimItem.filter_validity()).filter(
        Q(Q(rejection_reason__isnull=True) | Q(rejection_reason=0))
    ).update(
        remunerated_amount=ExpressionWrapper(
            ratio * elm_approved_exp(), output_field=DecimalField()
        )
    )


def update_claim_total(
    claims_qs,
    ratio=1,
    claim_based_value_subquery=0,
    updates={},
    field="approved",
    elm_sum=None,
):

    if not elm_sum:
        elm_sum = ExpressionWrapper(
            ratio * elm_approved_exp(), output_field=DecimalField()
        )

    service_subquery = Subquery(
        ClaimItem.objects.filter(
            claim=OuterRef("pk"),
            *ClaimItem.filter_validity(),
        )
        .filter(Q(Q(rejection_reason__isnull=True) | Q(rejection_reason=0)))
        .values("claim_id")
        .annotate(elm_sum=elm_sum)
        .values("elm_sum")
        .order_by()[:1],
        output_field=FloatField(),
    )
    item_subquery = Subquery(
        ClaimService.objects.filter(
            claim=OuterRef("pk"),
            *ClaimService.filter_validity(),
        )
        .filter(Q(Q(rejection_reason__isnull=True) | Q(rejection_reason=0)))
        .values("claim_id")
        .annotate(elm_sum=elm_sum)
        .values("elm_sum")
        .order_by()[:1],
        output_field=FloatField(),
    )
    updates[field] = (
        Coalesce(service_subquery, 0)
        + Coalesce(item_subquery, 0)
        + Coalesce(claim_based_value_subquery, 0)
    )
    claims_qs.update(**updates)


def update_claim_approved(claims_qs, ratio=1, updates={}):

    return update_claim_total(claims_qs, ratio=ratio, updates=updates, field="approved")


def update_claim_valuated(claims_qs, ratio=1, claim_based_value_subquery=0, updates={}):
    elm_sum = ExpressionWrapper(elm_valuate_exp(), output_field=DecimalField())
    updates["status"] = Claim.STATUS_VALUATED
    return update_claim_total(
        claims_qs,
        ratio=1,
        claim_based_value_subquery=claim_based_value_subquery,
        updates=updates,
        field="valuated",
        elm_sum=elm_sum,
    )


def update_claim_indexed_remunerated(
    claims_qs, ratio=1, claim_based_value_subquery=0, updates={}
):
    updates["status"] = Claim.STATUS_VALUATED
    return update_claim_total(
        claims_qs,
        ratio=1,
        claim_based_value_subquery=claim_based_value_subquery,
        updates=updates,
        field="remunerated",
    )

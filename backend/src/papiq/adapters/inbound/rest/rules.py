"""`/rules`: rules that tag, file and check documents; their versions."""

from uuid import UUID

from fastapi import APIRouter, Query

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import (
    RuleCreate,
    RuleDefinitionIn,
    RuleOut,
    RulePatch,
    RuleVersionOut,
)
from papiq.core.domain.ids import RuleId
from papiq.core.domain.rules import RuleScope, definition_from_json

router = APIRouter(prefix="/rules", tags=["rules"], dependencies=PROTECTED)

WHO_CHANGES = "A user rule: its owner. A global rule: admins."
WHO_READS = (
    "Global rules: everyone; user rules: their owner and admins. Other users' rules are not "
    "found (404)."
)
CHECKS = (
    " The content is checked: operators fit the field and the attribute's data type, patterns "
    "compile, a global rule only tags, sets attributes and forces reviews (422); contacts, "
    "types, tags, attributes and drawers exist (404); a user rule files only into drawers its "
    "owner may write to (403)."
)


@router.get(
    "",
    summary="List rules",
    description=(
        "The caller's rules and the global rules, in the order they are applied (priority, "
        "then global before user rules, then older first). Deleted rules are not listed."
    ),
    response_model=list[RuleOut],
    responses=problem_responses(401, 403, 422),
)
async def list_rules(
    user: CurrentUser,
    context: Context,
    scope: RuleScope | None = None,
    include_disabled: bool = True,
    all_users: bool = Query(
        default=False, description="The rules of every user instead of the caller's (admins)."
    ),
) -> list[RuleOut]:
    rules = await context.rules.list(
        user, scope=scope, include_disabled=include_disabled, all_users=all_users
    )
    return [RuleOut.of(rule) for rule in rules]


@router.post(
    "",
    status_code=201,
    summary="Create a rule",
    description="Global rules: admins only (403)." + CHECKS,
    response_model=RuleOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def create_rule(body: RuleCreate, user: CurrentUser, context: Context) -> RuleOut:
    definition = definition_from_json(body.to_json())
    return RuleOut.of(await context.rules.create(user, body.scope, definition))


@router.get(
    "/{id}",
    summary="A rule",
    description=WHO_READS,
    response_model=RuleOut,
    responses=problem_responses(401, 404, 422),
)
async def get_rule(id: UUID, user: CurrentUser, context: Context) -> RuleOut:
    return RuleOut.of(await context.rules.get(user, RuleId(id)))


@router.put(
    "/{id}",
    summary="Change a rule",
    description="Makes a new version; earlier ones stay readable. " + WHO_CHANGES + CHECKS,
    response_model=RuleOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def change_rule(
    id: UUID, body: RuleDefinitionIn, user: CurrentUser, context: Context
) -> RuleOut:
    definition = definition_from_json(body.to_json())
    return RuleOut.of(await context.rules.change(user, RuleId(id), definition))


@router.patch(
    "/{id}",
    summary="Enable or disable a rule",
    description=(
        WHO_CHANGES + " Makes no version. Enabling checks the references again: a rule "
        "disabled because something it uses was deleted stays disabled until it is changed."
    ),
    response_model=RuleOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def patch_rule(id: UUID, body: RulePatch, user: CurrentUser, context: Context) -> RuleOut:
    return RuleOut.of(await context.rules.set_enabled(user, RuleId(id), body.enabled))


@router.delete(
    "/{id}",
    status_code=204,
    summary="Delete a rule",
    description=(
        WHO_CHANGES + " The rule never runs again and is no longer listed; its versions stay "
        "readable, so the processing log can be read."
    ),
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def delete_rule(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.rules.delete(user, RuleId(id))


@router.get(
    "/{id}/versions",
    summary="Versions of a rule",
    description="Oldest first; also of a deleted rule. " + WHO_READS,
    response_model=list[RuleVersionOut],
    responses=problem_responses(401, 404, 422),
)
async def list_rule_versions(id: UUID, user: CurrentUser, context: Context) -> list[RuleVersionOut]:
    return [RuleVersionOut.of(item) for item in await context.rules.versions(user, RuleId(id))]


@router.get(
    "/{id}/versions/{number}",
    summary="A version of a rule",
    description=WHO_READS,
    response_model=RuleVersionOut,
    responses=problem_responses(401, 404, 422),
)
async def get_rule_version(
    id: UUID, number: int, user: CurrentUser, context: Context
) -> RuleVersionOut:
    return RuleVersionOut.of(await context.rules.version(user, RuleId(id), number))

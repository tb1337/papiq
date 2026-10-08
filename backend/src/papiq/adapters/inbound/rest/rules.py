"""`/rules`: rules that tag, file and check documents; their versions."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import (
    ApplyPreviewItemOut,
    ApplyPreviewOut,
    ApplyPreviewRequest,
    ApplyRequest,
    RuleApplicationOut,
    RuleCreate,
    RuleDefinitionIn,
    RuleEffectOut,
    RuleNoteOut,
    RuleOut,
    RulePatch,
    RuleVersionOut,
)
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import DocumentId, RuleApplicationId, RuleId
from papiq.core.domain.rule_engine import Note
from papiq.core.domain.rules import RuleScope, definition_from_json

router = APIRouter(prefix="/rules", tags=["rules"], dependencies=PROTECTED)

WHO_CHANGES = (
    "A user rule: its owner and admins (its references are checked for its owner). A global "
    "rule: admins."
)
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


WHO_APPLIES = (
    "A user rule: its owner and admins, on the documents of the rule's owner. A global rule: "
    "anyone, on the documents they may write to (admins: every document). Documents in "
    "processing are left out."
)


@router.post(
    "/{id}/apply/preview",
    summary="Preview applying a rule to existing documents",
    description=(
        "The documents the rule's current version would change, newest first, with what would "
        "change and the conflicts (a field that has another value). Nothing is stored. "
        + WHO_APPLIES
    ),
    response_model=ApplyPreviewOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def preview_apply(
    id: UUID, body: ApplyPreviewRequest, user: CurrentUser, context: Context
) -> ApplyPreviewOut:
    preview = await context.rule_applications.preview(
        user, RuleId(id), before=_cursor(body.cursor), limit=body.limit
    )
    return ApplyPreviewOut(
        rule_id=preview.rule.id,
        version=preview.rule.current.number,
        items=[
            ApplyPreviewItemOut(
                document_id=item.document.id,
                title=item.document.title,
                changes=[RuleEffectOut(field=e.field, old=e.old, new=e.new) for e in item.effects],
                conflicts=[_note(note) for note in item.conflicts],
                notes=[_note(note) for note in item.notes],
            )
            for item in preview.items
        ],
        next_cursor=None if preview.next_cursor is None else str(preview.next_cursor),
    )


@router.post(
    "/{id}/apply",
    status_code=202,
    summary="Apply a rule to existing documents",
    description=(
        "Applies the given version to the selected documents in the background; follow it at "
        "`GET /rule-applications/{id}`. Rights are checked again for every document. Conflicts "
        "are applied only for documents in `accept_conflicts`; forced reviews do not act. "
        + WHO_APPLIES
    ),
    response_model=RuleApplicationOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def apply_rule(
    id: UUID,
    body: ApplyRequest,
    request: Request,
    response: Response,
    user: CurrentUser,
    context: Context,
) -> RuleApplicationOut:
    application = await context.rule_applications.start(
        user,
        RuleId(id),
        version=body.version,
        documents=[DocumentId(item) for item in body.document_ids],
        accept_conflicts=[DocumentId(item) for item in body.accept_conflicts],
    )
    response.headers["Location"] = str(request.url_for("get_application", id=application.id).path)
    return RuleApplicationOut.of(application)


applications = APIRouter(prefix="/rule-applications", tags=["rules"], dependencies=PROTECTED)


@applications.get(
    "/{id}",
    summary="Progress of a rule application",
    description="For the user who started it and admins.",
    response_model=RuleApplicationOut,
    responses=problem_responses(401, 404, 422),
)
async def get_application(id: UUID, user: CurrentUser, context: Context) -> RuleApplicationOut:
    return RuleApplicationOut.of(await context.rule_applications.get(user, RuleApplicationId(id)))


def _note(note: Note) -> RuleNoteOut:
    return RuleNoteOut.model_validate(
        {"field": note.field, "kind": note.kind, "reason": note.reason}
    )


def _cursor(value: str | None) -> DocumentId | None:
    if value is None or not value.strip():
        return None
    try:
        return DocumentId(UUID(value.strip()))
    except ValueError:
        raise ValidationError(f"cursor: not a valid cursor: {value!r}") from None

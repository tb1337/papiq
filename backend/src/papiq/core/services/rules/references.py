"""Rules whose references go away.

Deleting a contact, type, tag, attribute or drawer is not blocked by rules that refer to it: the
person deleting it would have to change rules they may not see or should not have to look after
(a drawer owner does not see the rules of users who may file into the drawer). Instead the
affected rules are disabled, with the reason, in the same unit of work. Enabling such a rule
checks its references again.
"""

from collections.abc import Callable, Mapping
from datetime import datetime

from papiq.core.domain.attributes import AttributeDefinition
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import AttributeId
from papiq.core.domain.rules import References, Rule, check_attributes
from papiq.core.ports import UnitOfWork


async def disable_rules(
    uow: UnitOfWork, now: datetime, reason: str, affected: Callable[[Rule], bool]
) -> int:
    """Disable every enabled rule (global or of any user) for which `affected` holds; returns
    how many."""
    count = 0
    for rule in await uow.rules.list_for(owners=None, include_global=True):
        if rule.enabled and affected(rule):
            rule.disable(now, reason)
            await uow.rules.update(rule)
            count += 1
    return count


def refers_to(
    select: Callable[[References], frozenset[object]], id: object
) -> Callable[[Rule], bool]:
    """`affected` for `disable_rules`: the rule refers to `id` among the selected references."""

    def affected(rule: Rule) -> bool:
        return id in select(rule.definition.references())

    return affected


def misfits(
    changed: AttributeId, definitions: Mapping[AttributeId, AttributeDefinition]
) -> Callable[[Rule], bool]:
    """`affected` for `disable_rules`: the rule refers to the `changed` attribute and its
    attribute conditions or values no longer fit `definitions` (all attribute definitions), e.g.
    because a choice it uses was removed."""

    def affected(rule: Rule) -> bool:
        if changed not in rule.definition.references().attributes:
            return False
        try:
            check_attributes(rule.definition, definitions)
        except ValidationError:
            return True
        return False

    return affected

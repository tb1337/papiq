"""Contract suite for the rule repositories in the unit of work: `RuleRepository` (rules with
their versions) and `RuleApplicationRepository`."""

from datetime import timedelta

import pytest

from papiq.core.domain.errors import ConcurrencyError, ConflictError, NotFoundError
from papiq.core.domain.ids import (
    ContactId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    FieldId,
    RuleApplicationId,
    RuleId,
    TagId,
    new_id,
)
from papiq.core.domain.rules import (
    AddTags,
    ApplicationStatus,
    Condition,
    ConditionField,
    ForceReview,
    Group,
    Operator,
    RemoveTags,
    Rule,
    RuleApplication,
    RuleDefinition,
    RuleScope,
    SetContact,
    SetDocumentType,
    SetDrawer,
    SetField,
    SetTitle,
    Trigger,
    definition_to_json,
)
from papiq.core.domain.users import User
from papiq.core.ports import UnitOfWorkFactory
from tests import builders
from tests.builders import NOW
from tests.contracts.unit_of_work import owner_with_drawer, seed

LATER = NOW + timedelta(hours=1)


def simple(name: str = "Tag API uploads", *, priority: int = 100) -> RuleDefinition:
    """A small definition: tag what arrives over the API."""
    return RuleDefinition(
        name=name,
        priority=priority,
        conditions=Group(
            mode="all",
            items=(Condition(field=ConditionField.CHANNEL, op=Operator.IS, value="api"),),
        ),
        actions=(AddTags(frozenset({TagId(new_id())})),),
    )


def full() -> RuleDefinition:
    """Every action type, nested and negated groups, field conditions, values of every
    JSON kind and text beyond ASCII."""
    amount, flag, note = FieldId(new_id()), FieldId(new_id()), FieldId(new_id())
    return RuleDefinition(
        name="Stromrechnung Größe",
        priority=7,
        triggers=frozenset({Trigger.INGEST}),
        conditions=Group(
            mode="all",
            items=(
                Condition(
                    field=ConditionField.CONTACT,
                    op=Operator.IN,
                    value=[str(new_id()), str(new_id())],
                ),
                Condition(
                    field=ConditionField.TEXT,
                    op=Operator.MATCHES,
                    value=r"Strom\s*rechnung",
                    case_sensitive=True,
                ),
                Group(
                    mode="any",
                    negate=True,
                    items=(
                        Condition(
                            field=ConditionField.TAGS, op=Operator.CONTAINS, value=str(new_id())
                        ),
                        Condition(field=ConditionField.CHANNEL, op=Operator.IS, value="migration"),
                        Group(
                            mode="all",
                            items=(
                                Condition(
                                    field=ConditionField.DOCUMENT_DATE,
                                    op=Operator.LT,
                                    value="2020-01-01",
                                ),
                                Condition(field=ConditionField.DOCUMENT_TYPE, op=Operator.MISSING),
                            ),
                        ),
                    ),
                ),
                Condition(
                    field=ConditionField.FIELD,
                    op=Operator.GT,
                    value={"amount": "100.50", "currency": "EUR"},
                    field_id=amount,
                ),
                Condition(field=ConditionField.FIELD, op=Operator.PRESENT, field_id=note),
                Condition(
                    field=ConditionField.FIELD,
                    op=Operator.MATCHES,
                    value="^Zähler",
                    field_id=note,
                ),
                Condition(field=ConditionField.FIELD, op=Operator.IS, value=False, field_id=flag),
            ),
        ),
        actions=(
            SetDrawer(DrawerId(new_id())),
            SetContact(ContactId(new_id())),
            SetDocumentType(DocumentTypeId(new_id())),
            SetTitle("{contact}: {document_date} ({filename})"),
            AddTags(frozenset({TagId(new_id()), TagId(new_id())})),
            RemoveTags(frozenset({TagId(new_id())})),
            SetField(amount, {"amount": "12.30", "currency": "EUR"}),
            SetField(flag, True),
            ForceReview("Zählerstand prüfen"),
        ),
    )


def user_rule(owner: User, definition: RuleDefinition | None = None) -> Rule:
    return Rule.create(
        scope=RuleScope.USER,
        owner_id=owner.id,
        definition=definition or simple(),
        by=owner.id,
        now=NOW,
    )


def global_rule(admin: User, definition: RuleDefinition | None = None) -> Rule:
    return Rule.create(
        scope=RuleScope.GLOBAL,
        owner_id=None,
        definition=definition or simple("Tag everything"),
        by=admin.id,
        now=NOW,
    )


def application(rule: Rule, by: User, documents: int = 3) -> RuleApplication:
    ids = tuple(DocumentId(new_id()) for _ in range(documents))
    return RuleApplication.create(
        rule_id=rule.id,
        rule_version=rule.current.number,
        user_id=by.id,
        documents=ids,
        accept_conflicts=frozenset(ids[:1]),
        now=NOW,
    )


async def store(
    uow_factory: UnitOfWorkFactory, *rules: Rule, applications: tuple[RuleApplication, ...] = ()
) -> None:
    async with uow_factory() as uow:
        for rule in rules:
            await uow.rules.add(rule)
        for item in applications:
            await uow.rule_applications.add(item)
        await uow.commit()


async def users(uow_factory: UnitOfWorkFactory, count: int) -> list[User]:
    """Users without drawers (so they can be removed again)."""
    created = [builders.user() for _ in range(count)]
    await seed(uow_factory, *created)
    return created


async def change(
    uow_factory: UnitOfWorkFactory, id: RuleId, definition: RuleDefinition, by: User
) -> Rule:
    """Store a new version of the rule."""
    async with uow_factory() as uow:
        rule = await uow.rules.get(id)
        rule.change(definition, by=by.id, now=LATER)
        await uow.rules.update(rule)
        await uow.commit()
    return rule


class RuleRepositoriesContract:
    # --- rules: round trip ----------------------------------------------------------------------

    async def test_a_rule_round_trips_with_its_definition(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner, full())
        await store(uow_factory, rule)
        async with uow_factory() as uow:
            stored = await uow.rules.get(rule.id)
            assert await uow.rules.find(rule.id) == stored
            versions = await uow.rules.versions(rule.id)
            first = await uow.rules.get_version(rule.id, 1)
        assert stored == rule
        assert definition_to_json(stored.definition) == definition_to_json(rule.definition)
        assert stored.definition.conditions.items[2] == rule.definition.conditions.items[2]
        assert (stored.scope, stored.owner_id, stored.version) == (RuleScope.USER, owner.id, 1)
        assert stored.current.created_by == owner.id
        assert stored.created_at == stored.updated_at == NOW
        assert versions == [rule.current] and first == rule.current

    async def test_a_global_rule_has_no_owner(self, uow_factory: UnitOfWorkFactory) -> None:
        (admin,) = await users(uow_factory, 1)
        rule = global_rule(admin)
        await store(uow_factory, rule)
        async with uow_factory() as uow:
            stored = await uow.rules.get(rule.id)
        assert stored == rule
        assert (stored.scope, stored.owner_id) == (RuleScope.GLOBAL, None)
        assert stored.current.created_by == admin.id

    async def test_missing_rules_and_versions(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        await store(uow_factory, rule)
        missing = RuleId(new_id())
        async with uow_factory() as uow:
            assert await uow.rules.find(missing) is None
            with pytest.raises(NotFoundError):
                await uow.rules.get(missing)
            with pytest.raises(NotFoundError):
                await uow.rules.versions(missing)
            with pytest.raises(NotFoundError):
                await uow.rules.get_version(missing, 1)
            with pytest.raises(NotFoundError):
                await uow.rules.get_version(rule.id, 2)

    async def test_add_twice_is_a_conflict(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        await store(uow_factory, rule)
        with pytest.raises(ConflictError):
            await store(uow_factory, rule)

    # --- rules: versions ------------------------------------------------------------------------

    async def test_update_stores_a_new_version_and_keeps_the_earlier_ones(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        (editor,) = await users(uow_factory, 1)
        rule = user_rule(owner, simple("first"))
        await store(uow_factory, rule)
        second, third = full(), simple("third", priority=900)
        async with uow_factory() as uow:
            loaded = await uow.rules.get(rule.id)
            loaded.change(second, by=editor.id, now=LATER)
            await uow.rules.update(loaded)
            assert loaded.version == 2
            await uow.commit()
        async with uow_factory() as uow:
            loaded = await uow.rules.get(rule.id)
            loaded.change(third, by=owner.id, now=LATER + timedelta(minutes=1))
            await uow.rules.update(loaded)
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.rules.get(rule.id)
            versions = await uow.rules.versions(rule.id)
            middle = await uow.rules.get_version(rule.id, 2)
        assert (stored.current.number, stored.definition, stored.version) == (3, third, 3)
        assert stored.updated_at == LATER + timedelta(minutes=1)
        assert stored.created_at == NOW
        assert [version.number for version in versions] == [1, 2, 3]
        assert [version.definition.name for version in versions] == ["first", second.name, "third"]
        assert (versions[1].definition, versions[2].definition) == (second, third)
        assert versions[0] == rule.current
        assert definition_to_json(middle.definition) == definition_to_json(second)
        assert (middle.created_by, middle.created_at) == (editor.id, LATER)
        assert versions[2] == stored.current

    async def test_several_versions_in_one_unit_of_work(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner, simple("v1"))
        async with uow_factory() as uow:
            await uow.rules.add(rule)
            for name in ("v2", "v3"):
                rule.change(simple(name), by=owner.id, now=LATER)
                await uow.rules.update(rule)
            await uow.commit()
        async with uow_factory() as uow:
            versions = await uow.rules.versions(rule.id)
            stored = await uow.rules.get(rule.id)
        assert [version.definition.name for version in versions] == ["v1", "v2", "v3"]
        assert (stored.current.number, stored.version) == (3, 3)

    async def test_an_update_without_a_new_version_keeps_the_versions(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        """Enabling and disabling are state, not content: no version."""
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        await store(uow_factory, rule)
        async with uow_factory() as uow:
            loaded = await uow.rules.get(rule.id)
            loaded.disable(LATER, reason="contact 'ACME' was deleted")
            await uow.rules.update(loaded)
            await uow.commit()
        async with uow_factory() as uow:
            disabled = await uow.rules.get(rule.id)
            assert await uow.rules.versions(rule.id) == [rule.current]
        assert (disabled.enabled, disabled.disabled_reason) == (False, "contact 'ACME' was deleted")
        assert (disabled.current, disabled.version, disabled.updated_at) == (
            rule.current,
            2,
            LATER,
        )
        assert not disabled.is_active
        async with uow_factory() as uow:
            disabled.enable(LATER)
            await uow.rules.update(disabled)
            await uow.commit()
        async with uow_factory() as uow:
            enabled = await uow.rules.get(rule.id)
            assert await uow.rules.versions(rule.id) == [rule.current]
        assert (enabled.enabled, enabled.disabled_reason, enabled.version) == (True, None, 3)

    async def test_stale_update_is_rejected(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner, simple("v1"))
        await store(uow_factory, rule)
        async with uow_factory() as first, uow_factory() as second:
            mine = await first.rules.get(rule.id)
            theirs = await second.rules.get(rule.id)
            mine.change(simple("mine"), by=owner.id, now=LATER)
            await first.rules.update(mine)
            await first.commit()
            theirs.change(simple("theirs"), by=owner.id, now=LATER)
            with pytest.raises(ConcurrencyError):
                await second.rules.update(theirs)
                await second.commit()
        async with uow_factory() as uow:
            stored = await uow.rules.get(rule.id)
            versions = await uow.rules.versions(rule.id)
        assert (stored.definition.name, stored.version) == ("mine", 2)
        assert [version.definition.name for version in versions] == ["v1", "mine"]

    async def test_update_of_missing_rule(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        async with uow_factory() as uow:
            with pytest.raises(NotFoundError):
                await uow.rules.update(user_rule(owner))

    # --- rules: deleting and listing ------------------------------------------------------------

    async def test_a_deleted_rule_stays_readable_but_is_not_listed(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        kept, deleted = user_rule(owner, simple("kept")), user_rule(owner, simple("deleted"))
        await store(uow_factory, kept, deleted)
        await change(uow_factory, deleted.id, simple("deleted v2"), owner)
        async with uow_factory() as uow:
            loaded = await uow.rules.get(deleted.id)
            loaded.delete(LATER)
            await uow.rules.update(loaded)
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.rules.get(deleted.id)
            assert await uow.rules.find(deleted.id) == stored
            versions = await uow.rules.versions(deleted.id)
            first = await uow.rules.get_version(deleted.id, 1)
            listed = await uow.rules.list_for(owners=None, include_global=True)
            own = await uow.rules.list_for(owners=[owner.id], include_global=False)
        assert (stored.deleted_at, stored.enabled, stored.is_active) == (LATER, False, False)
        assert [version.definition.name for version in versions] == ["deleted", "deleted v2"]
        assert first == deleted.current
        assert [rule.id for rule in listed] == [kept.id]
        assert [rule.id for rule in own] == [kept.id]

    async def test_rules_are_listed_by_owner_and_scope(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        admin, alice, bob, carol = await users(uow_factory, 4)
        everyone = global_rule(admin)
        of_alice, of_bob, of_carol = user_rule(alice), user_rule(bob), user_rule(carol)
        disabled = user_rule(alice, simple("disabled"))
        disabled.disable(NOW)
        await store(uow_factory, everyone, of_alice, of_bob, of_carol, disabled)

        async def listed(owners: list[User] | None, include_global: bool) -> set[RuleId]:
            async with uow_factory() as uow:
                found = await uow.rules.list_for(
                    owners=None if owners is None else {user.id for user in owners},
                    include_global=include_global,
                )
            return {rule.id for rule in found}

        users_rules = {of_alice.id, of_bob.id, of_carol.id, disabled.id}
        assert await listed(None, True) == users_rules | {everyone.id}
        assert await listed(None, False) == users_rules
        assert await listed([alice], False) == {of_alice.id, disabled.id}
        assert await listed([alice, bob], True) == {
            of_alice.id,
            disabled.id,
            of_bob.id,
            everyone.id,
        }
        assert await listed([], True) == {everyone.id}
        assert await listed([], False) == set()
        assert await listed([admin], False) == set()
        async with uow_factory() as uow:
            (found,) = await uow.rules.list_for(owners=[bob.id], include_global=False)
        assert found == of_bob  # with the current version

    async def test_remove_for_owner(self, uow_factory: UnitOfWorkFactory) -> None:
        """The owner's rules with their versions, and every application of them or by the owner,
        go for good; nothing else does."""
        admin, owner, other = await users(uow_factory, 3)
        first, second = user_rule(owner), user_rule(owner)
        others, everyone = user_rule(other), global_rule(admin)
        await store(uow_factory, first, second, others, everyone)
        await change(uow_factory, first.id, simple("first v2"), owner)
        await change(uow_factory, everyone.id, simple("everyone v2"), admin)
        removed_applications = [
            application(first, owner),  # of the owner's rule
            application(first, admin),  # of the owner's rule, by someone else
            application(everyone, owner),  # by the owner, of another rule
        ]
        kept_applications = [application(others, other), application(everyone, admin)]
        await store(uow_factory, applications=(*removed_applications, *kept_applications))

        async with uow_factory() as uow:
            assert await uow.rules.remove_for_owner(owner.id) == 2
            await uow.commit()

        async with uow_factory() as uow:
            for rule in (first, second):
                assert await uow.rules.find(rule.id) is None
                with pytest.raises(NotFoundError):
                    await uow.rules.versions(rule.id)
                with pytest.raises(NotFoundError):
                    await uow.rules.get_version(rule.id, 1)
            for item in removed_applications:
                assert await uow.rule_applications.find(item.id) is None
            for item in kept_applications:
                assert await uow.rule_applications.get(item.id) == item
            assert await uow.rules.get(others.id) == others
            assert [v.number for v in await uow.rules.versions(everyone.id)] == [1, 2]
            remaining = await uow.rules.list_for(owners=None, include_global=True)
            assert {rule.id for rule in remaining} == {others.id, everyone.id}
            # Nothing refers to the owner any more: the user can go.
            assert await uow.rules.remove_for_owner(owner.id) == 0
            await uow.users.remove(owner.id)
            # A global rule has no owner; its author's removal leaves it alone.
            assert await uow.rules.remove_for_owner(admin.id) == 0
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.users.find(owner.id) is None
            assert (await uow.rules.get(everyone.id)).current.number == 2

    # --- rules: copies and transactions ---------------------------------------------------------

    async def test_reads_are_copies(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner, full())
        await store(uow_factory, rule)
        async with uow_factory() as uow:
            loaded = await uow.rules.get(rule.id)
            loaded.disable(LATER, reason="changed")
            loaded.change(simple("changed"), by=owner.id, now=LATER)
            (listed,) = await uow.rules.list_for(owners=[owner.id], include_global=False)
            listed.enabled = False
            assert await uow.rules.get(rule.id) == rule
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.rules.get(rule.id) == rule
            assert await uow.rules.versions(rule.id) == [rule.current]

    async def test_versions_are_copies(self, uow_factory: UnitOfWorkFactory) -> None:
        """A version is frozen, but values in its conditions are JSON lists and objects."""
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner, full())
        await store(uow_factory, rule)
        async with uow_factory() as uow:
            for version in [
                await uow.rules.get_version(rule.id, 1),
                *(await uow.rules.versions(rule.id)),
                (await uow.rules.get(rule.id)).current,
            ]:
                contacts = version.definition.conditions.items[0]
                assert isinstance(contacts, Condition) and isinstance(contacts.value, list)
                contacts.value.append("changed")
            assert await uow.rules.get_version(rule.id, 1) == rule.current
            assert await uow.rules.versions(rule.id) == [rule.current]
            assert (await uow.rules.get(rule.id)).current == rule.current

    async def test_without_commit_no_rule_is_stored(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        added, rolled_back = user_rule(owner), user_rule(owner)
        async with uow_factory() as uow:
            await uow.rules.add(added)
            assert await uow.rules.get(added.id) == added  # own changes are visible
        async with uow_factory() as uow:
            await uow.rules.add(rolled_back)
            await uow.rollback()
        async with uow_factory() as uow:
            assert await uow.rules.find(added.id) is None
            assert await uow.rules.find(rolled_back.id) is None
            assert await uow.rules.list_for(owners=None, include_global=True) == []

    async def test_a_rolled_back_change_keeps_the_versions(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner, simple("v1"))
        await store(uow_factory, rule)
        with pytest.raises(LookupError):
            async with uow_factory() as uow:
                loaded = await uow.rules.get(rule.id)
                loaded.change(simple("v2"), by=owner.id, now=LATER)
                await uow.rules.update(loaded)
                assert [v.number for v in await uow.rules.versions(rule.id)] == [1, 2]
                raise LookupError
        async with uow_factory() as uow:
            await uow.rules.add(user_rule(owner))
            assert await uow.rules.remove_for_owner(owner.id) == 2
            await uow.rollback()
        async with uow_factory() as uow:
            assert await uow.rules.get(rule.id) == rule
            assert await uow.rules.versions(rule.id) == [rule.current]
            with pytest.raises(NotFoundError):
                await uow.rules.get_version(rule.id, 2)
        # The number is free again: the next change stores version 2.
        await change(uow_factory, rule.id, simple("v2 again"), owner)
        async with uow_factory() as uow:
            second = await uow.rules.get_version(rule.id, 2)
        assert second.definition.name == "v2 again"

    # --- applications ---------------------------------------------------------------------------

    async def test_an_application_round_trips(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        item = application(rule, owner, documents=4)
        await store(uow_factory, rule, applications=(item,))
        async with uow_factory() as uow:
            stored = await uow.rule_applications.get(item.id)
            assert await uow.rule_applications.find(item.id) == stored
        assert stored == item
        assert stored.documents == item.documents  # in the order selected
        assert stored.accept_conflicts == frozenset(item.documents[:1])
        assert (stored.status, stored.position, stored.skipped) == (ApplicationStatus.QUEUED, 0, [])
        assert (stored.rule_id, stored.rule_version, stored.user_id) == (rule.id, 1, owner.id)
        assert stored.remaining == item.documents

    async def test_application_progress_is_stored(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        item = application(rule, owner, documents=5)
        first, second, third, *rest = item.documents
        await store(uow_factory, rule, applications=(item,))
        async with uow_factory() as uow:
            running = await uow.rule_applications.get(item.id)
            running.record(first, "applied")
            running.record(second, "unchanged")
            running.record(third, "the document was deleted")
            await uow.rule_applications.update(running)
            assert running.version == 2
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.rule_applications.get(item.id)
        assert stored == running
        assert (stored.status, stored.position, stored.applied, stored.unchanged) == (
            ApplicationStatus.RUNNING,
            3,
            1,
            1,
        )
        assert stored.skipped == [(third, "the document was deleted")]
        assert stored.remaining == tuple(rest)
        assert stored.finished_at is None and stored.error is None

        async with uow_factory() as uow:
            stored.finish(LATER, error="the rule was deleted")
            await uow.rule_applications.update(stored)
            await uow.commit()
        async with uow_factory() as uow:
            failed = await uow.rule_applications.get(item.id)
        assert failed == stored
        assert (failed.status, failed.error, failed.finished_at, failed.version) == (
            ApplicationStatus.FAILED,
            "the rule was deleted",
            LATER,
            3,
        )

    async def test_a_finished_application(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        item = application(rule, owner, documents=1)
        item.record(item.documents[0], "applied")
        item.finish(LATER)
        await store(uow_factory, rule, applications=(item,))
        async with uow_factory() as uow:
            stored = await uow.rule_applications.get(item.id)
        assert stored == item
        assert (stored.status, stored.error, stored.remaining) == (ApplicationStatus.DONE, None, ())

    async def test_missing_applications(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        await store(uow_factory, rule)
        async with uow_factory() as uow:
            missing = RuleApplicationId(new_id())
            assert await uow.rule_applications.find(missing) is None
            with pytest.raises(NotFoundError):
                await uow.rule_applications.get(missing)
            with pytest.raises(NotFoundError):
                await uow.rule_applications.update(application(rule, owner))

    async def test_stale_application_update_is_rejected(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        item = application(rule, owner)
        await store(uow_factory, rule, applications=(item,))
        async with uow_factory() as first, uow_factory() as second:
            mine = await first.rule_applications.get(item.id)
            theirs = await second.rule_applications.get(item.id)
            mine.record(item.documents[0], "applied")
            await first.rule_applications.update(mine)
            await first.commit()
            theirs.finish(LATER, error="cancelled")
            with pytest.raises(ConcurrencyError):
                await second.rule_applications.update(theirs)
                await second.commit()
        async with uow_factory() as uow:
            stored = await uow.rule_applications.get(item.id)
        assert (stored.status, stored.position, stored.version) == (ApplicationStatus.RUNNING, 1, 2)

    async def test_application_reads_are_copies(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        item = application(rule, owner)
        await store(uow_factory, rule, applications=(item,))
        async with uow_factory() as uow:
            loaded = await uow.rule_applications.get(item.id)
            loaded.record(item.documents[0], "skipped for a reason")
            loaded.skipped.append((item.documents[1], "changed"))
            assert await uow.rule_applications.get(item.id) == item
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.rule_applications.get(item.id) == item

    async def test_without_commit_no_application_is_stored(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        rule = user_rule(owner)
        await store(uow_factory, rule)
        item = application(rule, owner)
        async with uow_factory() as uow:
            await uow.rule_applications.add(item)
            await uow.rollback()
        async with uow_factory() as uow:
            assert await uow.rule_applications.find(item.id) is None

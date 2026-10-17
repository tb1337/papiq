import pytest

from papiq.core.domain.documents import Document
from papiq.core.domain.permissions import (
    can_control_document,
    can_file_into,
    can_manage_master_data,
    can_manage_space,
    can_manage_users,
    can_move_document,
    can_read_document,
    can_write_document,
    document_access,
    in_reach,
    is_document_owner,
    reach_access,
    reach_space_access,
    space_access,
)
from papiq.core.domain.pipeline import Lane, Node, NodeResult
from papiq.core.domain.spaces import ShareLevel, Space
from papiq.core.domain.users import User
from tests import builders
from tests.builders import FAILED, UNCERTAIN


@pytest.fixture
def owner() -> User:
    return builders.user("owner")


@pytest.fixture
def reader() -> User:
    return builders.user("reader")


@pytest.fixture
def writer() -> User:
    return builders.user("writer")


@pytest.fixture
def stranger() -> User:
    return builders.user("stranger")


@pytest.fixture
def shared(owner: User, reader: User, writer: User) -> Space:
    space = builders.space(owner, "Household")
    space.share(reader.id, ShareLevel.READ)
    space.share(writer.id, ShareLevel.READ_WRITE)
    return space


def test_space_access(
    owner: User, reader: User, writer: User, stranger: User, shared: Space
) -> None:
    assert space_access(owner, shared) is ShareLevel.READ_WRITE
    assert space_access(reader, shared) is ShareLevel.READ
    assert space_access(writer, shared) is ShareLevel.READ_WRITE
    assert space_access(stranger, shared) is None


def test_green_document_is_visible_through_the_share(
    owner: User, reader: User, writer: User, stranger: User, shared: Space
) -> None:
    document = builders.processed(owner, shared)
    assert document.lane is Lane.GREEN
    assert document_access(owner, document, shared) is ShareLevel.READ_WRITE
    assert document_access(reader, document, shared) is ShareLevel.READ
    assert document_access(writer, document, shared) is ShareLevel.READ_WRITE
    assert document_access(stranger, document, shared) is None
    assert can_read_document(reader, document, shared)
    assert not can_write_document(reader, document, shared)
    assert can_write_document(writer, document, shared)


@pytest.mark.parametrize(
    "results",
    [{Node.JUDGE: UNCERTAIN}, {Node.OCR: FAILED}],
    ids=["yellow", "red"],
)
def test_yellow_and_red_documents_are_visible_to_the_owner_only(
    owner: User, reader: User, writer: User, shared: Space, results: dict[Node, NodeResult]
) -> None:
    document = builders.processed(owner, shared, results)
    assert document.lane in {Lane.YELLOW, Lane.RED}
    assert document_access(owner, document, shared) is ShareLevel.READ_WRITE
    assert document_access(reader, document, shared) is None
    assert document_access(writer, document, shared) is None


def test_document_in_processing_is_visible_to_the_owner_only(
    owner: User, writer: User, shared: Space
) -> None:
    document = builders.document(owner, shared)
    assert document.lane is None
    assert can_write_document(owner, document, shared)
    assert not can_read_document(writer, document, shared)


def test_space_owner_sees_green_documents_of_others_in_the_space(
    owner: User, writer: User, shared: Space
) -> None:
    filed_by_writer = builders.processed(writer, shared)
    assert document_access(writer, filed_by_writer, shared) is ShareLevel.READ_WRITE
    assert document_access(owner, filed_by_writer, shared) is ShareLevel.READ_WRITE
    yellow = builders.processed(writer, shared, {Node.JUDGE: UNCERTAIN})
    assert document_access(owner, yellow, shared) is None


def test_access_needs_the_documents_space(owner: User, shared: Space) -> None:
    document = builders.processed(owner, shared)
    with pytest.raises(ValueError, match="not in space"):
        document_access(owner, document, builders.space(owner))


def test_filing_needs_write_access_to_the_space(
    owner: User, reader: User, writer: User, stranger: User, shared: Space
) -> None:
    assert can_file_into(owner, shared)
    assert can_file_into(writer, shared)
    assert not can_file_into(reader, shared)
    assert not can_file_into(stranger, shared)


def test_the_space_owner_and_admins_manage_it(
    owner: User, reader: User, writer: User, stranger: User, shared: Space
) -> None:
    assert can_manage_space(owner, shared)
    assert can_manage_space(builders.admin(), shared)
    assert not can_manage_space(writer, shared)
    assert not can_manage_space(reader, shared)
    assert not can_manage_space(stranger, shared)


def test_only_the_document_owner_controls_it(owner: User, writer: User, shared: Space) -> None:
    document: Document = builders.processed(owner, shared)
    assert is_document_owner(owner, document)
    assert not is_document_owner(writer, document)


def test_only_admins_manage_master_data_and_users() -> None:
    admin, user = builders.admin(), builders.user()
    assert can_manage_master_data(admin)
    assert can_manage_users(admin)
    assert not can_manage_master_data(user)
    assert not can_manage_users(user)


@pytest.mark.parametrize(
    "results",
    [None, {Node.JUDGE: UNCERTAIN}, {Node.OCR: FAILED}, "processing"],
    ids=["green", "yellow", "red", "processing"],
)
def test_admins_have_every_right_on_documents_and_spaces(
    owner: User, shared: Space, results: dict[Node, NodeResult] | str | None
) -> None:
    """Tobi, 08.10.2026: admins have every right and see everything."""
    if results == "processing":
        document = builders.document(owner, shared)
    else:
        assert not isinstance(results, str)
        document = builders.processed(owner, shared, results)
    admin = builders.admin()
    assert document_access(admin, document, shared) is ShareLevel.READ_WRITE
    assert can_read_document(admin, document, shared)
    assert can_write_document(admin, document, shared)
    assert can_control_document(admin, document)
    assert space_access(admin, shared) is ShareLevel.READ_WRITE
    assert can_file_into(admin, shared)
    assert can_manage_space(admin, shared)
    # Not within their reach: lists and webhooks stay as for every user.
    assert reach_access(admin, document, shared) is None
    assert not in_reach(admin, document, shared)
    assert reach_space_access(admin, shared) is None
    assert not is_document_owner(admin, document)


def test_the_reach_is_access_without_the_admins_rights(
    owner: User, reader: User, writer: User, stranger: User, shared: Space
) -> None:
    green = builders.processed(owner, shared)
    yellow = builders.processed(owner, shared, {Node.JUDGE: UNCERTAIN})
    for user in (owner, reader, writer, stranger):
        for document in (green, yellow):
            assert reach_access(user, document, shared) is document_access(user, document, shared)
            assert in_reach(user, document, shared) is can_read_document(user, document, shared)
        assert reach_space_access(user, shared) is space_access(user, shared)


def test_owner_and_admins_control_a_document(
    owner: User, writer: User, stranger: User, shared: Space
) -> None:
    document = builders.processed(owner, shared)
    assert can_control_document(owner, document)
    assert can_control_document(builders.admin(), document)
    assert not can_control_document(writer, document)
    assert not can_control_document(stranger, document)


def test_moving_is_for_the_owner_and_admins(
    owner: User, writer: User, stranger: User, shared: Space
) -> None:
    document = builders.processed(owner, shared)
    owners_private = builders.space(owner)
    foreign = builders.space(stranger)
    assert can_move_document(owner, document, owners_private)
    assert not can_move_document(owner, document, foreign)
    assert not can_move_document(writer, document, shared)
    assert can_move_document(builders.admin(), document, foreign)


def test_deactivated_users_have_no_rights() -> None:
    owner, admin = builders.user(), builders.admin()
    space = builders.default_space(owner)
    document = builders.processed(owner, space)
    owner.active = admin.active = False
    assert space_access(owner, space) is None
    assert not can_read_document(owner, document, space)
    assert not can_write_document(owner, document, space)
    assert not can_file_into(owner, space)
    assert not can_manage_space(owner, space)
    assert not is_document_owner(owner, document)
    assert not can_move_document(admin, document, space)
    assert space_access(admin, space) is None
    assert not can_read_document(admin, document, space)
    assert not can_write_document(admin, document, space)
    assert not can_file_into(admin, space)
    assert not can_manage_space(admin, space)
    assert not can_control_document(admin, document)
    assert not can_control_document(owner, document)
    assert not can_manage_master_data(admin)
    assert not can_manage_users(admin)

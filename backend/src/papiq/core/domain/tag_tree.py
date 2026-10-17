"""The tags as trees: what a tag's ancestors and descendants are, and where it may go.

Only the tag set is stored (at a document, a contact or a type); its ancestors are derived from
here. A document is found by a tag and by each of its ancestors, so filters and rule conditions
on a tag match its descendants too. Trees are small (a household's tags), so everything is
computed in memory from all tags.

Stored data is trusted to be a forest (`check_parent` keeps it one); should a cycle get in
anyway, walking up stops where it would repeat itself.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from papiq.core.domain.errors import NotFoundError, ValidationError
from papiq.core.domain.ids import TagId
from papiq.core.domain.master_data import Tag, TagScope

MAX_LEVELS = 5
"""Levels of a tree, its top-level tag included."""
SEPARATOR = " \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} "
"""Between the names of a path, as people read it ("Insurance", the sign, "Health")."""


@dataclass(frozen=True)
class TagTree:
    """All tags (of every scope) by id, with their children."""

    tags: dict[TagId, Tag]
    children: dict[TagId | None, tuple[TagId, ...]]

    @classmethod
    def of(cls, tags: Iterable[Tag]) -> "TagTree":
        by_id = {tag.id: tag for tag in tags}
        children: dict[TagId | None, list[TagId]] = {}
        for tag in sorted(by_id.values(), key=lambda item: item.name.casefold()):
            parent = tag.parent_id if tag.parent_id in by_id else None
            children.setdefault(parent, []).append(tag.id)
        return cls(by_id, {parent: tuple(ids) for parent, ids in children.items()})

    def __contains__(self, id: object) -> bool:
        return id in self.tags

    def get(self, id: TagId) -> Tag:
        """NotFoundError if there is no such tag."""
        try:
            return self.tags[id]
        except KeyError:
            raise NotFoundError("tag", id) from None

    def of_scope(self, scope: TagScope) -> list[Tag]:
        """The tags of `scope`, in the order of the tree: each after its parent, siblings by
        name."""
        return [self.tags[id] for id in self._walk(None) if self.tags[id].scope is scope]

    # --- up and down ----------------------------------------------------------------------------

    def ancestors(self, id: TagId) -> list[Tag]:
        """The tags above `id`, its parent first; none for an unknown tag."""
        found: list[Tag] = []
        seen = {id}
        tag = self.tags.get(id)
        while tag is not None and tag.parent_id is not None and tag.parent_id not in seen:
            seen.add(tag.parent_id)
            tag = self.tags.get(tag.parent_id)
            if tag is not None:
                found.append(tag)
        return found

    def descendants(self, id: TagId) -> list[Tag]:
        """The tags below `id`, in the order of the tree."""
        return [self.tags[child] for child in self._walk(id)]

    def with_ancestors(self, ids: Iterable[TagId]) -> frozenset[TagId]:
        """`ids` and the ancestors of each (an unknown id stays as it is)."""
        return frozenset(
            found for id in ids for found in (id, *(tag.id for tag in self.ancestors(id)))
        )

    def with_descendants(self, ids: Iterable[TagId]) -> frozenset[TagId]:
        """`ids` and the descendants of each (an unknown id stays as it is): what a filter or
        a rule condition on them matches."""
        return frozenset(
            found for id in ids for found in (id, *(tag.id for tag in self.descendants(id)))
        )

    def holders(self, members: Iterable[Iterable[TagId]]) -> dict[TagId, int]:
        """How many of `members` (each by the tags it has: a contact's groups or topics, a
        type's groups) each tag holds: those with it or a tag below it, each once, as a filter
        on the tag finds them. Only tags that hold one; an unknown id counts for itself."""
        counts: dict[TagId, int] = {}
        for tags in members:
            for id in self.with_ancestors(tags):
                counts[id] = counts.get(id, 0) + 1
        return counts

    def path(self, id: TagId) -> list[str]:
        """The names from the top-level tag down to `id`'s own."""
        tag = self.get(id)
        return [*(item.name for item in reversed(self.ancestors(id))), tag.name]

    def label(self, id: TagId) -> str:
        """The path written out, the names joined by `SEPARATOR`."""
        return SEPARATOR.join(self.path(id))

    def depth(self, id: TagId) -> int:
        """The level of `id`: 1 for a top-level tag."""
        return 1 + len(self.ancestors(id))

    def height(self, id: TagId) -> int:
        """The levels of `id`'s subtree, its own included: 1 for a tag without children."""
        highest, stack, seen = 1, [(id, 1)], {id}
        while stack:
            current, level = stack.pop()
            highest = max(highest, level)
            for child in self.children.get(current, ()):
                if child not in seen:
                    seen.add(child)
                    stack.append((child, level + 1))
        return highest

    # --- rules of the tree ----------------------------------------------------------------------

    def check_parent(self, tag: Tag, parent_id: TagId | None) -> None:
        """Whether `tag` may be nested under `parent_id`: a tag of the same scope, not the tag
        itself or one of its descendants, and with the tag's subtree at most `MAX_LEVELS`
        levels deep. NotFoundError for an unknown parent, ValidationError otherwise. `tag` may
        be new (not in the tree yet)."""
        if parent_id is None:
            return
        parent = self.get(parent_id)
        if parent.scope is not tag.scope:
            raise ValidationError(
                f"'{parent.name}' is a tag of another scope ({parent.scope}): a parent tag has "
                f"the scope of its children ({tag.scope})"
            )
        if parent.id == tag.id or parent.id in {item.id for item in self.descendants(tag.id)}:
            raise ValidationError(
                f"'{tag.name}' cannot go under '{parent.name}', which is below it"
            )
        height = self.height(tag.id) if tag.id in self.tags else 1
        if self.depth(parent.id) + height > MAX_LEVELS:
            raise ValidationError(f"tags are nested at most {MAX_LEVELS} levels deep")

    def normalise(self, ids: Iterable[TagId]) -> frozenset[TagId]:
        """`ids` without the ancestors of others among them: a child and its parent set
        together leave the child (it says more, and the parent is derived from it)."""
        given = frozenset(ids)
        above = {tag.id for id in given for tag in self.ancestors(id)}
        return given - above

    def _walk(self, parent: TagId | None) -> Iterator[TagId]:
        """The tags below `parent` (None: all), depth first, each once. Walking all, tags no
        top-level tag leads to (a cycle in stored data) come last, as if at the top, so that
        they stay visible and can be fixed."""
        seen: set[TagId] = set() if parent is None else {parent}
        stacks = [list(reversed(self.children.get(parent, ())))]
        if parent is None:
            stacks += [
                [id] for id in sorted(self.tags, key=lambda id: self.tags[id].name.casefold())
            ]
        for stack in stacks:
            while stack:
                id = stack.pop()
                if id in seen:
                    continue
                seen.add(id)
                yield id
                stack.extend(reversed(self.children.get(id, ())))

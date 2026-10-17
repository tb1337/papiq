"""The classifier (see `ports.classifier`): trained from the documents a person confirmed, and
asked for a new document's contact, type, topic and tags (the signal `signal_classifier`).

A recurring check queues a training once enough documents were confirmed since the last one
(or, without a model, once there are enough at all); an admin can ask for one. Training also
learns identifiers of contacts from the same documents (`identifiers.mine_identifiers`), and
keeps the latest `KEPT_MODELS` models.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from papiq.core.domain.classifier import KEPT_MODELS, ClassifierModel
from papiq.core.domain.documents import Document
from papiq.core.domain.errors import (
    ClassifierError,
    ConcurrencyError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from papiq.core.domain.events import ClassifierTrained
from papiq.core.domain.identifiers import (
    IdentifierKind,
    IdentifierSource,
    identifiers_in,
    mine_identifiers,
)
from papiq.core.domain.ids import ClassifierModelId, ContactId, TagId, UserId
from papiq.core.domain.jobs import Job
from papiq.core.domain.json_value import JsonObject
from papiq.core.ports import (
    Classifier,
    Clock,
    DocumentFilter,
    DocumentPosition,
    ObjectStore,
    Prediction,
    TrainedModel,
    TrainingExample,
    UnitOfWork,
    UnitOfWorkFactory,
)
from papiq.core.services._access import load_actor
from papiq.core.services.master_data import replace_learned_identifiers
from papiq.core.services.objects import classifier_key, load_text

log = logging.getLogger(__name__)

CHECK_JOB = "classifier.check"
TRAIN_JOB = "classifier.train"
_TRAIN_ATTEMPTS = 3
_RETRY_DELAY = timedelta(minutes=5)
_PAGE = 500
_COMMIT_ATTEMPTS = 3  # a contact changed while the learned identifiers were written: again
_HOLDOUT_FROM = 50  # examples from which a share is held out to measure the model
_HOLDOUT_EVERY = 5  # every fifth example: a fifth held out
_MODEL_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class ClassifierPolicy:
    min_examples: int = 20  # confirmed documents before the first model
    retrain_after: int = 25  # confirmations since the last model before another
    threshold: float = 0.8  # a prediction this sure names its class (`judge`)
    check_interval: timedelta = timedelta(minutes=10)
    train_lease: timedelta = timedelta(hours=1)
    text_limit: int = 50_000  # characters of a document's text it learns from

    def __post_init__(self) -> None:
        if min(self.min_examples, self.retrain_after, self.text_limit) < 1:
            raise ValueError("limits must be at least 1")
        if not 0 < self.threshold <= 1:
            raise ValueError("the threshold is between 0 and 1")


@dataclass(frozen=True)
class ClassifierStatus:
    model: ClassifierModel | None
    confirmed: int  # documents a person confirmed
    since: int  # of them, confirmed after the model's examples
    policy: ClassifierPolicy


@dataclass(frozen=True)
class _Example:
    document: Document
    example: TrainingExample


class ClassifierService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        store: ObjectStore,
        clock: Clock,
        classifier: Classifier,
        policy: ClassifierPolicy | None = None,
        *,
        fetch_logos: bool = False,
    ) -> None:
        """`fetch_logos`: a contact without a logo, or with a fetched one, that learns a new
        domain gets its logo fetched (`logos.queue_logo_fetch`)."""
        self._fetch_logos = fetch_logos
        self._uow = uow
        self._store = store
        self._clock = clock
        self._classifier = classifier
        self.policy = policy or ClassifierPolicy()
        self._cached: tuple[ClassifierModelId, bytes] | None = None

    @property
    def name(self) -> str:
        return self._classifier.name

    # --- jobs -------------------------------------------------------------------------------------

    async def schedule(self) -> None:
        """At the start of a worker: make sure the recurring check is queued."""
        async with self._uow() as uow:
            await uow.jobs.enqueue(CHECK_JOB, {}, run_at=self._clock.now(), dedup_key=CHECK_JOB)
            await uow.commit()

    async def run_next_job(self) -> bool:
        """Claim and run a due check or training; False if none was due."""
        for kind, lease in (
            (CHECK_JOB, timedelta(minutes=5)),
            (TRAIN_JOB, self.policy.train_lease),
        ):
            async with self._uow() as uow:
                job = await uow.jobs.claim(now=self._clock.now(), lease=lease, kinds=[kind])
                await uow.commit()
            if job is not None:
                try:
                    await self._run_claimed(job)
                except asyncio.CancelledError:
                    await asyncio.shield(self._release(job))
                    raise
                return True
        return False

    async def check(self) -> bool:
        """Queue a training if one is due; returns whether it is."""
        async with self._uow() as uow:
            latest = await uow.classifier_models.latest()
            if latest is None:
                due = await uow.documents.count_confirmed() >= self.policy.min_examples
            else:
                since = await uow.documents.count_confirmed(after=latest.examples_until)
                due = since >= self.policy.retrain_after
            if due:
                await uow.jobs.enqueue(TRAIN_JOB, {}, run_at=self._clock.now(), dedup_key=TRAIN_JOB)
                await uow.commit()
        return due

    async def request_training(self, actor: UserId) -> None:
        """Admins only: queue a training, unless one is queued or running. ConflictError with
        fewer confirmed documents than a first model needs."""
        async with self._uow() as uow:
            if not (await load_actor(uow, actor)).is_active_admin:
                raise PermissionDeniedError("only admins train the classifier")
            confirmed = await uow.documents.count_confirmed()
            if confirmed < self.policy.min_examples:
                raise ConflictError(
                    f"{confirmed} confirmed documents; training needs {self.policy.min_examples}"
                )
            await uow.jobs.enqueue(TRAIN_JOB, {}, run_at=self._clock.now(), dedup_key=TRAIN_JOB)
            await uow.commit()

    async def status(self, actor: UserId) -> ClassifierStatus:
        """Admins only: the latest model and the confirmations since."""
        async with self._uow() as uow:
            if not (await load_actor(uow, actor)).is_active_admin:
                raise PermissionDeniedError("only admins see the classifier")
            latest = await uow.classifier_models.latest()
            confirmed = await uow.documents.count_confirmed()
            since = (
                confirmed
                if latest is None
                else await uow.documents.count_confirmed(after=latest.examples_until)
            )
        return ClassifierStatus(latest, confirmed, since, self.policy)

    # --- training ---------------------------------------------------------------------------------

    async def train(self) -> ClassifierModel:
        """A new model from the confirmed documents, and the identifiers learned from them.
        ClassifierError if there is nothing to learn from."""
        examples = await self._examples()
        if len(examples) < 2:
            raise ClassifierError(f"{len(examples)} confirmed documents with text: too few")
        accuracy = await self._accuracy(examples)
        trained = await self._classifier.train([item.example for item in examples])
        until = max(
            (item.document.confirmed_at for item in examples if item.document.confirmed_at),
            default=None,
        )
        found = [
            (item.document.contact_id, item.document.topic_id, identifiers_in(item.example.text))
            for item in examples
        ]
        for attempt in range(1, _COMMIT_ATTEMPTS + 1):
            try:
                model, learned, old = await self._store_model(
                    trained, found, accuracy, len(examples), until
                )
                break
            except ConcurrencyError:
                if attempt == _COMMIT_ATTEMPTS:
                    raise
                log.info("a contact changed while the model was stored; storing again")
        for item in old:
            await self._forget(item.object_key)
        log.info(
            "classifier trained",
            extra={
                "number": model.number,
                "examples": len(examples),
                "learned_identifiers": learned,
            },
        )
        return model

    async def _store_model(
        self,
        trained: TrainedModel,
        found: list[tuple[ContactId | None, TagId | None, set[tuple[IdentifierKind, str]]]],
        accuracy: JsonObject | None,
        examples: int,
        until: datetime | None,
    ) -> tuple[ClassifierModel, int, list[ClassifierModel]]:
        """Store the model with the next number, the identifiers learned, the pruning and the
        event `classifier.trained` in one commit. One training at a time does it (a lock), so two
        trainings, the worker's and the command's, never take the same number."""
        async with self._uow() as uow:
            await uow.lock(TRAIN_JOB)
            latest = await uow.classifier_models.latest()
            number = 1 if latest is None else latest.number + 1
            key = classifier_key(number)
            await self._store.put(key, trained.data, content_type=_MODEL_TYPE)
            taken = [
                item.key
                for contact in await uow.contacts.list_all()
                for item in contact.identifiers
                if item.source is not IdentifierSource.LEARNED
            ]
            learned = await replace_learned_identifiers(
                uow,
                mine_identifiers(found, taken),
                fetch_logos_at=self._clock.now() if self._fetch_logos else None,
            )
            summary: JsonObject = {
                **trained.summary,
                "method": self._classifier.name,
                "learned_identifiers": learned,
                "accuracy": accuracy,
            }
            now = self._clock.now()
            model = ClassifierModel.create(
                number=number,
                now=now,
                examples=examples,
                examples_until=until,
                object_key=key,
                summary=summary,
            )
            await uow.classifier_models.add(model)
            old = (await uow.classifier_models.list_all())[KEPT_MODELS:]  # newest first
            for item in old:
                await uow.classifier_models.remove(item.id)
            await uow.outbox.add(
                [ClassifierTrained(occurred_at=now, number=number, examples=examples)]
            )
            await uow.commit()
        return model, learned, old

    async def _examples(self) -> list[_Example]:
        """The confirmed documents with their texts (one without a text yet is left out)."""
        documents: list[Document] = []
        after: DocumentPosition | None = None
        async with self._uow() as uow:
            # Only the tags and topics the classification sets are learned, not those set by
            # hand.
            tags = await uow.tags.list_all()
            targets = {tag.id for tag in tags if tag.is_target}
            topics = {tag.id for tag in tags if tag.is_topic_target}
        while True:
            async with self._uow() as uow:
                page = await uow.documents.query(
                    DocumentFilter(confirmed=True), after=after, limit=_PAGE
                )
            documents.extend(page)
            if len(page) < _PAGE:
                break
            after = DocumentPosition(page[-1].id)
        examples = []
        for document in documents:
            try:
                raw = await load_text(self._store, document.id)
            except NotFoundError:
                continue
            text = _shortened(raw.decode("utf-8", "replace"), self.policy.text_limit)
            example = TrainingExample(
                text=text,
                contact=document.contact_id,
                document_type=document.document_type_id,
                tags=frozenset(document.tag_ids & targets),
                topic=document.topic_id if document.topic_id in topics else None,
            )
            examples.append(_Example(document, example))
        return examples

    async def _accuracy(self, examples: Sequence[_Example]) -> JsonObject | None:
        """The share of held-out examples whose contact and type the model trained on the rest
        names first; None with too few examples to tell."""
        if len(examples) < _HOLDOUT_FROM:
            return None
        ordered = sorted(examples, key=lambda item: item.document.id)
        held = ordered[::_HOLDOUT_EVERY]
        rest = [item for index, item in enumerate(ordered) if index % _HOLDOUT_EVERY]
        try:
            model = await self._classifier.train([item.example for item in rest])
            predictions = [
                await self._classifier.predict(model.data, item.example.text) for item in held
            ]
        except ClassifierError as error:
            log.warning("the classifier could not be measured", extra={"error": str(error)})
            return None
        examples_held = [item.example for item in held]
        return {
            "tested": len(held),
            "contact": _hits(examples_held, predictions, lambda e: e.contact, _first_contact),
            "document_type": _hits(
                examples_held, predictions, lambda e: e.document_type, _first_type
            ),
        }

    async def _forget(self, key: str) -> None:
        try:
            await self._store.delete(key)
        except Exception:  # an old model left behind costs space, nothing else
            log.warning("an old classifier model could not be deleted", extra={"key": key})

    # --- predicting -------------------------------------------------------------------------------

    async def predict(self, text: str) -> tuple[ClassifierModel, Prediction] | None:
        """The latest model's prediction for `text`; None without a model. ClassifierError if
        the model cannot be read."""
        async with self._uow() as uow:
            model = await uow.classifier_models.latest()
        if model is None:
            return None
        if self._cached is None or self._cached[0] != model.id:
            try:
                data = await self._store.get(model.object_key)
            except NotFoundError:
                raise ClassifierError(f"the file of model {model.number} is missing") from None
            self._cached = (model.id, data)
        text = _shortened(text, self.policy.text_limit)
        return model, await self._classifier.predict(self._cached[1], text)

    # --- job bookkeeping --------------------------------------------------------------------------

    async def _run_claimed(self, job: Job) -> None:
        try:
            if job.kind == CHECK_JOB:
                await self.check()
            else:
                await self.train()
        except ClassifierError as error:
            log.warning("the classifier cannot be trained", extra={"error": str(error)})
            await self._finish(job, error=str(error))
            return
        except Exception as error:
            await self._failed(job, error)
            return
        await self._finish(job)

    async def _finish(self, job: Job, *, error: str | None = None) -> None:
        try:
            async with self._uow() as uow:
                if error is None:
                    await uow.jobs.complete(job)
                else:
                    await uow.jobs.fail(job, error=error)
                if job.kind == CHECK_JOB:
                    await self._next_check(uow)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a classifier job", extra={"job_id": str(job.id)})

    async def _failed(self, job: Job, error: Exception) -> None:
        reason = f"{type(error).__name__}: {error}"
        log.warning("classifier job failed", extra={"kind": job.kind}, exc_info=True)
        try:
            async with self._uow() as uow:
                if job.kind == CHECK_JOB:
                    await uow.jobs.fail(job, error=reason)
                    await self._next_check(uow)
                elif job.tries >= _TRAIN_ATTEMPTS:
                    await uow.jobs.fail(job, error=reason)
                else:
                    run_at = self._clock.now() + _RETRY_DELAY * job.tries
                    await uow.jobs.reschedule(job, run_at=run_at, error=reason)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a classifier job", extra={"job_id": str(job.id)})

    async def _next_check(self, uow: UnitOfWork) -> None:
        run_at = self._clock.now() + self.policy.check_interval
        await uow.jobs.enqueue(CHECK_JOB, {}, run_at=run_at, dedup_key=CHECK_JOB)

    async def _release(self, job: Job) -> None:
        try:
            async with self._uow() as uow:
                await uow.jobs.release(job, run_at=self._clock.now(), error="worker stopped")
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a classifier job", extra={"job_id": str(job.id)})


def _shortened(text: str, limit: int) -> str:
    """The start and the end of a long text: there it says what it is and from whom."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return text[:head] + "\n…\n" + text[len(text) - (limit - head) :]


def _first_contact(prediction: Prediction) -> object:
    return prediction.contacts[0][0] if prediction.contacts else None


def _first_type(prediction: Prediction) -> object:
    return prediction.document_types[0][0] if prediction.document_types else None


def _hits(
    examples: Sequence[TrainingExample],
    predictions: Sequence[Prediction],
    label: Callable[[TrainingExample], object],
    first: Callable[[Prediction], object],
) -> float | None:
    """The share of examples with a label whose first prediction is it."""
    pairs = [
        (label(example), first(prediction))
        for example, prediction in zip(examples, predictions, strict=True)
        if label(example) is not None
    ]
    if not pairs:
        return None
    return round(sum(wanted == got for wanted, got in pairs) / len(pairs), 3)

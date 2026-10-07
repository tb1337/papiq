"""The classification of the evaluation set on the real persistence: the same documents and
fixed answers as `evaluate --fake` (in memory), through the SQL unit of work (SQLite with the
filesystem; Postgres with S3). The lanes and the checks must come out as in memory."""

from pathlib import Path

from papiq.adapters.inbound.evaluation import Case, Environment, evaluate, load_set
from papiq.adapters.outbound.memory import FakeLanguageModel
from papiq.adapters.outbound.sql import Database, SqlUnitOfWorkFactory
from papiq.adapters.outbound.system import SystemClock
from papiq.composition.container import policy_of
from papiq.composition.settings import Settings
from papiq.core.ports import LanguageModel, ObjectStore

SET = Path(__file__).parents[2] / "evaluation"


async def test_the_fixed_answers_give_the_expected_lanes(
    stores: tuple[Database, ObjectStore],
) -> None:
    database, store = stores
    evaluation = load_set(SET)
    environment = Environment(SqlUnitOfWorkFactory(database), store, SystemClock())

    def model_for(case: Case) -> LanguageModel | None:
        assert case.fake is not None
        return FakeLanguageModel(case.fake.respond, model="fixed answers")

    results = await evaluate(
        evaluation, environment, model_for, policy_of(Settings.model_construct())
    )

    assert len(results) == len(evaluation.cases)
    for result in results:
        assert result.case.fake is not None
        assert result.lane is result.case.fake.lane, (result.case.name, result.reasons)
        assert result.violations == (), result.case.name

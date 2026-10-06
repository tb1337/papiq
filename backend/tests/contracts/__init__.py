"""Contract test suites: every adapter of a port must pass them.

A suite is a class whose tests use an adapter fixture (e.g. `uow_factory`). An adapter's test
module subclasses it as `Test...` and provides the fixture; in-memory adapters run them under
`tests/unit`, database and storage adapters under `tests/integration`.
"""

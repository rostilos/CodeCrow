"""Pytest registration local to the P0-02 characterization suite."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "legacy_defect: locks in an observed unsafe legacy result for a later task to invert",
    )

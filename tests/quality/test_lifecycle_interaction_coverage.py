"""Fast contracts for the APMLifecycle primitive-target interaction array."""

import pytest

from tests.integration import test_primitive_target_covering_array as interaction_array
from tests.integration.test_primitive_target_covering_array import (
    _DYNAMIC_REFUSAL_ROWS,
    _KNOWN_SECOND_PASS_GAPS,
    _MAX_CATALOG_ROWS,
    _ROWS,
    _TRANSITION_ROWS,
    _assert_covering_array,
)

RATCHET_TEST_SCOPE = "repository"


def test_lifecycle_routing_cells_cover_the_live_target_catalog() -> None:
    """Keep every valid primitive, target, and scope cell executable."""
    _assert_covering_array()


def test_lifecycle_interaction_rows_are_unique_and_bounded() -> None:
    """Prevent duplicate work and silent merge-group runtime growth."""
    row_ids = [row.id for row in _ROWS]
    catalog_rows = [row for row in _ROWS if row.catalog_cell]

    assert len(row_ids) == len(set(row_ids))
    assert len(catalog_rows) <= _MAX_CATALOG_ROWS
    assert not set(_TRANSITION_ROWS) & set(_DYNAMIC_REFUSAL_ROWS)
    assert all(len(row.targets) == len(row.primitives) == 1 for row in catalog_rows)
    covered_cells = {(row.targets[0], row.primitives[0], row.user_scope) for row in catalog_rows}
    assert set(_KNOWN_SECOND_PASS_GAPS) <= covered_cells
    assert len(_KNOWN_SECOND_PASS_GAPS) == 1


def test_lifecycle_routing_ratchet_rejects_a_missing_catalog_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the fast ratchet fails when one executable routing cell disappears."""
    first_catalog_row = next(row for row in _ROWS if row.catalog_cell)
    monkeypatch.setattr(
        interaction_array,
        "_ROWS",
        tuple(row for row in _ROWS if row != first_catalog_row),
    )

    with pytest.raises(AssertionError):
        interaction_array._assert_covering_array()

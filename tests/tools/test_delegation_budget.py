import threading

import pytest

from tools.delegation_budget import BudgetExhausted, DelegationTreeBudget


def test_reservation_rolls_back_and_commits_once():
    budget = DelegationTreeBudget(max_children=1, max_iterations=10)
    reservation = budget.reserve_child(iterations=4)
    assert budget.snapshot()["reserved_children"] == 1
    reservation.rollback()
    assert budget.snapshot()["reserved_children"] == 0
    committed = budget.reserve_child(iterations=3)
    committed.commit()
    assert budget.snapshot()["started_children"] == 1
    with pytest.raises(RuntimeError):
        committed.commit()


def test_concurrent_reservations_never_exceed_tree_limit():
    budget = DelegationTreeBudget(max_children=3, max_iterations=30)
    successes = []
    lock = threading.Lock()

    def reserve():
        try:
            token = budget.reserve_child(iterations=1)
            token.commit()
            with lock:
                successes.append(True)
        except BudgetExhausted:
            pass

    threads = [threading.Thread(target=reserve) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(successes) == 3


def test_committed_iterations_remain_counted_against_hard_ceiling():
    budget = DelegationTreeBudget(max_children=3, max_iterations=5)
    reservation = budget.reserve_child(iterations=3)
    reservation.commit()

    snapshot = budget.snapshot()
    assert snapshot["iterations_remaining"] == 2
    assert snapshot["children_remaining"] == 2
    with pytest.raises(BudgetExhausted, match="iteration budget exhausted"):
        budget.reserve_child(iterations=3)

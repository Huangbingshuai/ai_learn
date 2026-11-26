from app.api.records import _coin_snapshot


def test_coin_snapshot_only_counts_new_settlements_and_uses_final_balance():
    answers = [
        {"coinSettlementNew": True, "coinDelta": 10, "coinBalance": 10},
        {"coinSettlementNew": False, "coinDelta": 10, "coinBalance": 10},
        {"coinSettlementNew": True, "coinDelta": -5, "coinBalance": 5},
    ]
    assert _coin_snapshot(answers, 3) == (5, 5)


def test_coin_snapshot_unknown_for_incomplete_or_legacy_receipts():
    assert _coin_snapshot([{"coinDelta": 10, "coinBalance": 10}], 1) == (None, None)
    assert _coin_snapshot([], 1) == (None, None)

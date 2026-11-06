from decimal import Decimal

from sqlibrarian.evaluation.score import normalize, summarize


def test_normalize_ignores_row_order_and_number_type():
    a = [(Decimal("2.250"), "CS 201 "), (Decimal("1.0"), "BS 101")]
    b = [(1.0, "BS 101"), (2.25, "CS 201")]
    assert normalize(a) == normalize(b)


def test_normalize_keeps_column_order():
    assert normalize([(1, "a")]) != normalize([("a", 1)])


def test_summarize_counts_each_status():
    rows = [
        {"lang": "en", "difficulty": "easy", "status": "correct"},
        {"lang": "ar", "difficulty": "easy", "status": "wrong"},
        {"lang": "ar", "difficulty": "hard", "status": "invalid"},
        {"lang": "mixed", "difficulty": "hard", "status": "error"},
    ]
    s = summarize(rows)
    assert s["accuracy"] == 25.0
    assert s["accuracy_lang_en"] == 100.0 and s["accuracy_lang_ar"] == 0.0
    assert s["invalid_pct"] == 25.0 and s["error_pct"] == 25.0

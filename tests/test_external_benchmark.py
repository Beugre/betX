from betx.external.normalization import normalize_team_name, parse_selection_to_1x2, score_to_1x2
from betx.external.scoring import compute_quality_score, flat_roi


def test_normalize_team_name():
    assert normalize_team_name("FC Porto") == "porto"
    assert normalize_team_name("Paris-Saint Germain") == "paris saint germain"


def test_parse_selection_to_1x2():
    assert parse_selection_to_1x2("1") == "home"
    assert parse_selection_to_1x2("X") == "draw"
    assert parse_selection_to_1x2("away") == "away"
    assert parse_selection_to_1x2("domicile") == "home"


def test_score_to_1x2():
    assert score_to_1x2(2, 1) == "home"
    assert score_to_1x2(1, 1) == "draw"
    assert score_to_1x2(0, 1) == "away"


def test_quality_score_and_roi():
    roi = flat_roi(wins=55, losses=45)
    assert roi > 0
    score = compute_quality_score(hit_rate=0.55, roi_flat=roi, graded_count=100)
    assert score > 0

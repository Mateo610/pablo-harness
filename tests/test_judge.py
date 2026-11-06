def test_a_reply_cut_off_after_its_score_keeps_it_but_asks() -> None:
    from pablo.judge import CUT_CAP, parse_judgement

    low = parse_judgement('{"certainty": 0.3, "summary": "The diff truncates at the word bou')
    assert low.certainty == 0.3 and "cut off" in low.question
    high = parse_judgement('{"certainty": 0.95, "summary": "Looks right, but the limit=0 case')
    assert high.certainty == CUT_CAP  # never ready on a reply that ran out of room
    assert parse_judgement("I think this is fine.").certainty == 0.0

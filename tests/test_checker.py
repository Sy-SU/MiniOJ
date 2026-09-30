from minioj.judge.checker import normalize_output, outputs_match


def test_checker_ignores_trailing_spaces_and_final_newlines():
    assert outputs_match("one  \n two\t\n\n", "one\n two\n")


def test_checker_preserves_meaningful_whitespace():
    assert not outputs_match("1 2", "12")
    assert normalize_output("a\r\nb\r\n") == "a\nb"

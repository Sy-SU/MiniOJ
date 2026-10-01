from __future__ import annotations

import pytest

from minioj.judge import SubmissionStatus, Verdict, validate_submission_result


def test_non_terminal_submission_rejects_final_fields():
    with pytest.raises(ValueError, match="Non-terminal"):
        validate_submission_result(
            SubmissionStatus.RUNNING,
            Verdict.AC,
            {"success": True},
            {"verdict": "AC"},
        )


def test_finished_submission_requires_matching_verdict():
    with pytest.raises(ValueError, match="matching judge result"):
        validate_submission_result(
            SubmissionStatus.FINISHED,
            Verdict.WA,
            {"success": True},
            {"verdict": "AC"},
        )


def test_internal_error_can_finish_without_compile_result():
    validate_submission_result(
        SubmissionStatus.FINISHED,
        Verdict.IE,
        None,
        {"verdict": "IE"},
    )

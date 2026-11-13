"""Single scoring admissibility policy shared by diagnosis and displayed summaries."""
PENDING_SCORE_STATUSES = {"pending_key", "needs_manual", "needs_model_scoring", "ungraded"}
ADMISSIBLE_AUTHORITIES = {"teacher_key", "paper_snapshot", "snapshot_rule", "ai_auto_checked", "verified"}


def admissible_score(attempt, score, version=None):
    if attempt is None or score is None:
        return False
    if attempt.status != "submitted" or not attempt.is_effective or attempt.attendance_status == "absent":
        return False
    if attempt.grading_status == "pending_key":
        return False
    if attempt.assessment_version_id and version is None:
        return False  # a missing classroom version is not evidence of a valid key
    if version is not None and version.scoring_status == "pending_key":
        return False
    return (score.correct is not None and score.scoring_status not in PENDING_SCORE_STATUSES
            and score.authority_status in ADMISSIBLE_AUTHORITIES)

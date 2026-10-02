from sqlalchemy import func, select

from minioj.models import Problem, Submission


def profile_statistics(db, user_id: int) -> dict:
    total = db.scalar(
        select(func.count(Submission.id)).where(Submission.user_id == user_id)
    )
    accepted_filter = (
        Submission.user_id == user_id,
        Submission.status == "FINISHED",
        Submission.verdict == "AC",
    )
    accepted = db.scalar(select(func.count(Submission.id)).where(*accepted_filter))
    solved = db.execute(
        select(
            Problem,
            func.min(
                func.coalesce(Submission.finished_at, Submission.created_at)
            ).label("solved_at"),
        )
        .join(Submission, Submission.problem_id == Problem.id)
        .where(*accepted_filter)
        .group_by(Problem.id)
        .order_by(Problem.id)
    ).all()
    return {
        "total": total,
        "accepted": accepted,
        "ac_rate": round(100 * accepted / total, 1) if total else 0,
        "solved_count": len(solved),
        "solved": solved,
    }

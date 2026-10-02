from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from minioj.contest_performance import calculate_performance
from minioj.models import (
    Contest,
    ContestParticipant,
    ContestProblem,
    Problem,
    Submission,
    User,
)
from minioj.permissions import Permission, can, require_permission
from minioj.submissions import enqueue_submission

WRONG_PENALTY_MINUTES = 20
PENALIZED_VERDICTS = frozenset({"WA", "RE", "TLE", "MLE", "OLE"})


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def contest_status(contest: Contest, now: datetime | None = None) -> str:
    now = utc(now or datetime.now(UTC))
    if now < utc(contest.start_time):
        return "UPCOMING"
    return "RUNNING" if now < utc(contest.end_time) else "ENDED"


def problem_label(position: int) -> str:
    value, label = position, ""
    while value:
        value, remainder = divmod(value - 1, 26)
        label = chr(65 + remainder) + label
    return label


def get_contest(db: Session, contest_id: int, *, lock: bool = False) -> Contest:
    if lock:
        result = db.execute(
            update(Contest)
            .where(Contest.id == contest_id, Contest.deleted_at.is_(None))
            .values(updated_at=Contest.updated_at)
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=404, detail="Contest not found")
    contest = db.get(Contest, contest_id)
    if lock and contest:
        db.refresh(contest)
    if contest is None or contest.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Contest not found")
    return contest


def contest_problems(contest: Contest, user: User | None) -> list[ContestProblem]:
    if contest_status(contest) == "UPCOMING" and not can(user, Permission.CONTESTS):
        return []
    return list(contest.problems)


def save_contest(
    db: Session,
    actor: User,
    title: str,
    description: str,
    start: datetime,
    end: datetime,
    problem_ids: list[str],
    contest_id: int | None = None,
) -> Contest:
    require_permission(actor, Permission.CONTESTS)
    title = title.strip()
    if not title or len(title) > 255 or len(description.encode()) > 64 * 1024:
        raise HTTPException(
            status_code=422, detail="Invalid contest title or description"
        )
    start, end = utc(start), utc(end)
    if end <= start:
        raise HTTPException(status_code=422, detail="End time must be after start time")
    if len(problem_ids) != len(set(problem_ids)) or len(problem_ids) > 100:
        raise HTTPException(
            status_code=422, detail="Problems must be unique (maximum 100)"
        )
    contest = get_contest(db, contest_id, lock=True) if contest_id is not None else None
    if (
        contest
        and contest_status(contest) != "UPCOMING"
        and (start != utc(contest.start_time) or end != utc(contest.end_time))
    ):
        raise HTTPException(
            status_code=409,
            detail="Contest times are locked after start",
        )
    for problem_id in problem_ids:
        problem = db.get(Problem, problem_id)
        if problem is None or problem.deleted_at is not None:
            raise HTTPException(
                status_code=422, detail=f"Problem {problem_id} is unavailable"
            )
    if contest is None:
        contest = Contest(
            created_by=actor.id,
            title=title,
            description=description,
            start_time=start,
            end_time=end,
        )
        db.add(contest)
        db.flush()
    else:
        contest.title, contest.description, contest.start_time, contest.end_time = (
            title,
            description,
            start,
            end,
        )
        db.execute(
            delete(ContestProblem).where(ContestProblem.contest_id == contest.id)
        )
    db.add_all(
        [
            ContestProblem(contest_id=contest.id, problem_id=problem_id, position=i)
            for i, problem_id in enumerate(problem_ids, 1)
        ]
    )
    db.commit()
    db.expire(contest, ["problems"])
    return contest


def join_contest(db: Session, contest: Contest, user: User) -> None:
    if (
        db.scalar(
            select(ContestParticipant.id).where(
                ContestParticipant.contest_id == contest.id,
                ContestParticipant.user_id == user.id,
            )
        )
        is None
    ):
        db.add(ContestParticipant(contest_id=contest.id, user_id=user.id))
        db.flush()


def submit_to_contest(db: Session, contest_id: int, user: User, payload) -> Submission:
    contest = get_contest(db, contest_id, lock=True)
    accepted_at = datetime.now(UTC)
    if contest_status(contest, accepted_at) != "RUNNING":
        raise HTTPException(
            status_code=409,
            detail="Contest submissions are only accepted while running",
        )
    if payload.problem_id not in {row.problem_id for row in contest.problems}:
        raise HTTPException(
            status_code=422, detail="Problem does not belong to this contest"
        )
    join_contest(db, contest, user)
    return enqueue_submission(
        db, user, payload, contest_id=contest.id, created_at=accepted_at
    )


def standings(
    db: Session, contest: Contest, *, problems: list[ContestProblem] | None = None
) -> list[dict]:
    problems = list(contest.problems) if problems is None else problems
    participants = db.scalars(
        select(ContestParticipant).where(ContestParticipant.contest_id == contest.id)
    ).all()
    rows = {
        p.user_id: {
            "user_id": p.user_id,
            "username": p.user.username,
            "solved": 0,
            "penalty": 0,
            "problems": {
                r.problem_id: {
                    "solved": False,
                    "wrong": 0,
                    "minute": None,
                    "pending": False,
                }
                for r in problems
            },
        }
        for p in participants
    }
    submissions = db.scalars(
        select(Submission)
        .where(
            Submission.contest_id == contest.id,
            Submission.created_at >= contest.start_time,
            Submission.created_at < contest.end_time,
        )
        .order_by(Submission.created_at, Submission.id)
    ).all()
    for submission in submissions:
        if submission.user_id not in rows:
            rows[submission.user_id] = {
                "user_id": submission.user_id,
                "username": submission.user.username,
                "solved": 0,
                "penalty": 0,
                "problems": {
                    r.problem_id: {
                        "solved": False,
                        "wrong": 0,
                        "minute": None,
                        "pending": False,
                    }
                    for r in problems
                },
            }
        row = rows[submission.user_id]
        cell = row["problems"].get(submission.problem_id)
        if cell is None or cell["solved"]:
            continue
        if submission.status != "FINISHED":
            cell["pending"] = True
        elif submission.verdict == "AC":
            cell["solved"] = True
            cell["pending"] = False
            cell["minute"] = max(
                0,
                int(
                    (
                        utc(submission.created_at) - utc(contest.start_time)
                    ).total_seconds()
                    // 60
                ),
            )
            row["solved"] += 1
            row["penalty"] += cell["minute"] + WRONG_PENALTY_MINUTES * cell["wrong"]
        elif submission.verdict in PENALIZED_VERDICTS:
            cell["wrong"] += 1
    ranked = sorted(
        rows.values(),
        key=lambda row: (
            -row["solved"],
            row["penalty"],
            row["username"].lower(),
            row["user_id"],
        ),
    )
    previous, rank = None, 0
    for index, row in enumerate(ranked, 1):
        row["performance"] = calculate_performance(
            [
                (p.problem.rating, row["problems"][p.problem_id]["solved"])
                for p in problems
            ]
        )
        score = (row["solved"], row["penalty"])
        if score != previous:
            rank = index
        row["rank"], previous = rank, score
    return ranked

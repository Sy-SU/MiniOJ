from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from minioj.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    __table_args__ = (
        Index("uq_users_username_lower", func.lower(username), unique=True),
    )
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    tokens: Mapped[list[ApiToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    submissions: Mapped[list[Submission]] = relationship(back_populates="user")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_preview: Mapped[str | None] = mapped_column(String(19))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="tokens")


class Problem(Base):
    __tablename__ = "problems"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(255))
    statement: Mapped[str] = mapped_column(Text)
    input_specification: Mapped[str] = mapped_column(Text, default="")
    output_specification: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    time_limit_ms: Mapped[int] = mapped_column(Integer, default=2000)
    memory_limit_mb: Mapped[int] = mapped_column(Integer, default=256)
    source: Mapped[str | None] = mapped_column(String(100))
    source_id: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    rating: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[str] = mapped_column(Text, default="")
    standard_source: Mapped[str | None] = mapped_column(Text)
    standard_sha256: Mapped[str | None] = mapped_column(String(64))
    standard_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    samples: Mapped[list[Sample]] = relationship(
        back_populates="problem", cascade="all, delete-orphan", order_by="Sample.order"
    )
    testcases: Mapped[list[TestCase]] = relationship(
        back_populates="problem",
        cascade="all, delete-orphan",
        order_by="TestCase.order",
    )
    submissions: Mapped[list[Submission]] = relationship(back_populates="problem")
    testcase_builds: Mapped[list[TestcaseBuild]] = relationship(
        back_populates="problem", cascade="all, delete-orphan"
    )


class Sample(Base):
    __tablename__ = "samples"
    __table_args__ = (
        UniqueConstraint("problem_id", "order"),
        Index("uq_samples_testcase_id", "testcase_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[str] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE")
    )
    testcase_id: Mapped[int | None] = mapped_column(
        ForeignKey("testcases.id", ondelete="SET NULL")
    )
    input: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[str] = mapped_column(Text, default="")
    order: Mapped[int] = mapped_column(Integer, default=1)

    problem: Mapped[Problem] = relationship(back_populates="samples")


class TestCase(Base):
    __tablename__ = "testcases"
    __table_args__ = (UniqueConstraint("problem_id", "order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[str] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(16), default="hidden")
    input_path: Mapped[str] = mapped_column(String(1000))
    output_path: Mapped[str] = mapped_column(String(1000))
    input_sha256: Mapped[str | None] = mapped_column(String(64))
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    problem: Mapped[Problem] = relationship(back_populates="testcases")


class TestcaseBuild(Base):
    __tablename__ = "testcase_builds"

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[str] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    testcase_type: Mapped[str] = mapped_column(String(16), default="hidden")
    standard_source: Mapped[str] = mapped_column(Text)
    standard_sha256: Mapped[str] = mapped_column(String(64))
    input_data: Mapped[str | None] = mapped_column(Text)
    generator_source: Mapped[str | None] = mapped_column(Text)
    case_count: Mapped[int] = mapped_column(Integer, default=1)
    base_seed: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    problem: Mapped[Problem] = relationship(back_populates="testcase_builds")


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__: ClassVar[dict[str, bool]] = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id"), index=True)
    problem_revision: Mapped[int] = mapped_column(Integer, server_default="1")
    language: Mapped[str] = mapped_column(String(20), default="cpp20")
    source_code: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    verdict: Mapped[str | None] = mapped_column(String(8), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    compile_result: Mapped[str | None] = mapped_column(Text)
    judge_result: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="submissions")
    problem: Mapped[Problem] = relationship(back_populates="submissions")

    @property
    def problem_warning(self) -> str | None:
        if self.problem.deleted_at is not None:
            return "This problem has been deleted. This submission is retained for reference."
        if self.problem_revision != self.problem.revision:
            return (
                "This problem has been modified since this submission. "
                "Its result may not match the current statement or testcases."
            )
        return None


@event.listens_for(Submission, "before_insert")
def _capture_problem_revision(_mapper, connection, submission: Submission) -> None:
    if submission.problem_revision is None:
        submission.problem_revision = (
            connection.scalar(
                select(Problem.revision).where(Problem.id == submission.problem_id)
            )
            or 1
        )

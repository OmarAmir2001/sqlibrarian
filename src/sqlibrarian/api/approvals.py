"""TA review of study plans (UC3 view, UC4 write: the only write in the system).

The write is one fixed, parameterised UPDATE (the model never writes SQL here), run as
sqlib_approver, which may change only plans.status / reviewed_by / reviewed_at, and
only for the TA's own advisees (row-level security), and only as that TA
(WITH CHECK reviewed_by = app.ta_id). Every decision is written to audit_log.
"""

from dataclasses import dataclass
from enum import StrEnum

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from sqlibrarian.api.identity import Identity
from sqlibrarian.helpers.enums import PlanStatus, Role


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


STATUS_FOR = {Decision.APPROVE: PlanStatus.APPROVED, Decision.REJECT: PlanStatus.REJECTED}


class ReviewError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PendingPlan:
    plan_id: int
    student_id: int
    student_name: str
    courses: list[str]
    flags: list[dict]


def _require_ta(identity: Identity) -> int:
    if identity.role is not Role.TA or identity.ta_id is None:
        raise ReviewError(403, "only teaching assistants review plans")
    return identity.ta_id


async def pending_plans(
    reader: AsyncConnectionPool, app: AsyncConnectionPool, identity: Identity
) -> list[PendingPlan]:
    ta_id = _require_ta(identity)
    async with reader.connection() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.ta_id', %s, true)", (str(ta_id),))
        cur = await conn.execute(
            "SELECT p.plan_id, p.student_id, s.name_en, array_agg(pc.course_code ORDER BY 1)"
            " FROM plans p JOIN students s USING (student_id)"
            " JOIN plan_courses pc USING (plan_id)"
            " WHERE p.status = %s GROUP BY p.plan_id, p.student_id, s.name_en ORDER BY p.plan_id",
            (PlanStatus.PENDING,),
        )
        plans = await cur.fetchall()
    ids = [p[0] for p in plans]
    async with app.connection() as conn:
        cur = await conn.execute(
            "SELECT plan_id, rule, details FROM plan_flags WHERE plan_id = ANY(%s)", (ids,)
        )
        flags = await cur.fetchall()
    by_plan: dict[int, list[dict]] = {}
    for pid, rule, details in flags:
        by_plan.setdefault(pid, []).append({"rule": rule, "details": details})
    return [PendingPlan(p[0], p[1], p[2], list(p[3]), by_plan.get(p[0], [])) for p in plans]


async def review(
    approver: AsyncConnectionPool,
    app: AsyncConnectionPool,
    identity: Identity,
    plan_id: int,
    decision: Decision,
) -> PlanStatus:
    ta_id = _require_ta(identity)
    status = STATUS_FOR[decision]
    async with approver.connection() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.ta_id', %s, true)", (str(ta_id),))
        cur = await conn.execute(
            "UPDATE plans SET status = %s, reviewed_by = %s, reviewed_at = now()"
            " WHERE plan_id = %s AND status = %s RETURNING plan_id",
            (status, ta_id, plan_id, PlanStatus.PENDING),
        )
        updated = await cur.fetchone()
        if updated is None:
            visible = await conn.execute("SELECT status FROM plans WHERE plan_id = %s", (plan_id,))
            row = await visible.fetchone()
            if row is None:  # not this TA's advisee (row-level security) or no such plan
                raise ReviewError(404, "no such plan among your advisees")
            raise ReviewError(409, f"the plan was already {row[0]}")
    async with app.connection() as conn:
        await conn.execute(
            "INSERT INTO audit_log (user_id, action, target, details) VALUES (%s, %s, %s, %s)",
            (identity.user_id, decision, f"plan:{plan_id}", Jsonb({"ta_id": ta_id})),
        )
    return status

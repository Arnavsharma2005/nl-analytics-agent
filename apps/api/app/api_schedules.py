"""/v1/schedules: a signed-in user's scheduled reports and alerts (app/schedules.py), run hourly by
app/jobs/schedules.py. Works with the sign-in cookie or an API key."""

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.problems import Problem
from app.schedules import PER_USER, Notifier, ScheduleRequestV1, Schedules, ScheduleV1, allowed_email
from app.usage import Visitor


def schedules_router(*, schedules: Callable[[], Schedules], notifier: Callable[[], Notifier],
                     identify: Callable[[Request], Visitor], email_allowlist: list[str], enabled: bool) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["v1"])

    def user_of(request: Request) -> int:
        if not enabled:
            raise Problem(404, "schedules_off", "Scheduled reports are not turned on here",
                          "Set SCHEDULES=true on the server, with email or the Telegram bot set up.")
        who = identify(request)
        if not who.user:
            raise Problem(401, "sign_in_required", "Sign in to schedule reports and alerts")
        return who.user

    @router.post("/schedules", status_code=201)
    def create_schedule(body: ScheduleRequestV1, request: Request) -> ScheduleV1:
        """Ask a question daily or weekly and send the answer by email or Telegram; or, as an alert, send only when
        its number crosses a threshold."""
        user = user_of(request)
        send = notifier()
        if body.channel == "email":
            if not send.email:
                raise Problem(422, "channel_off", "Email is not set up here", "Use Telegram instead.")
            if not allowed_email(body.to, email_allowlist):
                raise Problem(422, "recipient_not_allowed", "This address can't receive scheduled email here",
                              "The server only sends to addresses in SCHEDULE_EMAIL_ALLOWLIST.")
        if body.channel == "telegram" and not send.telegram:
            raise Problem(422, "channel_off", "The Telegram bot is not set up here", "Use email instead.")
        made = schedules().create(user, body)
        if made is None:
            raise Problem(409, "too_many_schedules", f"You can have {PER_USER} schedules",
                          "Delete one you no longer need first.")
        return made

    @router.get("/schedules")
    def list_schedules(request: Request) -> list[ScheduleV1]:
        """Your schedules, with when each runs next and how its last run went."""
        return schedules().list(user_of(request))

    @router.delete("/schedules/{schedule_id}", status_code=204)
    def delete_schedule(schedule_id: UUID, request: Request) -> Response:
        if not schedules().delete(schedule_id, user_of(request)):
            raise Problem(404, "schedule_not_found", "No such schedule")
        return Response(status_code=204)

    return router

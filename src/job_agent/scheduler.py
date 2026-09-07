from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from .models import (
    Schedule,
    ScheduleCreateRequest,
    ScheduleRunSummary,
    ScheduleUpdateRequest,
)
from .service import SearchService
from .utils import parse_duration_seconds


class ScheduleNotFoundError(Exception):
    """Raised when a schedule id doesn't exist."""


class _ScheduleState:
    def __init__(self, schedule: Schedule) -> None:
        self.schedule = schedule
        self.task: asyncio.Task | None = None


class JobScheduler:
    """Runs recurring searches against configured sources in the background.

    This is an in-process scheduler built on asyncio - no extra dependencies,
    no separate worker process. Schedules live in memory only, so they don't
    survive an app restart; if you need that, persist `list_schedules()` and
    recreate them in the app's `lifespan` startup.
    """

    def __init__(self, search_service: SearchService) -> None:
        self._service = search_service
        self._schedules: dict[str, _ScheduleState] = {}

    def create(self, request: ScheduleCreateRequest) -> Schedule:
        interval_seconds = parse_duration_seconds(request.interval)
        if interval_seconds is None:
            raise ValueError("interval must look like '30m', '1h', '2h', '1d', etc.")

        schedule = Schedule(
            id=uuid.uuid4().hex[:12],
            name=request.name,
            query=request.query,
            sources=request.sources,
            max_pages_per_source=request.max_pages_per_source,
            interval=request.interval,
            interval_seconds=interval_seconds,
            enabled=request.enabled,
            created_at=datetime.now(timezone.utc),
            next_run_at=datetime.now(timezone.utc) if request.enabled else None,
        )
        state = _ScheduleState(schedule)
        self._schedules[schedule.id] = state
        if request.enabled:
            self._start(state)
        return schedule

    def list_schedules(self) -> list[Schedule]:
        return [state.schedule for state in self._schedules.values()]

    def get(self, schedule_id: str) -> Schedule:
        state = self._schedules.get(schedule_id)
        if state is None:
            raise ScheduleNotFoundError(schedule_id)
        return state.schedule

    def update(self, schedule_id: str, request: ScheduleUpdateRequest) -> Schedule:
        state = self._schedules.get(schedule_id)
        if state is None:
            raise ScheduleNotFoundError(schedule_id)

        if request.interval is not None:
            interval_seconds = parse_duration_seconds(request.interval)
            if interval_seconds is None:
                raise ValueError("interval must look like '30m', '1h', '2h', '1d', etc.")
            state.schedule.interval = request.interval
            state.schedule.interval_seconds = interval_seconds

        if request.enabled is not None and request.enabled != state.schedule.enabled:
            state.schedule.enabled = request.enabled
            if request.enabled:
                self._start(state)
            else:
                self._stop(state)

        return state.schedule

    def delete(self, schedule_id: str) -> None:
        state = self._schedules.pop(schedule_id, None)
        if state is None:
            raise ScheduleNotFoundError(schedule_id)
        self._stop(state)

    async def run_now(self, schedule_id: str) -> Schedule:
        state = self._schedules.get(schedule_id)
        if state is None:
            raise ScheduleNotFoundError(schedule_id)
        await self._run_once(state)
        return state.schedule

    async def close(self) -> None:
        tasks = [state.task for state in self._schedules.values() if state.task is not None]
        for state in self._schedules.values():
            self._stop(state)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _start(self, state: _ScheduleState) -> None:
        if state.task is not None and not state.task.done():
            return
        state.task = asyncio.create_task(self._loop(state))
        state.schedule.next_run_at = datetime.now(timezone.utc)

    def _stop(self, state: _ScheduleState) -> None:
        if state.task is not None:
            state.task.cancel()
            state.task = None
        state.schedule.next_run_at = None

    async def _loop(self, state: _ScheduleState) -> None:
        try:
            while True:
                await self._run_once(state)
                state.schedule.next_run_at = datetime.now(timezone.utc) + timedelta(
                    seconds=state.schedule.interval_seconds
                )
                await asyncio.sleep(state.schedule.interval_seconds)
        except asyncio.CancelledError:
            pass

    async def _run_once(self, state: _ScheduleState) -> None:
        schedule = state.schedule
        started_at = datetime.now(timezone.utc)
        results = await self._service.search_all(
            schedule.query, schedule.sources, schedule.max_pages_per_source
        )
        finished_at = datetime.now(timezone.utc)
        jobs = [job for result in results for job in result.jobs]
        errors = {result.source: result.error for result in results if result.error}
        schedule.last_run = ScheduleRunSummary(
            started_at=started_at,
            finished_at=finished_at,
            jobs_found=len(jobs),
            errors=errors,
            jobs=jobs,
        )
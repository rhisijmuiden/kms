"""Background jobs: scheduled backups and the daily update check."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import backup, store, system

log = logging.getLogger(__name__)
_sched = None


def _hm(value, default="03:00"):
    try:
        h, m = (value or default).split(":")
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except ValueError:
        return 3, 0


def _update_check():
    if not store.get("update.auto_check"):
        return
    try:
        system.check_update(notify=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("update check failed: %s", exc)


def reload():
    """(Re)create jobs from the current settings."""
    if _sched is None:
        return
    for job in _sched.get_jobs():
        job.remove()
    s = store.section("backup")
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    fh, fm = _hm(s.get("full_time"))
    full = s.get("full_schedule")
    full_trigger = None
    if full == "daily":
        full_trigger = CronTrigger(hour=fh, minute=fm)
    elif full == "weekly":
        full_trigger = CronTrigger(day_of_week=days[int(s.get("full_weekday") or 6) % 7], hour=fh, minute=fm)
    elif full == "monthly":
        full_trigger = CronTrigger(day=1, hour=fh, minute=fm)
    if full_trigger:
        _sched.add_job(backup.run_safe, full_trigger, args=["full", "scheduled"], id="backup-full",
                       misfire_grace_time=3600, coalesce=True, max_instances=1)

    ih, im = _hm(s.get("inc_time"), "03:30")
    inc = s.get("inc_schedule")
    if inc == "hourly":
        _sched.add_job(backup.run_safe, CronTrigger(minute=im), args=["incremental", "scheduled"], id="backup-inc",
                       misfire_grace_time=1800, coalesce=True, max_instances=1)
    elif inc == "daily":
        _sched.add_job(backup.run_safe, CronTrigger(hour=ih, minute=im), args=["incremental", "scheduled"], id="backup-inc",
                       misfire_grace_time=3600, coalesce=True, max_instances=1)

    _sched.add_job(_update_check, CronTrigger(hour=6, minute=17), id="update-check", coalesce=True)
    _sched.add_job(_blocked_check, CronTrigger(minute="*/15"), id="blocked-check", coalesce=True, max_instances=1)


def _blocked_check():
    from . import network
    try:
        network.check_blocked()
    except Exception as exc:  # noqa: BLE001 - a background job must never crash the scheduler
        store.log_event("warning", f"Blocked-attempts check failed: {exc}")


def start():
    global _sched
    if _sched is not None:
        return
    _sched = BackgroundScheduler(daemon=True)
    _sched.start()
    reload()


def jobs():
    if _sched is None:
        return []
    return [{"id": j.id, "next": j.next_run_time} for j in _sched.get_jobs()]

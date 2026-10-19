"""Atomic database admission shared across API instances and worker processes."""

from datetime import datetime, timezone
import threading
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.database import begin_write
from app.services.runtime_coordination import LeaseLost, LeaseStore, database_now


class DatabaseRequestLease:
    def __init__(self, limiter, store, token, daily_remaining, *, ttl=60):
        self.limiter, self.store, self.token = limiter, store, token
        self.daily_remaining, self.ttl = max(0, daily_remaining), ttl
        self.stopped, self.lost = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True, name="admission-heartbeat")

    def _heartbeat(self):
        while not self.stopped.wait(self.ttl / 3):
            try:
                with self.store.connection() as db:
                    begin_write(db)
                    now = database_now(db)
                    result = db.execute("""UPDATE agent_request_slots SET expires_at=?
                        WHERE token=? AND expires_at>?""", (now + self.ttl, self.token, now))
                    if result.rowcount != 1:
                        self.lost.set()
                        return
            except Exception:
                self.lost.set()
                return

    def assert_current(self, db):
        row = db.execute("SELECT expires_at FROM agent_request_slots WHERE token=?", (self.token,)).fetchone()
        if self.lost.is_set() or row is None or row[0] <= database_now(db):
            raise LeaseLost("Global Agent execution slot expired")

    def ensure_active(self):
        with self.store.connection() as db:
            self.assert_current(db)

    def release(self):
        self.stopped.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)
        with self.store.connection() as db:
            begin_write(db)
            db.execute("DELETE FROM agent_request_slots WHERE token=?", (self.token,))
        with self.limiter._lock:
            self.limiter._leases.pop(self.token, None)


class DatabaseAgentRateLimiter:
    """Admission serializes quota reservation, including daily count races."""

    def __init__(self, database_path=None):
        self.database_path = database_path
        self._lock = threading.Lock()
        self._leases = {}

    def acquire(self, owner_id, ip_address, *, daily_request_count, daily_cost_usd=0.0):
        from app.services.agent_rate_limit import (
            AgentRateLimitError, AgentRequestLease, load_agent_rate_limit_config,
        )
        config = load_agent_rate_limit_config()
        if not config.enabled:
            return AgentRequestLease(self, owner_id, enabled=False, daily_remaining=config.requests_per_day)
        store = LeaseStore(self.database_path)
        rejection = None
        token = uuid4().hex
        with store.connection() as db:
            begin_write(db)
            now = database_now(db)
            local = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
            day_key = local.date().isoformat()
            day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
            db.execute("DELETE FROM agent_request_attempts WHERE attempted_at<=?", (now - 60,))
            db.execute("DELETE FROM agent_request_slots WHERE expires_at<=?", (now,))
            owner = db.execute("""SELECT COUNT(*), MIN(attempted_at) FROM agent_request_attempts
                WHERE owner_id=?""", (owner_id,)).fetchone()
            ip = db.execute("""SELECT COUNT(*), MIN(attempted_at) FROM agent_request_attempts
                WHERE ip_address=?""", (ip_address,)).fetchone()
            if owner[0] >= config.requests_per_minute:
                rejection = ("owner_minute_limit", "提问过于频繁，请稍后再试。", int(61 - now + owner[1]))
            elif ip[0] >= config.ip_requests_per_minute:
                rejection = ("ip_minute_limit", "当前网络请求过于频繁，请稍后再试。", int(61 - now + ip[1]))
            else:
                # Commit rejected concurrency/quota attempts too, matching the existing guard.
                db.execute("""INSERT INTO agent_request_attempts(attempt_id,owner_id,ip_address,attempted_at)
                    VALUES(?,?,?,?)""", (token, owner_id, ip_address, now))
                usage = db.execute("""SELECT COUNT(*), COALESCE(SUM(estimated_cost_usd),0)
                    FROM agent_usage_events WHERE owner_id=? AND started_at>=? AND status!='rejected'""",
                    (owner_id, day_start)).fetchone()
                reserved = db.execute("""SELECT request_count FROM agent_request_days
                    WHERE owner_id=? AND day_key=?""", (owner_id, day_key)).fetchone()
                used = max(daily_request_count, usage[0], reserved[0] if reserved else 0)
                cost = max(daily_cost_usd, float(usage[1]))
                active_owner = db.execute("SELECT COUNT(*) FROM agent_request_slots WHERE owner_id=?", (owner_id,)).fetchone()[0]
                active_global = db.execute("SELECT COUNT(*) FROM agent_request_slots").fetchone()[0]
                if used >= config.requests_per_day:
                    rejection = ("owner_daily_limit", "今天的 Agent 使用次数已达上限，请明天再试。", 3600)
                elif config.daily_cost_budget_usd > 0 and cost >= config.daily_cost_budget_usd:
                    rejection = ("owner_daily_cost_limit", "今天的 Agent 成本额度已用完，请明天再试。", 3600)
                elif active_owner >= config.max_concurrent_per_owner:
                    rejection = ("owner_concurrency_limit", "上一条问题仍在处理中，请等待回答完成。", 2)
                elif active_global >= config.max_concurrent_global:
                    rejection = ("global_concurrency_limit", "Agent 当前任务较多，请稍后再试。", 3)
                else:
                    db.execute("""INSERT INTO agent_request_days(owner_id,day_key,request_count)
                        VALUES(?,?,?) ON CONFLICT(owner_id,day_key)
                        DO UPDATE SET request_count=excluded.request_count""", (owner_id, day_key, used + 1))
                    db.execute("INSERT INTO agent_request_slots(token,owner_id,expires_at) VALUES(?,?,?)",
                               (token, owner_id, now + 60))
        # Raising outside the transaction preserves short-window rejected attempts.
        if rejection:
            raise AgentRateLimitError(*rejection)
        lease = DatabaseRequestLease(self, store, token, config.requests_per_day - used - 1)
        with self._lock:
            self._leases[token] = lease
        try:
            lease.thread.start()
        except Exception:
            lease.release()
            raise
        return lease

    def snapshot(self):
        store = LeaseStore(self.database_path)
        with store.connection() as db:
            now = database_now(db)
            row = db.execute("""SELECT COUNT(*),COUNT(DISTINCT owner_id)
                FROM agent_request_slots WHERE expires_at>?""", (now,)).fetchone()
            return {"active_global": row[0], "active_owner_count": row[1]}

    def reset(self):
        """Release this limiter's slots only; other instances and quotas survive."""
        with self._lock:
            leases = list(self._leases.values())
        for lease in leases:
            lease.release()

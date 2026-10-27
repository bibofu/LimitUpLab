"""Explicit owner memories with CAS, tombstones and fenced operation receipts."""

from contextlib import contextmanager
from datetime import date
from hashlib import sha256
import json
from uuid import uuid4

from app.database import begin_write, connect, connect_readonly, initialize_database
from app.memory_models import (
    MemoryMutationResult,
    MemoryRecord,
    MemoryWrite,
    memory_scope_key,
)
from app.services.runtime_coordination import LeaseLost

MEMORY_TYPES = {"preference", "research_context", "research_archive"}
ARCHIVE_ANSWER_MAX_BYTES = 64 * 1024
ARCHIVE_METADATA_MAX_BYTES = 1024 * 1024


class MemoryConflict(ValueError):
    """A revision or operation identity conflicts with the current record."""


class MemoryNotFound(KeyError):
    """The requested memory is unavailable to this owner."""


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _content_hash(value):
    return sha256(value.encode("utf-8")).hexdigest() if value else None


def _normalized_memory_text(value):
    return " ".join((value or "").strip().split()).casefold()


def _same_effective_content(row, write):
    """Compare the behavior a candidate would activate, not its wording.

    Structured display policies are authoritative: a rephrased sentence with
    the same columns and scope is a no-op. Free-text memories without
    structured fields compare normalized prose.
    """

    if row["scope_type"] != write.scope_type:
        return False
    if (row["canonical_key"] or row["memory_key"]) != (write.canonical_key or write.key):
        return False
    if json.loads(row["scope_json"]) != write.scope:
        return False
    if json.loads(row["structured_json"]) != write.structured:
        return False
    if row["expires_at"] != write.expires_at:
        return False
    if write.structured:
        return True
    return _normalized_memory_text(row["value"]) == _normalized_memory_text(write.value)


def _record(row):
    item = dict(row)
    item["key"] = item.pop("memory_key")
    item["topics"] = json.loads(item.pop("topics_json"))
    item["metadata"] = json.loads(item.pop("metadata_json"))
    item["scope"] = json.loads(item.pop("scope_json", "{}") or "{}")
    item["structured"] = json.loads(item.pop("structured_json", "{}") or "{}")
    return MemoryRecord.model_validate(item)


def context_identity_in_connection(db, owner_id, now=None):
    """Reuse the caller's snapshot/write lock when validating recalled memory."""
    timestamp = _effective_now(db, now)
    version = db.execute("SELECT revision FROM memory_owner_versions WHERE owner_id=?", (owner_id,)).fetchone()
    active = db.execute("""SELECT memory_id,revision FROM long_term_memories WHERE owner_id=?
        AND status='active' AND deleted_at IS NULL
        AND (expires_at IS NULL OR expires_at>?) ORDER BY memory_id""",
        (owner_id, timestamp)).fetchall()
    return {"owner_revision": int(version[0]) if version else 0,
            "context_fingerprint": sha256(_encode([[row[0], row[1]] for row in active]).encode()).hexdigest()}


def _effective_now(db, now=None):
    if now is not None:
        if now.tzinfo is None:
            raise ValueError("Memory time must include a timezone")
        return now
    return db.execute("SELECT clock_timestamp()").fetchone()[0]


def dependencies_unchanged_in_connection(db, owner_id, dependencies, now=None):
    """Check only the effective memories actually used by one answer.

    Management counters, candidates and unrelated memories are intentionally
    absent from this check.
    """

    if not dependencies:
        return True
    if not isinstance(dependencies, (list, tuple)) or len(dependencies) > 200:
        return False
    normalized = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            return False
        memory_id = dependency.get("memory_id")
        revision = dependency.get("effective_revision")
        if not isinstance(memory_id, str) or not memory_id:
            return False
        if not isinstance(revision, int) or revision < 1:
            return False
        normalized.append((
            memory_id,
            revision,
            str(dependency.get("scope_key") or "global"),
        ))
    if len({item[0] for item in normalized}) != len(normalized):
        return False
    timestamp = _effective_now(db, now)
    placeholders = ",".join("?" for _ in normalized)
    rows = db.execute(f"""SELECT memory_id,revision,scope_key,status,deleted_at,expires_at
        FROM long_term_memories WHERE owner_id=? AND memory_id IN ({placeholders})""",
        (owner_id, *(item[0] for item in normalized))).fetchall()
    current = {row["memory_id"]: row for row in rows}
    for memory_id, revision, scope_key in normalized:
        row = current.get(memory_id)
        if row is None:
            return False
        if row["status"] != "active" or row["deleted_at"] is not None:
            return False
        if row["expires_at"] is not None and row["expires_at"] <= timestamp:
            return False
        if int(row["revision"]) != revision or row["scope_key"] != scope_key:
            return False
    return True


class LongTermMemoryRepository:
    def __init__(self, database_url=None):
        self.database_url = database_url

    @contextmanager
    def _connection(self, *, readonly=False):
        db = (connect_readonly if readonly else connect)(self.database_url)
        try:
            initialize_database(db)
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _owned(db, owner_id, memory_id, now=None):
        timestamp = _effective_now(db, now)
        return db.execute("""SELECT *,COALESCE(expires_at<=?,FALSE) AS expired
            FROM long_term_memories WHERE owner_id=? AND memory_id=?""", (timestamp, owner_id, memory_id)).fetchone()

    @staticmethod
    def _owner_revision(db, owner_id):
        row = db.execute("SELECT revision FROM memory_owner_versions WHERE owner_id=?", (owner_id,)).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _lock_identity(db, owner_id, memory_type, canonical_key, scope_key):
        """Serialize consolidation, confirmation and explicit writes for one identity."""

        db.execute("""SELECT pg_advisory_xact_lock(hashtextextended(?,0))""",
                   (f"{owner_id}\x1f{memory_type}\x1f{canonical_key}\x1f{scope_key}",))

    def get_owner_revision(self, owner_id):
        with self._connection(readonly=True) as db:
            return self._owner_revision(db, owner_id)

    def dependencies_unchanged(self, owner_id, dependencies, now=None):
        with self._connection(readonly=True) as db:
            return dependencies_unchanged_in_connection(
                db, owner_id, dependencies, now,
            )

    def get(self, owner_id, memory_id, *, include_inactive=False):
        with self._connection(readonly=True) as db:
            row = self._owned(db, owner_id, memory_id)
            if row is None or (
                not include_inactive
                and (row["deleted_at"] or row["expired"] or row["status"] != "active")
            ):
                return None
            return _record(row)

    def get_by_key(self, owner_id, memory_type, key, *, include_inactive=False):
        with self._connection(readonly=True) as db:
            row = db.execute("""SELECT *,COALESCE(expires_at<=clock_timestamp(),FALSE) AS expired
                FROM long_term_memories WHERE owner_id=? AND memory_type=? AND memory_key=?
                ORDER BY CASE status
                    WHEN 'active' THEN 0 WHEN 'candidate' THEN 1 WHEN 'deleted' THEN 2
                    ELSE 3 END,updated_at DESC,memory_id LIMIT 1""",
                (owner_id, memory_type, key)).fetchone()
            if row is None or (
                not include_inactive
                and (row["deleted_at"] or row["expired"] or row["status"] != "active")
            ):
                return None
            return _record(row)

    def read_context(self, owner_id, *, memory_types=None, exact_keys=None, topics=None, limit=100, now=None):
        """Read the revision and active memories in one repeatable-read snapshot."""
        selected = tuple(memory_types) if memory_types is not None else tuple(sorted(MEMORY_TYPES))
        if not set(selected).issubset(MEMORY_TYPES) or not 1 <= limit <= 200:
            raise ValueError("Invalid memory filter or limit")
        if now is not None and now.tzinfo is None:
            raise ValueError("Memory time must include a timezone")
        conditions = ["owner_id=?", "status='active'", "deleted_at IS NULL"]
        params = [owner_id]
        if selected:
            conditions.append("memory_type IN (" + ",".join("?" for _ in selected) + ")")
            params.extend(selected)
        else:
            conditions.append("FALSE")
        if exact_keys is not None:
            keys = tuple(exact_keys)
            conditions.append("memory_key IN (" + ",".join("?" for _ in keys) + ")" if keys else "FALSE")
            params.extend(keys)
        if topics:
            conditions.append("EXISTS (SELECT 1 FROM jsonb_array_elements_text(topics_json::jsonb) AS topic(value) "
                              "WHERE topic.value IN (" + ",".join("?" for _ in topics) + "))")
            params.extend(topics)
        with self._connection(readonly=True) as db:
            timestamp = _effective_now(db, now)
            identity = context_identity_in_connection(db, owner_id, timestamp)
            conditions.append("(expires_at IS NULL OR expires_at>?)")
            params.append(timestamp)
            rows = db.execute("SELECT *,FALSE AS expired FROM long_term_memories WHERE " +
                              " AND ".join(conditions) + " ORDER BY updated_at DESC,memory_id LIMIT ?",
                              (*params, limit)).fetchall()
            return {**identity, "as_of": timestamp.isoformat(),
                    "items": [_record(row).model_dump(mode="json") for row in rows]}

    def list_active(self, owner_id, *, memory_types=None, exact_keys=None, topics=None, limit=100, now=None):
        return self.read_context(owner_id, memory_types=memory_types, exact_keys=exact_keys,
                                 topics=topics, limit=limit, now=now)["items"]

    @staticmethod
    def _assert_control(db, owner_id, control, source_user_message=None):
        if control is None:
            return
        if control.execution is None or control.row["owner_id"] != owner_id:
            raise LeaseLost("Memory write requires the owner's current execution")
        control.lifecycle.services.coordinator.assert_current(
            db, control.row["run_id"], control.execution, control.admission,
        )
        row = db.execute("""SELECT request_json FROM react_runs WHERE run_id=? AND owner_id=?
            AND active=1 AND cancelled=0""", (control.row["run_id"], owner_id)).fetchone()
        if row is None:
            raise LeaseLost("Memory write run is no longer active")
        if source_user_message is not None and source_user_message != json.loads(row["request_json"])["message"].strip():
            raise ValueError("Memory source must be the current user's explicit message")

    def _receipt(self, db, owner_id, operation_id, fingerprint):
        row = db.execute("SELECT * FROM memory_operation_receipts WHERE owner_id=? AND operation_id=?",
                         (owner_id, operation_id)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != fingerprint:
            raise MemoryConflict("Idempotency key was already used for a different memory operation")
        now = _effective_now(db)
        current = self._owned(db, owner_id, row["memory_id"], now)
        identity = context_identity_in_connection(db, owner_id, now)
        return MemoryMutationResult(current=_record(current), operation_revision=row["result_revision"],
                                    **identity, replayed=True)

    @staticmethod
    def _source(db, owner_id, write):
        if write.source_run_id:
            source = db.execute("SELECT request_json,response_json FROM react_runs WHERE run_id=? AND owner_id=?",
                                (write.source_run_id, owner_id)).fetchone()
            if source is None:
                raise ValueError("Memory source run is unavailable to this owner")
        else:
            source = None
        if write.memory_type == "research_archive":
            if not source or not source["response_json"]:
                raise ValueError("Research archives require a committed source run")
            return _archive_from_response(write, json.loads(source["request_json"]), json.loads(source["response_json"]))
        return write

    @staticmethod
    def _fingerprint(operation, payload, operation_id):
        if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 200:
            raise ValueError("A bounded explicit operation id is required")
        return sha256(_encode([operation, payload]).encode()).hexdigest()

    def _finish(self, db, owner_id, operation_id, fingerprint, operation, memory_id, *, changed=True):
        if changed:
            db.execute("""INSERT INTO memory_owner_versions(owner_id,revision) VALUES(?,1)
                ON CONFLICT(owner_id) DO UPDATE SET revision=memory_owner_versions.revision+1""", (owner_id,))
        now = _effective_now(db)
        current = _record(self._owned(db, owner_id, memory_id, now))
        identity = context_identity_in_connection(db, owner_id, now)
        db.execute("""INSERT INTO memory_operation_receipts
            (owner_id,operation_id,request_hash,memory_id,operation,result_revision) VALUES(?,?,?,?,?,?)""",
            (owner_id, operation_id, fingerprint, memory_id, operation, current.revision))
        return MemoryMutationResult(current=current, operation_revision=current.revision,
                                    **identity)

    def put(self, owner_id, write, operation_id, *, control=None, expected_owner_revision=None):
        from app.agents.memory_policy import validate_memory_write
        write = MemoryWrite.model_validate(write)
        write = validate_memory_write(write, owner_id=owner_id, source="agent" if control else "api")
        fingerprint = self._fingerprint("put", [write.model_dump(mode="json"), expected_owner_revision], operation_id)
        with self._connection() as db:
            begin_write(db)
            self._assert_control(db, owner_id, control, write.source_user_message)
            replay = self._receipt(db, owner_id, operation_id, fingerprint)
            if replay is not None:
                return replay
            self._check_owner_revision(db, owner_id, expected_owner_revision)
            write = self._source(db, owner_id, write)
            scope_json = _encode(write.scope)
            scope_key = memory_scope_key(write.scope_type, write.scope)
            canonical_key = write.canonical_key or write.key
            self._lock_identity(db, owner_id, write.memory_type, canonical_key, scope_key)
            row = db.execute("""SELECT * FROM long_term_memories
                WHERE owner_id=? AND memory_type=? AND scope_key=?
                  AND COALESCE(canonical_key,memory_key)=?
                  AND status IN ('active','deleted','expired','revoked')
                ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'deleted' THEN 1 ELSE 2 END,
                    updated_at DESC,memory_id LIMIT 1 FOR UPDATE""",
                (owner_id, write.memory_type, scope_key, canonical_key)).fetchone()
            if (row is None and write.expected_revision != 0) or (row and row["revision"] != write.expected_revision):
                raise MemoryConflict("Memory revision changed; reload the current record")
            if row and row["deleted_at"] and not write.restore_deleted:
                raise MemoryConflict("Deleted memory requires an explicit restore and its current revision")
            if row is None and write.restore_deleted:
                raise MemoryConflict("Only a deleted memory can be restored")
            # An explicit write supersedes any pending automatic proposal for the
            # same identity. Candidate rows are not effective memories.
            db.execute("""UPDATE long_term_memories SET status='superseded',
                revision=revision+1,updated_at=clock_timestamp()
                WHERE owner_id=? AND memory_type=? AND scope_key=?
                  AND COALESCE(canonical_key,memory_key)=?
                  AND status='candidate' AND deleted_at IS NULL""",
                (owner_id, write.memory_type, scope_key, canonical_key))
            memory_id = row["memory_id"] if row else "memory_" + uuid4().hex
            structured_json = _encode(write.structured)
            source_quote = write.source_quote
            content_hash = _content_hash(write.value)
            values = (
                write.value, _encode(write.topics), _encode(write.metadata),
                scope_json, scope_key, canonical_key, structured_json,
                source_quote, content_hash, write.expires_at,
                write.source_run_id, write.source_user_message,
            )
            if row:
                same = (
                    not row["deleted_at"]
                    and row["status"] == "active"
                    and row["memory_key"] == write.key
                    and row["value"] == write.value
                    and json.loads(row["topics_json"]) == write.topics
                    and json.loads(row["metadata_json"]) == write.metadata
                    and json.loads(row["scope_json"]) == write.scope
                    and row["scope_type"] == write.scope_type
                    and row["canonical_key"] == canonical_key
                    and json.loads(row["structured_json"]) == write.structured
                    and row["source_quote"] == source_quote
                    and row["expires_at"] == write.expires_at
                )
                if same:
                    self._link_exception_relations(
                        db, owner_id, memory_id, write.memory_type,
                        canonical_key, write.scope_type,
                    )
                    return self._finish(db, owner_id, operation_id, fingerprint, "put", memory_id, changed=False)
                db.execute("""UPDATE long_term_memories SET memory_key=?,value=?,topics_json=?,
                    metadata_json=?,scope_json=?,scope_key=?,canonical_key=?,structured_json=?,
                    source_quote=?,content_hash=?,expires_at=?,source_run_id=?,source_user_message=?,
                    status='active',source_kind='explicit',candidate_action='upsert',
                    activation_kind='manual',activation_reason=NULL,
                    deleted_at=NULL,supersedes_memory_id=NULL,supersedes_revision=NULL,
                    revision=revision+1,updated_at=clock_timestamp()
                    WHERE owner_id=? AND memory_id=? AND revision=?""",
                    (write.key, *values, owner_id, memory_id, write.expected_revision))
            else:
                db.execute("""INSERT INTO long_term_memories
                    (memory_id,owner_id,memory_type,memory_key,revision,value,topics_json,metadata_json,
                     scope_json,scope_key,canonical_key,structured_json,source_quote,content_hash,
                     expires_at,source_run_id,source_user_message,scope_type,status,source_kind,
                     candidate_action,activation_kind)
                    VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,'active','explicit',
                           'upsert','manual')""",
                    (memory_id, owner_id, write.memory_type, write.key, *values,
                     write.scope_type))
            self._link_exception_relations(
                db, owner_id, memory_id, write.memory_type, canonical_key, write.scope_type,
            )
            return self._finish(db, owner_id, operation_id, fingerprint, "put", memory_id)

    def consolidate_candidate(
        self, owner_id, write, operation_id, *, source_kind="auto", action="upsert",
    ):
        """Merge one automatic proposal with the active identity and open candidate.

        The candidate is never activated here. A changed active identity produces
        an ``update`` candidate carrying the exact target revision; a repeated
        proposal is a no-op, a newer differing proposal supersedes the prior
        pending candidate, and an explicit deletion becomes a targeted remove
        candidate.
        """

        from app.agents.memory_policy import (
            FORBIDDEN_MEMORY_TERMS,
            validate_memory_delete_target,
            validate_memory_write,
        )
        if action not in {"upsert", "delete"}:
            raise ValueError("Unsupported candidate action")
        write = MemoryWrite.model_validate(write)
        if action == "upsert":
            write = validate_memory_write(write, owner_id=owner_id, source="agent")
        else:
            validate_memory_delete_target(write.memory_type, write.key)
            if not write.value or any(
                term in write.value.casefold() for term in FORBIDDEN_MEMORY_TERMS
            ):
                raise ValueError("Deletion candidate contains forbidden content")
            write = write.model_copy(update={"structured": {}})
        fingerprint = self._fingerprint(
            "put", [action, write.model_dump(mode="json"), None], operation_id,
        )
        scope_json = _encode(write.scope)
        scope_key = memory_scope_key(write.scope_type, write.scope)
        canonical_key = write.canonical_key or write.key
        with self._connection() as db:
            begin_write(db)
            replay = self._receipt(db, owner_id, operation_id, fingerprint)
            if replay is not None:
                return {
                    "decision": "replay",
                    "candidate_id": None,
                    "target_memory_id": replay.current.memory_id,
                    "superseded_candidate_id": None,
                }
            self._lock_identity(db, owner_id, write.memory_type, canonical_key, scope_key)
            now = _effective_now(db)
            active = db.execute("""SELECT * FROM long_term_memories
                WHERE owner_id=? AND memory_type=? AND scope_key=?
                  AND COALESCE(canonical_key,memory_key)=?
                  AND status='active' AND deleted_at IS NULL
                ORDER BY updated_at DESC,memory_id LIMIT 1 FOR UPDATE""",
                (owner_id, write.memory_type, scope_key, canonical_key)).fetchone()
            if active is not None and active["expires_at"] is not None and active["expires_at"] <= now:
                db.execute("""UPDATE long_term_memories SET status='expired',
                    revision=revision+1,updated_at=clock_timestamp()
                    WHERE owner_id=? AND memory_id=?""",
                    (owner_id, active["memory_id"]))
                active = None
            pending = db.execute("""SELECT * FROM long_term_memories
                WHERE owner_id=? AND memory_type=? AND scope_key=?
                  AND COALESCE(canonical_key,memory_key)=?
                  AND status='candidate' AND deleted_at IS NULL
                ORDER BY created_at DESC,memory_id LIMIT 1 FOR UPDATE""",
                (owner_id, write.memory_type, scope_key, canonical_key)).fetchone()
            if action == "delete":
                if active is None:
                    return {
                        "decision": "noop",
                        "candidate_id": None,
                        "target_memory_id": None,
                        "superseded_candidate_id": None,
                    }
                target_id = active["memory_id"]
                target_revision = int(active["revision"])
                if (pending is not None and pending["candidate_action"] == "delete"
                        and pending["supersedes_memory_id"] == target_id):
                    changed_target = pending["supersedes_revision"] != target_revision
                    if changed_target:
                        db.execute("""UPDATE long_term_memories
                            SET supersedes_revision=?,revision=revision+1,
                                updated_at=clock_timestamp()
                            WHERE owner_id=? AND memory_id=? AND status='candidate'""",
                            (target_revision, owner_id, pending["memory_id"]))
                    self._finish(db, owner_id, operation_id, fingerprint, "put",
                                 pending["memory_id"], changed=False)
                    return {
                        "decision": "refresh" if changed_target else "noop",
                        "candidate_id": pending["memory_id"],
                        "target_memory_id": target_id,
                        "superseded_candidate_id": None,
                    }
                replaced_candidate = pending["memory_id"] if pending is not None else None
                if replaced_candidate is not None:
                    self._supersede_candidate(db, owner_id, replaced_candidate)
                memory_id = "memory_" + uuid4().hex
                db.execute("""INSERT INTO long_term_memories
                    (memory_id,owner_id,memory_type,memory_key,revision,value,topics_json,
                     metadata_json,scope_json,scope_key,canonical_key,structured_json,
                     source_quote,content_hash,expires_at,source_run_id,source_user_message,
                     scope_type,status,source_kind,candidate_action,
                     supersedes_memory_id,supersedes_revision)
                    VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,'candidate',?,'delete',?,?)""",
                    (memory_id, owner_id, write.memory_type, write.key, write.value,
                     _encode(write.topics), _encode(write.metadata), scope_json, scope_key,
                     canonical_key, "{}", write.source_quote, _content_hash(write.value),
                     write.expires_at, write.source_run_id, write.source_user_message,
                     write.scope_type, source_kind, target_id, target_revision))
                if replaced_candidate is not None:
                    self._relation(db, owner_id, memory_id, replaced_candidate, "supersedes",
                                   "deletion proposal replaced an older candidate")
                self._finish(db, owner_id, operation_id, fingerprint, "put",
                             memory_id, changed=False)
                return {
                    "decision": "delete",
                    "candidate_id": memory_id,
                    "target_memory_id": target_id,
                    "superseded_candidate_id": replaced_candidate,
                }
            if active is not None and _same_effective_content(active, write):
                superseded_candidate = None
                if pending is not None:
                    superseded_candidate = pending["memory_id"]
                    self._supersede_candidate(db, owner_id, pending["memory_id"])
                self._finish(db, owner_id, operation_id, fingerprint, "put",
                             active["memory_id"], changed=False)
                return {
                    "decision": "noop",
                    "candidate_id": None,
                    "target_memory_id": active["memory_id"],
                    "superseded_candidate_id": superseded_candidate,
                }
            target_id = active["memory_id"] if active is not None else None
            target_revision = int(active["revision"]) if active is not None else None
            if (pending is not None and pending["candidate_action"] == "upsert"
                    and _same_effective_content(pending, write)):
                changed_target = (
                    pending["supersedes_memory_id"] != target_id
                    or pending["supersedes_revision"] != target_revision
                )
                if changed_target:
                    db.execute("""UPDATE long_term_memories
                        SET supersedes_memory_id=?,supersedes_revision=?,
                            revision=revision+1,updated_at=clock_timestamp()
                        WHERE owner_id=? AND memory_id=? AND status='candidate'""",
                        (target_id, target_revision, owner_id, pending["memory_id"]))
                self._finish(db, owner_id, operation_id, fingerprint, "put",
                             pending["memory_id"], changed=False)
                return {
                    "decision": "refresh" if changed_target else "noop",
                    "candidate_id": pending["memory_id"],
                    "target_memory_id": target_id,
                    "superseded_candidate_id": None,
                }
            replaced_candidate = pending["memory_id"] if pending is not None else None
            if replaced_candidate is not None:
                self._supersede_candidate(db, owner_id, replaced_candidate)
            memory_id = "memory_" + uuid4().hex
            db.execute("""INSERT INTO long_term_memories
                (memory_id,owner_id,memory_type,memory_key,revision,value,topics_json,metadata_json,
                 scope_json,scope_key,canonical_key,structured_json,source_quote,content_hash,
                 expires_at,source_run_id,source_user_message,scope_type,status,source_kind,
                 candidate_action,supersedes_memory_id,supersedes_revision)
                VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,'candidate',?,'upsert',?,?)""",
                (memory_id, owner_id, write.memory_type, write.key, write.value,
                 _encode(write.topics), _encode(write.metadata), scope_json, scope_key,
                 canonical_key, _encode(write.structured), write.source_quote,
                 _content_hash(write.value), write.expires_at, write.source_run_id,
                 write.source_user_message, write.scope_type, source_kind,
                 target_id, target_revision))
            if replaced_candidate is not None:
                self._relation(db, owner_id, memory_id, replaced_candidate, "supersedes",
                               "newer automatic candidate replaced an older proposal")
            self._finish(db, owner_id, operation_id, fingerprint, "put",
                         memory_id, changed=False)
            return {
                "decision": "update" if active is not None else "add",
                "candidate_id": memory_id,
                "target_memory_id": target_id,
                "superseded_candidate_id": replaced_candidate,
            }

    @staticmethod
    def _supersede_candidate(db, owner_id, memory_id):
        db.execute("""UPDATE long_term_memories SET status='superseded',
            revision=revision+1,updated_at=clock_timestamp()
            WHERE owner_id=? AND memory_id=? AND status='candidate'""",
            (owner_id, memory_id))

    @staticmethod
    def _relation(db, owner_id, from_memory_id, to_memory_id, relation, reason):
        db.execute("""INSERT INTO memory_relations
            (relation_id,owner_id,from_memory_id,to_memory_id,relation,reason)
            VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
            ("relation_" + uuid4().hex, owner_id, from_memory_id, to_memory_id,
             relation, reason))

    @classmethod
    def _link_exception_relations(
        cls, db, owner_id, memory_id, memory_type, canonical_key, scope_type,
    ):
        """Record a scoped memory as an exception to its global same-key setting."""

        if scope_type == "global":
            children = db.execute("""SELECT memory_id FROM long_term_memories
                WHERE owner_id=? AND memory_type=? AND scope_type<>'global'
                  AND COALESCE(canonical_key,memory_key)=?
                  AND status='active' AND deleted_at IS NULL AND memory_id<>?""",
                (owner_id, memory_type, canonical_key, memory_id)).fetchall()
            for child in children:
                cls._relation(db, owner_id, child["memory_id"], memory_id,
                              "exception_to", "scoped memory overrides the global setting")
            return
        parent = db.execute("""SELECT memory_id FROM long_term_memories
            WHERE owner_id=? AND memory_type=? AND scope_type='global'
              AND COALESCE(canonical_key,memory_key)=?
              AND status='active' AND deleted_at IS NULL AND memory_id<>?
            ORDER BY updated_at DESC,memory_id LIMIT 1""",
            (owner_id, memory_type, canonical_key, memory_id)).fetchone()
        if parent is not None:
            cls._relation(db, owner_id, memory_id, parent["memory_id"],
                          "exception_to", "scoped memory overrides the global setting")

    def list_candidates(self, owner_id, *, limit=100):
        with self._connection(readonly=True) as db:
            rows = db.execute("""SELECT *,COALESCE(expires_at<=clock_timestamp(),FALSE) AS expired
                FROM long_term_memories WHERE owner_id=? AND status='candidate'
                AND deleted_at IS NULL ORDER BY created_at DESC,memory_id LIMIT ?""",
                (owner_id, max(1, min(limit, 200)))).fetchall()
            return [_record(row).model_dump(mode="json") for row in rows]

    def confirm_candidate(
        self, owner_id, memory_id, operation_id, *,
        activation_kind="manual", activation_reason=None,
    ):
        """Atomically activate a candidate and supersede its exact target."""

        if activation_kind not in {"manual", "safe_auto"}:
            raise ValueError("Unsupported memory activation kind")
        if activation_reason is not None:
            activation_reason = str(activation_reason)[:500]
        fingerprint = self._fingerprint("put", [memory_id, None], operation_id)
        with self._connection() as db:
            begin_write(db)
            replay = self._receipt(db, owner_id, operation_id, fingerprint)
            if replay is not None:
                return replay
            initial = self._owned(db, owner_id, memory_id)
            if initial is None or initial["status"] != "candidate" or initial["value"] is None:
                raise MemoryNotFound("Candidate not found")
            canonical_key = initial["canonical_key"] or initial["memory_key"]
            self._lock_identity(
                db, owner_id, initial["memory_type"], canonical_key, initial["scope_key"],
            )
            row = db.execute("""SELECT * FROM long_term_memories
                WHERE owner_id=? AND memory_id=? FOR UPDATE""",
                (owner_id, memory_id)).fetchone()
            if row is None or row["status"] != "candidate" or row["value"] is None:
                raise MemoryNotFound("Candidate not found")
            target_id = row["supersedes_memory_id"]
            target_revision = row["supersedes_revision"]
            if target_id is not None:
                target = db.execute("""SELECT * FROM long_term_memories
                    WHERE owner_id=? AND memory_id=? FOR UPDATE""",
                    (owner_id, target_id)).fetchone()
                if (target is None or target["status"] != "active"
                        or target["deleted_at"] is not None
                        or int(target["revision"]) != int(target_revision)
                        or target["scope_key"] != row["scope_key"]
                        or (target["canonical_key"] or target["memory_key"]) != canonical_key):
                    raise MemoryConflict(
                        "Target memory changed since this candidate was created; refresh candidates"
                    )
                if row["candidate_action"] == "delete":
                    db.execute("""UPDATE long_term_memories SET value=NULL,topics_json='[]',
                        metadata_json='{}',structured_json='{}',source_quote=NULL,
                        source_run_id=NULL,source_user_message='',expires_at=NULL,
                        status='deleted',deleted_at=clock_timestamp(),
                        updated_at=clock_timestamp(),revision=revision+1
                        WHERE owner_id=? AND memory_id=? AND revision=? AND status='active'""",
                        (owner_id, target_id, target_revision))
                    db.execute("""UPDATE long_term_memories SET value=NULL,topics_json='[]',
                        metadata_json='{}',structured_json='{}',source_quote=NULL,
                        source_run_id=NULL,source_user_message='',expires_at=NULL,
                        status='deleted',deleted_at=clock_timestamp(),
                        activation_kind=?,activation_reason=?,
                        updated_at=clock_timestamp(),revision=revision+1
                        WHERE owner_id=? AND memory_id=? AND revision=? AND status='candidate'""",
                        (activation_kind, activation_reason,
                         owner_id, memory_id, row["revision"]))
                    self._relation(db, owner_id, memory_id, target_id, "supersedes",
                                   "explicit deletion candidate confirmation")
                    return self._finish(
                        db, owner_id, operation_id, fingerprint, "put", memory_id,
                    )
                db.execute("""UPDATE long_term_memories SET status='superseded',
                    revision=revision+1,updated_at=clock_timestamp()
                    WHERE owner_id=? AND memory_id=? AND revision=? AND status='active'""",
                    (owner_id, target_id, target_revision))
                self._relation(db, owner_id, memory_id, target_id, "supersedes",
                               "explicit candidate confirmation")
            else:
                existing = db.execute("""SELECT * FROM long_term_memories
                    WHERE owner_id=? AND memory_type=? AND scope_key=?
                      AND (memory_key=? OR COALESCE(canonical_key,memory_key)=?)
                      AND status='active' AND deleted_at IS NULL
                    ORDER BY updated_at DESC,memory_id LIMIT 1 FOR UPDATE""",
                    (owner_id, row["memory_type"], row["scope_key"],
                     row["memory_key"], canonical_key)).fetchone()
                if (existing is not None and existing["expires_at"] is not None
                        and existing["expires_at"] <= _effective_now(db)):
                    db.execute("""UPDATE long_term_memories SET status='expired',
                        revision=revision+1,updated_at=clock_timestamp()
                        WHERE owner_id=? AND memory_id=?""",
                        (owner_id, existing["memory_id"]))
                    existing = None
                if existing is not None:
                    raise MemoryConflict(
                        "An active memory already exists for this identity; refresh candidates"
                    )
            db.execute("""UPDATE long_term_memories SET status='active',
                activation_kind=?,activation_reason=?,
                revision=revision+1,updated_at=clock_timestamp()
                WHERE owner_id=? AND memory_id=? AND revision=? AND status='candidate'""",
                (activation_kind, activation_reason, owner_id, memory_id, row["revision"]))
            self._link_exception_relations(
                db, owner_id, memory_id, row["memory_type"], canonical_key, row["scope_type"],
            )
            return self._finish(db, owner_id, operation_id, fingerprint, "put", memory_id)

    def reject_candidate(self, owner_id, memory_id, expected_revision, operation_id):
        if not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValueError("Rejection requires a positive expected revision")
        fingerprint = self._fingerprint("delete", [memory_id, expected_revision, None], operation_id)
        with self._connection() as db:
            begin_write(db)
            replay = self._receipt(db, owner_id, operation_id, fingerprint)
            if replay is not None:
                return replay
            row = self._owned(db, owner_id, memory_id)
            if row is None or row["status"] != "candidate":
                raise MemoryNotFound("Candidate not found")
            if row["revision"] != expected_revision:
                raise MemoryConflict("Candidate revision changed; reload the current record")
            db.execute("""UPDATE long_term_memories SET value=NULL,topics_json='[]',
                metadata_json='{}',structured_json='{}',source_quote=NULL,
                source_user_message='',expires_at=NULL,status='revoked',
                deleted_at=clock_timestamp(),updated_at=clock_timestamp(),
                revision=revision+1
                WHERE owner_id=? AND memory_id=? AND revision=?""",
                (owner_id, memory_id, expected_revision))
            return self._finish(
                db, owner_id, operation_id, fingerprint, "delete", memory_id, changed=False,
            )

    def delete(self, owner_id, memory_id, expected_revision, operation_id, *, control=None, expected_owner_revision=None):
        if not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValueError("Deletion requires a positive expected revision")
        fingerprint = self._fingerprint("delete", [memory_id, expected_revision, expected_owner_revision], operation_id)
        with self._connection() as db:
            begin_write(db)
            self._assert_control(db, owner_id, control)
            replay = self._receipt(db, owner_id, operation_id, fingerprint)
            if replay is not None:
                return replay
            self._check_owner_revision(db, owner_id, expected_owner_revision)
            row = self._owned(db, owner_id, memory_id)
            if row is None:
                raise MemoryNotFound("Memory not found")
            canonical_key = row["canonical_key"] or row["memory_key"]
            self._lock_identity(
                db, owner_id, row["memory_type"], canonical_key, row["scope_key"],
            )
            row = db.execute("""SELECT * FROM long_term_memories
                WHERE owner_id=? AND memory_id=? FOR UPDATE""",
                (owner_id, memory_id)).fetchone()
            if row is None:
                raise MemoryNotFound("Memory not found")
            if row["revision"] != expected_revision:
                raise MemoryConflict("Memory revision changed; reload the current record")
            if row["deleted_at"]:
                return self._finish(db, owner_id, operation_id, fingerprint, "delete", memory_id, changed=False)
            db.execute("""UPDATE long_term_memories SET status='superseded',
                revision=revision+1,updated_at=clock_timestamp()
                WHERE owner_id=? AND memory_type=? AND scope_key=?
                  AND COALESCE(canonical_key,memory_key)=?
                  AND status='candidate' AND memory_id<>?""",
                (owner_id, row["memory_type"], row["scope_key"], canonical_key, memory_id))
            db.execute("""UPDATE long_term_memories SET value=NULL,topics_json='[]',metadata_json='{}',
                structured_json='{}',source_quote=NULL,source_run_id=NULL,source_user_message='',
                expires_at=NULL,status='deleted',deleted_at=clock_timestamp(),
                updated_at=clock_timestamp(),revision=revision+1 WHERE owner_id=? AND memory_id=? AND revision=?""",
                (owner_id, memory_id, expected_revision))
            return self._finish(db, owner_id, operation_id, fingerprint, "delete", memory_id)

    def _check_owner_revision(self, db, owner_id, expected):
        if expected is not None and (not isinstance(expected, int) or expected < 0
                                     or self._owner_revision(db, owner_id) != expected):
            raise MemoryConflict("Owner memory changed; the pending operation must be confirmed again")


def _archive_from_response(write, request, response):
    if response.get("task_status") not in {"complete", "partial", "empty"} or not response.get("answer"):
        raise ValueError("Research archive source must be a completed research response")
    if not isinstance(response["answer"], str) or len(response["answer"].encode("utf-8")) > ARCHIVE_ANSWER_MAX_BYTES:
        raise ValueError("Research archive answer exceeds the 64 KiB storage limit")
    references, snapshots, sources, dates, cutoffs = [], [], set(), set(), set()
    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"trade_date", "as_of", "end_date"} and isinstance(item, str):
                    try:
                        parsed = date.fromisoformat(item).isoformat()
                        dates.add(parsed)
                        if key in {"as_of", "end_date"}:
                            cutoffs.add(parsed)
                    except ValueError:
                        pass
                if key == "source" and isinstance(item, str) and item.strip():
                    sources.add(item)
                if key == "sources" and isinstance(item, list):
                    sources.update(source for source in item if isinstance(source, str) and source.strip())
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    for index, trace in enumerate(response.get("tool_results", [])):
        if not isinstance(trace, dict) or trace.get("status") != "success":
            continue
        result = trace.get("result") or {}
        if result.get("status") == "error":
            continue
        payload = result.get("payload") or trace.get("output")
        if not isinstance(payload, dict) or not payload:
            continue
        reference = f"run://{write.source_run_id}/tool/{index}"
        references.append(reference)
        snapshots.append({"reference": reference, "name": trace.get("name"),
                          "status": result.get("status") or "ok", "payload": payload})
        inspect(payload)
        if trace.get("name"):
            sources.add("tool:" + trace["name"])
    if not references:
        raise ValueError("Research archive source has no committed structured evidence")
    requested_date = request.get("trade_date")
    if requested_date:
        try:
            as_of = date.fromisoformat(requested_date).isoformat()
        except ValueError:
            raise ValueError("Research archive source has no valid date") from None
        if dates and as_of not in dates:
            raise ValueError("Research archive request date conflicts with its evidence")
    elif len(cutoffs) == 1:
        as_of = next(iter(cutoffs))
    elif len(dates) == 1:
        as_of = next(iter(dates))
    else:
        raise ValueError("Research archive source has no unambiguous as_of date")
    metadata = {
        "as_of": as_of, "report_version": "archive-v1", "source_references": sorted(sources),
        "evidence_references": references, "source_generated_by": response.get("generated_by"),
        "evidence_snapshot": snapshots,
        "evidence_sha256": sha256(_encode(snapshots).encode("utf-8")).hexdigest(),
    }
    if len(_encode(metadata).encode("utf-8")) > ARCHIVE_METADATA_MAX_BYTES:
        raise ValueError("Research archive evidence exceeds the 1 MiB storage limit")
    return write.model_copy(update={"value": response["answer"], "metadata": metadata})

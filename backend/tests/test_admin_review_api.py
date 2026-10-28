"""Administrator review API authentication and queue operations."""

from fastapi.testclient import TestClient

from app.main import app
from app.services.review_cases import ReviewCaseService
from review_fixtures import completed_run, downvote, owner as fixture_owner


def test_admin_review_api_requires_key_and_supports_the_queue(
    postgresql_database, monkeypatch,
):
    owner = fixture_owner("b1")
    run_id = completed_run(owner, "review-api-case")
    downvote(owner, run_id)
    service = ReviewCaseService()
    service.aggregate_signals(limit=10)
    case = service.list_cases()[0]

    monkeypatch.setenv("LIMITUPLAB_ADMIN_KEY", "r" * 32)
    headers = {"X-LimitUpLab-Admin-Key": "r" * 32}
    with TestClient(app) as client:
        assert client.get("/api/admin/review/cases").status_code == 401
        listing = client.get("/api/admin/review/cases", headers=headers)
        assert listing.status_code == 200
        assert listing.json()["cases"][0]["case_id"] == case.case_id

        detail = client.get(f"/api/admin/review/cases/{case.case_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["signals"][0]["kind"] == "downvote"

        updated = client.patch(
            f"/api/admin/review/cases/{case.case_id}",
            headers=headers,
            json={
                "status": "confirmed",
                "symptom": "日期错误",
                "note": "确认",
                "revision": detail.json()["revision"],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "confirmed"

        exported = client.get(f"/api/admin/review/cases/{case.case_id}/export", headers=headers)
        assert exported.status_code == 200
        assert "市场最高标是哪个" in exported.text

        feedback = client.get("/api/admin/review/feedback", headers=headers)
        assert feedback.status_code == 200
        assert feedback.json()["feedback"][0]["run_id"] == run_id

        jobs = client.get("/api/admin/review/jobs", headers=headers)
        assert jobs.status_code == 200
        drain = client.post("/api/admin/review/jobs/drain", headers=headers)
        assert drain.status_code == 200
        sample = client.post("/api/admin/review/sample?limit=1", headers=headers)
        assert sample.status_code == 200
        recheck = client.post(
            f"/api/admin/review/runs/{run_id}/recheck", headers=headers,
        )
        assert recheck.status_code == 200
        assert recheck.json()["deterministic_jobs"] == 1

        missing = client.get("/api/admin/review/cases/case_missing", headers=headers)
        assert missing.status_code == 404


def test_admin_bulk_dismisses_new_cases(postgresql_database, monkeypatch):
    owner = fixture_owner("b3")
    run_id = completed_run(owner, "review-api-bulk")
    downvote(owner, run_id)
    service = ReviewCaseService()
    service.aggregate_signals(limit=10)
    case = service.list_cases()[0]
    monkeypatch.setenv("LIMITUPLAB_ADMIN_KEY", "t" * 32)
    headers = {"X-LimitUpLab-Admin-Key": "t" * 32}

    with TestClient(app) as client:
        response = client.post(
            "/api/admin/review/cases/bulk-dismiss",
            headers=headers,
            json={"case_ids": [case.case_id], "note": "批量驳回：正常波动"},
        )
        assert response.status_code == 200
        assert response.json() == {"dismissed": 1, "skipped": 0}
        detail = client.get(
            f"/api/admin/review/cases/{case.case_id}", headers=headers,
        )
        assert detail.json()["status"] == "dismissed"


def test_admin_review_rejects_conflicting_revisions(postgresql_database, monkeypatch):
    owner = fixture_owner("b2")
    run_id = completed_run(owner, "review-api-conflict")
    downvote(owner, run_id)
    service = ReviewCaseService()
    service.aggregate_signals(limit=10)
    case = service.list_cases()[0]
    monkeypatch.setenv("LIMITUPLAB_ADMIN_KEY", "s" * 32)
    headers = {"X-LimitUpLab-Admin-Key": "s" * 32}

    with TestClient(app) as client:
        first = client.patch(
            f"/api/admin/review/cases/{case.case_id}",
            headers=headers,
            json={"symptom": "日期错误", "note": "补充症状", "revision": 1},
        )
        assert first.status_code == 200
        stale = client.patch(
            f"/api/admin/review/cases/{case.case_id}",
            headers=headers,
            json={"symptom": "再次修改", "note": "旧页面", "revision": 1},
        )
        assert stale.status_code == 409

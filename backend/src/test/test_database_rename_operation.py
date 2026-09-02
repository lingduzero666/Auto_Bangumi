"""RenameOperationDatabase — #1078 durable rename/replacement state."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from module.database import Database
from module.database.rename_operation import RenameOperationDatabase
from module.models import RenameOperation


def _operation(**overrides) -> RenameOperation:
    values = {
        "downloader_type": "qbittorrent",
        "kind": "replacement",
        "state": "planned",
        "new_task_id": "new-v2",
        "old_task_id": "old-v1",
        "save_path": "/downloads/Anime/Season 1",
        "source_path": "[ANi] Anime - 01 [V2].mp4",
        "target_path": "Anime S01E01.mp4",
        "staged_path": ".ab-replace/old-v1/Anime S01E01.mp4",
        "bangumi_id": 42,
        "media_type": "episode",
        "season": 1,
        "episode": 1.0,
        "group_name": "ANi",
        "resolution": "1080p",
        "old_revision": 1,
        "new_revision": 2,
        "revision_metadata": '{"old":"V1","new":"V2"}',
    }
    values.update(overrides)
    return RenameOperation(**values)


async def test_get_or_create_persists_complete_operation_metadata(db_session):
    repo = RenameOperationDatabase(db_session)

    row, created = await repo.get_or_create(_operation())

    assert created is True
    assert row.id is not None
    assert row.old_task_id == "old-v1"
    assert row.staged_path == ".ab-replace/old-v1/Anime S01E01.mp4"
    assert row.old_revision == 1
    assert row.new_revision == 2
    assert row.revision_metadata == '{"old":"V1","new":"V2"}'
    assert row.attempt_count == 0
    assert row.created_at is not None
    assert row.updated_at is not None

    same, duplicate_created = await repo.get_or_create(_operation())
    assert duplicate_created is False
    assert same.id == row.id
    assert await repo.get(row.id) == row


async def test_active_target_is_unique_but_done_history_does_not_block_v3(db_session):
    repo = RenameOperationDatabase(db_session)
    first, _ = await repo.get_or_create(_operation(state="conflict"))

    with pytest.raises(IntegrityError):
        await repo.get_or_create(
            _operation(
                new_task_id="new-v3",
                source_path="[ANi] Anime - 01 [V3].mp4",
                new_revision=3,
            )
        )

    await repo.set_state(first.id, "done")
    v3, created = await repo.get_or_create(
        _operation(
            new_task_id="new-v3",
            source_path="[ANi] Anime - 01 [V3].mp4",
            new_revision=3,
        )
    )
    assert created is True
    assert v3.new_revision == 3


async def test_upsert_conflict_is_idempotent_and_refreshes_error(db_session):
    repo = RenameOperationDatabase(db_session)

    first, created = await repo.upsert_conflict(
        _operation(state="retry", last_error="qB 409")
    )
    assert created is True
    assert first.state == "conflict"
    assert first.retry_at is None

    same, created_again = await repo.upsert_conflict(
        _operation(
            state="planned",
            old_task_id="resolved-old-owner",
            last_error="destination still exists",
        )
    )
    assert created_again is False
    assert same.id == first.id
    assert same.state == "conflict"
    assert same.old_task_id == "resolved-old-owner"
    assert same.last_error == "destination still exists"


async def test_claim_is_atomic_due_only_and_increments_attempt(db_session):
    repo = RenameOperationDatabase(db_session)
    now = datetime.now(timezone.utc)
    row, _ = await repo.get_or_create(
        _operation(state="retry", retry_at=now - timedelta(seconds=1))
    )

    claimed = await repo.claim(
        row.id,
        from_states=("retry",),
        to_state="planned",
        now=now,
    )

    assert claimed is not None
    assert claimed.state == "planned"
    assert claimed.attempt_count == 1
    assert claimed.retry_at is None
    assert (
        await repo.claim(
            row.id,
            from_states=("retry",),
            to_state="planned",
            now=now,
        )
        is None
    )

    future, _ = await repo.get_or_create(
        _operation(
            new_task_id="future-v3",
            source_path="future-v3.mkv",
            target_path="Anime S01E02.mp4",
            state="retry",
            retry_at=now + timedelta(hours=1),
        )
    )
    assert (
        await repo.claim(
            future.id,
            from_states=("retry",),
            to_state="planned",
            now=now,
        )
        is None
    )


async def test_mark_notified_is_one_shot(db_session):
    repo = RenameOperationDatabase(db_session)
    row, _ = await repo.upsert_conflict(_operation())
    notified_at = datetime.now(timezone.utc)

    assert await repo.mark_notified(row.id, notified_at) is True
    assert await repo.mark_notified(row.id, notified_at + timedelta(seconds=1)) is False

    loaded = await repo.get(row.id)
    assert loaded is not None
    assert loaded.notified_at == notified_at


async def test_recover_stale_running_uses_compare_and_swap(db_session):
    repo = RenameOperationDatabase(db_session)
    now = datetime.now(timezone.utc)
    row, _ = await repo.get_or_create(_operation(state="running"))
    row.updated_at = now - timedelta(minutes=10)
    db_session.add(row)
    await db_session.commit()

    assert await repo.recover_stale_running(row.id, before=now - timedelta(minutes=5))
    assert not await repo.recover_stale_running(
        row.id, before=now - timedelta(minutes=5)
    )
    loaded = await repo.get(row.id)
    assert loaded is not None
    assert loaded.state == "retry"


async def test_replacement_phase_lease_fences_workers_and_state_commit(db_session):
    repo = RenameOperationDatabase(db_session)
    now = datetime.now(timezone.utc)
    row, _ = await repo.get_or_create(_operation())

    first = await repo.claim_replacement_lease(row.id, owner="worker-a", now=now)
    second = await repo.claim_replacement_lease(row.id, owner="worker-b", now=now)

    assert first is not None
    assert first.lease_owner == "worker-a"
    assert first.attempt_count == 1
    assert second is None
    assert (
        await repo.set_state_claimed(row.id, owner="worker-b", state="old_staged")
        is None
    )

    advanced = await repo.set_state_claimed(
        row.id, owner="worker-a", state="old_staged"
    )
    assert advanced is not None
    assert advanced.state == "old_staged"
    assert advanced.lease_owner is None
    assert advanced.lease_expires_at is None


async def test_expired_replacement_phase_lease_can_be_reclaimed(db_session):
    repo = RenameOperationDatabase(db_session)
    now = datetime.now(timezone.utc)
    row, _ = await repo.get_or_create(_operation())
    assert await repo.claim_replacement_lease(
        row.id,
        owner="crashed-worker",
        now=now - timedelta(minutes=10),
        lease_for=timedelta(minutes=5),
    )

    reclaimed = await repo.claim_replacement_lease(
        row.id, owner="recovery-worker", now=now
    )

    assert reclaimed is not None
    assert reclaimed.lease_owner == "recovery-worker"
    assert reclaimed.attempt_count == 2


async def test_retry_query_and_done_pruning(db_session):
    repo = RenameOperationDatabase(db_session)
    now = datetime.now(timezone.utc)
    due, _ = await repo.get_or_create(
        _operation(state="retry", retry_at=now - timedelta(seconds=1))
    )
    await repo.get_or_create(
        _operation(
            new_task_id="later-v3",
            source_path="later-v3.mkv",
            target_path="Anime S01E02.mp4",
            state="retry",
            retry_at=now + timedelta(hours=1),
        )
    )
    conflict, _ = await repo.upsert_conflict(
        _operation(
            new_task_id="conflict-v4",
            source_path="conflict-v4.mkv",
            target_path="Anime S01E03.mp4",
        )
    )

    assert [row.id for row in await repo.list_retryable(now)] == [due.id]
    assert [row.id for row in await repo.list_conflicts()] == [conflict.id]

    await repo.set_state(conflict.id, "done")
    conflict.updated_at = now - timedelta(days=31)
    db_session.add(conflict)
    await db_session.commit()
    assert await repo.prune_done(before=now - timedelta(days=30)) == 1


async def test_database_facade_exposes_rename_operation_repository(db_engine):
    async with Database(engine=db_engine) as db:
        row, created = await db.rename_operation.get_or_create(_operation())
        assert created is True
        assert await db.rename_operation.get(row.id) is not None


class TestStaleCompletionIsNotHonoured:
    """删除番剧后重新添加同一种子时，上一轮的 done 行不能当成既成事实。

    身份五元组（下载器 / 任务 id / 保存路径 / 源文件名 / 目标名）会被完整
    复现，而磁盘上是刚下回来的原始文件。沿用旧行会让种子被打上
    ``ab:renamed`` 却一个字节都没改，且该标签是终态标记。
    """

    async def test_done_row_older_than_the_task_is_rebuilt(self, db_session):
        repo = RenameOperationDatabase(db_session)
        first, created = await repo.get_or_create(_operation(state="done"))
        assert created is True

        # 种子在这条记录完成之后才被重新加入下载器
        re_added_at = _as_utc_updated(first) + timedelta(hours=1)
        second, created = await repo.get_or_create(
            _operation(state="retry"), stale_before=re_added_at
        )

        assert created is True, "陈旧的 done 行应被丢弃并重建"
        assert second.state == "retry"
        # 身份索引唯一，查回来的必须是重建后的那行而不是旧的 done
        # （SQLite 会复用被删行的 id，所以不能按 id 断言）
        remaining = await repo.get_by_identity(
            downloader_type="qbittorrent",
            new_task_id="new-v2",
            save_path="/downloads/Anime/Season 1",
            source_path="[ANi] Anime - 01 [V2].mp4",
            target_path="Anime S01E01.mp4",
        )
        assert remaining is not None
        assert remaining.state == "retry"

    async def test_done_row_newer_than_the_task_still_wins(self, db_session):
        """同一轮里的幂等保护必须保留：并发的第二个 renamer 仍应直接复用。"""
        repo = RenameOperationDatabase(db_session)
        first, _ = await repo.get_or_create(_operation(state="done"))

        # 种子远早于这次改名加入，说明这条 done 属于本轮
        added_long_ago = _as_utc_updated(first) - timedelta(hours=1)
        second, created = await repo.get_or_create(
            _operation(state="retry"), stale_before=added_long_ago
        )

        assert created is False
        assert second.id == first.id
        assert second.state == "done"

    async def test_without_task_time_the_old_behaviour_is_kept(self, db_session):
        """拿不到加入时间的下载器不做任何陈旧判断。"""
        repo = RenameOperationDatabase(db_session)
        first, _ = await repo.get_or_create(_operation(state="done"))
        second, created = await repo.get_or_create(_operation(state="retry"))

        assert created is False
        assert second.id == first.id
        assert second.state == "done"

    async def test_unfinished_row_is_never_treated_as_stale(self, db_session):
        """只有 done 行才可能陈旧；running/retry 行是活跃状态，必须复用。"""
        repo = RenameOperationDatabase(db_session)
        first, _ = await repo.get_or_create(_operation(state="running"))
        second, created = await repo.get_or_create(
            _operation(state="retry"),
            stale_before=_as_utc_updated(first) + timedelta(hours=1),
        )

        assert created is False
        assert second.id == first.id
        assert second.state == "running"


def _as_utc_updated(row) -> datetime:
    value = row.updated_at
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

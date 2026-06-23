"""Phase 7 smoke tests: file watcher debounce + scheduler incremental re-crawl.

All filesystem/network/scheduler internals are mocked — these tests never spin a
real watchdog observer, never touch the network, and never sleep on a real timer.
They verify the *logic* Phase 7 owns:

  - Debouncer coalesces a burst of triggers into a single fire.
  - A file-change event routes into the incremental ingest path (reindex callback).
  - Irrelevant file types and directory events are ignored.
  - The scheduler registers an interval job using ``refresh_interval_hours``.
  - A scheduled re-crawl passes ``known_hashes`` to the loader so unchanged pages
    are skipped (the loader drops pages whose content_hash is already known).
  - watch_mode / auto_refresh toggles gate whether anything starts.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.config_schemas import (
    RAGConfigData,
    SourcesConfig,
    WebSourcesConfig,
)
from app.db import init_db
from app.rag import scheduler as scheduler_mod
from app.rag import watcher as watcher_mod
from app.rag.loader import ParsedDocument, compute_content_hash
from app.rag.watcher import Debouncer, FileWatcher
from app.rag.web_loader import WebSourceLoader


@pytest.fixture(autouse=True)
def _tables():
    """Ensure DB tables exist for tests that exercise the real ingest-job path."""
    init_db()


# ── Debouncer ─────────────────────────────────────────────────────────


def test_debouncer_coalesces_burst_into_single_fire():
    """Multiple rapid triggers within the window fire the callback once."""

    async def main():
        fired = []

        async def cb():
            fired.append(1)

        loop = asyncio.get_running_loop()
        # tiny interval so the test is fast but still exercises the timer path
        deb = Debouncer(interval=0.05, callback=cb, loop=loop)

        for _ in range(5):
            deb.trigger()  # burst

        assert deb.trigger_count == 5
        # wait past the debounce window for the timer + loop callback to run
        await asyncio.sleep(0.2)
        assert deb.fire_count == 1
        assert fired == [1]

    asyncio.run(main())


def test_debouncer_cancel_prevents_fire():
    async def main():
        fired = []

        async def cb():
            fired.append(1)

        loop = asyncio.get_running_loop()
        deb = Debouncer(interval=0.05, callback=cb, loop=loop)
        deb.trigger()
        deb.cancel()
        await asyncio.sleep(0.15)
        assert deb.fire_count == 0
        assert fired == []

    asyncio.run(main())


# ── FileWatcher event routing ─────────────────────────────────────────


def test_file_event_triggers_incremental_ingest():
    """A relevant file change schedules a re-index that calls the ingest path."""

    async def main():
        reindex_calls = []

        async def fake_reindex(user_id):
            reindex_calls.append(user_id)

        loop = asyncio.get_running_loop()
        cfg = SourcesConfig(watch_mode=True, polling_interval=0)  # 0 -> immediate
        fw = FileWatcher(user_id=7, config=cfg, reindex=fake_reindex, loop=loop)

        scheduled = fw.handle_event("/docs/notes.md", is_directory=False)
        assert scheduled is True

        await asyncio.sleep(0.05)  # let the debounced callback run
        assert reindex_calls == [7]

    asyncio.run(main())


def test_file_event_ignores_irrelevant_extension_and_dirs():
    async def main():
        reindex_calls = []

        async def fake_reindex(user_id):
            reindex_calls.append(user_id)

        loop = asyncio.get_running_loop()
        cfg = SourcesConfig(watch_mode=True, polling_interval=0, file_types=[".md"])
        fw = FileWatcher(user_id=1, config=cfg, reindex=fake_reindex, loop=loop)

        assert fw.handle_event("/docs/image.png", is_directory=False) is False
        assert fw.handle_event("/docs/subdir", is_directory=True) is False

        await asyncio.sleep(0.05)
        assert reindex_calls == []

    asyncio.run(main())


def test_default_reindex_uses_pipeline_ingest():
    """The default re-index action delegates to pipeline.ingest (no dup logic)."""

    async def main():
        with patch("app.rag.pipeline.ingest") as mock_ingest, patch(
            "app.rag.loader.LocalFileLoader"
        ) as mock_loader:

            async def _noop(*a, **k):
                return None

            mock_ingest.side_effect = _noop
            await watcher_mod._default_reindex(user_id=1)
            assert mock_ingest.called
            kwargs = mock_ingest.call_args.kwargs
            assert kwargs["source_type"] == "local"
            assert kwargs["loader"] is mock_loader.return_value

    asyncio.run(main())


def test_start_watcher_noops_when_disabled():
    """watch_mode off => no watcher started, no observer touched."""
    watcher_mod.stop_watcher()
    result = watcher_mod.start_watcher(
        user_id=1, config=SourcesConfig(watch_mode=False)
    )
    assert result is None
    assert watcher_mod.get_watcher() is None


def test_start_watcher_noops_when_watchdog_missing():
    watcher_mod.stop_watcher()
    with patch("app.rag.watcher._has_watchdog", return_value=False):
        result = watcher_mod.start_watcher(
            user_id=1, config=SourcesConfig(watch_mode=True)
        )
    assert result is None
    assert watcher_mod.get_watcher() is None


# ── Scheduler ─────────────────────────────────────────────────────────


def test_scheduler_registers_job_with_configured_interval():
    """start() adds an interval job using refresh_interval_hours."""
    fake_sched = MagicMock()
    fake_sched_cls = MagicMock(return_value=fake_sched)

    with patch("app.rag.scheduler._has_apscheduler", return_value=True), patch.dict(
        "sys.modules",
        {
            "apscheduler.schedulers.asyncio": MagicMock(
                AsyncIOScheduler=fake_sched_cls
            )
        },
    ):
        cfg = WebSourcesConfig(auto_refresh=True, refresh_interval_hours=6)
        sched = scheduler_mod.WebScheduler(user_id=3, config=cfg)
        assert sched.start() is True

    assert fake_sched.add_job.called
    kwargs = fake_sched.add_job.call_args.kwargs
    assert kwargs["trigger"] == "interval"
    assert kwargs["hours"] == 6
    assert kwargs["args"] == [3]
    assert kwargs["id"] == scheduler_mod.JOB_ID
    assert fake_sched.start.called


def test_start_scheduler_noops_when_disabled():
    scheduler_mod.stop_scheduler()
    result = scheduler_mod.start_scheduler(
        user_id=1, config=WebSourcesConfig(auto_refresh=False)
    )
    assert result is None
    assert scheduler_mod.get_scheduler() is None


def test_start_scheduler_noops_when_apscheduler_missing():
    scheduler_mod.stop_scheduler()
    with patch("app.rag.scheduler._has_apscheduler", return_value=False):
        result = scheduler_mod.start_scheduler(
            user_id=1, config=WebSourcesConfig(auto_refresh=True)
        )
    assert result is None
    assert scheduler_mod.get_scheduler() is None


# ── Incremental re-crawl: known_hashes skips unchanged pages ──────────


def test_recrawl_passes_known_hashes_and_skips_unchanged():
    """A re-crawl collects known hashes and the loader drops unchanged pages.

    We mock the network fetch so the loader extracts a fixed page, set that page's
    content_hash as 'known', and assert the loader returns zero docs (skipped).
    Then we capture the ingest call to confirm the loader carried known_hashes.
    """

    async def main():
        page_html = (
            "<html><head><title>Doc</title></head>"
            "<body><main><p>Stable documentation content that is long enough.</p>"
            "</main></body></html>"
        )

        # Determine the content_hash the loader will compute for this page so we
        # can pre-seed it as "already stored".
        from app.rag import web_loader as wl

        clean = wl._extract_main_text(page_html)
        known_hash = compute_content_hash(clean)

        captured = {}

        async def fake_fetch_static(url, timeout):
            return page_html, None

        async def fake_ingest(job_id, config, loader, source_type):
            # capture the loader's known_hashes and run its load() to prove skip
            captured["known_hashes"] = set(loader.known_hashes)
            captured["docs"] = await loader.load()
            captured["source_type"] = source_type
            return None

        async def fake_known(cfg):
            return {known_hash}

        # Seed a config row for a fresh user so recrawl reads real web_sources.
        from sqlmodel import Session

        from app.db import engine
        from app.models import RAGConfig

        web_cfg = WebSourcesConfig(
            auto_refresh=True,
            web_mode="single",
            web_urls=["https://example.com/doc"],
        )
        full_cfg = RAGConfigData(web_sources=web_cfg)
        user_id = 80501
        with Session(engine) as session:
            session.add(RAGConfig(user_id=user_id, data=full_cfg.model_dump()))
            session.commit()

        with patch(
            "app.rag.scheduler.collect_known_hashes", side_effect=fake_known
        ), patch(
            "app.rag.web_loader._fetch_html_static", side_effect=fake_fetch_static
        ), patch(
            "app.rag.pipeline.ingest", side_effect=fake_ingest
        ):
            await scheduler_mod.recrawl_web_sources(user_id=user_id)

        # The loader received the known hash...
        assert known_hash in captured["known_hashes"]
        # ...and therefore skipped the unchanged page (no docs to re-embed).
        assert captured["docs"] == []
        assert captured["source_type"] == "web"

    asyncio.run(main())


def test_loader_known_hashes_skip_is_the_mechanism():
    """Direct check: WebSourceLoader drops a page whose hash is in known_hashes."""

    async def main():
        page_html = (
            "<html><head><title>Doc</title></head>"
            "<body><main><p>Stable documentation content that is long enough.</p>"
            "</main></body></html>"
        )
        from app.rag import web_loader as wl

        clean = wl._extract_main_text(page_html)
        known_hash = compute_content_hash(clean)

        async def fake_fetch_static(url, timeout):
            return page_html, None

        cfg = WebSourcesConfig(
            web_mode="single", web_urls=["https://example.com/doc"]
        )

        with patch(
            "app.rag.web_loader._fetch_html_static", side_effect=fake_fetch_static
        ):
            # Without known hashes: one doc emitted.
            loader_new = WebSourceLoader(cfg)
            docs_new = await loader_new.load()
            assert len(docs_new) == 1
            assert isinstance(docs_new[0], ParsedDocument)

            # With the page's hash known: skipped.
            loader_known = WebSourceLoader(cfg, known_hashes={known_hash})
            docs_known = await loader_known.load()
            assert docs_known == []

    asyncio.run(main())

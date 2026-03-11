import asyncio
import subprocess
from pathlib import Path

from rich.text import Text
from textual.css.query import NoMatches
from textual.widgets import Input, Label

from gitrepo import GitRepo
from gitdiffnavtool import DIFF_FOOTER_2, GitDiffNavTool
from scripts.svg_text_extract import svg_plain_text


def _run(cmd: list[str], cwd: Path) -> str:
    out = subprocess.check_output(cmd, cwd=str(cwd), text=True)
    return out.strip()


def _make_temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)

    f = repo / "a.txt"
    f.write_text("line1\n", encoding="utf-8")
    _run(["git", "add", "a.txt"], repo)
    _run(["git", "commit", "-q", "-m", "initial"], repo)

    f.write_text("line1\nline2 changed\n", encoding="utf-8")
    _run(["git", "add", "a.txt"], repo)
    _run(["git", "commit", "-q", "-m", "second"], repo)

    return repo


def _build_app(repo_path: Path) -> GitDiffNavTool:
    return GitDiffNavTool(
        gitRepo=GitRepo(str(repo_path)),
        rel_dir="",
        rel_file="",
        repo_first=False,
        repo_hashes=[],
        no_ignored=True,
        no_untracked=True,
        no_initial_popup=True,
        verbose=0,
        highlight=None,
        color_scheme="style",
        diff_variant="classic",
        hash_length=12,
        add_authors=True,
        unified_context=3,
        history_limit=0,
        minimum_sidebyside_width=60,
        blank_before_hunk=False,
        output_directory=None,
    )


def _label_plain(label: Label) -> str:
    renderable = getattr(label, "renderable", None)
    if isinstance(renderable, Text):
        return renderable.plain
    txt = getattr(label, "text", None)
    if txt is not None:
        return str(txt)
    return str(renderable) if renderable is not None else ""


def _find_visible(app: GitDiffNavTool) -> bool:
    try:
        app.screen.query_one("#find-container")
        return True
    except NoMatches:
        return False


def _diff_body_snapshot(app: GitDiffNavTool) -> tuple:
    rows = []
    for node in app.diff_list.nodes() or []:
        rows.append(getattr(node, "_search_text", app.diff_list.text_of(node)))
    return (tuple(app.diff_list.output), tuple(rows))


def _prepare_diff_view(app: GitDiffNavTool, repo_path: Path) -> None:
    prev_hash = _run(["git", "rev-parse", "HEAD~1"], repo_path)
    curr_hash = _run(["git", "rev-parse", "HEAD"], repo_path)
    app.diff_list.prepDiffList("a.txt", prev_hash, curr_hash, 0, ("history_file", "right-file-list", None))
    app.change_state("diff_fullscreen", "#diff-list", DIFF_FOOTER_2)


def _save_svg_artifact(name: str, svg_text: str) -> Path:
    artifacts_dir = Path(__file__).resolve().parent.parent / "tmp" / "svg-artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_path = artifacts_dir / f"{name}.svg"
    out_path.write_text(svg_text, encoding="utf-8")
    return out_path


def test_find_overlay_visible_with_greater_and_escape_keeps_diff_body(tmp_path: Path):
    repo_path = _make_temp_repo(tmp_path)
    app = _build_app(repo_path)

    async def _scenario() -> None:
        async with app.run_test() as pilot:
            _prepare_diff_view(app, repo_path)
            await pilot.pause()

            before = _diff_body_snapshot(app)

            await pilot.press(">")
            await pilot.pause()

            assert _find_visible(app)
            assert getattr(app, "_find_overlay_title", "") == "Find (forward)"
            assert _diff_body_snapshot(app) == before

            await pilot.press("escape")
            await pilot.pause()

            assert not _find_visible(app)
            assert _diff_body_snapshot(app) == before

    asyncio.run(_scenario())


def test_find_overlay_visible_with_less_and_enter_keeps_diff_body(tmp_path: Path):
    repo_path = _make_temp_repo(tmp_path)
    app = _build_app(repo_path)

    async def _scenario() -> None:
        async with app.run_test() as pilot:
            _prepare_diff_view(app, repo_path)
            await pilot.pause()

            before = _diff_body_snapshot(app)

            await pilot.press("<")
            await pilot.pause()

            assert _find_visible(app)
            assert getattr(app, "_find_overlay_title", "") == "Find (backward)"
            assert _diff_body_snapshot(app) == before

            find_input = app.screen.query_one("#find-input", Input)
            find_input.value = "line1"

            await pilot.press("enter")
            await pilot.pause()

            assert not _find_visible(app)
            assert _diff_body_snapshot(app) == before

    asyncio.run(_scenario())


def test_find_overlay_appears_in_file_list_view():
    """Opening the real project directory in file-list mode and pressing > shows Find (forward)."""
    repo_path = Path(__file__).resolve().parent.parent
    app = _build_app(repo_path)

    async def _scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()

            before_svg = app.export_screenshot()
            _save_svg_artifact("filemode-before-find", before_svg)
            before_plain = svg_plain_text(before_svg)

            assert not _find_visible(app)
            assert "Find (forward)" not in before_plain

            await pilot.press(">")
            await pilot.pause()

            after_svg = app.export_screenshot()
            _save_svg_artifact("filemode-after-find", after_svg)
            after_plain = svg_plain_text(after_svg)

            assert _find_visible(app)
            assert getattr(app, "_find_overlay_title", "") == "Find (forward)"
            assert "Find (forward)" in after_plain

            find_input = app.screen.query_one("#find-input", Input)
            assert app.focused is find_input

    asyncio.run(_scenario())

#!/usr/bin/env python3
"""

Usage: python textual_three_column.py [path]
"""
from __future__ import annotations

import argparse
import os
from typing import Optional
from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual import events
from textual.widgets import (
    Static,
    Header,
    Footer,
    ListView,
    ListItem,
    Label,
)


class FileList(ListView):
    """A ListView showing directory contents. Directories have a blue background.

    Navigation: arrow keys (up/down) move selection automatically because ListView
    handles keyboard navigation. The app focuses this widget on mount.
    """

    def set_path(self, path: str) -> None:
        path = os.path.abspath(path)
        self.path = path
        # keep the app aware of the full path currently displayed
        try:
            if hasattr(self, "app"):
                self.app.displayed_path = self.path
        except Exception:
            pass
        try:
            entries = sorted(os.listdir(path))
        except Exception as exc:
            self.clear()
            self.append(ListItem(Label(Text(f"Error reading {path}: {exc}", style="red"))))
            return

        self.clear()
        # Parent entry
        parent_item = ListItem(Label(Text("..", style="white on blue")))
        parent_item._filename = ".."
        self.append(parent_item)

        for name in entries:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                li = ListItem(Label(Text(name, style="white on blue")))
            else:
                li = ListItem(Label(name))
            # attach filename to the ListItem for reliable lookup later
            try:
                li._filename = name
            except Exception:
                pass
            self.append(li)

    def on_key(self, event: events.Key) -> None:
        """Only allow up/down/left/right in this column.

        - Up: move to previous entry
        - Down: move to next entry
        - Left/Right: show a temporary "TBD" modal
        - Other keys: ignore
        """
        key = event.key
        if key == "q":
            # Allow global quit to bubble to the app
            return
        if key == "up":
            event.stop()
            self.action_cursor_up()
        elif key == "down":
            event.stop()
            self.action_cursor_down()
        elif key == "right":
            event.stop()
            # If the highlighted entry is a directory (and not ".."), enter it.
            child = self.highlighted_child
            if child is None:
                return
            # Prefer filename attached to the ListItem (set in set_path)
            item_name = getattr(child, "_filename", None)
            if item_name is None:
                try:
                    label = child.query_one(Label)
                    # Label implementations vary: prefer `text`, then `renderable`.
                    if hasattr(label, "text"):
                        item_name = label.text
                    else:
                        renderable = getattr(label, "renderable", None)
                        if isinstance(renderable, Text):
                            item_name = renderable.plain
                        elif renderable is not None:
                            item_name = str(renderable)
                        else:
                            item_name = str(label)
                except Exception as exc:
                    # Fallback: show the exception message in the modal
                    try:
                        self.app.push_screen(_TBDModal(str(exc)))
                    except Exception:
                        # Last-resort fallback
                        self.app.push_screen(_TBDModal())
                    return

            if item_name != "..":
                full = os.path.join(self.path, item_name)
                if os.path.isdir(full):
                    # switch the listing to the selected directory
                    self.set_path(full)
                    # ensure highlight resets to first item
                    try:
                        self.index = 0
                    except Exception:
                        pass
                    # update app-level current path as well
                    try:
                        self.app.path = os.path.abspath(full)
                    except Exception:
                        pass
                    # focus back on this list
                    self.focus()
                    return

            # Not a directory we can enter — show TBD for now
            self.app.push_screen(_TBDModal())
        elif key == "left":
            event.stop()
            # left arrow not implemented yet
            self.app.push_screen(_TBDModal())
        else:
            # ignore other keys
            event.stop()


class _TBDModal(ModalScreen):
    """Simple modal that shows a message (default "TBD") and closes on any key."""

    def __init__(self, message: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message = message or "TBD"

    def compose(self) -> ComposeResult:
        yield Static(Text(self.message, style="bold"), id="tbd-msg")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.app.pop_screen()


class GitHistoryTool(App):
    TITLE = "Git History Tool"
    CSS = """
Horizontal {
    height: 100%;
}
#left {
    border: solid white;
}
#right1, #right2 {
    border: heavy #555555;
}
"""

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, path: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.path = os.path.abspath(path or os.getcwd())
        # store the full path that will be displayed at startup
        self.displayed_path = self.path

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            # left column: flex so it takes remaining space
            with Vertical(id="left-column"):
                yield Label(Text("Files", style="bold"), id="left-title")
                yield FileList(id="left")
            # two minimal right columns
            with Vertical(id="right1-column"):
                yield Label(Text("History", style="bold"), id="right1-title")
                yield Static("Right minimal 1", id="right1")
            with Vertical(id="right2-column"):
                yield Label(Text("Diff", style="bold"), id="right2-title")
                yield Static("Right minimal 2", id="right2")
        yield Footer()

    async def on_mount(self) -> None:  # set sizes and populate left
        left = self.query_one("#left", FileList)
        right1 = self.query_one("#right1", Static)
        right2 = self.query_one("#right2", Static)

        # make left flexible so it expands, keep the right columns small
        left.styles.flex = 1
        right1.styles.width = 20
        right2.styles.width = 12

        left.set_path(self.path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Three-column Textual TUI")
    parser.add_argument("path", nargs="?", help="Directory to list", default=os.getcwd())
    args = parser.parse_args()

    app = GitHistoryTool(args.path)
    app.run()


if __name__ == "__main__":
    main()

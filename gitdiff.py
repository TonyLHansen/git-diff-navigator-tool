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
        try:
            entries = sorted(os.listdir(path))
        except Exception as exc:
            self.clear()
            self.append(ListItem(Label(Text(f"Error reading {path}: {exc}", style="red"))))
            return

        self.clear()
        # Parent entry
        self.append(ListItem(Label(Text("..", style="white on blue"))))

        for name in entries:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                self.append(ListItem(Label(Text(name, style="white on blue"))))
            else:
                self.append(ListItem(Label(name)))

    def on_key(self, event: events.Key) -> None:
        """Only allow up/down/left/right in this column.

        - Up: move to previous entry
        - Down: move to next entry
        - Left/Right: show a temporary "TBD" modal
        - Other keys: ignore
        """
        key = event.key
        if key == "up":
            event.stop()
            self.action_cursor_up()
        elif key == "down":
            event.stop()
            self.action_cursor_down()
        elif key in ("left", "right"):
            event.stop()
            # show a simple modal informing this is TBD
            self.app.push_screen(_TBDModal())
        else:
            # ignore other keys
            event.stop()


class _TBDModal(ModalScreen):
    """Simple modal that shows a "TBD" message and closes on any key."""

    def compose(self) -> ComposeResult:
        yield Static(Text("TBD", style="bold"), id="tbd-msg")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.app.pop_screen()


class ThreeColumnApp(App):
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

    app = ThreeColumnApp(args.path)
    app.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""

Usage: python textual_three_column.py [path]
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal
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
            self.append(ListItem(Label(f"Error reading {path}: {exc}", style="red")))
            return

        self.clear()
        # Parent entry
        self.append(ListItem(Label("..", style="white on blue")))

        for name in entries:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                self.append(ListItem(Label(name, style="white on blue")))
            else:
                self.append(ListItem(Label(name)))


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
            yield FileList(id="left")
            # two minimal right columns
            yield Static("Right minimal 1", id="right1")
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

import inspect

import gitdiffnavtool
from gitdiffnavtool import AppBase


def _derived_appbase_classes():
    classes = []
    for _name, obj in inspect.getmembers(gitdiffnavtool, inspect.isclass):
        if not issubclass(obj, AppBase):
            continue
        if obj is AppBase:
            continue
        classes.append(obj)
    return classes


def test_all_appbase_derived_classes_discoverable():
    names = {cls.__name__ for cls in _derived_appbase_classes()}
    expected = {
        "RightSideBase",
        "FullScreenBase",
        "FileListBase",
        "FileModeFileList",
        "RepoModeFileList",
        "HistoryListBase",
        "FileModeHistoryList",
        "RepoModeHistoryList",
        "DiffList",
        "HelpList",
        "OpenFileList",
    }

    assert expected.issubset(names)


def test_derived_classes_construct_without_required_args():
    failures = []
    for cls in _derived_appbase_classes():
        try:
            cls()
        except Exception as exc:
            failures.append((cls.__name__, str(exc)))

    assert not failures, f"Failed constructors: {failures}"

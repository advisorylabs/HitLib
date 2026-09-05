"""The macOS path: the system frame, and the stand-in title bar behind it.

macOS draws a caption the app has no business reimplementing -- traffic
lights at the left, menus in the screen's menu bar, the green button doing
native fullscreen -- so window_chrome.install() hands back a NativeTitleBar
that adds nothing to the window.

The window still calls set_title(), sync_window_state() and
resize_grips.reposition() on it every time it moves or is renamed, so the
stand-in has to answer all of them. These tests force the darwin branch
rather than skipping off it, so a change that breaks the Mac build fails on
Windows CI too, where it would otherwise go unnoticed until a release.
"""

import sys

import pytest
from PySide6.QtCore import Qt

from pattern_studio import __version__, window_chrome
from pattern_studio.main_window import MainWindow


@pytest.fixture
def mac(monkeypatch):
    """Take the darwin branch wherever these actually run."""
    monkeypatch.setattr(sys, "platform", "darwin")


def test_window_keeps_the_system_frame(qapp, mac):
    win = MainWindow()
    assert not win.windowFlags() & Qt.FramelessWindowHint


def test_title_bar_is_the_native_stand_in(qapp, mac):
    win = MainWindow()
    assert isinstance(win.title_bar, window_chrome.NativeTitleBar)


def test_stand_in_takes_up_no_room(qapp, mac):
    """It still occupies a slot in the window's root layout, so a bar with
    any height would push a blank strip above the brand rule."""
    win = MainWindow()
    assert win.title_bar.height() == 0
    assert win.title_bar.isHidden()


def test_menus_are_built_on_a_bar_the_platform_can_hoist(qapp, mac):
    win = MainWindow()
    labels = [action.text() for action in win.title_bar.menu_bar.actions()]
    assert labels == ["&File", "&Export"]
    # Parented to the window rather than to the hidden stand-in: the platform
    # finds the menu bar to hoist by the window it belongs to, and a bar
    # hanging off a widget that never shows has none.
    assert win.title_bar.menu_bar.parent() is win


@pytest.mark.skipif(sys.platform != "darwin", reason="needs the cocoa plugin")
def test_menu_bar_really_is_the_system_one(qapp):
    """Only answerable where it happens: asking for a native menu bar is a
    request, and every platform plugin without one refuses it. Windows
    reports False here no matter what install() set."""
    win = MainWindow()
    assert win.title_bar.menu_bar.isNativeMenuBar()


def test_system_caption_carries_the_title(qapp, mac):
    """The stand-in writes no title of its own, so setWindowTitle() is the
    only thing naming the window and has to say everything."""
    win = MainWindow()
    assert win.windowTitle() == f"HitLib Pattern Studio v{__version__}"
    win.title_bar.set_title("HitLib Pattern Studio", __version__, "design.hlp")


def test_window_state_calls_are_all_answered(qapp, mac):
    """resizeEvent and changeEvent drive these on every move and resize."""
    win = MainWindow()
    win.show()
    qapp.processEvents()
    win.resize(1100, 700)
    qapp.processEvents()
    win.title_bar.sync_window_state()
    win.title_bar.resize_grips.reposition()


def test_windows_only_hooks_stay_out_of_the_way(qapp, mac):
    """showEvent and nativeEvent run unconditionally; on macOS each has to be
    a no-op rather than a Win32 call into nothing."""
    win = MainWindow()
    win.show()
    qapp.processEvents()
    window_chrome.round_corners(win)
    window_chrome.enable_native_snap(win)
    assert window_chrome.handle_native_event(win.title_bar, b"NSEvent", 0) is None

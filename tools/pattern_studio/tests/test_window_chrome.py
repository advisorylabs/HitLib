"""The app's own title bar, caption buttons and resize edges.

A frameless window gives up things the OS was doing for free, like moving,
resizing and the maximize/restore distinction. So what these tests pin is that
each of them is actually wired back up, and that the menus really did move
into the chrome row rather than leaving a second strip behind.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from pattern_studio import __version__, window_chrome
from pattern_studio.main_window import MainWindow


def test_window_is_frameless(qapp):
    win = MainWindow()
    assert win.windowFlags() & Qt.FramelessWindowHint


def test_menus_live_in_the_title_bar(qapp):
    """Not in a strip of QMainWindow's own. That strip is what the custom
    chrome exists to absorb."""
    win = MainWindow()
    labels = [action.text() for action in win.title_bar.menu_bar.actions()]
    assert labels == ["&File", "&Export"]
    assert win.title_bar.menu_bar.parent() is win.title_bar


def test_alt_mnemonic_still_opens_the_file_menu(qapp):
    """Taking the menu bar out of QMainWindow is exactly the kind of move that
    quietly costs you Alt+F."""
    win = MainWindow()
    win.show()
    qapp.processEvents()

    QTest.keyClick(win, Qt.Key_F, Qt.AltModifier)
    qapp.processEvents()
    active = win.title_bar.menu_bar.activeAction()
    assert active is not None and active.text() == "&File"

    QTest.keyClick(win.title_bar.menu_bar, Qt.Key_Escape)
    qapp.processEvents()


def test_title_bar_carries_name_version_and_file(qapp):
    win = MainWindow()
    assert win.title_bar.title_label.text() == "HitLib Pattern Studio"
    assert __version__ in win.title_bar.subtitle_label.text()

    win._current_file_path = Path("show.hlprofile")
    win._update_title()
    assert "show.hlprofile" in win.title_bar.subtitle_label.text()
    # The window title still matters even with the caption gone: it's what
    # the taskbar and Alt-Tab show.
    assert "show.hlprofile" in win.windowTitle()


def test_logo_sits_at_the_far_left(qapp):
    win = MainWindow()
    win.show()  # nothing has a position until the layout has actually run
    qapp.processEvents()
    bar = win.title_bar
    assert not bar.logo.pixmap().isNull()
    assert bar.logo.x() < bar.menu_bar.x() < bar.title_label.x()
    assert bar.title_label.x() < bar.minimize_btn.x()


def test_caption_buttons_run_minimize_maximize_close(qapp):
    win = MainWindow()
    win.show()

    win.title_bar.maximize_btn.click()
    assert win.isMaximized()
    assert win.title_bar.maximize_btn.kind == "restore"

    win.title_bar.maximize_btn.click()
    assert not win.isMaximized()
    assert win.title_bar.maximize_btn.kind == "maximize"

    win.title_bar.close_btn.click()
    assert not win.isVisible()


def test_double_clicking_the_bar_toggles_maximize(qapp):
    win = MainWindow()
    win.show()
    win.title_bar.toggle_maximized()
    assert win.isMaximized()
    win.title_bar.toggle_maximized()
    assert not win.isMaximized()


def test_resize_grips_hug_the_window_and_step_aside_when_maximized(qapp):
    win = MainWindow()
    win.show()
    win.resize(900, 600)
    qapp.processEvents()

    grips = win.title_bar.resize_grips
    assert len(grips._grips) == 8
    for grip, where in grips._grips:
        assert grip.isVisible(), where
        # Every grip has to touch the border it resizes from.
        assert (
            grip.x() == 0
            or grip.y() == 0
            or grip.geometry().right() >= win.width() - 1
            or grip.geometry().bottom() >= win.height() - 1
        ), where

    win.showMaximized()
    qapp.processEvents()
    assert not any(grip.isVisible() for grip, _ in grips._grips)


def test_round_corners_is_safe_to_call(qapp):
    """Best-effort by design: no window handle, no attribute, no exception."""
    win = MainWindow()
    window_chrome.round_corners(win)


# ----------------------------------------------------------------------
# Snap layouts
# ----------------------------------------------------------------------
#
# caption_message() is plain Python, so these run everywhere; only the MSG
# unpacking around it is Windows-only.


def _lparam(x: int, y: int) -> int:
    return (y & 0xFFFF) << 16 | (x & 0xFFFF)


def _shown(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    return win


def test_hit_test_claims_the_maximize_button(qapp):
    """Answering HTMAXBUTTON there is the whole trigger for the Windows 11
    snap layouts flyout."""
    win = _shown(qapp)
    button = win.title_bar.maximize_btn
    center = button.mapToGlobal(button.rect().center())

    result = window_chrome.caption_message(
        win.title_bar, window_chrome.WM_NCHITTEST, 0, _lparam(center.x(), center.y())
    )
    assert result == (True, window_chrome.HTMAXBUTTON)
    assert button.native_hover


def test_hit_test_leaves_the_rest_of_the_window_to_qt(qapp):
    win = _shown(qapp)
    win.title_bar.maximize_btn.set_native_state(hover=True)
    label = win.title_bar.title_label
    center = label.mapToGlobal(label.rect().center())

    result = window_chrome.caption_message(
        win.title_bar, window_chrome.WM_NCHITTEST, 0, _lparam(center.x(), center.y())
    )
    assert result is None
    assert not win.title_bar.maximize_btn.native_hover


def test_native_click_toggles_maximize(qapp):
    """Claiming the button costs it Qt's mouse events, so the press and
    release have to be handed back by hand."""
    win = _shown(qapp)
    button = win.title_bar.maximize_btn

    down = window_chrome.caption_message(
        win.title_bar, window_chrome.WM_NCLBUTTONDOWN, window_chrome.HTMAXBUTTON, 0
    )
    assert down == (True, 0), "an unswallowed press starts a caption drag"
    assert button.native_down

    window_chrome.caption_message(
        win.title_bar, window_chrome.WM_NCLBUTTONUP, window_chrome.HTMAXBUTTON, 0
    )
    qapp.processEvents()
    assert win.isMaximized()
    assert not button.native_down
    assert button.kind == "restore"


def test_native_messages_elsewhere_are_ignored(qapp):
    win = _shown(qapp)
    assert (
        window_chrome.caption_message(
            win.title_bar, window_chrome.WM_NCLBUTTONUP, 2, 0  # HTCAPTION
        )
        is None
    )


def test_hit_test_coordinates_survive_a_monitor_left_of_primary(qapp):
    """Screen coordinates are packed unsigned; read raw, a click on a monitor
    at negative x lands tens of thousands of pixels away."""
    assert window_chrome._signed_word(_lparam(-40, -12)) == -40
    assert window_chrome._signed_word(_lparam(-40, -12) >> 16) == -12
    assert window_chrome._signed_word(_lparam(1920, 8)) == 1920


def test_title_bar_pieces_share_one_baseline(themed):
    """A QMenuBar lays its items out from its own top edge, so stretching it
    to the full bar height left File/Export riding 5px above the title."""
    win = _shown(themed)
    bar = win.title_bar

    def baseline(y, height, metrics):
        return y + (height - metrics.height()) // 2 + metrics.ascent()

    item = bar.menu_bar.actionGeometry(bar.menu_bar.actions()[0])
    menu = baseline(bar.menu_bar.y() + item.y(), item.height(), bar.menu_bar.fontMetrics())
    title = baseline(
        bar.title_label.y(), bar.title_label.height(), bar.title_label.fontMetrics()
    )
    version = baseline(
        bar.subtitle_label.y(), bar.subtitle_label.height(), bar.subtitle_label.fontMetrics()
    )
    assert menu == title == version


def test_logo_is_not_marooned_from_the_menus(themed):
    win = _shown(themed)
    bar = win.title_bar
    item = bar.menu_bar.actionGeometry(bar.menu_bar.actions()[0])
    gap = bar.menu_bar.x() + item.x() - (bar.logo.x() + bar.logo.width())
    assert 0 <= gap <= 10, f"{gap}px between the logo and the File item"

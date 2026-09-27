"""Round-8 demo: the X Bookmarks plugin with CLEARLY FAKE demo data.

No network and no real X account: the X API is replaced by an in-process mock
transport (FAKE_TRANSPORT below) serving invented posts and locally generated
images. Output: demo/r8/ (library + config) and screenshots/r8_*.png.

    DISPLAY=:8 python scripts/demo_round8.py
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "r8"
SHOTS = ROOT / "screenshots"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["DAILY_VIBE_CONFIG_DIR"] = str(DEMO / "config")

import demo_round4 as r4  # noqa: E402

os.environ["DAILY_VIBE_CONFIG_DIR"] = str(DEMO / "config")  # demo_round4 sets its own on import
from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtGui import QIcon, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daily_vibe import secrets  # noqa: E402
from daily_vibe.config import Config  # noqa: E402
from daily_vibe.plugin_manager import PluginManager  # noqa: E402
from daily_vibe.storage import Journal  # noqa: E402
from daily_vibe.ui.main_window import ICON_PATH, MainWindow  # noqa: E402
from daily_vibe.xapi import net, oauth  # noqa: E402
from daily_vibe.xapi.tokens import TokenStore  # noqa: E402

FAKE_CLIENT_ID = "DEMO-FAKE-CLIENT-ID-not-a-real-app"


def log(m):
    print(m, flush=True)


def grab_screen(app, widgets, name):
    geo = widgets[0].frameGeometry()
    for w in widgets[1:]:
        geo = geo.united(w.frameGeometry())
    app.primaryScreen().grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height()).save(str(SHOTS / name))
    log(f"saved screenshots/{name}")


# Fake X API ---------------------------------------------------------------------------
def _fake_image(color, label) -> bytes:
    img = Image.new("RGB", (1200, 675), color)
    d = ImageDraw.Draw(img)
    for i in range(0, 1200, 60):
        d.line([(i, 675), (i + 300, 0)], fill=tuple(min(255, c + 25) for c in color), width=18)
    d.rectangle([40, 540, 1000, 640], fill=(20, 20, 30))
    try:
        from PIL import ImageFont
        font = ImageFont.load_default(size=44)
    except Exception:
        font = None
    d.text((64, 566), f"DEMO IMAGE (fake): {label}", fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


USERS = [
    {"id": "9001", "name": "Demo Author One (fake)", "username": "demo_fake_one"},
    {"id": "9002", "name": "Sample Person (fake)", "username": "sample_fake_two"},
    {"id": "9003", "name": "Placeholder Lab (fake)", "username": "placeholder_lab"},
]
now = dt.datetime.now(dt.timezone.utc)
iso = lambda h: (now - dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # noqa: E731
POSTS = [
    {"id": "1000000000000000003", "author_id": "9003", "created_at": iso(3),
     "text": "DEMO POST (fake data): our sample trail map is up https://t.co/demo1 https://t.co/pic3",
     "entities": {"urls": [
         {"url": "https://t.co/demo1", "expanded_url": "https://example.com/trail-map", "display_url": "example.com/trail-map"},
         {"url": "https://t.co/pic3", "expanded_url": "https://x.com/placeholder_lab/status/1000000000000000003/photo/1",
          "media_key": "3_demo_a"}]},
     "attachments": {"media_keys": ["3_demo_a"]}},
    {"id": "1000000000000000002", "author_id": "9002", "created_at": iso(20),
     "text": "Invented example text for the demo. Five things I learned writing a journal app:\n"
             "one, plain Markdown ages well &amp; two, keep your data local.",
     "referenced_tweets": [{"type": "quoted", "id": "1000000000000000000"}]},
    {"id": "1000000000000000001", "author_id": "9001", "created_at": iso(50),
     "text": "This is a fake bookmark used only for screenshots. Reading list: https://t.co/demo2",
     "entities": {"urls": [{"url": "https://t.co/demo2", "expanded_url": "https://example.org/reading-list"}]}},
]
QUOTED = {"id": "1000000000000000000", "author_id": "9001", "created_at": iso(80),
          "text": "Also fake: Markdown files are the most portable journal format https://t.co/q1",
          "entities": {"urls": [{"url": "https://t.co/q1", "expanded_url": "https://example.net/portable"}]}}
PAGE = {"data": POSTS,
        "includes": {"users": USERS, "tweets": [QUOTED],
                     "media": [{"media_key": "3_demo_a", "type": "photo", "alt_text": "Demo trail map (fake)",
                                "url": "https://pbs.twimg.com/media/DEMO_FAKE_A.jpg"}]},
        "meta": {"result_count": 3}}


def FAKE_TRANSPORT(method, url, *, params=None, data=None, headers=None, timeout=None):
    """Stands in for the network. Logs every request it would have made."""
    log(f"  [mock] {method} {url.split('?')[0]}")
    if url.startswith("https://api.x.com/2/users/") and "/bookmarks" in url:
        return net.Response(200, {"x-rate-limit-remaining": "179"}, json.dumps(PAGE).encode())
    if url.startswith("https://pbs.twimg.com/media/DEMO_FAKE_A"):
        return net.Response(200, {"content-type": "image/jpeg"}, _fake_image((70, 120, 90), "trail map"))
    return net.Response(404, {}, b"{}")


DAY_TEXT = """# Saturday

Slow morning, long walk by the creek. Spent the afternoon sorting my reading list.

"""


def main() -> int:
    shutil.rmtree(DEMO, ignore_errors=True)
    for old in SHOTS.glob("r8_*"):
        old.unlink()
    (DEMO / "config").mkdir(parents=True)
    net.request = FAKE_TRANSPORT          # everything X-related goes to the mock
    secrets._keyring = lambda: None      # demo tokens go to the (fake) fallback file under demo/r8/config

    today = dt.date.today()
    lib_root = DEMO / "Library"
    r4.DEMO = DEMO
    r4.build_library(lib_root, today)
    p = Journal(lib_root / "Personal")

    cfg = Config.load()
    cfg.journal_root = lib_root
    cfg.data["last_journal"] = "Personal"
    cfg.data["appearance"].update(mode="dark", dark_theme="Catppuccin Mocha", light_theme="Default Light")
    cfg.data["plugins"]["enabled"] = ["x_bookmarks"]
    cfg.data["plugins"]["x_bookmarks"] = {"client_id": FAKE_CLIENT_ID, "first_run": "Import last N",
                                          "first_run_count": 3, "download_media": True, "show_quoted": True}
    cfg.save()

    # 1) Run the real plugin against the mock API with a fake token -> today's entry.
    TokenStore().save(oauth.Token(access_token="FAKE-ACCESS", refresh_token="FAKE-REFRESH",
                                  expires_at=9e12, client_id=FAKE_CLIENT_ID, user_id="4242",
                                  username="demo_you_fake", name="Demo User (fake)"))
    pm = PluginManager(cfg, use_entry_points=False)
    result = pm.run_plugin(pm.plugins["x_bookmarks"], today, p)
    log(f"plugin ok={result.ok}")
    p.write(today, DAY_TEXT + pm.blocks_markdown([result]) + "\n")
    (DEMO / "sample_output.md").write_text(p.read(today), encoding="utf-8")
    TokenStore().clear()                  # settings screenshot shows "not connected"
    cfg.data["plugins"]["x_bookmarks"]["first_run"] = "Ask first"
    cfg.save()

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow(cfg)
    win.resize(1270, 820)
    win.show()
    r4.pump(app, 600)
    win.open_date(today, force=True)
    ed = win.editor
    block = ed.document().begin()
    while block.isValid() and "Slow morning" not in block.text():
        block = block.next()
    c = QTextCursor(block)
    ed.setTextCursor(c)
    ed.setFocus()
    ed.verticalScrollBar().setValue(0)
    r4.pump(app, 900)
    grab_screen(app, [win], "r8_02_live_preview_x_bookmarks.png")
    ed.verticalScrollBar().setValue(ed.verticalScrollBar().maximum())
    r4.pump(app, 700)
    grab_screen(app, [win], "r8_03_live_preview_x_bookmarks_bottom.png")

    # 2) Preferences → Plugins → X Bookmarks (not connected)
    win.open_settings()
    r4.pump(app, 300)
    dlg = win._settings_dialog
    dlg.resize(840, 735)
    dlg.move(win.frameGeometry().x() + 200, 0)
    dlg.tabs.setCurrentIndex(3)
    page = dlg.plugins_page
    for i in range(page.tree.topLevelItemCount()):
        if page.tree.topLevelItem(i).text(0) == "X Bookmarks":
            page.tree.setCurrentItem(page.tree.topLevelItem(i))
            page.tree.scrollToItem(page.tree.topLevelItem(i))
    split = page.tree.parentWidget()
    split.setSizes([118, 600])
    r4.pump(app, 300)
    page.tree.verticalScrollBar().setValue(page.tree.verticalScrollBar().maximum())
    r4.pump(app, 300)
    log("status: " + page.action_rows[("x_bookmarks", "account")][1].text())
    grab_screen(app, [dlg], "r8_01_settings_x_bookmarks_not_connected.png")
    dlg.reject()
    r4.pump(app, 200)
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DAILY_VIBE_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture(scope="session")
def qapp():
    import gc
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    # Tear Qt objects down while the QApplication is still alive (avoids
    # crashes at interpreter exit from out-of-order destruction).
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.processEvents()
    gc.collect()
    app.processEvents()


def wait_until(app, predicate, timeout=10.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return predicate()

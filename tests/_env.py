"""Import đầu tiên của mọi bài kiểm thử: cô lập cấu hình và dữ liệu khỏi cấu hình thật của người dùng (xem bài học 'test ghi đè data_dir')."""
import atexit
import os
import shutil
import tempfile

if "KEVIT_SETTINGS_FILE" not in os.environ:
    _tmp = tempfile.mkdtemp(prefix="kevit-test-")
    atexit.register(shutil.rmtree, _tmp, ignore_errors=True)
    os.environ["KEVIT_SETTINGS_FILE"] = os.path.join(_tmp, "settings.ini")
    os.environ["VEO_DATA_DIR"] = os.path.join(_tmp, "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
assert "kevit-test-" in os.environ["KEVIT_SETTINGS_FILE"] or os.environ.get("KEVIT_TEST_OK"), "Test phải dùng file cấu hình riêng"

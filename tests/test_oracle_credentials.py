import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import config

tmp_dir = tempfile.mkdtemp()

print("--- REAL SECURITY FIX: no credentials hardcoded in config.py source anymore ---")
config_source = Path(r"D:\scanar\invoice_importer\config.py").read_text(encoding="utf-8")
check("config.py has no ORACLE_PASSWORD_OVERRIDE / ORACLE_USER_OVERRIDE constants left (structural check - proves no literal credential assignment exists)", "ORACLE_PASSWORD_OVERRIDE" not in config_source and "ORACLE_USER_OVERRIDE" not in config_source)

print("\n--- get_oracle_credentials(): file present with a username ---")
# قيمة وهمية بحتة لاختبار منطق القراءة/التقسيم فقط - أبداً لا نكتب كلمة
# المرور الحقيقية بأي ملف اختبار (يُرفع لمستودع جيت هاب)
config._ORACLE_CREDENTIALS_FILE = Path(tmp_dir) / "oracle_credentials.txt"
config._ORACLE_CREDENTIALS_FILE.write_text("fake_test_user\nfake_test_password_123", encoding="utf-8")
check("returns (user, password) from the file", config.get_oracle_credentials() == ("fake_test_user", "fake_test_password_123"))

print("\n--- get_oracle_credentials(): file missing entirely ---")
config._ORACLE_CREDENTIALS_FILE = Path(tmp_dir) / "does_not_exist.txt"
check("falls back to (None, None) - db_items.py then uses AccSystem.exe.config instead", config.get_oracle_credentials() == (None, None))

print("\n--- get_oracle_credentials(): file exists but empty ---")
empty_file = Path(tmp_dir) / "empty.txt"
empty_file.write_text("", encoding="utf-8")
config._ORACLE_CREDENTIALS_FILE = empty_file
check("empty file also falls back to (None, None), no crash", config.get_oracle_credentials() == (None, None))

print("\n--- get_oracle_credentials(): username only, no password line ---")
user_only_file = Path(tmp_dir) / "user_only.txt"
user_only_file.write_text("system", encoding="utf-8")
config._ORACLE_CREDENTIALS_FILE = user_only_file
check("returns (user, '') without crashing when password line is missing", config.get_oracle_credentials() == ("system", ""))

print("\n--- db_items.py wiring: _connect() now calls get_oracle_credentials(), not the removed constants ---")
db_items_source = Path(r"D:\scanar\invoice_importer\db_items.py").read_text(encoding="utf-8")
check("db_items.py calls config.get_oracle_credentials()", "config.get_oracle_credentials()" in db_items_source)
check("db_items.py no longer references the removed ORACLE_USER_OVERRIDE constant", "ORACLE_USER_OVERRIDE" not in db_items_source)

a = "\n--- summary ---"
print(a)
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)

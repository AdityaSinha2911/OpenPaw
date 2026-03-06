"""Quick validation script for path rule fixes."""
import os
import file_tools
import ollama_connector
import main as _main

home = os.path.expanduser("~")
p = ollama_connector.SYSTEM_PROMPT

checks = [
    ("home path injected into prompt",       home in p),
    ("no forward-slash C:/Users/ in prompt", "C:/Users/" not in p),
    ("old hardcoded machine text gone",      "adity's Windows machine" not in p),
    ("find_path action in prompt",           "find_path" in p),
    ("scan_temp action in prompt",           "scan_temp" in p),
    ("scan_apps action in prompt",           "scan_apps" in p),
    ("rglob abs pattern no crash",           "Non-relative" not in file_tools.search_files(home, "/abs_pattern", [])),
    ("ALLOWED_DIRS are absolute paths",      all(os.path.isabs(d) for d in _main.ALLOWED_DIRS)),
    ("scan_temp_files callable",             "Error" not in file_tools.scan_temp_files(2)),
    ("scan_app_sizes callable",              "Error" not in file_tools.scan_app_sizes(2)),
]

all_pass = True
for label, ok in checks:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}")
    if not ok:
        all_pass = False

print()
print("ALL CHECKS PASSED" if all_pass else "SOME CHECKS FAILED")

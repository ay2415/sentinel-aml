"""Smoke test: render every dashboard page for every role against a running API (set API_URL).
Run: API_URL=http://localhost:8000 python frontend/test_dashboard.py"""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).parent))
from api_client import Api  # noqa: E402

USERS = [("emerald", "alice.analyst"), ("emerald", "ivan.investigator"), ("emerald", "maeve.mlro"),
         ("emerald", "aoife.auditor"), ("emerald", "adam.admin"), ("liffey", "lorcan.mlro")]


def main() -> int:
    from pages_config import ROLE_PAGES
    failures = []
    for tenant, user in USERS:
        client = Api()
        data = client.login(tenant, user, "Demo!Passw0rd")
        for page in ROLE_PAGES[data["role"]]:
            at = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=120)
            at.session_state["api"] = client
            at.session_state["role"], at.session_state["tenant"], at.session_state["username"] = data["role"], tenant, user
            at.run()
            at.sidebar.radio[0].set_value(page).run()
            errs = [e.value for e in at.error] + [str(e.value) for e in at.exception]
            status = "ok" if not errs else f"FAIL {errs[:1]}"
            print(f"{tenant}/{user:18s} {page:26s} {status}")
            if errs:
                failures.append((user, page, errs))
    print(f"{len(failures)} failing page renders")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""Run from the project folder:  python diag_pdf.py
Logs in with the demo account, runs the sample forecast, calls the PDF
export and prints the real error if there is one."""
import traceback
import app as appmod

flask_app = appmod.app
flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["PROPAGATE_EXCEPTIONS"] = True
flask_app.config["TESTING"] = False

c = flask_app.test_client()
try:
    r = c.post("/login", data={"email": "engineer@hacksphere.local", "password": "Grid@2026"})
    print("login:", r.status_code)
    r = c.post("/api/forecast/sample")
    print("sample forecast:", r.status_code)
    r = c.get("/api/export/pdf")
    print("pdf export:", r.status_code, r.content_type)
except Exception:
    print("\n===== REAL ERROR =====")
    traceback.print_exc()

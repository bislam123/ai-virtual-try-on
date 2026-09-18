#!/usr/bin/env python3
"""Pre-production smoke test -- see docs/DEPLOYMENT.md section 17.

Exercises a REAL, running instance of this application end to end over
the network (local dev, staging, or a genuine deployment) -- distinct
from the automated pytest suite, which uses an in-process TestClient and
never actually boots a real server or the real AI pipeline. This script
is deliberately NOT run by CI (.github/workflows/ci.yml never boots a
full instance, including the real model) and needs no GPU/external
provider itself -- it only confirms job *submission* and *status
polling* work, never waiting for a real AI generation to finish (per
this project's own measured Tesla T4 benchmark, ~8 minutes; a CPU one can
take over an hour -- see docs/DEPLOYMENT.md's GPU section).

Creates and cleans up two throwaway accounts of its own -- never touches
real user data, and never requires the operator to have valid GPU/SMTP
credentials to run it (forgot-password's response is checked, not
whether a real inbox actually received anything).

Usage:
    ai\\.venv\\Scripts\\python.exe deploy\\smoke_test.py
    ai\\.venv\\Scripts\\python.exe deploy\\smoke_test.py --base-url https://your-deployment.example.com --cors-origin https://your-frontend.example.com
"""

import argparse
import io
import sys
import uuid

import httpx
from PIL import Image


class SmokeTestFailure(Exception):
    pass


def _png_bytes(color: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=color).save(buf, format="PNG")
    return buf.getvalue()


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {name}")
    else:
        raise SmokeTestFailure(f"{name} -- {detail}")


def run(base_url: str, cors_origin: str) -> None:
    client = httpx.Client(base_url=base_url, timeout=30.0)
    email = f"smoke-test-{uuid.uuid4().hex}@example.com"
    other_email = f"smoke-test-other-{uuid.uuid4().hex}@example.com"
    password = "smoke-test-correct-horse-battery-999"

    print(f"Smoke test against {base_url}\n")

    print("1. Health")
    resp = client.get("/health")
    check("GET /health returns 200", resp.status_code == 200, resp.text)

    print("2. CORS")
    resp = client.options(
        "/api/try-on",
        headers={"Origin": "https://an-untrusted-origin.example.com", "Access-Control-Request-Method": "POST"},
    )
    check(
        "disallowed origin is not granted CORS access",
        "access-control-allow-origin" not in resp.headers,
        f"got {resp.headers.get('access-control-allow-origin')!r}",
    )
    if cors_origin:
        resp = client.options(
            "/api/try-on", headers={"Origin": cors_origin, "Access-Control-Request-Method": "POST"}
        )
        check(
            f"configured frontend origin {cors_origin} is granted CORS access",
            resp.headers.get("access-control-allow-origin") == cors_origin,
            resp.text,
        )
    else:
        print("  SKIP configured-origin CORS check (--cors-origin not given)")

    print("3. Body size rejection")
    oversized = b"0" * (30 * 1024 * 1024)
    resp = client.post(
        "/api/try-on",
        files={
            "person_image": ("p.bin", oversized, "image/png"),
            "garment_image": ("g.png", _png_bytes("blue"), "image/png"),
        },
        data={"category": "tops"},
    )
    check("oversized request rejected with 413", resp.status_code == 413, resp.text)

    print("4. Signup")
    resp = client.post("/api/auth/signup", json={"email": email, "password": password})
    check("signup succeeds", resp.status_code == 201, resp.text)
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    print("5. Login")
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    check("login succeeds", resp.status_code == 200, resp.text)

    print("6. Password reset request (no account-existence enumeration)")
    known = client.post("/api/auth/forgot-password", json={"email": email})
    unknown = client.post("/api/auth/forgot-password", json={"email": f"nobody-{uuid.uuid4().hex}@example.com"})
    check("forgot-password returns 200 for a known email", known.status_code == 200, known.text)
    check(
        "forgot-password response is identical for known/unknown email",
        known.json() == unknown.json(),
        f"{known.json()} != {unknown.json()}",
    )

    print("7. Try-on submission, status, and ownership protection")
    resp = client.post(
        "/api/try-on",
        files={
            "person_image": ("p.png", _png_bytes("red"), "image/png"),
            "garment_image": ("g.png", _png_bytes("green"), "image/png"),
        },
        data={"category": "tops"},
        headers=headers,
    )
    check("try-on submission accepted (202)", resp.status_code == 202, resp.text)
    job_id = resp.json()["job_id"]

    resp = client.get(f"/api/try-on/{job_id}", headers=headers)
    check("job status reachable by its owner", resp.status_code == 200, resp.text)
    check(
        "job status is a real, valid value",
        resp.json()["status"] in {"pending", "processing", "completed", "failed"},
        resp.text,
    )

    other_signup = client.post("/api/auth/signup", json={"email": other_email, "password": password})
    check("second (ownership-test) account signs up", other_signup.status_code == 201, other_signup.text)
    other_headers = {"Authorization": f"Bearer {other_signup.json()['access_token']}"}
    resp = client.get(f"/api/try-on/{job_id}", headers=other_headers)
    check("a different signed-in user cannot view this job (403)", resp.status_code == 403, resp.text)

    # 200 if the job happened to finish already (unlikely in a smoke
    # test's short window -- see module docstring), 409 ("not ready
    # yet") if not -- both are the correct, expected response.
    resp = client.get(f"/api/try-on/{job_id}/result", headers=headers)
    check(
        "result endpoint responds sensibly (200 if already done, 409 if not ready yet)",
        resp.status_code in {200, 409},
        resp.text,
    )

    print("8. Admin access (expected refusal for a non-admin account)")
    resp = client.get("/api/admin/users", headers=headers)
    check("non-admin account is refused admin access (403)", resp.status_code == 403, resp.text)

    print("9. Account deletion (both throwaway accounts)")
    resp = client.request("DELETE", "/api/auth/me", json={"password": password}, headers=headers)
    check("primary account deletion succeeds", resp.status_code == 204, resp.text)
    resp = client.get("/api/auth/me", headers=headers)
    check("token is invalid immediately after account deletion", resp.status_code == 401, resp.text)

    resp = client.request("DELETE", "/api/auth/me", json={"password": password}, headers=other_headers)
    check("second account deletion succeeds", resp.status_code == 204, resp.text)

    client.close()
    print("\nAll smoke test checks passed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--cors-origin",
        default="",
        help="A frontend origin AITRYON_CORS_ORIGINS actually allows, to verify CORS is really configured (optional).",
    )
    args = parser.parse_args()
    try:
        run(args.base_url, args.cors_origin)
    except SmokeTestFailure as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1
    except httpx.HTTPError as e:
        print(f"\nFAILED (network error): {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

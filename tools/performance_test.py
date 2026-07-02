"""Small stdlib-only HTTP performance test runner for SecureTest.

The default profile intentionally hits only safe public GET endpoints. Use this
for thesis evidence without mutating database records or sending emails.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener


DEFAULT_ENDPOINTS = [
    ("GET", "/", "Root redirect"),
    ("GET", "/login", "Login page"),
    ("GET", "/register", "Register page"),
    ("GET", "/admin-login", "Admin login page"),
    ("GET", "/static/styles.css", "Main stylesheet"),
]


@dataclass
class Sample:
    started_at: str
    method: str
    path: str
    label: str
    status: int
    elapsed_ms: float
    bytes_read: int
    error: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a safe HTTP performance test.")
    parser.add_argument(
        "--target",
        default="https://classiface-hardened.onrender.com",
        help="Base URL to test. Default: deployed Render URL.",
    )
    parser.add_argument("--duration", type=float, default=60.0, help="Test duration in seconds.")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent virtual users.")
    parser.add_argument("--warmup", type=int, default=5, help="Sequential warm-up requests before measuring.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    parser.add_argument("--think-time", type=float, default=0.2, help="Delay between worker requests in seconds.")
    parser.add_argument(
        "--out-dir",
        default="reports/performance",
        help="Directory for CSV, JSON, and Markdown outputs.",
    )
    parser.add_argument(
        "--name",
        default="render-public",
        help="Output filename prefix.",
    )
    return parser.parse_args()


def make_opener():
    return build_opener(HTTPCookieProcessor(CookieJar()))


def fetch(base_url: str, endpoint: tuple[str, str, str], timeout: float) -> Sample:
    method, path, label = endpoint
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    started_at = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()
    status = 0
    bytes_read = 0
    error = ""
    opener = make_opener()

    try:
        request = Request(
            url,
            method=method,
            headers={
                "User-Agent": "SecureTestPerformanceTest/1.0",
                "Accept": "text/html,application/json,text/css,*/*",
            },
        )
        with opener.open(request, timeout=timeout) as response:
            body = response.read()
            status = int(response.getcode() or 0)
            bytes_read = len(body)
    except HTTPError as exc:
        status = int(exc.code or 0)
        try:
            bytes_read = len(exc.read() or b"")
        except Exception:
            bytes_read = 0
        error = f"HTTPError: {exc.reason}"
    except URLError as exc:
        error = f"URLError: {exc.reason}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return Sample(started_at, method, path, label, status, elapsed_ms, bytes_read, error)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarise(samples: Iterable[Sample], elapsed_seconds: float) -> dict:
    rows = list(samples)
    latencies = [s.elapsed_ms for s in rows]
    successes = [s for s in rows if 200 <= s.status < 400 and not s.error]
    failures = [s for s in rows if not (200 <= s.status < 400 and not s.error)]

    summary = {
        "requests": len(rows),
        "successes": len(successes),
        "failures": len(failures),
        "success_rate_percent": round((len(successes) / len(rows) * 100.0) if rows else 0.0, 2),
        "throughput_rps": round((len(rows) / elapsed_seconds) if elapsed_seconds > 0 else 0.0, 2),
        "avg_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "median_ms": round(statistics.median(latencies), 2) if latencies else 0.0,
        "min_ms": round(min(latencies), 2) if latencies else 0.0,
        "max_ms": round(max(latencies), 2) if latencies else 0.0,
        "p90_ms": round(percentile(latencies, 90), 2),
        "p95_ms": round(percentile(latencies, 95), 2),
        "p99_ms": round(percentile(latencies, 99), 2),
    }

    by_path = {}
    for path in sorted({s.path for s in rows}):
        path_rows = [s for s in rows if s.path == path]
        path_latencies = [s.elapsed_ms for s in path_rows]
        path_successes = [s for s in path_rows if 200 <= s.status < 400 and not s.error]
        by_path[path] = {
            "requests": len(path_rows),
            "success_rate_percent": round((len(path_successes) / len(path_rows) * 100.0) if path_rows else 0.0, 2),
            "avg_ms": round(statistics.mean(path_latencies), 2) if path_latencies else 0.0,
            "median_ms": round(statistics.median(path_latencies), 2) if path_latencies else 0.0,
            "p95_ms": round(percentile(path_latencies, 95), 2),
            "max_ms": round(max(path_latencies), 2) if path_latencies else 0.0,
        }

    return {"overall": summary, "by_path": by_path}


def write_outputs(args: argparse.Namespace, samples: list[Sample], summary: dict, elapsed_seconds: float) -> dict:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = out_dir / f"{args.name}-{stamp}"

    csv_path = prefix.with_suffix(".csv")
    json_path = prefix.with_suffix(".json")
    md_path = prefix.with_suffix(".md")

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(samples[0]).keys()) if samples else [])
        if samples:
            writer.writeheader()
            for sample in samples:
                writer.writerow(asdict(sample))

    metadata = {
        "target": args.target,
        "duration_seconds_configured": args.duration,
        "elapsed_seconds_actual": round(elapsed_seconds, 2),
        "concurrency": args.concurrency,
        "warmup_requests": args.warmup,
        "timeout_seconds": args.timeout,
        "think_time_seconds": args.think_time,
        "endpoints": [{"method": m, "path": p, "label": l} for m, p, l in DEFAULT_ENDPOINTS],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump({"metadata": metadata, "summary": summary}, handle, indent=2)

    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("# SecureTest Performance Test Report\n\n")
        handle.write("## Test Configuration\n\n")
        handle.write(f"- Target URL: `{args.target}`\n")
        handle.write(f"- Duration: `{args.duration}` seconds\n")
        handle.write(f"- Actual elapsed time: `{elapsed_seconds:.2f}` seconds\n")
        handle.write(f"- Concurrent virtual users: `{args.concurrency}`\n")
        handle.write(f"- Warm-up requests: `{args.warmup}`\n")
        handle.write(f"- Think time per virtual user: `{args.think_time}` seconds\n")
        handle.write("- Scope: safe public GET endpoints only; no account creation, email sending, quiz submission, or delete/update actions.\n\n")

        overall = summary["overall"]
        handle.write("## Overall Results\n\n")
        handle.write("| Metric | Value |\n|---|---:|\n")
        for key, value in overall.items():
            handle.write(f"| {key} | {value} |\n")

        handle.write("\n## Per-Endpoint Results\n\n")
        handle.write("| Endpoint | Requests | Success Rate | Average ms | Median ms | P95 ms | Max ms |\n")
        handle.write("|---|---:|---:|---:|---:|---:|---:|\n")
        for path, item in summary["by_path"].items():
            handle.write(
                f"| `{path}` | {item['requests']} | {item['success_rate_percent']}% | "
                f"{item['avg_ms']} | {item['median_ms']} | {item['p95_ms']} | {item['max_ms']} |\n"
            )

        handle.write("\n## Thesis Interpretation\n\n")
        handle.write(
            "The test measured HTTP response time, throughput, and success rate under a controlled low-load scenario. "
            "Because the test used public pages only, these figures represent baseline web interface responsiveness rather than "
            "authenticated workflows such as quiz submission, face verification, or administrative database operations.\n"
        )

    return {"csv": str(csv_path), "json": str(json_path), "markdown": str(md_path)}


def main() -> int:
    args = parse_args()
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be at least 1")
    if args.duration <= 0:
        raise SystemExit("--duration must be greater than 0")

    print(f"Target: {args.target}")
    print(f"Warm-up requests: {args.warmup}")
    for index in range(args.warmup):
        sample = fetch(args.target, DEFAULT_ENDPOINTS[index % len(DEFAULT_ENDPOINTS)], args.timeout)
        print(f"warmup {index + 1}/{args.warmup}: {sample.path} {sample.status} {sample.elapsed_ms:.1f} ms")

    samples: list[Sample] = []
    lock = threading.Lock()
    stop_at = time.perf_counter() + args.duration
    start_at = time.perf_counter()

    def worker(worker_id: int) -> None:
        random.seed(worker_id + int(time.time()))
        while time.perf_counter() < stop_at:
            endpoint = random.choice(DEFAULT_ENDPOINTS)
            sample = fetch(args.target, endpoint, args.timeout)
            with lock:
                samples.append(sample)
            if args.think_time > 0:
                time.sleep(args.think_time)

    print(f"Running test: concurrency={args.concurrency}, duration={args.duration}s")
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(worker, i) for i in range(args.concurrency)]
        for future in futures:
            future.result()

    elapsed = time.perf_counter() - start_at
    summary = summarise(samples, elapsed)
    paths = write_outputs(args, samples, summary, elapsed)

    print("\nOverall summary")
    for key, value in summary["overall"].items():
        print(f"{key}: {value}")

    print("\nOutput files")
    for key, value in paths.items():
        print(f"{key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

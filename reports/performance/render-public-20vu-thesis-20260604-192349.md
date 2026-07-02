# SecureTest Performance Test Report

## Test Configuration

- Target URL: `https://classiface-hardened.onrender.com`
- Duration: `30.0` seconds
- Actual elapsed time: `30.98` seconds
- Concurrent virtual users: `20`
- Warm-up requests: `5`
- Think time per virtual user: `0.5` seconds
- Scope: safe public GET endpoints only; no account creation, email sending, quiz submission, or delete/update actions.

## Overall Results

| Metric | Value |
|---|---:|
| requests | 646 |
| successes | 646 |
| failures | 0 |
| success_rate_percent | 100.0 |
| throughput_rps | 20.85 |
| avg_ms | 426.56 |
| median_ms | 372.5 |
| min_ms | 240.57 |
| max_ms | 1204.0 |
| p90_ms | 681.08 |
| p95_ms | 737.56 |
| p99_ms | 825.19 |

## Per-Endpoint Results

| Endpoint | Requests | Success Rate | Average ms | Median ms | P95 ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|
| `/` | 128 | 100.0% | 677.42 | 671.76 | 814.28 | 1204.0 |
| `/admin-login` | 108 | 100.0% | 354.04 | 339.9 | 475.95 | 741.76 |
| `/login` | 147 | 100.0% | 362.05 | 356.17 | 475.91 | 973.27 |
| `/register` | 138 | 100.0% | 376.11 | 363.37 | 488.98 | 853.0 |
| `/static/styles.css` | 125 | 100.0% | 363.92 | 353.47 | 469.68 | 625.43 |

## Thesis Interpretation

The test measured HTTP response time, throughput, and success rate under a controlled low-load scenario. Because the test used public pages only, these figures represent baseline web interface responsiveness rather than authenticated workflows such as quiz submission, face verification, or administrative database operations.

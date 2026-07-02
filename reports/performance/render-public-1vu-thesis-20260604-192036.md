# ClassiFace Performance Test Report

## Test Configuration

- Target URL: `https://classiface-hardened.onrender.com`
- Duration: `30.0` seconds
- Actual elapsed time: `30.53` seconds
- Concurrent virtual users: `1`
- Warm-up requests: `5`
- Think time per virtual user: `0.5` seconds
- Scope: safe public GET endpoints only; no account creation, email sending, quiz submission, or delete/update actions.

## Overall Results

| Metric | Value |
|---|---:|
| requests | 36 |
| successes | 36 |
| failures | 0 |
| success_rate_percent | 100.0 |
| throughput_rps | 1.18 |
| avg_ms | 339.22 |
| median_ms | 290.45 |
| min_ms | 246.11 |
| max_ms | 603.62 |
| p90_ms | 546.57 |
| p95_ms | 550.88 |
| p99_ms | 588.96 |

## Per-Endpoint Results

| Endpoint | Requests | Success Rate | Average ms | Median ms | P95 ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|
| `/` | 7 | 100.0% | 547.7 | 546.7 | 591.06 | 603.62 |
| `/admin-login` | 9 | 100.0% | 281.16 | 279.55 | 331.12 | 338.54 |
| `/login` | 11 | 100.0% | 294.25 | 287.75 | 350.38 | 360.5 |
| `/register` | 5 | 100.0% | 291.17 | 279.98 | 338.8 | 346.52 |
| `/static/styles.css` | 4 | 100.0% | 288.73 | 290.12 | 304.7 | 306.74 |

## Thesis Interpretation

The test measured HTTP response time, throughput, and success rate under a controlled low-load scenario. Because the test used public pages only, these figures represent baseline web interface responsiveness rather than authenticated workflows such as quiz submission, face verification, or administrative database operations.

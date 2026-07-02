# ClassiFace Performance Test Report

## Test Configuration

- Target URL: `https://classiface-hardened.onrender.com`
- Duration: `30.0` seconds
- Actual elapsed time: `31.04` seconds
- Concurrent virtual users: `10`
- Warm-up requests: `5`
- Think time per virtual user: `0.5` seconds
- Scope: safe public GET endpoints only; no account creation, email sending, quiz submission, or delete/update actions.

## Overall Results

| Metric | Value |
|---|---:|
| requests | 349 |
| successes | 349 |
| failures | 0 |
| success_rate_percent | 100.0 |
| throughput_rps | 11.24 |
| avg_ms | 363.56 |
| median_ms | 303.91 |
| min_ms | 233.71 |
| max_ms | 802.25 |
| p90_ms | 583.03 |
| p95_ms | 610.94 |
| p99_ms | 659.7 |

## Per-Endpoint Results

| Endpoint | Requests | Success Rate | Average ms | Median ms | P95 ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|
| `/` | 82 | 100.0% | 578.25 | 571.4 | 635.91 | 793.29 |
| `/admin-login` | 69 | 100.0% | 293.9 | 281.25 | 348.81 | 751.62 |
| `/login` | 67 | 100.0% | 293.29 | 290.93 | 343.48 | 363.79 |
| `/register` | 62 | 100.0% | 297.3 | 299.81 | 344.76 | 362.93 |
| `/static/styles.css` | 69 | 100.0% | 305.86 | 297.75 | 374.25 | 802.25 |

## Thesis Interpretation

The test measured HTTP response time, throughput, and success rate under a controlled low-load scenario. Because the test used public pages only, these figures represent baseline web interface responsiveness rather than authenticated workflows such as quiz submission, face verification, or administrative database operations.

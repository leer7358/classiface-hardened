# ClassiFace Performance Test Report

## Test Configuration

- Target URL: `https://classiface-hardened.onrender.com`
- Duration: `30.0` seconds
- Actual elapsed time: `31.26` seconds
- Concurrent virtual users: `50`
- Warm-up requests: `5`
- Think time per virtual user: `0.5` seconds
- Scope: safe public GET endpoints only; no account creation, email sending, quiz submission, or delete/update actions.

## Overall Results

| Metric | Value |
|---|---:|
| requests | 1038 |
| successes | 1038 |
| failures | 0 |
| success_rate_percent | 100.0 |
| throughput_rps | 33.2 |
| avg_ms | 911.06 |
| median_ms | 862.72 |
| min_ms | 270.17 |
| max_ms | 2316.58 |
| p90_ms | 1301.32 |
| p95_ms | 1573.71 |
| p99_ms | 1991.19 |

## Per-Endpoint Results

| Endpoint | Requests | Success Rate | Average ms | Median ms | P95 ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|
| `/` | 214 | 100.0% | 1345.55 | 1258.85 | 1988.3 | 2316.58 |
| `/admin-login` | 186 | 100.0% | 766.02 | 761.36 | 1137.39 | 1553.81 |
| `/login` | 217 | 100.0% | 803.39 | 807.51 | 1132.63 | 1625.54 |
| `/register` | 214 | 100.0% | 833.65 | 835.16 | 1151.4 | 1764.53 |
| `/static/styles.css` | 207 | 100.0% | 785.11 | 804.16 | 1073.01 | 1292.97 |

## Thesis Interpretation

The test measured HTTP response time, throughput, and success rate under a controlled low-load scenario. Because the test used public pages only, these figures represent baseline web interface responsiveness rather than authenticated workflows such as quiz submission, face verification, or administrative database operations.

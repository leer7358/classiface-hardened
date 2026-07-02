# SecureTest Performance Test Report

## Test Configuration

- Target URL: `https://classiface-hardened.onrender.com`
- Duration: `30.0` seconds
- Actual elapsed time: `31.02` seconds
- Concurrent virtual users: `30`
- Warm-up requests: `5`
- Think time per virtual user: `0.5` seconds
- Scope: safe public GET endpoints only; no account creation, email sending, quiz submission, or delete/update actions.

## Overall Results

| Metric | Value |
|---|---:|
| requests | 829 |
| successes | 829 |
| failures | 0 |
| success_rate_percent | 100.0 |
| throughput_rps | 26.73 |
| avg_ms | 564.5 |
| median_ms | 542.08 |
| min_ms | 249.8 |
| max_ms | 1588.3 |
| p90_ms | 804.35 |
| p95_ms | 939.18 |
| p99_ms | 1050.66 |

## Per-Endpoint Results

| Endpoint | Requests | Success Rate | Average ms | Median ms | P95 ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|
| `/` | 145 | 100.0% | 838.51 | 822.4 | 1048.69 | 1588.3 |
| `/admin-login` | 169 | 100.0% | 496.17 | 500.03 | 662.27 | 969.91 |
| `/login` | 189 | 100.0% | 506.9 | 510.68 | 677.93 | 1210.45 |
| `/register` | 175 | 100.0% | 515.18 | 528.96 | 656.58 | 818.2 |
| `/static/styles.css` | 151 | 100.0% | 507.12 | 512.06 | 659.54 | 1049.85 |

## Thesis Interpretation

The test measured HTTP response time, throughput, and success rate under a controlled low-load scenario. Because the test used public pages only, these figures represent baseline web interface responsiveness rather than authenticated workflows such as quiz submission, face verification, or administrative database operations.

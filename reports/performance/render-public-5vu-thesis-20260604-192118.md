# SecureTest Performance Test Report

## Test Configuration

- Target URL: `https://classiface-hardened.onrender.com`
- Duration: `30.0` seconds
- Actual elapsed time: `30.66` seconds
- Concurrent virtual users: `5`
- Warm-up requests: `5`
- Think time per virtual user: `0.5` seconds
- Scope: safe public GET endpoints only; no account creation, email sending, quiz submission, or delete/update actions.

## Overall Results

| Metric | Value |
|---|---:|
| requests | 172 |
| successes | 172 |
| failures | 0 |
| success_rate_percent | 100.0 |
| throughput_rps | 5.61 |
| avg_ms | 374.97 |
| median_ms | 308.57 |
| min_ms | 243.57 |
| max_ms | 981.78 |
| p90_ms | 590.73 |
| p95_ms | 614.57 |
| p99_ms | 695.65 |

## Per-Endpoint Results

| Endpoint | Requests | Success Rate | Average ms | Median ms | P95 ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|
| `/` | 44 | 100.0% | 577.75 | 575.67 | 646.69 | 677.68 |
| `/admin-login` | 34 | 100.0% | 311.48 | 300.96 | 374.48 | 739.66 |
| `/login` | 31 | 100.0% | 294.53 | 289.46 | 343.95 | 399.2 |
| `/register` | 33 | 100.0% | 318.24 | 296.75 | 357.05 | 981.78 |
| `/static/styles.css` | 30 | 100.0% | 295.02 | 287.9 | 342.28 | 373.73 |

## Thesis Interpretation

The test measured HTTP response time, throughput, and success rate under a controlled low-load scenario. Because the test used public pages only, these figures represent baseline web interface responsiveness rather than authenticated workflows such as quiz submission, face verification, or administrative database operations.

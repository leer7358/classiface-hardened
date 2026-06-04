# ClassiFace Performance Test Results

Test date: June 4, 2026  
Target system: `https://classiface-hardened.onrender.com`  
Test tool: `tools/performance_test.py`  
Deployment platform: Render  

## Objective

The performance test measured the baseline responsiveness, throughput, and reliability of the deployed ClassiFace web application under controlled public-page load. The test was designed to be safe for production by using read-only public GET requests only.

## Test Scope

The following endpoints were included:

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Root route / redirect |
| GET | `/login` | Login page |
| GET | `/register` | Registration page |
| GET | `/admin-login` | Admin login page |
| GET | `/static/styles.css` | Main stylesheet |

The test did not create accounts, send forgot-password emails, submit quizzes, capture camera frames, delete records, or update database records.

## Test Configuration

| Load Level | Duration | Concurrent Virtual Users | Warm-up Requests | Think Time |
|---|---:|---:|---:|---:|
| Baseline | 30 seconds | 1 | 5 | 0.5 seconds |
| Normal load | 30 seconds | 5 | 5 | 0.5 seconds |
| Moderate load | 30 seconds | 10 | 5 | 0.5 seconds |

Each test used a 30-second request timeout. Warm-up requests were excluded from the measured results.

## Overall Results

| Load Level | Requests | Success Rate | Throughput | Average Response Time | Median | P90 | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 virtual user | 38 | 100.00% | 1.24 req/s | 296.22 ms | 265.50 ms | 364.66 ms | 539.92 ms | 575.31 ms | 587.87 ms |
| 5 virtual users | 181 | 100.00% | 5.88 req/s | 336.97 ms | 286.60 ms | 571.81 ms | 603.07 ms | 669.37 ms | 1000.35 ms |
| 10 virtual users | 349 | 100.00% | 11.36 req/s | 360.65 ms | 307.05 ms | 581.08 ms | 606.59 ms | 685.78 ms | 822.40 ms |

## Per-Endpoint Results at 10 Virtual Users

| Endpoint | Requests | Success Rate | Average Response Time | Median | P95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| `/` | 70 | 100.00% | 581.28 ms | 576.13 ms | 660.51 ms | 698.01 ms |
| `/admin-login` | 67 | 100.00% | 302.45 ms | 296.81 ms | 345.46 ms | 817.94 ms |
| `/login` | 66 | 100.00% | 315.67 ms | 303.71 ms | 382.53 ms | 709.91 ms |
| `/register` | 61 | 100.00% | 293.74 ms | 292.65 ms | 344.86 ms | 393.09 ms |
| `/static/styles.css` | 85 | 100.00% | 307.77 ms | 296.06 ms | 353.50 ms | 822.40 ms |

## Interpretation

The deployed ClassiFace application maintained a 100% success rate across all measured load levels. Throughput increased from 1.24 requests per second at one virtual user to 11.36 requests per second at ten virtual users, showing that the public web interface handled the tested concurrent access without request failures.

Average response time increased moderately as concurrency rose, from 296.22 ms at one virtual user to 360.65 ms at ten virtual users. The 95th percentile response time remained close to 600 ms under both five-user and ten-user tests, indicating stable response-time behavior for public page access under the tested load.

The root endpoint `/` had the highest average response time because it redirects to the login route. Public page endpoints such as `/login`, `/register`, and `/admin-login` generally remained near or below 350 ms at the 95th percentile during the ten-user test.

## Limitations

These results represent public web page performance only. They do not measure authenticated workflows such as login verification, dashboard data loading, quiz submission, face recognition, camera monitoring, Firebase operations, or PostgreSQL write-heavy activity.

Render hosting conditions may also affect response time, especially cold starts, regional network latency, and free/low-resource instance limits. For a final thesis evaluation, these results may be presented as baseline public-interface performance. A separate authenticated workflow test should be performed if valid test accounts and safe test data are available.

## Generated Artifacts

Detailed CSV, JSON, and Markdown reports were generated under `reports/performance/`:

| Load Level | CSV | JSON | Markdown |
|---|---|---|---|
| 1 virtual user | `render-public-1vu-20260604-180901.csv` | `render-public-1vu-20260604-180901.json` | `render-public-1vu-20260604-180901.md` |
| 5 virtual users | `render-public-5vu-20260604-181741.csv` | `render-public-5vu-20260604-181741.json` | `render-public-5vu-20260604-181741.md` |
| 10 virtual users | `render-public-10vu-20260604-182146.csv` | `render-public-10vu-20260604-182146.json` | `render-public-10vu-20260604-182146.md` |

## Reproduction Command

Example command:

```powershell
py -3.10 tools\performance_test.py --target https://classiface-hardened.onrender.com --name render-public-10vu --duration 30 --concurrency 10 --warmup 5 --think-time 0.5 --timeout 30
```

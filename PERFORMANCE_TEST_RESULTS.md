# SecureTest Performance Test Results

Test date: June 4, 2026  
Target system: `https://classiface-hardened.onrender.com`  
Test tool: `tools/performance_test.py`  
Deployment platform: Render  

## Objective

The performance test measured the baseline responsiveness, throughput, and reliability of the deployed SecureTest web application under increasing public-page load. The test was designed to be safe for production by using read-only public GET requests only.

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
| Light load | 30 seconds | 5 | 5 | 0.5 seconds |
| Moderate load | 30 seconds | 10 | 5 | 0.5 seconds |
| Increased load | 30 seconds | 20 | 5 | 0.5 seconds |
| High load | 30 seconds | 30 | 5 | 0.5 seconds |
| Stress load | 30 seconds | 50 | 5 | 0.5 seconds |

Each test used a 30-second request timeout. Warm-up requests were excluded from the measured results.

## Overall Results

| Virtual Users | Requests | Success Rate | Throughput | Average Response Time | Median | P90 | P95 | P99 | Max |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 36 | 100.00% | 1.18 req/s | 339.22 ms | 290.45 ms | 546.57 ms | 550.88 ms | 588.96 ms | 603.62 ms |
| 5 | 172 | 100.00% | 5.61 req/s | 374.97 ms | 308.57 ms | 590.73 ms | 614.57 ms | 695.65 ms | 981.78 ms |
| 10 | 349 | 100.00% | 11.24 req/s | 363.56 ms | 303.91 ms | 583.03 ms | 610.94 ms | 659.70 ms | 802.25 ms |
| 20 | 646 | 100.00% | 20.85 req/s | 426.56 ms | 372.50 ms | 681.08 ms | 737.56 ms | 825.19 ms | 1204.00 ms |
| 30 | 829 | 100.00% | 26.73 req/s | 564.50 ms | 542.08 ms | 804.35 ms | 939.18 ms | 1050.66 ms | 1588.30 ms |
| 50 | 1038 | 100.00% | 33.20 req/s | 911.06 ms | 862.72 ms | 1301.32 ms | 1573.71 ms | 1991.19 ms | 2316.58 ms |

## Per-Endpoint Results at 50 Virtual Users

| Endpoint | Requests | Success Rate | Average Response Time | Median | P95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| `/` | 214 | 100.00% | 1345.55 ms | 1258.85 ms | 1988.30 ms | 2316.58 ms |
| `/admin-login` | 186 | 100.00% | 766.02 ms | 761.36 ms | 1137.39 ms | 1553.81 ms |
| `/login` | 217 | 100.00% | 803.39 ms | 807.51 ms | 1132.63 ms | 1625.54 ms |
| `/register` | 214 | 100.00% | 833.65 ms | 835.16 ms | 1151.40 ms | 1764.53 ms |
| `/static/styles.css` | 207 | 100.00% | 785.11 ms | 804.16 ms | 1073.01 ms | 1292.97 ms |

## Interpretation

The deployed SecureTest application maintained a 100% success rate across all tested load levels from 1 to 50 virtual users. This means the public web pages remained available and did not return failed HTTP responses during the test.

Throughput increased as the number of virtual users increased, from 1.18 requests per second at 1 virtual user to 33.20 requests per second at 50 virtual users. However, the increase was not perfectly linear. This indicates that the server began reaching a practical resource limit as concurrency increased.

Average response time stayed below 500 ms from 1 to 20 virtual users. At 30 virtual users, average response time increased to 564.50 ms and the 95th percentile reached 939.18 ms. At 50 virtual users, average response time increased to 911.06 ms and the 95th percentile reached 1573.71 ms. This shows that the system remained reliable, but response time became slower under heavier concurrent access.

The root endpoint `/` had the highest response time at 50 virtual users because it performs a redirect before reaching the login page. The login, registration, admin login, and stylesheet endpoints also became slower at higher concurrency, which is expected when a cloud-hosted Flask application is handling many simultaneous requests with limited server resources.

## Why the Response Time Became Slow

The slower response time is mainly caused by production hosting and resource limits rather than request failures. The Render deployment runs the Flask application behind Gunicorn with a limited number of worker resources. When many users send requests at the same time, requests may wait in the server queue before being processed.

The deployed system is also accessed over the public internet. Compared with localhost, every request includes network routing, HTTPS/TLS processing, and Render platform overhead. These factors add latency even for simple public pages.

The test measured public pages only. Real authenticated workflows may be slower because login, viewing records, deleting records, quiz access, and session validation may require Firebase Authentication, PostgreSQL/Supabase database queries, and server-side template rendering.

Render hosting conditions can also affect response time, especially cold starts, region distance, low-resource instance limits, and the distance between Render and the database provider. If the Render service and Supabase database are hosted in different regions, database-related requests can become slower.

## Conclusion

The performance test shows that SecureTest remained stable under the tested load, with a 100% success rate from 1 to 50 virtual users. The system provided acceptable baseline public-page performance up to 20 virtual users. At 30 and 50 virtual users, response time increased noticeably, showing performance degradation under heavier load. This slowdown is expected for a cloud-hosted Flask application running on limited Render resources and does not indicate request failure.

For thesis reporting, these results can be presented as baseline public-interface performance. A separate authenticated workflow test should be performed if valid test accounts and safe test data are available.

## Generated Artifacts

Detailed CSV, JSON, and Markdown reports were generated under `reports/performance/`:

| Virtual Users | CSV | JSON | Markdown |
|---:|---|---|---|
| 1 | `render-public-1vu-thesis-20260604-192036.csv` | `render-public-1vu-thesis-20260604-192036.json` | `render-public-1vu-thesis-20260604-192036.md` |
| 5 | `render-public-5vu-thesis-20260604-192118.csv` | `render-public-5vu-thesis-20260604-192118.json` | `render-public-5vu-thesis-20260604-192118.md` |
| 10 | `render-public-10vu-thesis-20260604-192234.csv` | `render-public-10vu-thesis-20260604-192234.json` | `render-public-10vu-thesis-20260604-192234.md` |
| 20 | `render-public-20vu-thesis-20260604-192349.csv` | `render-public-20vu-thesis-20260604-192349.json` | `render-public-20vu-thesis-20260604-192349.md` |
| 30 | `render-public-30vu-thesis-20260604-192439.csv` | `render-public-30vu-thesis-20260604-192439.json` | `render-public-30vu-thesis-20260604-192439.md` |
| 50 | `render-public-50vu-thesis-20260604-193328.csv` | `render-public-50vu-thesis-20260604-193328.json` | `render-public-50vu-thesis-20260604-193328.md` |

## Reproduction Command

Example command:

```powershell
py -3.10 tools\performance_test.py --target https://classiface-hardened.onrender.com --name render-public-50vu-thesis --duration 30 --concurrency 50 --warmup 5 --think-time 0.5 --timeout 30
```

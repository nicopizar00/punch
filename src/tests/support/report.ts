export interface SummaryJson {
  testSuite: string;
  testType: string;
  targetUrl: string;
  durationMs: number;
  totalRequests: number;
  errorRate: number;
  p90Ms: number;
  checkPassRate: number;
  passed: boolean;
}

export interface ReportMeta {
  title: string;
  testType: string;
  targetUrl: string;
}

interface RequestMetrics {
  count: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  failed: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  duration: any;
}

// Protocol runs report http_*; k6 browser runs report browser_http_* and no
// request counter, so their count is the failed rate's passes + fails.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function requestMetrics(metrics: any): RequestMetrics {
  const prefix = metrics['http_req_duration'] || !metrics['browser_http_req_duration']
    ? 'http'
    : 'browser_http';
  const failed = metrics[`${prefix}_req_failed`]?.values;
  const duration = metrics[`${prefix}_req_duration`]?.values;
  const count = metrics[`${prefix}_reqs`]?.values?.count
    ?? (failed ? (failed.passes ?? 0) + (failed.fails ?? 0) : 0);
  return { count, failed, duration };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function buildSummaryJson(data: any, meta: ReportMeta): SummaryJson {
  const metrics = data.metrics ?? {};
  const requests = requestMetrics(metrics);
  const errorRate = requests.failed?.rate ?? 1;
  const checkRate = metrics['checks']?.values?.rate ?? 0;
  const p90 = requests.duration?.['p(90)'] ?? 0;

  // If k6 produced threshold evaluation results, base pass/fail on those.
  let passed: boolean | null = null;
  for (const mv of Object.values(metrics)) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mv as any;
    if (m.thresholds && Object.keys(m.thresholds).length > 0) {
      for (const t of Object.values(m.thresholds)) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const thr = t as any;
        if (typeof thr.ok === 'boolean') {
          if (passed === null) passed = true;
          passed = passed && thr.ok;
        }
      }
    }
  }
  if (passed === null) {
    // No threshold results available — fall back to laxer p90 rules.
    passed = errorRate < 0.10 && checkRate >= 0.90 && p90 < 1000;
  }
  return {
    testSuite: meta.title,
    testType: meta.testType,
    targetUrl: meta.targetUrl,
    durationMs: data.state?.testRunDurationMs ?? 0,
    totalRequests: requests.count,
    errorRate: errorRate,
    p90Ms: p90,
    checkPassRate: checkRate,
    passed,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function collectChecks(group: any): Record<string, { name: string; passes: number; fails: number }> {
  const result = { ...(group.checks ?? {}) };
  for (const sub of Object.values(group.groups ?? {})) {
    Object.assign(result, collectChecks(sub));
  }
  return result;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function buildHtml(data: any, meta: ReportMeta): string {
  const metrics = data.metrics ?? {};
  const checks = collectChecks(data.root_group ?? {});
  const durationMs: number = data.state?.testRunDurationMs ?? 0;

  const requests = requestMetrics(metrics);
  const httpReqs = requests.count;
  const reqRate = ((metrics['http_reqs']?.values?.rate
    ?? (durationMs > 0 ? httpReqs / (durationMs / 1000) : 0)) as number).toFixed(2);
  const p95 = ((requests.duration?.['p(95)'] ?? 0) as number).toFixed(2);
  const avg = ((requests.duration?.avg ?? 0) as number).toFixed(2);
  const p90 = ((requests.duration?.['p(90)'] ?? 0) as number).toFixed(2);
  const errorRate = (((requests.failed?.rate ?? 0) as number) * 100).toFixed(2);
  const checkRate = (((metrics['checks']?.values?.rate ?? 0) as number) * 100).toFixed(2);

  const passed = Number(errorRate) < 1 && Number(checkRate) >= 99;
  const statusColor = passed ? '#2d6a4f' : '#c0392b';

  const thresholdRows: string[] = [];
  for (const [metricName, metricVal] of Object.entries(metrics)) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mv = metricVal as any;
    if (!mv.thresholds || Object.keys(mv.thresholds).length === 0) continue;
    for (const [expr, t] of Object.entries(mv.thresholds)) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const threshold = t as any;
      const color = threshold.ok ? '#2d6a4f' : '#c0392b';
      thresholdRows.push(
        `    <tr><td>${metricName}</td><td>${expr}</td><td style="color:${color};font-weight:bold">${threshold.ok ? 'PASS' : 'FAIL'}</td></tr>`,
      );
    }
  }

  const checkRows = Object.values(checks).map((c) => {
    const total = c.passes + c.fails;
    const pass = c.fails === 0;
    const color = pass ? '#2d6a4f' : '#c0392b';
    return `    <tr><td>${c.name}</td><td style="color:${color};font-weight:bold">${pass ? 'PASS' : 'FAIL'}</td><td>${c.passes}/${total}</td></tr>`;
  });

  const thresholdsSection = thresholdRows.length
    ? `  <h2>Thresholds</h2>
  <table>
    <tr><th>Metric</th><th>Expression</th><th>Result</th></tr>
${thresholdRows.join('\n')}
  </table>`
    : '';

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>${meta.title}</title>
  <style>
    body{font-family:sans-serif;max-width:760px;margin:40px auto;color:#222}
    h1{font-size:1.4rem;margin-bottom:.25rem}
    .badge{display:inline-block;padding:3px 10px;border-radius:4px;color:#fff;font-weight:bold;font-size:.95rem}
    p.meta{color:#666;font-size:.9rem;margin-top:0}
    h2{font-size:1.1rem;margin-top:2rem}
    table{border-collapse:collapse;width:100%;margin-top:1rem}
    th{text-align:left;background:#f4f4f4;padding:8px 12px;border-bottom:2px solid #ddd}
    td{padding:7px 12px;border-bottom:1px solid #eee}
  </style>
</head>
<body>
  <h1>${meta.title} &nbsp;<span class="badge" style="background:${statusColor}">${passed ? 'PASS' : 'FAIL'}</span></h1>
  <p class="meta">Type: ${meta.testType} &nbsp;|&nbsp; Target: ${meta.targetUrl} &nbsp;|&nbsp; Duration: ${(durationMs / 1000).toFixed(1)}s</p>
  <h2>HTTP Metrics</h2>
  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Total requests</td><td>${httpReqs}</td></tr>
    <tr><td>Request rate</td><td>${reqRate} req/s</td></tr>
    <tr><td>Avg response time</td><td>${avg} ms</td></tr>
    <tr><td>p90 response time</td><td>${p90} ms</td></tr>
    <tr><td>Error rate</td><td>${errorRate}%</td></tr>
    <tr><td>Check pass rate</td><td>${checkRate}%</td></tr>
  </table>
${thresholdsSection}
  <h2>Checks</h2>
  <table>
    <tr><th>Check</th><th>Result</th><th>Passes / Total</th></tr>
${checkRows.join('\n')}
  </table>
</body>
</html>`;
}

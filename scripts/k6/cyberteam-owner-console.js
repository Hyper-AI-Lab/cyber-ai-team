import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const apiBase = (__ENV.API_BASE || 'https://cyberteam.hyperailab.com').replace(/\/$/, '');
const ownerEmail = __ENV.OWNER_EMAIL;
const ownerPassword = __ENV.OWNER_PASSWORD;
const http5xx = new Rate('http_5xx');
const endpointNames = ['health', 'login', 'dashboard', 'readiness'];
let token = '';

export const options = {
  vus: Number(__ENV.K6_VUS || 5),
  duration: __ENV.K6_DURATION || '5m',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{endpoint:health}': ['p(95)<750'],
    'http_req_duration{endpoint:login}': ['p(95)<750'],
    'http_req_duration{endpoint:dashboard}': ['p(95)<750'],
    'http_req_duration{endpoint:readiness}': ['p(95)<750'],
    http_5xx: ['rate==0'],
    checks: ['rate>0.99'],
  },
};

function recordResponse(response, checkName, predicate) {
  http5xx.add(response.status >= 500);
  check(response, {
    [checkName]: predicate,
  });
}

function login() {
  const response = http.post(
    `${apiBase}/api/auth/login`,
    JSON.stringify({ email: ownerEmail, password: ownerPassword }),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { endpoint: 'login' },
    },
  );
  recordResponse(
    response,
    'login succeeds',
    (response) => response.status === 200 && response.json('access_token'),
  );
  token = response.json('access_token') || '';
}

export default function () {
  if (!token) {
    login();
  }
  const headers = {
    Authorization: `Bearer ${token}`,
  };
  const requests = [
    ['health', '/health', {}],
    ['dashboard', '/api/dashboard/kpis'],
    ['readiness', '/api/operations/readiness'],
  ];
  for (const [endpoint, path] of requests) {
    const response = http.get(`${apiBase}${path}`, {
      headers: endpoint === 'health' ? {} : headers,
      tags: { endpoint },
    });
    recordResponse(
      response,
      `${endpoint} returns 2xx`,
      (item) => item.status >= 200 && item.status < 300,
    );
  }
  sleep(1);
}

export function handleSummary(data) {
  const failedThresholds = [];
  for (const [metricName, metric] of Object.entries(data.metrics || {})) {
    for (const [threshold, result] of Object.entries(metric.thresholds || {})) {
      if (result.ok === false) {
        failedThresholds.push(`${metricName}: ${threshold}`);
      }
    }
  }
  const endpointP95Ms = Object.fromEntries(
    endpointNames.map((endpoint) => [
      endpoint,
      data.metrics[`http_req_duration{endpoint:${endpoint}}`]?.values?.['p(95)'] ?? null,
    ]),
  );
  const measuredEndpointP95 = Object.values(endpointP95Ms).filter((value) => value !== null);
  const payload = {
    status: failedThresholds.length ? 'failed' : 'passed',
    completed_at: new Date().toISOString(),
    api_base: apiBase,
    vus: options.vus,
    duration: options.duration,
    p95_ms: measuredEndpointP95.length ? Math.max(...measuredEndpointP95) : null,
    aggregate_p95_ms: data.metrics.http_req_duration?.values?.['p(95)'] ?? null,
    endpoint_p95_ms: endpointP95Ms,
    failure_rate: data.metrics.http_req_failed?.values?.rate ?? null,
    http_5xx_rate: data.metrics.http_5xx?.values?.rate ?? null,
    checks_rate: data.metrics.checks?.values?.rate ?? null,
    failed_thresholds: failedThresholds,
  };
  return {
    [__ENV.EVIDENCE_FILE || '/out/load-smoke-latest.json']: `${JSON.stringify(payload, null, 2)}\n`,
    stdout: textSummary(payload),
  };
}

function textSummary(payload) {
  return [
    `status=${payload.status}`,
    `p95_ms=${payload.p95_ms}`,
    `endpoint_p95_ms=${JSON.stringify(payload.endpoint_p95_ms)}`,
    `failure_rate=${payload.failure_rate}`,
    `http_5xx_rate=${payload.http_5xx_rate}`,
    `checks_rate=${payload.checks_rate}`,
    `failed_thresholds=${payload.failed_thresholds.join(',') || 'none'}`,
    '',
  ].join('\n');
}

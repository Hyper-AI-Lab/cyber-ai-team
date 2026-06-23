import http from 'k6/http';
import { check, sleep } from 'k6';

const apiBase = (__ENV.API_BASE || 'https://cyberteam.hyperailab.com').replace(/\/$/, '');
const ownerEmail = __ENV.OWNER_EMAIL;
const ownerPassword = __ENV.OWNER_PASSWORD;

export const options = {
  vus: Number(__ENV.K6_VUS || 5),
  duration: __ENV.K6_DURATION || '5m',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<750'],
    checks: ['rate>0.99'],
  },
};

export function setup() {
  const health = http.get(`${apiBase}/health`, { tags: { endpoint: 'health' } });
  check(health, {
    'health is ok': (response) => response.status === 200,
  });
  const login = http.post(
    `${apiBase}/api/auth/login`,
    JSON.stringify({ email: ownerEmail, password: ownerPassword }),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { endpoint: 'login' },
    },
  );
  check(login, {
    'login succeeds': (response) => response.status === 200 && response.json('access_token'),
  });
  return { token: login.json('access_token') };
}

export default function (data) {
  const headers = {
    Authorization: `Bearer ${data.token}`,
  };
  const requests = [
    ['dashboard', '/api/dashboard/kpis'],
    ['readiness', '/api/operations/readiness'],
    ['integrations', '/api/integrations/status'],
    ['tools', '/api/tools/'],
  ];
  for (const [endpoint, path] of requests) {
    const response = http.get(`${apiBase}${path}`, {
      headers,
      tags: { endpoint },
    });
    check(response, {
      [`${endpoint} returns 2xx`]: (item) => item.status >= 200 && item.status < 300,
    });
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
  const payload = {
    status: failedThresholds.length ? 'failed' : 'passed',
    completed_at: new Date().toISOString(),
    api_base: apiBase,
    vus: options.vus,
    duration: options.duration,
    p95_ms: data.metrics.http_req_duration?.values?.['p(95)'] ?? null,
    failure_rate: data.metrics.http_req_failed?.values?.rate ?? null,
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
    `failure_rate=${payload.failure_rate}`,
    `checks_rate=${payload.checks_rate}`,
    `failed_thresholds=${payload.failed_thresholds.join(',') || 'none'}`,
    '',
  ].join('\n');
}

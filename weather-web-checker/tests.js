'use strict';
// Unit tests for pure functions exported via window._testExports

const {
  parseTempBucket,
  parseDateFromQuestion,
  parseCityFromQuestion,
  parseCityFromSlug,
  bucketProbability,
  erf,
  slugFromPolymarketUrl,
} = window._testExports;

let passed = 0, failed = 0;

function assert(description, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) {
    passed++;
    return { ok, description };
  } else {
    failed++;
    return { ok, description, actual, expected };
  }
}

function assertClose(description, actual, expected, tol = 0.01) {
  const ok = actual != null && Math.abs(actual - expected) <= tol;
  if (ok) {
    passed++;
    return { ok, description };
  } else {
    failed++;
    return { ok, description, actual, expected, tol };
  }
}

function assertNotNull(description, actual) {
  const ok = actual !== null && actual !== undefined;
  if (ok) { passed++; return { ok, description }; }
  failed++;
  return { ok, description, actual };
}

const RESULTS = [];

// ─────────────────────────────────────────────────────────────────────────────
// parseTempBucket
// ─────────────────────────────────────────────────────────────────────────────

function testParseTempBucket() {
  const cases = [
    // Fahrenheit — above
    ['Will the high exceed 90°F?',
      { minC: (90-32)*5/9, maxC: null }],

    // Fahrenheit — below
    ['Will the high be below 70°F?',
      { minC: null, maxC: (70-32)*5/9 }],

    // Fahrenheit — between
    ['Will the high be between 75 and 85°F?',
      { minC: (75-32)*5/9, maxC: (85-32)*5/9 }],

    // Celsius — above
    ['Will the high exceed 35°C?',
      { minC: 35, maxC: null }],

    // Celsius — below
    ['Will the temperature be below 20 degrees Celsius?',
      { minC: null, maxC: 20 }],

    // "at least"
    ['Will the high be at least 100°F?',
      { minC: (100-32)*5/9, maxC: null }],

    // "no more than"
    ['Will the high be no more than 60°F?',
      { minC: null, maxC: (60-32)*5/9 }],

    // "reach"
    ['Will temperatures reach 40°C?',
      { minC: 40, maxC: null }],

    // Degrees (no unit marker) — treated as Fahrenheit
    ['Will the high exceed 80 degrees?',
      { minC: (80-32)*5/9, maxC: null }],

    // Null cases
    ['Will it rain tomorrow?', null],
    ['', null],
    [null, null],
  ];

  for (const [q, expected] of cases) {
    const result = parseTempBucket(q);
    if (expected === null) {
      RESULTS.push(assert(`parseTempBucket(${JSON.stringify(q)}) → null`, result, null));
    } else {
      if (result === null) {
        RESULTS.push(assert(`parseTempBucket("${q}") returned null (expected object)`, result, expected));
        continue;
      }
      const minOk = expected.minC === null
        ? result.minC === null
        : Math.abs(result.minC - expected.minC) < 0.05;
      const maxOk = expected.maxC === null
        ? result.maxC === null
        : Math.abs(result.maxC - expected.maxC) < 0.05;
      const ok = minOk && maxOk;
      if (ok) { passed++; RESULTS.push({ ok, description: `parseTempBucket("${q}")` }); }
      else { failed++; RESULTS.push({ ok, description: `parseTempBucket("${q}")`, actual: result, expected }); }
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// parseDateFromQuestion
// ─────────────────────────────────────────────────────────────────────────────

function testParseDateFromQuestion() {
  const thisYear = new Date().getFullYear();
  const nextYear = thisYear + 1;

  const cases = [
    // Explicit year
    ['Will it exceed 90°F on June 5, 2026?',    '2026-06-05'],
    ['Will the high exceed 80 on July 4, 2025?', '2025-07-04'],
    ['Will temperatures be above 35°C on December 25, 2026?', '2026-12-25'],
    // Month abbreviations
    ['on Jan 1, 2027', '2027-01-01'],
    ['on Aug 15, 2026', '2026-08-15'],
    // Ordinals
    ['for July 4th, 2026', '2026-07-04'],
    ['on June 3rd, 2026', '2026-06-03'],
    // No parseable date
    ['Will it rain?', null],
    ['', null],
  ];

  for (const [q, expected] of cases) {
    RESULTS.push(assert(`parseDateFromQuestion("${q}")`, parseDateFromQuestion(q), expected));
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// parseCityFromQuestion
// ─────────────────────────────────────────────────────────────────────────────

function testParseCityFromQuestion() {
  const cases = [
    ['Will the high temperature in Los Angeles on June 5 be above 90°F?', 'Los Angeles'],
    ['Will the high temp in New York City exceed 75 degrees on July 4?', 'New York City'],
    ['Will the high in Chicago be below 60°F on May 1?', 'Chicago'],
    ['Will temperatures in Tel Aviv reach 38°C on July 15?', 'Tel Aviv'],
    ['Will the temperature in San Francisco exceed 70°F on August 10?', 'San Francisco'],
  ];

  for (const [q, expected] of cases) {
    const result = parseCityFromQuestion(q);
    const ok = result !== null && result.toLowerCase() === expected.toLowerCase();
    if (ok) { passed++; RESULTS.push({ ok, description: `parseCityFromQuestion("${q}") → "${result}"` }); }
    else { failed++; RESULTS.push({ ok, description: `parseCityFromQuestion("${q}")`, actual: result, expected }); }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// parseCityFromSlug
// ─────────────────────────────────────────────────────────────────────────────

function testParseCityFromSlug() {
  const cases = [
    ['los-angeles-high-temperature-june-5-2026', 'Los Angeles'],
    ['new-york-city-temperature-july-4', 'New York City'],
    ['chicago-weather-high-temp-may-1-2026', 'Chicago'],
    ['tel-aviv-high-temperature-july-15-2026', 'Tel Aviv'],
  ];

  for (const [slug, expected] of cases) {
    const result = parseCityFromSlug(slug);
    const ok = result !== null && result.toLowerCase() === expected.toLowerCase();
    if (ok) { passed++; RESULTS.push({ ok, description: `parseCityFromSlug("${slug}") → "${result}"` }); }
    else { failed++; RESULTS.push({ ok, description: `parseCityFromSlug("${slug}")`, actual: result, expected }); }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// bucketProbability
// ─────────────────────────────────────────────────────────────────────────────

function testBucketProbability() {
  // Forecast = 30°C, sigma = 2.5

  // Open-ended above: bucket min = 30°C, maxC = null → ~50%
  RESULTS.push(assertClose(
    'bucketProbability(30, 30, null) ≈ 0.5',
    bucketProbability(30, 30, null), 0.5, 0.02
  ));

  // Forecast exactly at bucket max → ~50% chance it's below
  RESULTS.push(assertClose(
    'bucketProbability(30, null, 30) ≈ 0.5',
    bucketProbability(30, null, 30), 0.5, 0.02
  ));

  // Forecast well inside bucket → high probability
  RESULTS.push(assertClose(
    'bucketProbability(30, 25, 35) ≈ 0.95',
    bucketProbability(30, 25, 35), 0.95, 0.04
  ));

  // Forecast way below bucket → near 0
  const lowProb = bucketProbability(10, 35, null);
  RESULTS.push({
    ok: lowProb < 0.05,
    description: `bucketProbability(10, 35, null) < 0.05 (got ${lowProb.toFixed(4)})`,
  });
  if (lowProb < 0.05) passed++; else failed++;

  // Forecast well above bucket → near 0 chance it's below
  const aboveProb = bucketProbability(50, null, 20);
  RESULTS.push({
    ok: aboveProb < 0.05,
    description: `bucketProbability(50, null, 20) < 0.05 (got ${aboveProb.toFixed(4)})`,
  });
  if (aboveProb < 0.05) passed++; else failed++;

  // Result always in [0.01, 0.99]
  const vals = [bucketProbability(0, 100, null), bucketProbability(100, null, 0)];
  for (const v of vals) {
    RESULTS.push({
      ok: v >= 0.01 && v <= 0.99,
      description: `bucketProbability result clamped to [0.01, 0.99]: ${v.toFixed(4)}`,
    });
    if (v >= 0.01 && v <= 0.99) passed++; else failed++;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// erf
// ─────────────────────────────────────────────────────────────────────────────

function testErf() {
  RESULTS.push(assertClose('erf(0) ≈ 0', erf(0), 0, 1e-6));  // approximation, not exactly 0
  RESULTS.push(assertClose('erf(1) ≈ 0.8427', erf(1), 0.8427007929, 1e-4));
  RESULTS.push(assertClose('erf(-1) ≈ -0.8427', erf(-1), -0.8427007929, 1e-4));
  RESULTS.push(assertClose('erf(3) ≈ 0.9999779', erf(3), 0.9999779095, 1e-4));
  RESULTS.push(assertClose('erf(0.5) ≈ 0.5205', erf(0.5), 0.5204998778, 1e-4));
}

// ─────────────────────────────────────────────────────────────────────────────
// slugFromPolymarketUrl
// ─────────────────────────────────────────────────────────────────────────────

function testSlugFromUrl() {
  const cases = [
    ['https://polymarket.com/event/los-angeles-high-temp-june-5', 'los-angeles-high-temp-june-5'],
    ['https://polymarket.com/event/nyc-weather-july-4?ref=abc', 'nyc-weather-july-4'],
    ['polymarket.com/event/chicago-temperature', 'chicago-temperature'],
    ['https://polymarket.com/sports/tennis', null],
    ['not-a-url', null],
    ['', null],
  ];
  for (const [url, expected] of cases) {
    RESULTS.push(assert(`slugFromPolymarketUrl("${url}")`, slugFromPolymarketUrl(url), expected));
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Run all tests
// ─────────────────────────────────────────────────────────────────────────────

function runAllTests() {
  testErf();
  testBucketProbability();
  testParseTempBucket();
  testParseDateFromQuestion();
  testParseCityFromQuestion();
  testParseCityFromSlug();
  testSlugFromUrl();
  return { results: RESULTS, passed, failed };
}

window._runTests = runAllTests;

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// Configuration
// ─────────────────────────────────────────────────────────────────────────────

const API = {
  geocode:    'https://geocoding-api.open-meteo.com/v1/search',
  forecast:   'https://api.open-meteo.com/v1/forecast',
  polymarket: 'https://gamma-api.polymarket.com',
};

const POLY_HEADERS = {
  'Accept': 'application/json',
};

// Model definitions. fallback is tried if primary fails.
const MODELS = [
  {
    key: 'ecmwf',
    id: 'ecmwf_ifs025',
    label: 'ECMWF IFS',
    desc: 'European Centre — physics model',
    color: '#4e9af1',
  },
  {
    key: 'graphcast',
    id: 'gfs_graphcast',
    label: 'GFS-GraphCast',
    desc: 'NOAA × Google DeepMind AI model',
    fallback: 'ecmwf_aifs025_single',
    fallbackLabel: 'ECMWF AIFS (GraphCast-style)',
    fallbackDesc: 'ECMWF AI model — graph neural network, similar to GraphCast',
    color: '#f1c40f',
  },
];

const WEATHER_KEYWORDS = [
  'temperature', 'high temp', 'degrees', '°', 'fahrenheit',
  'celsius', 'weather', 'hottest', 'warmest', 'exceed',
];

// ─────────────────────────────────────────────────────────────────────────────
// Pure math helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Error function — Abramowitz & Stegun approximation (max error 1.5e-7).
 */
function erf(x) {
  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x);
  const p  = 0.3275911;
  const a1 =  0.254829592;
  const a2 = -0.284496736;
  const a3 =  1.421413741;
  const a4 = -1.453152027;
  const a5 =  1.061405429;
  const t  = 1 / (1 + p * x);
  const y  = 1 - (((((a5 * t + a4) * t + a3) * t + a2) * t + a1)) * t * Math.exp(-x * x);
  return sign * y;
}

/** Normal CDF: P(X ≤ x) where X ~ N(mu, sigma²). */
function normalCDF(x, mu, sigma) {
  return 0.5 * (1 + erf((x - mu) / (sigma * Math.SQRT2)));
}

/**
 * Gaussian probability that actual max temp falls within [minC, maxC].
 * sigma = 2.5 °C = typical day-ahead GFS/ECMWF MAE.
 */
function bucketProbability(forecastC, minC, maxC) {
  const sigma = 2.5;
  const lo = (minC !== null && minC !== undefined) ? normalCDF(minC, forecastC, sigma) : 0;
  const hi = (maxC !== null && maxC !== undefined) ? normalCDF(maxC, forecastC, sigma) : 1;
  return Math.max(0.01, Math.min(0.99, hi - lo));
}

// ─────────────────────────────────────────────────────────────────────────────
// Temperature bucket parser
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Extract temperature bucket from a Polymarket market question.
 * Returns { minC, maxC } (either can be null for open-ended),
 * or null if no temperature range is found.
 */
function parseTempBucket(question) {
  if (!question) return null;
  const q = question.toLowerCase();

  // Determine unit
  const isF = /°f|fahrenheit/.test(q) ||
              (/\bdegrees?\b/.test(q) && !/celsius/.test(q) && !/°c/.test(q));
  const toC = v => isF ? Math.round((v - 32) * 5 / 9 * 100) / 100 : Math.round(v * 100) / 100;

  let m;

  // "between X and Y" or "X to Y" or "X-Y"
  m = q.match(/between\s+(\d+\.?\d*)\s*(?:and|–|-|to)\s*(\d+\.?\d*)/);
  if (m) return { minC: toC(+m[1]), maxC: toC(+m[2]) };

  m = q.match(/(\d+\.?\d*)\s*(?:to|-)\s*(\d+\.?\d*)\s*(?:°|degrees?|f\b|c\b)/);
  if (m && +m[1] < +m[2]) return { minC: toC(+m[1]), maxC: toC(+m[2]) };

  // "no more than X" must be tested BEFORE "more than X" to avoid false match
  m = q.match(/\bno\s+more\s+than\s+(\d+\.?\d*)/);
  if (m) return { minC: null, maxC: toC(+m[1]) };

  // "above X" / "exceed X" / "over X" / "at least X" / "more than X" / "higher than X"
  m = q.match(/(?:above|exceed[s]?|over|higher than|at least|more than)\s+(\d+\.?\d*)/);
  if (m) return { minC: toC(+m[1]), maxC: null };

  // "below X" / "under X" / "less than X" / "at most X" / "lower than X" / "fewer than X"
  m = q.match(/(?:below|under|less than|lower than|at most|fewer than)\s+(\d+\.?\d*)/);
  if (m) return { minC: null, maxC: toC(+m[1]) };

  // "reach X" / "hit X" (implies >= X)
  m = q.match(/(?:reach|hit)\s+(\d+\.?\d*)/);
  if (m) return { minC: toC(+m[1]), maxC: null };

  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Date parser from market question
// ─────────────────────────────────────────────────────────────────────────────

const MONTHS = {
  january: 1, february: 2, march: 3, april: 4, may: 5, june: 6,
  july: 7, august: 8, september: 9, october: 10, november: 11, december: 12,
  jan: 1, feb: 2, mar: 3, apr: 4, jun: 6, jul: 7, aug: 8,
  sep: 9, oct: 10, nov: 11, dec: 12,
};

/**
 * Extract a YYYY-MM-DD date string from a Polymarket question.
 * Examples:
 *   "on June 5"            → "2026-06-05" (next occurrence)
 *   "on May 15, 2026"      → "2026-05-15"
 *   "on July 4th, 2025"    → "2025-07-04"
 */
function parseDateFromQuestion(question) {
  if (!question) return null;
  const q = question.toLowerCase();

  // "on/for MonthName Day[th|st|nd|rd][,] [Year]"
  const m = q.match(
    /(?:on|for)\s+([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?/
  );
  if (!m) return null;

  const month = MONTHS[m[1]];
  if (!month) return null;

  const day = parseInt(m[2], 10);
  if (day < 1 || day > 31) return null;

  let year = m[3] ? parseInt(m[3], 10) : null;
  if (!year) {
    // Pick next occurrence of this month/day
    const now = new Date();
    year = now.getFullYear();
    const candidate = new Date(year, month - 1, day);
    if (candidate < now) year++;
  }

  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// City name extractor from market question
// ─────────────────────────────────────────────────────────────────────────────

// Regex that marks the end of a city name inside a question
const CITY_END_RE = /\b(?:on|for|in\s+\d|exceed[s]?|above|below|be\s+above|be\s+below|be\s+over|reach|will\s+be|temperature\b)/;

/**
 * Extract city name from a Polymarket question like:
 *   "Will the high temperature in Los Angeles on June 5 be above 90°F?"
 *   "Will the high temp in New York City exceed 75 degrees on July 4?"
 */
function parseCityFromQuestion(question) {
  if (!question) return null;

  // Find "in {CITY}" — city starts with uppercase, can have spaces/hyphens
  const m = question.match(/\bin\s+([A-Z][A-Za-z\s'.-]+?)(?:\s+(?:on|for|exceed|above|below|be\s|reach|temperature\b|(?=\d)))/);
  if (m) return m[1].trim().replace(/\s+/g, ' ');

  // Fallback: "in {CITY}" without clear terminator — take first 3 words
  const m2 = question.match(/\bin\s+((?:[A-Z][a-z]+\s?){1,3})/);
  return m2 ? m2[1].trim() : null;
}

/**
 * Try to extract city from a Polymarket slug as a fallback.
 * "los-angeles-high-temperature-june-5-2025" → "Los Angeles"
 */
function parseCityFromSlug(slug) {
  if (!slug) return null;
  const stopWords = ['high', 'low', 'temperature', 'temp', 'weather', 'degrees', 'fahrenheit',
    'celsius', 'above', 'below', 'exceed', 'will', 'the', 'be', 'for', 'on',
    'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
    'september', 'october', 'november', 'december',
    ...Object.keys(MONTHS),
  ];
  const words = slug.split('-');
  const cityWords = [];
  for (const word of words) {
    if (stopWords.includes(word) || /^\d/.test(word)) break;
    cityWords.push(word.charAt(0).toUpperCase() + word.slice(1));
  }
  return cityWords.length ? cityWords.join(' ') : null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Geocoding
// ─────────────────────────────────────────────────────────────────────────────

async function geocodeCity(city) {
  const url = new URL(API.geocode);
  url.searchParams.set('name', city);
  url.searchParams.set('count', '5');
  url.searchParams.set('language', 'en');
  url.searchParams.set('format', 'json');

  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Geocoding service error: HTTP ${resp.status}`);
  const data = await resp.json();
  if (!data.results || !data.results.length) {
    throw new Error(`City not found: "${city}". Try a different spelling or a nearby major city.`);
  }
  const r = data.results[0];
  return {
    lat: r.latitude,
    lon: r.longitude,
    name: r.name,
    admin: r.admin1 || '',
    country: r.country || '',
    countryCode: (r.country_code || '').toUpperCase(),
    timezone: r.timezone || 'auto',
    displayName: [r.name, r.admin1, r.country].filter(Boolean).join(', '),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Weather forecasts
// ─────────────────────────────────────────────────────────────────────────────

async function fetchSingleModel(lat, lon, dateStr, modelId) {
  const url = new URL(API.forecast);
  const params = {
    latitude: lat,
    longitude: lon,
    daily: 'temperature_2m_max,temperature_2m_min',
    temperature_unit: 'celsius',
    timezone: 'auto',
    models: modelId,
    start_date: dateStr,
    end_date: dateStr,
  };
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));

  const resp = await fetch(url);
  if (!resp.ok) {
    const body = await resp.text().catch(() => '');
    throw new Error(`HTTP ${resp.status}${body ? ': ' + body.slice(0, 120) : ''}`);
  }
  const data = await resp.json();
  if (data.error) throw new Error(data.reason || 'Unknown API error');

  const daily = data.daily || {};
  const times = daily.time || [];
  const idx = times.indexOf(dateStr);
  if (idx === -1) throw new Error(`Date ${dateStr} not in forecast window (max ~16 days ahead)`);

  const maxC = (daily.temperature_2m_max || [])[idx];
  const minC = (daily.temperature_2m_min || [])[idx];
  if (maxC == null) throw new Error('No max temperature data returned');

  return {
    maxC: Math.round(maxC * 10) / 10,
    minC: minC != null ? Math.round(minC * 10) / 10 : null,
    maxF: Math.round((maxC * 9 / 5 + 32) * 10) / 10,
    minF: minC != null ? Math.round((minC * 9 / 5 + 32) * 10) / 10 : null,
  };
}

/**
 * Fetch forecast for a model definition, trying primary then fallback.
 * Returns { maxC, minC, maxF, minF, usedId, label, desc, color, error? }
 */
async function fetchModelWithFallback(lat, lon, dateStr, model) {
  const attempts = [
    { id: model.id, label: model.label, desc: model.desc },
    model.fallback
      ? { id: model.fallback, label: model.fallbackLabel, desc: model.fallbackDesc }
      : null,
  ].filter(Boolean);

  for (const attempt of attempts) {
    try {
      const f = await fetchSingleModel(lat, lon, dateStr, attempt.id);
      return { ...f, usedId: attempt.id, label: attempt.label, desc: attempt.desc, color: model.color };
    } catch (e) {
      console.warn(`[${attempt.id}] ${e.message}`);
    }
  }
  return {
    maxC: null, minC: null, maxF: null, minF: null,
    usedId: model.id, label: model.label, desc: model.desc, color: model.color,
    error: `All attempts failed for ${model.label}`,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Polymarket market fetching and parsing
// ─────────────────────────────────────────────────────────────────────────────

function parseMarketsFromEvents(events, cityFilter) {
  const filterLower = cityFilter ? cityFilter.toLowerCase() : null;
  const results = [];

  for (const event of events) {
    const slug = event.slug || '';
    for (const market of (event.markets || [])) {
      const question = market.question || '';
      const ql = question.toLowerCase();

      // Must mention weather/temperature keywords
      if (!WEATHER_KEYWORDS.some(kw => ql.includes(kw))) continue;
      // Must mention the city (if filter given)
      if (filterLower && !ql.includes(filterLower)) continue;

      // Parse prices (Gamma API returns JSON string or array)
      let prices = [0.5, 0.5];
      try {
        const raw = market.outcomePrices;
        const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
        if (Array.isArray(parsed) && parsed.length >= 1) {
          prices = parsed.map(p => parseFloat(p) || 0.5);
        }
      } catch (_) {}

      // Parse outcome names
      let outcomes = ['YES', 'NO'];
      try {
        const raw = market.outcomes;
        const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
        if (Array.isArray(parsed) && parsed.length >= 1) outcomes = parsed;
      } catch (_) {}

      const yesPrice = prices[0] ?? 0.5;
      const bucket = parseTempBucket(question);

      results.push({
        question,
        yesPrice,
        noPrice: 1 - yesPrice,
        outcomes,
        url: `https://polymarket.com/event/${slug}`,
        slug,
        conditionId: market.conditionId || '',
        endDate: market.endDate || event.endDate || '',
        bucket,
      });
    }
  }
  return results;
}

async function searchPolymarketByCity(cityName) {
  const url = new URL(`${API.polymarket}/events`);
  url.searchParams.set('q', cityName);
  url.searchParams.set('active', 'true');
  url.searchParams.set('closed', 'false');
  url.searchParams.set('limit', '50');

  const resp = await fetch(url, { headers: POLY_HEADERS });
  if (!resp.ok) throw new Error(`Polymarket search: HTTP ${resp.status}`);
  const data = await resp.json();
  const events = Array.isArray(data) ? data : (data.data || []);

  // City might appear with short name (e.g. "NYC" for "New York City")
  const shortName = cityName.split(' ').length > 2
    ? cityName.split(',')[0].split(' ').slice(0, 2).join(' ')
    : cityName;

  return parseMarketsFromEvents(events, shortName);
}

async function fetchPolymarketBySlug(slug) {
  const url = new URL(`${API.polymarket}/events`);
  url.searchParams.set('slug', slug);
  url.searchParams.set('limit', '1');

  const resp = await fetch(url, { headers: POLY_HEADERS });
  if (!resp.ok) throw new Error(`Polymarket event lookup: HTTP ${resp.status}`);
  const data = await resp.json();
  const events = Array.isArray(data) ? data : (data.data || []);
  if (!events.length) throw new Error(`No Polymarket event found for slug: "${slug}"`);
  return events[0];
}

function slugFromPolymarketUrl(raw) {
  if (!raw) return null;
  const m = raw.trim().match(/polymarket\.com\/event\/([^/?#\s]+)/);
  return m ? m[1] : null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Core analysis pipeline
// ─────────────────────────────────────────────────────────────────────────────

function buildAnalysis(location, dateStr, modelResults, markets) {
  const validForecasts = modelResults.filter(r => r.maxC != null);
  const allMaxC = validForecasts.map(r => r.maxC);
  const consensusC = allMaxC.length
    ? allMaxC.reduce((a, b) => a + b, 0) / allMaxC.length
    : null;
  const consensusF = consensusC != null ? consensusC * 9 / 5 + 32 : null;

  const marketsWithEdge = markets.map(m => {
    if (!m.bucket || !allMaxC.length) {
      return { ...m, modelProb: null, edge: null, perModelProbs: [], reason: m.bucket ? null : 'bucket-unparsed' };
    }
    const { minC, maxC } = m.bucket;
    const perModelProbs = validForecasts.map(f => ({
      label: f.label,
      prob: bucketProbability(f.maxC, minC, maxC),
      maxC: f.maxC,
    }));
    const modelProb = perModelProbs.reduce((s, p) => s + p.prob, 0) / perModelProbs.length;
    const edge = modelProb - m.yesPrice;
    return { ...m, modelProb, edge, perModelProbs };
  });

  return { location, dateStr, models: modelResults, consensusC, consensusF, markets: marketsWithEdge };
}

async function analyzeByCity(cityInput, dateStr) {
  validateDate(dateStr);
  const location = await geocodeCity(cityInput);
  const [modelResults, markets] = await Promise.all([
    Promise.all(MODELS.map(m => fetchModelWithFallback(location.lat, location.lon, dateStr, m))),
    searchPolymarketByCity(location.name).catch(e => { console.warn('Polymarket search failed:', e.message); return []; }),
  ]);
  return buildAnalysis(location, dateStr, modelResults, markets);
}

async function analyzeByUrl(rawUrl) {
  const slug = slugFromPolymarketUrl(rawUrl);
  if (!slug) throw new Error('Invalid Polymarket URL. Expected: https://polymarket.com/event/some-slug');

  const event = await fetchPolymarketBySlug(slug);
  const markets = parseMarketsFromEvents([event], null);
  if (!markets.length) {
    throw new Error('No temperature/weather markets found in this Polymarket event. Make sure the URL points to a weather temperature market.');
  }

  // Extract city and date from the first parseable market question
  let city = null, dateStr = null;
  for (const m of markets) {
    city = city || parseCityFromQuestion(m.question) || parseCityFromSlug(slug);
    dateStr = dateStr || parseDateFromQuestion(m.question);
    if (city && dateStr) break;
  }

  if (!city) throw new Error(`Could not extract city from market question: "${markets[0].question}". Use the "By City" tab instead.`);
  if (!dateStr) throw new Error(`Could not extract date from market question: "${markets[0].question}". Use the "By City" tab instead.`);

  validateDate(dateStr);
  const location = await geocodeCity(city);
  const modelResults = await Promise.all(
    MODELS.map(m => fetchModelWithFallback(location.lat, location.lon, dateStr, m))
  );
  return buildAnalysis(location, dateStr, modelResults, markets);
}

function validateDate(dateStr) {
  const d = new Date(dateStr + 'T12:00:00Z');
  if (isNaN(d.getTime())) throw new Error(`Invalid date: ${dateStr}`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (d < today) throw new Error('Date is in the past. Open-Meteo only provides forecasts for future dates.');
  const maxDate = new Date(today);
  maxDate.setDate(maxDate.getDate() + 15);
  if (d > maxDate) throw new Error('Date is more than 15 days ahead. Open-Meteo forecast horizon is ~16 days.');
}

// ─────────────────────────────────────────────────────────────────────────────
// Rendering
// ─────────────────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fC(v) { return v != null ? `${v.toFixed(1)}°C` : '—'; }
function fF(v) { return v != null ? `${v.toFixed(1)}°F` : '—'; }
function fPct(v) { return v != null ? `${(v * 100).toFixed(1)}%` : '—'; }

function fEdge(edge) {
  if (edge == null) return '<span class="na">—</span>';
  const pct = edge * 100;
  const sign = pct >= 0 ? '+' : '';
  const abs = Math.abs(pct);
  let cls = 'edge-neutral';
  if (abs >= 10) cls = pct >= 0 ? 'edge-strong-yes' : 'edge-strong-no';
  else if (abs >= 5) cls = pct >= 0 ? 'edge-mild-yes' : 'edge-mild-no';
  const label = pct >= 0 ? ' → BUY YES' : ' → BUY NO';
  return `<span class="${cls}">${sign}${pct.toFixed(1)}pp${label}</span>`;
}

function renderLocationCard(location, dateStr) {
  const date = new Date(dateStr + 'T12:00:00Z');
  const dateFormatted = date.toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC',
  });
  return `
    <div class="card location-card">
      <div class="card-label">Location</div>
      <h2 class="location-name">${esc(location.displayName)}</h2>
      <div class="location-meta">
        <span>${location.lat.toFixed(4)}°N, ${location.lon.toFixed(4)}°E</span>
        <span class="sep">·</span>
        <span>${esc(dateFormatted)}</span>
      </div>
    </div>`;
}

function renderForecastCard(models, consensusC, consensusF) {
  const rows = models.map(m => {
    if (m.error || m.maxC == null) {
      return `<tr>
        <td>
          <span class="model-dot" style="background:${m.color}"></span>
          <strong>${esc(m.label)}</strong>
          <small class="model-desc">${esc(m.desc)}</small>
        </td>
        <td colspan="2" class="error-cell">⚠ ${esc(m.error || 'No data')}</td>
      </tr>`;
    }
    return `<tr>
      <td>
        <span class="model-dot" style="background:${m.color}"></span>
        <strong>${esc(m.label)}</strong>
        <small class="model-desc">${esc(m.desc)}</small>
      </td>
      <td><span class="temp-primary">${fC(m.maxC)}</span> <span class="temp-secondary">/ ${fF(m.maxF)}</span></td>
      <td class="temp-secondary">${fC(m.minC)} / ${fF(m.minF)}</td>
    </tr>`;
  });

  const consensusRow = consensusC != null ? `
    <tr class="consensus-row">
      <td><strong>Consensus</strong> <small class="model-desc">avg of available models</small></td>
      <td><span class="temp-primary">${fC(consensusC)}</span> <span class="temp-secondary">/ ${fF(consensusF)}</span></td>
      <td>—</td>
    </tr>` : '';

  return `
    <div class="card">
      <div class="card-label">Temperature Forecast</div>
      <table class="forecast-table">
        <thead><tr><th>Model</th><th>Max Temp</th><th>Min Temp</th></tr></thead>
        <tbody>${rows.join('')}${consensusRow}</tbody>
      </table>
    </div>`;
}

function renderMarketsCard(markets, models) {
  if (!markets.length) {
    return `
      <div class="card">
        <div class="card-label">Polymarket Weather Markets</div>
        <p class="no-data">No temperature markets found for this city.<br>
        Try searching Polymarket directly for the city name, or paste the market URL in the second tab.</p>
      </div>`;
  }

  // Sort: largest absolute edge first
  const sorted = [...markets].sort((a, b) => Math.abs(b.edge ?? 0) - Math.abs(a.edge ?? 0));

  const rows = sorted.map(m => {
    const perModelHtml = m.perModelProbs && m.perModelProbs.length
      ? m.perModelProbs.map(p =>
          `<span class="per-model">${esc(p.label)}: ${fPct(p.prob)}</span>`
        ).join(' ')
      : '';

    const bucketStr = m.bucket
      ? formatBucket(m.bucket)
      : '<span class="na">unrecognised format</span>';

    return `<tr>
      <td class="question-cell">
        <a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.question)}</a>
        <div class="bucket-parsed">${bucketStr}</div>
        ${perModelHtml ? `<div class="per-model-row">${perModelHtml}</div>` : ''}
      </td>
      <td class="center">${fPct(m.yesPrice)}</td>
      <td class="center">${fPct(m.modelProb)}</td>
      <td class="center">${fEdge(m.edge)}</td>
    </tr>`;
  });

  return `
    <div class="card">
      <div class="card-label">Polymarket Weather Markets (${markets.length})</div>
      <div class="legend">
        <span class="edge-strong-yes">≥ +10pp → strong BUY YES</span>
        <span class="edge-mild-yes">+5–10pp → mild BUY YES</span>
        <span class="edge-strong-no">≤ −10pp → strong BUY NO</span>
        <span class="edge-mild-no">−5–10pp → mild BUY NO</span>
      </div>
      <div class="table-scroll">
        <table class="markets-table">
          <thead><tr>
            <th>Market Question</th>
            <th>Mkt Price</th>
            <th>Model Prob</th>
            <th>Edge</th>
          </tr></thead>
          <tbody>${rows.join('')}</tbody>
        </table>
      </div>
    </div>`;
}

function formatBucket(bucket) {
  const { minC, maxC } = bucket;
  const fVal = c => `${c.toFixed(1)}°C (${(c * 9/5 + 32).toFixed(1)}°F)`;
  if (minC !== null && maxC !== null) return `Bucket: ${fVal(minC)} – ${fVal(maxC)}`;
  if (minC !== null) return `Bucket: above ${fVal(minC)}`;
  if (maxC !== null) return `Bucket: below ${fVal(maxC)}`;
  return 'Bucket: —';
}

function renderResults(data) {
  const el = document.getElementById('results');
  el.innerHTML = [
    renderLocationCard(data.location, data.dateStr),
    renderForecastCard(data.models, data.consensusC, data.consensusF),
    renderMarketsCard(data.markets, data.models),
  ].join('');
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderError(msg) {
  document.getElementById('results').innerHTML = `
    <div class="card error-card">
      <div class="card-label">Error</div>
      <p>${esc(msg)}</p>
    </div>`;
}

function renderLoading(msg) {
  document.getElementById('results').innerHTML = `
    <div class="card loading-card">
      <div class="spinner"></div>
      <p>${esc(msg)}</p>
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// UI wiring
// ─────────────────────────────────────────────────────────────────────────────

function setLoading(form, loading) {
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = loading;
  btn.textContent = loading ? 'Loading…' : btn.dataset.label;
}

function initDateInput() {
  const input = document.getElementById('date');
  const today = new Date().toISOString().slice(0, 10);
  const max = new Date(Date.now() + 15 * 864e5).toISOString().slice(0, 10);
  input.value = today;
  input.min = today;
  input.max = max;
}

function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    });
  });
}

function initForms() {
  const formCity = document.getElementById('form-city');
  formCity.querySelector('button').dataset.label = 'Get Forecast & Compare';
  formCity.addEventListener('submit', async e => {
    e.preventDefault();
    const city = document.getElementById('city').value.trim();
    const dateStr = document.getElementById('date').value;
    if (!city || !dateStr) return;
    setLoading(formCity, true);
    renderLoading(`Fetching forecasts for ${city}…`);
    try {
      const data = await analyzeByCity(city, dateStr);
      renderResults(data);
    } catch (err) {
      renderError(err.message);
    } finally {
      setLoading(formCity, false);
    }
  });

  const formUrl = document.getElementById('form-url');
  formUrl.querySelector('button').dataset.label = 'Analyze Market';
  formUrl.addEventListener('submit', async e => {
    e.preventDefault();
    const rawUrl = document.getElementById('poly-url').value.trim();
    if (!rawUrl) return;
    setLoading(formUrl, true);
    renderLoading('Fetching market and forecasts…');
    try {
      const data = await analyzeByUrl(rawUrl);
      renderResults(data);
    } catch (err) {
      renderError(err.message);
    } finally {
      setLoading(formUrl, false);
    }
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initDateInput();
  initForms();
});

// Expose pure functions for test runner
if (typeof window !== 'undefined') {
  window._testExports = {
    parseTempBucket,
    parseDateFromQuestion,
    parseCityFromQuestion,
    parseCityFromSlug,
    bucketProbability,
    erf,
    slugFromPolymarketUrl,
  };
}

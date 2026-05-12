'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// Configuration
// ─────────────────────────────────────────────────────────────────────────────

const API = {
  geocode:    'https://geocoding-api.open-meteo.com/v1/search',
  forecast:   'https://api.open-meteo.com/v1/forecast',
  ensemble:   'https://ensemble-api.open-meteo.com/v1/ensemble',
  polymarket: 'https://gamma-api.polymarket.com',
};

// Exact ICAO station coordinates for cities where Polymarket uses a specific
// weather station (often not the city center). Keyed by ICAO code.
const POLYMARKET_STATIONS = {
  KBKF: { lat: 39.7169, lon: -104.7519, city: 'Denver',         state: 'CO', aliases: ['denver'] },
  KMDW: { lat: 41.7868, lon:  -87.7522, city: 'Chicago',        state: 'IL', aliases: ['chicago'] },
  KORD: { lat: 41.9742, lon:  -87.9073, city: 'Chicago',        state: 'IL', aliases: [] },
  KLGA: { lat: 40.7773, lon:  -73.8726, city: 'New York',       state: 'NY', aliases: ['new york', 'nyc', 'new york city'] },
  KJFK: { lat: 40.6413, lon:  -73.7781, city: 'New York',       state: 'NY', aliases: [] },
  KSFO: { lat: 37.6189, lon: -122.3750, city: 'San Francisco',  state: 'CA', aliases: ['san francisco', 'sf'] },
  KLAX: { lat: 33.9416, lon: -118.4085, city: 'Los Angeles',    state: 'CA', aliases: ['los angeles', 'la'] },
  KMIA: { lat: 25.7959, lon:  -80.2870, city: 'Miami',          state: 'FL', aliases: ['miami'] },
  KHOU: { lat: 29.6454, lon:  -95.2789, city: 'Houston',        state: 'TX', aliases: ['houston'] },
  KIAH: { lat: 29.9844, lon:  -95.3414, city: 'Houston',        state: 'TX', aliases: [] },
  KPHX: { lat: 33.4343, lon: -112.0078, city: 'Phoenix',        state: 'AZ', aliases: ['phoenix'] },
  KSEA: { lat: 47.4502, lon: -122.3088, city: 'Seattle',        state: 'WA', aliases: ['seattle'] },
  KATL: { lat: 33.6407, lon:  -84.4277, city: 'Atlanta',        state: 'GA', aliases: ['atlanta'] },
  KBOS: { lat: 42.3629, lon:  -71.0064, city: 'Boston',         state: 'MA', aliases: ['boston'] },
  KDAL: { lat: 32.8470, lon:  -96.8517, city: 'Dallas',         state: 'TX', aliases: ['dallas'] },
  KDFW: { lat: 32.8998, lon:  -97.0403, city: 'Dallas',         state: 'TX', aliases: [] },
  KDEN: { lat: 39.8561, lon: -104.6737, city: 'Denver',         state: 'CO', aliases: [] },
  KLAS: { lat: 36.0840, lon: -115.1537, city: 'Las Vegas',      state: 'NV', aliases: ['las vegas'] },
  KMCO: { lat: 28.4294, lon:  -81.3089, city: 'Orlando',        state: 'FL', aliases: ['orlando'] },
  KDCA: { lat: 38.8521, lon:  -77.0377, city: 'Washington DC',  state: 'DC', aliases: ['washington', 'dc', 'washington dc'] },
  KIAD: { lat: 38.9531, lon:  -77.4565, city: 'Washington DC',  state: 'VA', aliases: [] },
};

// Public CORS proxy fallbacks — used only if direct fetch fails
const CORS_PROXIES = [
  url => `https://corsproxy.io/?${encodeURIComponent(url)}`,
  url => `https://api.allorigins.win/raw?url=${encodeURIComponent(url)}`,
];

async function fetchWithCorsFallback(url) {
  // 1. Try direct (Polymarket Gamma generally allows CORS)
  try {
    const resp = await fetch(url);
    if (resp.ok) return resp;
    throw new Error(`HTTP ${resp.status}`);
  } catch (directErr) {
    // 2. Try proxies in order
    for (const buildProxyUrl of CORS_PROXIES) {
      try {
        const proxyUrl = buildProxyUrl(url.toString());
        const resp = await fetch(proxyUrl);
        if (resp.ok) return resp;
      } catch (_) { /* try next */ }
    }
    throw new Error(`Polymarket fetch failed (direct + proxies): ${directErr.message}`);
  }
}

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
    id: 'gfs_graphcast025',
    label: 'GFS-GraphCast',
    desc: 'NOAA × Google DeepMind AI model (0.25°, 10-day)',
    fallback: 'ecmwf_aifs025_single',
    fallbackLabel: 'ECMWF AIFS',
    fallbackDesc: 'ECMWF AI model — graph neural network',
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
    // Pick next occurrence of this month/day. Compare dates only (no time)
    // so "today" doesn't get bumped to next year.
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    year = now.getFullYear();
    const candidate = new Date(year, month - 1, day);
    if (candidate < today) year++;
  }

  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/**
 * Extract date from a Polymarket slug like "...-on-may-7-2026".
 * Returns YYYY-MM-DD or null.
 */
function parseDateFromSlug(slug) {
  if (!slug) return null;
  const s = slug.toLowerCase();
  // Pattern: month-day-year, e.g. "may-7-2026" or "may-7th-2026"
  const m = s.match(/(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)-(\d{1,2})(?:st|nd|rd|th)?-(\d{4})/);
  if (!m) return null;
  const month = MONTHS[m[1]];
  if (!month) return null;
  const day = parseInt(m[2], 10);
  const year = parseInt(m[3], 10);
  if (day < 1 || day > 31 || year < 2020 || year > 2099) return null;
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
// Station helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Extract ICAO station code from a Weather Underground URL embedded in text.
 * e.g. "https://www.wunderground.com/history/daily/us/co/aurora/KBKF" → "KBKF"
 */
function extractIcaoFromWunderground(text) {
  if (!text) return null;
  const m = text.match(/wunderground\.com\/history\/daily\/[^/]+\/[^/]+\/[^/]+\/([A-Z]{4})\b/);
  return m ? m[1] : null;
}

/**
 * Look up a city name in POLYMARKET_STATIONS by alias or city name.
 * Returns { icao, station } or null.
 */
function lookupStationByCity(cityName) {
  const lower = cityName.toLowerCase().trim();
  for (const [icao, st] of Object.entries(POLYMARKET_STATIONS)) {
    if (st.city.toLowerCase() === lower || st.aliases.includes(lower)) {
      return { icao, station: st };
    }
  }
  return null;
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
    // last-resort: any model (Open-Meteo's default best forecast)
    { id: 'best_match', label: model.label + ' (best_match)', desc: model.desc },
  ].filter(Boolean);

  const errors = [];
  for (const attempt of attempts) {
    try {
      const f = await fetchSingleModel(lat, lon, dateStr, attempt.id);
      return { ...f, usedId: attempt.id, label: attempt.label, desc: attempt.desc, color: model.color };
    } catch (e) {
      errors.push(`[${attempt.id}] ${e.message}`);
      console.warn(`[${attempt.id}] ${e.message}`);
    }
  }
  return {
    maxC: null, minC: null, maxF: null, minF: null,
    usedId: model.id, label: model.label, desc: model.desc, color: model.color,
    error: errors.join(' | ') || `All attempts failed for ${model.label}`,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Ensemble forecast
// ─────────────────────────────────────────────────────────────────────────────

const ENSEMBLE_MODELS = [
  { id: 'ecmwf_ifs04',  label: 'ECMWF IFS Ensemble 0.4°', members: 51 },
  { id: 'icon_global',  label: 'ICON Global Ensemble',     members: 40 },
  { id: 'gfs025',       label: 'GFS 0.25° Ensemble',       members: 31 },
];

/**
 * Fetch ensemble daily max temperatures for a single date.
 * Tries ECMWF → ICON → GFS in order; returns first success.
 * Returns { members: number[], modelId, memberCount } or null.
 */
async function fetchEnsembleForecast(lat, lon, dateStr) {
  for (const em of ENSEMBLE_MODELS) {
    try {
      const url = new URL(API.ensemble);
      const params = {
        latitude: lat,
        longitude: lon,
        daily: 'temperature_2m_max',
        models: em.id,
        start_date: dateStr,
        end_date: dateStr,
        timezone: 'auto',
      };
      Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));

      const resp = await fetch(url);
      if (!resp.ok) {
        const body = await resp.text().catch(() => '');
        throw new Error(`HTTP ${resp.status}${body ? ': ' + body.slice(0, 100) : ''}`);
      }
      const data = await resp.json();
      if (data.error) throw new Error(data.reason || 'API error');

      const daily = data.daily || {};
      const times = daily.time || [];
      const idx = times.indexOf(dateStr);
      if (idx === -1) throw new Error(`Date ${dateStr} not in ensemble window`);

      const members = [];
      for (const [key, values] of Object.entries(daily)) {
        if (key.startsWith('temperature_2m_max_member') && Array.isArray(values)) {
          const v = values[idx];
          if (v != null) members.push(Math.round(v * 10) / 10);
        }
      }
      if (!members.length) throw new Error('No member data in response');

      return { members, modelId: em.id, modelLabel: em.label, memberCount: members.length };
    } catch (e) {
      console.warn(`[ensemble:${em.id}] ${e.message}`);
    }
  }
  return null;
}

/**
 * Build probability distribution from ensemble members.
 * Returns percentiles, mean, stddev, and 1°C histogram bins.
 */
function buildEnsembleDistribution(members) {
  if (!members || !members.length) return null;
  const sorted = [...members].sort((a, b) => a - b);
  const n = sorted.length;

  const mean = sorted.reduce((s, v) => s + v, 0) / n;
  const variance = sorted.reduce((s, v) => s + (v - mean) ** 2, 0) / n;
  const stddev = Math.sqrt(variance);

  const pct = p => {
    const i = (p / 100) * (n - 1);
    const lo = Math.floor(i), hi = Math.ceil(i);
    return lo === hi ? sorted[lo] : sorted[lo] + (i - lo) * (sorted[hi] - sorted[lo]);
  };

  const minBin = Math.floor(sorted[0]);
  const maxBin = Math.ceil(sorted[n - 1]);
  const bins = [];
  for (let t = minBin; t <= maxBin; t++) {
    const count = members.filter(v => v >= t && v < t + 1).length;
    if (count > 0) {
      bins.push({
        tempC: t,
        tempF: Math.round((t * 9 / 5 + 32) * 10) / 10,
        count,
        prob: count / n,
      });
    }
  }

  return {
    mean:   Math.round(mean   * 10) / 10,
    stddev: Math.round(stddev * 10) / 10,
    p10:    Math.round(pct(10)  * 10) / 10,
    p25:    Math.round(pct(25)  * 10) / 10,
    p50:    Math.round(pct(50)  * 10) / 10,
    p75:    Math.round(pct(75)  * 10) / 10,
    p90:    Math.round(pct(90)  * 10) / 10,
    bins,
    n,
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

  const resp = await fetchWithCorsFallback(url);
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

  const resp = await fetchWithCorsFallback(url);
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

function buildAnalysis(location, dateStr, modelResults, markets, ensemble = null) {
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

  return { location, dateStr, models: modelResults, consensusC, consensusF, markets: marketsWithEdge, ensemble };
}

async function analyzeByCity(cityInput, dateStr) {
  validateDate(dateStr);
  const location = await geocodeCity(cityInput);

  // If this city maps to a known Polymarket ICAO station, use exact coordinates
  const stLookup = lookupStationByCity(cityInput);
  if (stLookup) {
    const { icao, station } = stLookup;
    location.lat = station.lat;
    location.lon = station.lon;
    location.icao = icao;
    location.displayName += ` (station ${icao})`;
  }

  const [modelResults, markets, ensembleRaw] = await Promise.all([
    Promise.all(MODELS.map(m => fetchModelWithFallback(location.lat, location.lon, dateStr, m))),
    searchPolymarketByCity(location.name).catch(e => {
      console.warn('Polymarket search failed:', e.message);
      return [{ _error: e.message }];
    }),
    fetchEnsembleForecast(location.lat, location.lon, dateStr).catch(e => {
      console.warn('Ensemble fetch failed:', e.message);
      return null;
    }),
  ]);

  const ensemble = ensembleRaw
    ? { ...ensembleRaw, dist: buildEnsembleDistribution(ensembleRaw.members) }
    : null;

  return buildAnalysis(location, dateStr, modelResults, markets, ensemble);
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

  // Fallback: extract date from the slug itself (e.g. "...-on-may-7-2026")
  if (!dateStr) dateStr = parseDateFromSlug(slug);
  // Fix year-mismatch: if the slug has an explicit year and it differs from
  // what was parsed from the question, trust the slug.
  const slugDate = parseDateFromSlug(slug);
  if (slugDate && dateStr && slugDate.slice(0, 4) !== dateStr.slice(0, 4)) {
    dateStr = slugDate;
  }

  if (!city) throw new Error(`Could not extract city from market question: "${markets[0].question}". Use the "By City" tab instead.`);
  if (!dateStr) throw new Error(`Could not extract date from market question: "${markets[0].question}". Use the "By City" tab instead.`);

  // Try to resolve the exact ICAO station Polymarket uses (embedded as a
  // Weather Underground URL in the event description).
  const icao = extractIcaoFromWunderground(event.description || '');
  let location;
  if (icao && POLYMARKET_STATIONS[icao]) {
    const st = POLYMARKET_STATIONS[icao];
    location = {
      lat: st.lat,
      lon: st.lon,
      name: city,
      admin: st.state || '',
      country: 'US',
      countryCode: 'US',
      timezone: 'auto',
      displayName: `${city} (${icao} — ${st.city}, ${st.state})`,
      icao,
    };
  } else {
    location = await geocodeCity(city);
    if (icao) location.icao = icao; // store even if not in table
  }

  // Date might be far in the future (Polymarket markets can be months ahead).
  // Attempt forecast anyway; Open-Meteo will return an error if out of range.
  const [modelResults, ensembleRaw] = await Promise.all([
    Promise.all(MODELS.map(m => fetchModelWithFallback(location.lat, location.lon, dateStr, m))),
    fetchEnsembleForecast(location.lat, location.lon, dateStr).catch(e => {
      console.warn('Ensemble fetch failed:', e.message);
      return null;
    }),
  ]);

  const ensemble = ensembleRaw
    ? { ...ensembleRaw, dist: buildEnsembleDistribution(ensembleRaw.members) }
    : null;

  return buildAnalysis(location, dateStr, modelResults, markets, ensemble);
}

function validateDate(dateStr) {
  const d = new Date(dateStr + 'T12:00:00Z');
  if (isNaN(d.getTime())) throw new Error(`Invalid date: ${dateStr}`);
  // Allow 1 day tolerance for timezone differences (e.g. user in Israel
  // checking a date that's still "today" in the US).
  const yesterday = new Date();
  yesterday.setHours(0, 0, 0, 0);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d < yesterday) throw new Error('Date is in the past. Open-Meteo only provides forecasts for future dates.');
  const maxDate = new Date();
  maxDate.setHours(0, 0, 0, 0);
  maxDate.setDate(maxDate.getDate() + 16);
  if (d > maxDate) throw new Error('Date is more than 16 days ahead. Open-Meteo forecast horizon is ~16 days.');
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
  const fetchError = markets.length === 1 && markets[0]._error ? markets[0]._error : null;
  const realMarkets = markets.filter(m => !m._error);

  if (fetchError) {
    return `
      <div class="card">
        <div class="card-label">Polymarket Weather Markets</div>
        <p class="no-data" style="color:#e3b341">⚠ Polymarket search failed (CORS / network): ${esc(fetchError)}<br>
        Use the <strong>"By Polymarket URL"</strong> tab and paste the market URL directly — that bypasses the search limitation.</p>
      </div>`;
  }
  if (!realMarkets.length) {
    return `
      <div class="card">
        <div class="card-label">Polymarket Weather Markets</div>
        <p class="no-data">No temperature markets found for this city.<br>
        Try the <strong>"By Polymarket URL"</strong> tab and paste the market URL directly.</p>
      </div>`;
  }
  // Sort: largest absolute edge first
  const sorted = [...realMarkets].sort((a, b) => Math.abs(b.edge ?? 0) - Math.abs(a.edge ?? 0));

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
      <div class="card-label">Polymarket Weather Markets (${realMarkets.length})</div>
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

function renderEnsembleCard(ensemble) {
  if (!ensemble || !ensemble.dist) return '';

  const { dist, modelId, modelLabel, memberCount } = ensemble;
  const toF = c => Math.round((c * 9 / 5 + 32) * 10) / 10;

  const statsHtml = [
    { label: 'P10',     val: dist.p10 },
    { label: 'P25',     val: dist.p25 },
    { label: 'Median',  val: dist.p50 },
    { label: 'P75',     val: dist.p75 },
    { label: 'P90',     val: dist.p90 },
    { label: 'Mean',    val: dist.mean },
    { label: 'Spread ±', val: dist.stddev, noF: true },
  ].map(s => `
    <div class="stat-item">
      <span class="stat-label">${s.label}</span>
      <span class="stat-value">${s.val.toFixed(1)}°C${!s.noF ? ` / ${toF(s.val).toFixed(1)}°F` : ''}</span>
    </div>`).join('');

  const maxProb = Math.max(...dist.bins.map(b => b.prob));

  const rows = dist.bins.map(b => {
    const barW = maxProb > 0 ? Math.round((b.prob / maxProb) * 100) : 0;
    const hiF  = Math.round(((b.tempC + 1) * 9 / 5 + 32) * 10) / 10;
    return `<tr>
      <td class="ens-temp">${b.tempC}–${b.tempC + 1}°C</td>
      <td class="ens-temp-f">${b.tempF.toFixed(1)}–${hiF.toFixed(1)}°F</td>
      <td class="ens-count">${b.count}/${memberCount}</td>
      <td class="ens-prob">${(b.prob * 100).toFixed(1)}%</td>
      <td class="ens-bar-cell"><div class="ens-bar" style="width:${barW}%"></div></td>
    </tr>`;
  }).join('');

  return `
    <div class="card">
      <div class="card-label">Ensemble Probability Distribution · ${esc(modelLabel || modelId)} · ${memberCount} members</div>
      <div class="ensemble-stats">${statsHtml}</div>
      <div class="table-scroll">
        <table class="ensemble-table">
          <thead><tr>
            <th>Bucket (°C)</th>
            <th>Bucket (°F)</th>
            <th>Members</th>
            <th>Probability</th>
            <th style="width:160px">Distribution</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;
}

function renderResults(data) {
  const el = document.getElementById('results');
  el.innerHTML = [
    renderLocationCard(data.location, data.dateStr),
    renderForecastCard(data.models, data.consensusC, data.consensusF),
    renderEnsembleCard(data.ensemble),
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
  // Use LOCAL date, not UTC, to avoid timezone issues
  const fmt = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const now = new Date();
  const yesterday = new Date(now); yesterday.setDate(yesterday.getDate() - 1);
  const max = new Date(now); max.setDate(max.getDate() + 16);
  input.value = fmt(now);
  input.min = fmt(yesterday);
  input.max = fmt(max);
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

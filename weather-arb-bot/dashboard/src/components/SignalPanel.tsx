import type { ForecastSignal, Signals } from "../api/client";

interface Props {
  signals: Signals;
}

function Row({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex justify-between items-start py-2 border-b border-gray-800 last:border-0">
      <span className="text-gray-400 text-sm">{label}</span>
      <div className="text-right">
        <span className="text-white text-sm font-medium">{value}</span>
        {sub && <p className="text-gray-500 text-xs">{sub}</p>}
      </div>
    </div>
  );
}

function ForecastRow({ label, fc }: { label: string; fc: ForecastSignal | null | undefined }) {
  if (!fc || fc.predicted_high_f == null) return null;
  const low = fc.predicted_low_f != null ? ` / Lo ${fc.predicted_low_f}°F` : "";
  const cond = fc.conditions ? ` — ${fc.conditions}` : "";
  return <Row label={label} value={`Hi ${fc.predicted_high_f}°F${low}`} sub={cond || undefined} />;
}

export default function SignalPanel({ signals }: Props) {
  const pm = signals.primary_metar as Record<string, number | null> | null;
  const rm = signals.reference_metar as Record<string, number | null> | null;
  const trend = signals.metar_trend;
  const mp = signals.market_price;

  const coordStr =
    signals.city_lat != null && signals.city_lon != null
      ? `${Math.abs(signals.city_lat).toFixed(3)}°${signals.city_lat >= 0 ? "N" : "S"}, ` +
        `${Math.abs(signals.city_lon).toFixed(3)}°${signals.city_lon >= 0 ? "E" : "W"}`
      : null;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
      <h3 className="text-white font-semibold mb-1">Signal Panel</h3>
      {coordStr && <p className="text-gray-500 text-xs mb-4">{coordStr}</p>}

      {pm && (
        <Row
          label="Primary METAR"
          value={`${pm.temperature_f ?? "—"}°F`}
          sub={`DP ${pm.dew_point_f ?? "—"}°F | Wind ${pm.wind_direction ?? "—"}°/${pm.wind_speed_kt ?? "—"}kt`}
        />
      )}

      {rm && (
        <Row
          label="Reference Station"
          value={`${rm.temperature_f ?? "—"}°F`}
          sub={`Wind ${rm.wind_direction ?? "—"}°/${rm.wind_speed_kt ?? "—"}kt`}
        />
      )}

      {trend && (
        <Row
          label="Temp Trend"
          value={`${trend.temp_rate_per_hour > 0 ? "+" : ""}${trend.temp_rate_per_hour}°F/hr`}
          sub={`Current: ${trend.current_temp_f}°F`}
        />
      )}

      <div className="mt-3 mb-1">
        <p className="text-gray-600 text-xs uppercase tracking-wider">Forecast Sources</p>
      </div>

      <ForecastRow label="Wunderground" fc={signals.wunderground_forecast} />
      <ForecastRow label="GFS (global)" fc={signals.gfs_forecast} />
      <ForecastRow label="ECMWF" fc={signals.ecmwf_forecast} />
      <ForecastRow label="HRRR (3km CONUS)" fc={signals.hrrr_forecast} />
      <ForecastRow label="NWS (official)" fc={signals.nws_forecast} />
      <ForecastRow label="Tomorrow.io" fc={signals.tomorrowio_forecast} />
      <ForecastRow label="Meteosource" fc={signals.meteosource_forecast} />

      {mp && (
        <>
          <div className="mt-3 mb-1">
            <p className="text-gray-600 text-xs uppercase tracking-wider">Market</p>
          </div>
          <Row
            label="Market Price (YES)"
            value={`${Math.round(mp.yes_price * 100)}¢`}
            sub={`NO: ${Math.round(mp.no_price * 100)}¢`}
          />
        </>
      )}
    </div>
  );
}

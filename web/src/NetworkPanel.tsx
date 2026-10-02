import {useEffect, useState} from 'react';

type Latency = {mean: number | null; p50: number | null; p95: number | null; p99: number | null};
export type NetworkUE = {
  ue_id: string; profile: string; longitude: number; latitude: number; elevation_m: number;
  serving_sector_id: string | null; sinr_db: number | null; goodput_mbps: number;
  allocated_bandwidth_mhz: number; dropped_packets: number; pending_packets: number;
  radio_queue_latency_ms: Latency;
  mean_mcs?: number | null; observed_tbler?: number | null;
};
export type NetworkResult = {
  name: string; backend: string; device: string; created_at: string;
  scenario: {run_id: string; frequency_mhz: number; bandwidth_mhz: number; duration_s: number; core_latency_ms: number;
    direction?: 'uplink' | 'downlink'; link_adaptation?: 'fixed' | 'adaptive';
    fixed_mcs?: number | null; mcs_table_index?: number; calibrated?: boolean};
  summary: {ue_count: number; connected_ue_count: number; serving_cell_count: number;
    total_goodput_mbps: number; packet_delivery_ratio: number; packet_loss_ratio: number;
    pending_packets: number; dropped_packets: number; radio_queue_latency_ms: Latency;
    estimated_e2e_latency_ms: Latency; total_throughput_mbps?: number; observed_tbler?: number | null};
  assumptions: string[]; ues: NetworkUE[];
  time_series: {time_s: number; goodput_mbps: number}[];
};
type Props = {apiBase: string; runId: string; onResult: (result: NetworkResult | null) => void;
  onSelect: (point: {latitude: number; longitude: number}) => void};
const number = (value: number | null | undefined, digits = 2) => value == null ? '—' : value.toFixed(digits);

export default function NetworkPanel({apiBase, runId, onResult, onSelect}: Props) {
  const [experiments, setExperiments] = useState<NetworkResult[]>([]);
  const [name, setName] = useState('');
  const [result, setResult] = useState<NetworkResult | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setName(''); setResult(null); onResult(null); setError(''); setExperiments([]);
    fetch(`${apiBase}/v1/network?${new URLSearchParams({run_id: runId})}`, {signal: abort.signal})
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then((items: NetworkResult[]) => {
        setExperiments(items);
        setName(items.find(item => item.name === 'ottawa-mixed-traffic')?.name ?? items[0]?.name ?? '');
      }).catch(e => { if (e.name !== 'AbortError') setError(`Traffic results unavailable: ${e.message}`); });
    return () => abort.abort();
  }, [apiBase, runId, revision, onResult]);
  useEffect(() => {
    if (!name) { setResult(null); onResult(null); return; }
    const abort = new AbortController();
    setResult(null); onResult(null); setError('');
    fetch(`${apiBase}/v1/network/${encodeURIComponent(name)}`, {signal: abort.signal})
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then((item: NetworkResult) => { setResult(item); onResult(item); })
      .catch(e => { if (e.name !== 'AbortError') setError(`Traffic result unavailable: ${e.message}`); });
    return () => abort.abort();
  }, [apiBase, name, onResult, revision]);
  const maxRate = Math.max(1, ...(result?.time_series.map(p => p.goodput_mbps) ?? []));
  const points = result?.time_series.map(p =>
    `${p.time_s / result.scenario.duration_s * 300},${75 - p.goodput_mbps / maxRate * 65}`).join(' ');
  return <section className="network-panel" aria-label="Network traffic experiments">
    <p className="eyebrow">SIONNA SYS · NETWORK TRAFFIC</p>
    <h2>UE performance</h2>
    <label>Traffic experiment<select value={name} onChange={e => setName(e.target.value)}>
      <option value="">UE overlay off</option>
      {experiments.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
    </select></label>
    <button className="network-refresh" onClick={() => setRevision(v => v + 1)}>Refresh traffic results</button>
    {!experiments.length && !error && <p>No saved traffic experiments for this simulation run.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {result && <>
      <p><b>{result.scenario.direction === 'uplink' ? 'Uplink · sensor/UE to base station' : 'Downlink · base station to UE'}</b><br/>
        {result.scenario.link_adaptation === 'fixed' ? `Fixed MCS ${result.scenario.fixed_mcs}` : 'Adaptive MCS'}
        {' · '}NR table {result.scenario.mcs_table_index ?? 1}
        {' · '}{result.scenario.calibrated ? 'DL power calibration applied' : 'Uncalibrated power model'}</p>
      <p>{result.summary.connected_ue_count}/{result.summary.ue_count} UEs have resolved channels · {result.summary.serving_cell_count} cells<br/>
        {result.scenario.frequency_mhz} MHz · {result.scenario.bandwidth_mhz} MHz per cell · {result.scenario.duration_s} s</p>
      <div className="network-stats">
        <span><small>Application goodput</small><b>{number(result.summary.total_goodput_mbps)} Mbps</b></span>
        {result.summary.total_throughput_mbps != null && <span><small>Served payload throughput</small><b>{number(result.summary.total_throughput_mbps)} Mbps</b></span>}
        {result.summary.observed_tbler != null && <span><small>Observed block failures</small><b>{number(result.summary.observed_tbler * 100, 1)}%</b></span>}
        <span><small>Radio / queue P95</small><b>{number(result.summary.radio_queue_latency_ms.p95)} ms</b></span>
        <span><small>Estimated E2E P95</small><b>{number(result.summary.estimated_e2e_latency_ms.p95)} ms</b></span>
        <span><small>Packets delivered</small><b>{number(result.summary.packet_delivery_ratio * 100, 1)}%</b></span>
      </div>
      <p>{result.summary.dropped_packets} dropped · {result.summary.pending_packets} pending at end.<br/>
        Delay includes delivered packets only; E2E adds {result.scenario.core_latency_ms} ms configured core delay.</p>
      <svg className="network-chart" viewBox="0 0 300 90" role="img" aria-label="Application goodput over time">
        <line x1="0" y1="75" x2="300" y2="75" stroke="#29415d"/>
        <polyline points={points} fill="none" stroke="#a3e635" strokeWidth="2"/>
        <text x="2" y="12" fill="#91a5be" fontSize="9">{number(maxRate)} Mbps</text>
        <text x="260" y="88" fill="#91a5be" fontSize="9">{result.scenario.duration_s} s</text>
      </svg>
      <div className="network-ue-table">
        <div className="row headings"><span>UE</span><span>Mbps</span><span>MHz avg.</span><span>P95 ms</span></div>
        {result.ues.map(ue => <button className="row network-ue" key={ue.ue_id}
          onClick={() => onSelect({latitude: ue.latitude, longitude: ue.longitude})}>
          <span><b>{ue.ue_id}</b><small>{ue.sinr_db == null ? (ue.serving_sector_id ? 'No scheduled SINR' : 'No resolved RF channel') : `${number(ue.sinr_db, 1)} dB SINR`}</small>
            {ue.mean_mcs != null && <small>MCS {number(ue.mean_mcs, 1)} · block failures {number((ue.observed_tbler ?? 0) * 100, 1)}%</small>}</span>
          <span>{number(ue.goodput_mbps)}</span><span>{number(ue.allocated_bandwidth_mhz)}</span>
          <span>{number(ue.radio_queue_latency_ms.p95, 1)}</span>
        </button>)}
      </div>
      <p><a href={`${apiBase}/v1/network/${encodeURIComponent(name)}/csv`}>Download UE results</a></p>
      <details><summary>Experiment assumptions</summary>{result.assumptions.map(a => <p key={a}>{a}</p>)}</details>
    </>}
  </section>;
}

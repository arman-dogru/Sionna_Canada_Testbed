import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import DeckGL from '@deck.gl/react';
import {BitmapLayer, GeoJsonLayer, ScatterplotLayer} from '@deck.gl/layers';
import Map, {MapRef} from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import CesiumGlobe, {CesiumContentMode} from './CesiumGlobe';
import {coverageCanvas, CoverageMetric, CoverageTile, metricRgba} from './coverage';
import './styles.css';

type FeatureCollection = {type: 'FeatureCollection'; features: Array<any>};
type SectorPrediction = {
  sector_id: string; operator: string; technology: string; frequency_mhz: number;
  rsrp_dbm: number; rssi_dbm: number; sinr_db: number; confidence: string; rank: number;
};
type ServicePrediction = {
  latitude: number; longitude: number; run_id: string; model_version: string;
  service_available: boolean; serving_sector_id: string | null;
  predictions: SectorPrediction[]; warnings: string[];
};
type Coordinate = {latitude: number; longitude: number};
type RunSummary = {
  run_id: string;
  cell_size_m: number;
  width_m: number;
  anchor_wgs84: Coordinate;
  frequency_groups_mhz: number[];
  coverage_tiles: string[];
};

const anchor = {latitude: 45.340455200216, longitude: -75.91114196736};
const apiBase = import.meta.env.VITE_API_BASE ?? '';
const viewParameters = new URLSearchParams(window.location.search);
const requestedZoom = Number(viewParameters.get('zoom'));
const requestedPitch = Number(viewParameters.get('pitch'));
const requestedBearing = Number(viewParameters.get('bearing'));
const requestedRunId = viewParameters.get('run') ?? '';
const requestedCoverageFrequency = viewParameters.get('coverage-frequency') ?? '';
const requestedMetric = viewParameters.get('metric') as CoverageMetric | null;
const requestedView = viewParameters.get('view');
const requestedCesiumContent = viewParameters.get('cesium-content');
const initialZoom = viewParameters.has('zoom') && Number.isFinite(requestedZoom)
  ? Math.min(22, Math.max(0, requestedZoom)) : 13.2;
const initialPitch = viewParameters.has('pitch') && Number.isFinite(requestedPitch)
  ? Math.min(85, Math.max(0, requestedPitch)) : 48;
const initialBearing = viewParameters.has('bearing') && Number.isFinite(requestedBearing)
  ? requestedBearing : -18;
const captureMapOnly = viewParameters.get('capture') === 'map';

function runBounds(run: RunSummary): [[number, number], [number, number]] {
  const halfWidthM = run.width_m / 2;
  const latitudeDelta = halfWidthM / 111_320;
  const longitudeDelta = halfWidthM /
    (111_320 * Math.cos(run.anchor_wgs84.latitude * Math.PI / 180));
  return [
    [run.anchor_wgs84.longitude - longitudeDelta, run.anchor_wgs84.latitude - latitudeDelta],
    [run.anchor_wgs84.longitude + longitudeDelta, run.anchor_wgs84.latitude + latitudeDelta],
  ];
}

function signalColor(value: number): string {
  if (value >= -85) return '#2dd4bf';
  if (value >= -100) return '#facc15';
  if (value >= -110) return '#fb923c';
  return '#fb7185';
}

function App() {
  const mapRef = useRef<MapRef | null>(null);
  const [stations, setStations] = useState<FeatureCollection>({type: 'FeatureCollection', features: []});
  const [buildings, setBuildings] = useState<FeatureCollection>({type: 'FeatureCollection', features: []});
  const [point, setPoint] = useState(anchor);
  const [height, setHeight] = useState(1.5);
  const [operator, setOperator] = useState('');
  const [frequency, setFrequency] = useState('');
  const [prediction, setPrediction] = useState<ServicePrediction | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState('');
  const [coverageFrequency, setCoverageFrequency] = useState('');
  const [coverageMetric, setCoverageMetric] = useState<CoverageMetric>(
    requestedMetric && ['path_gain', 'rss', 'rsrp', 'sinr'].includes(requestedMetric) ? requestedMetric : 'rsrp'
  );
  const [coverage, setCoverage] = useState<CoverageTile[]>([]);
  const [viewMode, setViewMode] = useState<'map' | 'cesium'>(
    !captureMapOnly && (requestedView === 'cesium' || (
      requestedView !== 'map' && Boolean((import.meta.env.VITE_CESIUM_ION_TOKEN ?? '').trim())
    )) ? 'cesium' : 'map'
  );
  const [cesiumContent, setCesiumContent] = useState<CesiumContentMode>(
    requestedCesiumContent === 'planning' ? 'planning' : 'photorealistic'
  );
  const [measurements, setMeasurements] = useState<FeatureCollection>({type: 'FeatureCollection', features: []});
  const [selectedMeasurement, setSelectedMeasurement] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch(`${apiBase}/v1/stations?limit=10000`)
      .then(response => response.ok ? response.json() : Promise.reject(response.statusText))
      .then(setStations)
      .catch(reason => setError(`Station layer unavailable: ${reason}`));
    fetch(`${apiBase}/v1/scenes/legget-2000m/buildings`)
      .then(response => response.ok ? response.json() : {type: 'FeatureCollection', features: []})
      .then(setBuildings)
      .catch(() => undefined);
    fetch(`${apiBase}/v1/measurements?limit=10000`)
      .then(response => response.ok ? response.json() : {type: 'FeatureCollection', features: []})
      .then(setMeasurements).catch(() => undefined);
    fetch(`${apiBase}/v1/runs`).then(response => response.json()).then((items: RunSummary[]) => {
      setRuns(items);
      const requested = items.find(item => item.run_id === requestedRunId && item.coverage_tiles?.length);
      const available = requested ?? items.find(item => item.coverage_tiles?.length);
      if (available) {
        setSelectedRun(available.run_id);
        const first = available.frequency_groups_mhz?.find(group =>
          available.coverage_tiles.some(name => name.endsWith(`${group.toFixed(1).replace('.', 'p')}MHz.npz`))
        );
        if (requestedCoverageFrequency && available.frequency_groups_mhz?.includes(Number(requestedCoverageFrequency))) {
          setCoverageFrequency(requestedCoverageFrequency);
        } else if (first !== undefined) setCoverageFrequency(String(first));
      }
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!selectedRun || !coverageFrequency) { setCoverage([]); return; }
    const run = runs.find(item => item.run_id === selectedRun);
    const suffix = `${Number(coverageFrequency).toFixed(1).replace('.', 'p')}MHz.npz`;
    const names = run?.coverage_tiles?.filter(name => name.endsWith(suffix)) ?? [];
    Promise.all(names.map(name => fetch(
      `${apiBase}/v1/coverage/${selectedRun}/${name}?stride=1&metric=${coverageMetric}`
    ).then(r => r.json())))
      .then(setCoverage).catch(() => setCoverage([]));
  }, [runs, selectedRun, coverageFrequency, coverageMetric]);

  const selectedRunSummary = useMemo(
    () => runs.find(run => run.run_id === selectedRun),
    [runs, selectedRun],
  );

  useEffect(() => {
    if (viewMode !== 'map' || !selectedRunSummary || !mapRef.current) return;
    mapRef.current.fitBounds(runBounds(selectedRunSummary), {
      padding: 55,
      duration: 900,
    });
  }, [selectedRunSummary, viewMode]);

  function selectRun(runId: string) {
    setSelectedRun(runId);
    const run = runs.find(item => item.run_id === runId);
    if (!run || !coverageFrequency) return;
    const selectedFrequency = Number(coverageFrequency);
    const available = run.frequency_groups_mhz.filter(group =>
      run.coverage_tiles.some(name =>
        name.endsWith(`${group.toFixed(1).replace('.', 'p')}MHz.npz`)
      )
    );
    if (!available.includes(selectedFrequency)) {
      setCoverageFrequency(available.length ? String(available[0]) : '');
    }
  }

  async function runQuery(next = point) {
    setLoading(true); setError('');
    const params = new URLSearchParams({
      latitude: String(next.latitude), longitude: String(next.longitude),
      height_agl_m: String(height), limit: '12'
    });
    if (operator) params.set('operator', operator);
    const queryFrequency = frequency || coverageFrequency;
    if (queryFrequency) params.set('frequency_mhz', queryFrequency);
    if (selectedRun) params.set('run_id', selectedRun);
    try {
      const response = await fetch(`${apiBase}/v1/query?${params}`);
      if (!response.ok) throw new Error(await response.text());
      setPrediction(await response.json());
    } catch (reason) {
      setError(`Query failed: ${reason}`);
    } finally {
      setLoading(false);
    }
  }

  const filtered = useMemo(() => ({
    ...stations,
    features: stations.features.filter(feature => {
      const p = feature.properties;
      return (!operator || p.operator.toLowerCase().includes(operator.toLowerCase())) &&
        (!frequency || Math.abs(Number(p.frequency_mhz) - Number(frequency)) <= 5);
    })
  }), [stations, operator, frequency]);

  const layers = [
    ...coverage.map(tile => new BitmapLayer({
      id: `coverage-${tile.tile}-${tile.metric}`, image: coverageCanvas(tile.values, tile.metric), bounds: tile.bounds_wgs84,
      opacity: 0.72, pickable: false
    })),
    new GeoJsonLayer({
      id: 'buildings', data: buildings as any, extruded: true, wireframe: false,
      getElevation: feature => Number(feature.properties?.height_m ?? 10),
      getFillColor: [31, 57, 82, 210], getLineColor: [74, 108, 140, 150],
      material: {ambient: 0.55, diffuse: 0.7, shininess: 20, specularColor: [45, 65, 85]}
    }),
    new GeoJsonLayer({
      id: 'measurements', data: measurements as any, pickable: true, pointRadiusMinPixels: 4,
      getPointRadius: 22, getFillColor: feature => metricRgba(Number(feature.properties?.rsrp_dbm ?? -200), 'rsrp'),
      getLineColor: [255, 255, 255, 210], stroked: true, lineWidthMinPixels: 1,
      onClick: info => {
        if (!info.object) return;
        const coordinates = info.object.geometry.coordinates;
        const next = {longitude: coordinates[0], latitude: coordinates[1]};
        setSelectedMeasurement(info.object.properties); setPoint(next); runQuery(next);
        return true;
      }
    }),
    new GeoJsonLayer({
      id: 'sectors', data: filtered as any, pickable: true, pointRadiusMinPixels: 3,
      getPointRadius: 35, getFillColor: [56, 189, 248, 175], getLineColor: [224, 242, 254, 220]
    }),
    new ScatterplotLayer({
      id: 'query-point', data: [[point.longitude, point.latitude]],
      getPosition: d => d as [number, number], getRadius: 45, radiusMinPixels: 7,
      getFillColor: [244, 63, 94], stroked: true, getLineColor: [255, 255, 255], lineWidthMinPixels: 2
    })
  ];

  function onDeckClick(info: {coordinate?: number[]}) {
    if (!info.coordinate || info.coordinate.length < 2) return;
    const next = {longitude: info.coordinate[0], latitude: info.coordinate[1]};
    setSelectedMeasurement(null);
    setPoint(next); runQuery(next);
  }

  return <main className={captureMapOnly ? 'capture-map' : undefined}>
    <section className="map-shell">
      {viewMode === 'cesium' && !captureMapOnly ?
        <CesiumGlobe
          anchor={anchor}
          buildings={buildings}
          stations={filtered}
          coverage={coverage}
          contentMode={cesiumContent}
          focusArea={selectedRunSummary ? {
            key: selectedRunSummary.run_id,
            latitude: selectedRunSummary.anchor_wgs84.latitude,
            longitude: selectedRunSummary.anchor_wgs84.longitude,
            widthM: selectedRunSummary.width_m,
          } : undefined}
          onSelect={next => { setPoint(next); runQuery(next); }}
        /> :
        <DeckGL
          initialViewState={{
            ...anchor, zoom: initialZoom, pitch: initialPitch, bearing: initialBearing
          }}
          controller={true} layers={layers} onClick={onDeckClick}
        >
          <Map
            ref={mapRef}
            mapStyle="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
          />
        </DeckGL>}
      {!captureMapOnly && <div className="view-switch" role="group" aria-label="Map view">
        <button className={viewMode === 'map' ? 'active' : ''} onClick={() => setViewMode('map')}>Planning map</button>
        <button className={viewMode === 'cesium' && cesiumContent === 'photorealistic' ? 'active' : ''}
          onClick={() => { setCesiumContent('photorealistic'); setViewMode('cesium'); }}>Photo 3D</button>
        <button className={viewMode === 'cesium' && cesiumContent === 'planning' ? 'active' : ''}
          onClick={() => { setCesiumContent('planning'); setViewMode('cesium'); }}>Planning 3D</button>
      </div>}
      <div className="brand">
        <div className="technology-lockup">
              <img
                src="/whiteLOGO.svg"
                alt="NVIDIA"
                title="NVIDIA GPU acceleration; technology identification only"
              />
          <span>+</span>
          <strong>CESIUM ion</strong>
        </div>
        <span className="eyebrow">SIONNA RT · OTTAWA</span>
        <h1>Cellular Digital Twin</h1>
        <p>350 Legget Drive testbed</p>
      </div>
      <div className="legend"><span>Strong</span><i className="good"/><i className="fair"/><i className="weak"/><i className="poor"/><span>Weak</span></div>
    </section>

    <aside>
      <header>
        <p className="eyebrow">OUTDOOR SERVICE QUERY</p>
        <h2>Inspect a coordinate</h2>
        <p>Click the map or enter a location. Results rank public ISED sectors using the newest available model.</p>
      </header>
      <div className="form-grid">
        <label className="wide">Simulation run<select value={selectedRun} onChange={e => selectRun(e.target.value)}>
          <option value="">Free-space preview</option>{runs.map(run =>
            <option key={run.run_id} value={run.run_id}>{run.run_id} · {run.cell_size_m} m</option>)}
        </select></label>
        <label className="wide">Coverage overlay<select value={coverageFrequency} onChange={e => setCoverageFrequency(e.target.value)}>
          <option value="">Off</option>{(runs.find(run => run.run_id === selectedRun)?.frequency_groups_mhz ?? []).map(value =>
            <option key={value} value={value}>{value.toFixed(1)} MHz</option>)}</select></label>
        <label className="wide">Coverage metric<select value={coverageMetric} onChange={e => setCoverageMetric(e.target.value as CoverageMetric)}>
          <option value="path_gain">Highest path gain</option>
          <option value="rss">Highest RSS</option>
          <option value="rsrp">Highest RSRP</option>
          <option value="sinr">Highest SINR</option>
        </select></label>
        <label>Latitude<input value={point.latitude} onChange={e => setPoint({...point, latitude: +e.target.value})}/></label>
        <label>Longitude<input value={point.longitude} onChange={e => setPoint({...point, longitude: +e.target.value})}/></label>
        <label>Height AGL (m)<input type="number" min="0" step="0.5" value={height} onChange={e => setHeight(+e.target.value)}/></label>
        <label>Frequency MHz<input placeholder="All" value={frequency} onChange={e => setFrequency(e.target.value)}/></label>
        <label className="wide">Operator<input placeholder="All public operators" value={operator} onChange={e => setOperator(e.target.value)}/></label>
      </div>
      <button onClick={() => runQuery()} disabled={loading}>{loading ? 'Computing…' : 'Query service'}</button>
      {error && <p className="error">{error}</p>}
      {prediction && <section className="results">
        <div className="result-head">
          <div><span className="eyebrow">{prediction.run_id}</span><h2>{prediction.service_available ? 'Service predicted' : 'Below threshold'}</h2></div>
          <span className={prediction.service_available ? 'status online' : 'status offline'}>{prediction.service_available ? 'AVAILABLE' : 'WEAK'}</span>
        </div>
        {prediction.warnings.map(warning => <p className="warning" key={warning}>{warning}</p>)}
        {selectedMeasurement && <div className="comparison">
          <span><small>Measured RSRP</small><b>{selectedMeasurement.rsrp_dbm?.toFixed?.(1) ?? '—'} dBm</b></span>
          <span><small>Matched simulation</small><b>{(() => {
            const match = prediction.predictions.find(item => item.sector_id === selectedMeasurement.matched_sector_id) ?? prediction.predictions[0];
            return match ? `${match.rsrp_dbm.toFixed(1)} dBm` : '—';
          })()}</b></span>
        </div>}
        <div className="table">
          <div className="row headings"><span>Sector</span><span>Band</span><span>RSRP</span><span>SINR</span></div>
          {prediction.predictions.map(item => <div className="row" key={item.sector_id}>
            <span><b>{item.operator}</b><small>{item.technology} · {item.confidence}</small></span>
            <span>{item.frequency_mhz.toFixed(1)}</span>
            <span style={{color: signalColor(item.rsrp_dbm)}}>{item.rsrp_dbm.toFixed(1)} dBm</span>
            <span>{item.sinr_db.toFixed(1)} dB</span>
          </div>)}
        </div>
      </section>}
      <footer>Planning estimate · Outdoor receiver · Not a service guarantee</footer>
    </aside>
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);

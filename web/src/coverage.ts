export type CoverageMetric = 'path_gain' | 'rss' | 'rsrp' | 'sinr';

export type CoverageTile = {
  tile: string;
  bounds_wgs84: [number, number, number, number];
  metric: CoverageMetric;
  field: string;
  unit: string;
  no_data_value: number;
  valid_cell_count: number;
  cell_size_m: number;
  values: number[][];
  heights_m?: number[][] | null;
};

const ranges: Record<CoverageMetric, [number, number]> = {
  path_gain: [-190, -70],
  rss: [-145, -35],
  rsrp: [-145, -45],
  sinr: [-20, 70],
};

const viridis = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [253, 231, 37],
];

export function metricRgba(value: number, metric: CoverageMetric, alpha = 195): [number, number, number, number] {
  if (!Number.isFinite(value) || value <= -199) return [0, 0, 0, 0];
  const [minimum, maximum] = ranges[metric];
  const normalized = Math.max(0, Math.min(1, (value - minimum) / (maximum - minimum)));
  const scaled = normalized * (viridis.length - 1);
  const lower = Math.floor(scaled);
  const upper = Math.min(viridis.length - 1, lower + 1);
  const blend = scaled - lower;
  const color = viridis[lower].map((channel, index) =>
    Math.round(channel + (viridis[upper][index] - channel) * blend)
  );
  return [color[0], color[1], color[2], alpha];
}

export function coverageCanvas(values: number[][], metric: CoverageMetric): HTMLCanvasElement {
  const height = values.length;
  const width = values[0]?.length ?? 1;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d')!;
  const image = context.createImageData(width, height);
  values.forEach((row, sourceY) => row.forEach((value, x) => {
    const y = height - sourceY - 1;
    const offset = (y * width + x) * 4;
    image.data.set(metricRgba(value, metric), offset);
  }));
  context.putImageData(image, 0, 0);
  return canvas;
}

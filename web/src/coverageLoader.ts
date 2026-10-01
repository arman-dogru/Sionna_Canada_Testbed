import type {CoverageTile} from './coverage';

export type CoverageProgress = {completed_bands: number; total_bands: number};

type LoaderOptions = {
  fetch?: typeof fetch;
  image?: () => HTMLImageElement;
  timeoutMs?: number;
  pollMs?: number;
  buildStallMs?: number;
  buildMaxMs?: number;
};

export function createCoverageLoader(apiBase = '', options: LoaderOptions = {}) {
  const fetchCoverage = options.fetch ?? globalThis.fetch.bind(globalThis);
  const newImage = options.image ?? (() => new Image());
  const timeoutMs = options.timeoutMs ?? 45_000;
  const pollMs = options.pollMs ?? 2_000;
  const buildStallMs = options.buildStallMs ?? 180_000;
  const buildMaxMs = options.buildMaxMs ?? 30 * 60_000;
  type Listener = (progress: CoverageProgress) => void;
  type Entry = {request: Promise<CoverageTile>; listeners: Set<Listener>; progress?: CoverageProgress};
  const cache = new Map<string, Entry>();

  async function metadata(url: string) {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        reject(new Error('Coverage request timed out. Check that the API is running, then retry.'));
        controller.abort();
      }, timeoutMs);
    });
    try {
      return await Promise.race([
        (async () => {
          const response = await fetchCoverage(url, {cache: 'no-store', signal: controller.signal});
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.detail ?? `HTTP ${response.status}`);
          return {response, payload};
        })(),
        timeout,
      ]);
    } finally {
      clearTimeout(timer!);
    }
  }

  async function imageReady(url: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const image = newImage();
      const timer = setTimeout(() => {
        finish(new Error('Coverage image timed out. Retry coverage loading.'));
        image.src = '';
      }, timeoutMs);
      function finish(error?: Error) {
        clearTimeout(timer);
        image.onload = null;
        image.onerror = null;
        if (error) reject(error); else resolve();
      }
      image.onload = () => finish(image.naturalWidth > 0 ? undefined : new Error('Coverage image is empty.'));
      image.onerror = () => finish(new Error('Coverage image could not be loaded. Retry coverage loading.'));
      image.src = url;
      if (image.complete) {
        finish(image.naturalWidth > 0 ? undefined : new Error('Coverage image could not be decoded.'));
      }
    });
  }

  async function load(url: string, emit: Listener): Promise<CoverageTile> {
    const started = Date.now();
    let lastProgress = started;
    let completed = -1;
    while (true) {
      const {response, payload} = await metadata(url);
      if (response.status !== 202) {
        const tile: CoverageTile = payload;
        const normalized = {
          ...tile,
          image_url: tile.image_url?.startsWith('/') ? `${apiBase}${tile.image_url}` : tile.image_url,
        };
        if (normalized.image_url) await imageReady(normalized.image_url);
        return normalized;
      }
      if (payload.status !== 'building') throw new Error('Unexpected coverage build response.');
      if (payload.completed_bands > completed) {
        completed = payload.completed_bands;
        lastProgress = Date.now();
      }
      emit({completed_bands: payload.completed_bands, total_bands: payload.total_bands});
      if (Date.now() - lastProgress >= buildStallMs) {
        throw new Error('Coverage recalculation has stopped making progress. Check the API log, then retry.');
      }
      if (Date.now() - started >= buildMaxMs) {
        throw new Error('Coverage recalculation is taking over 30 minutes. Check the API log, then retry.');
      }
      await new Promise(resolve => setTimeout(resolve, pollMs));
    }
  }

  return function cachedCoverage(url: string, onProgress?: Listener): Promise<CoverageTile> {
    let entry = cache.get(url);
    if (!entry) {
      const listeners = new Set<Listener>();
      const created: Entry = {listeners, request: undefined!};
      created.request = load(url, progress => {
        created.progress = progress;
        created.listeners.forEach(listener => listener(progress));
      });
      entry = created;
      cache.set(url, entry);
      created.request.then(() => {
        created.progress = undefined;
        // Limit decoded tile metadata retained across run/layer changes.
        if (cache.size > 256) cache.delete(cache.keys().next().value!);
      }, () => {
        if (cache.get(url) === created) cache.delete(url);
      });
    }
    if (onProgress) {
      entry.listeners.add(onProgress);
      if (entry.progress) onProgress(entry.progress);
    }
    const current = entry;
    return current.request.finally(() => {
      if (onProgress) current.listeners.delete(onProgress);
    });
  };
}

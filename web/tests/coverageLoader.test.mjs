import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createCoverageLoader} from '../src/coverageLoader.ts';

const tile = {tile: 'all-bands.npz', image_url: '/coverage.png'};
const response = (payload = tile, status = 200) => new Response(JSON.stringify(payload), {status});
const decodedImage = () => ({complete: true, naturalWidth: 1200});
const opts = {image: decodedImage, timeoutMs: 25, pollMs: 1};

test('shares in-flight requests, resolves a cached decoded image, and normalizes its URL', async () => {
  let calls = 0;
  const load = createCoverageLoader('http://api', {...opts, fetch: async () => {calls++; return response();}});
  const [a, b] = await Promise.all([load('/metadata'), load('/metadata')]);
  assert.equal(calls, 1);
  assert.equal(a.image_url, 'http://api/coverage.png');
  assert.equal(a, b);
});

test('times out a stalled fetch and evicts it so retry succeeds', async () => {
  let calls = 0;
  const load = createCoverageLoader('', {...opts, fetch: () => {
    calls++;
    return calls === 1 ? new Promise(() => {}) : Promise.resolve(response());
  }});
  await assert.rejects(load('/metadata'), /request timed out/);
  await load('/metadata');
  assert.equal(calls, 2);
});

test('times out stalled image decoding and rejects a cached broken image', async () => {
  const image = {complete: false, naturalWidth: 0};
  const load = createCoverageLoader('', {...opts, image: () => image, fetch: async () => response()});
  await assert.rejects(load('/metadata'), /image timed out/);
  assert.equal(image.onload, null);
  const broken = createCoverageLoader('', {...opts, image: () => ({complete: true, naturalWidth: 0}), fetch: async () => response()});
  await assert.rejects(broken('/metadata'), /could not be decoded/);
});

test('polls uncached builds, reports advancing progress, then loads the completed layer', async () => {
  let calls = 0;
  const progress = [];
  const load = createCoverageLoader('', {...opts, fetch: async () => {
    calls++;
    return calls < 3 ? response({status: 'building', completed_bands: calls, total_bands: 56}, 202) : response();
  }});
  await load('/metadata', value => progress.push(value.completed_bands));
  assert.deepEqual(progress, [1, 2]);
  assert.equal(calls, 3);
});

test('fails with an actionable message if a build stops progressing', async () => {
  const load = createCoverageLoader('', {...opts, buildStallMs: 5, fetch: async () => response({status: 'building', completed_bands: 0, total_bands: 56}, 202)});
  await assert.rejects(load('/metadata'), /stopped making progress/);
});

test('a backend build failure remains an error and can be retried', async () => {
  let calls = 0;
  const load = createCoverageLoader('', {...opts, fetch: async () => ++calls === 1 ? response({detail: 'Disk full'}, 500) : response()});
  await assert.rejects(load('/metadata'), /Disk full/);
  await load('/metadata');
});

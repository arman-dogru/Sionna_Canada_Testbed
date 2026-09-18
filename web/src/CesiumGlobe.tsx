import {useEffect, useRef, useState} from 'react';
import {
  BillboardGraphics,
  BoundingSphere,
  Cartesian2,
  Cartesian3,
  Cartographic,
  Color,
  ColorMaterialProperty,
  ConstantProperty,
  GeoJsonDataSource,
  HeightReference,
  HeadingPitchRange,
  ImageMaterialProperty,
  Ion,
  Math as CesiumMath,
  NearFarScalar,
  Rectangle,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  SingleTileImageryProvider,
  Terrain,
  VerticalOrigin,
  Viewer,
  createGooglePhotorealistic3DTileset,
  createOsmBuildingsAsync,
} from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';
import {coverageImage, CoverageTile} from './coverage';

type FeatureCollection = {type: 'FeatureCollection'; features: Array<any>};
type Coordinate = {latitude: number; longitude: number};
type FocusArea = {key: string; latitude: number; longitude: number; widthM: number};
export type CesiumContentMode = 'photorealistic' | 'planning';

// This is a visualization offset only: the saved Sionna receiver heights and
// RF values are unchanged. Keeping the translucent analysis plane above the
// low-rise photogrammetry prevents it from clipping into Google's terrain mesh.
const PHOTO_COVERAGE_HEIGHT_AGL_M = 35;
const PHOTO_COVERAGE_ALPHA = 0.72;

function environmentMegabytes(raw: string | undefined, fallback: number) {
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? Math.min(Math.max(Math.round(parsed), 128), 4096) : fallback;
}

const PHOTO_TILE_CACHE_MB = environmentMegabytes(import.meta.env.VITE_CESIUM_TILE_CACHE_MB, 1024);
const PHOTO_TILE_CACHE_OVERFLOW_MB = environmentMegabytes(
  import.meta.env.VITE_CESIUM_TILE_CACHE_OVERFLOW_MB,
  512,
);

type Props = {
  anchor: Coordinate;
  buildings: FeatureCollection;
  stations: FeatureCollection;
  coverage: CoverageTile[];
  contentMode: CesiumContentMode;
  focusArea?: FocusArea;
  onSelect: (point: Coordinate) => void;
};

export default function CesiumGlobe({
  anchor, buildings, stations, coverage, contentMode, focusArea, onSelect
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<Viewer | null>(null);
  const clickRef = useRef(onSelect);
  const [status, setStatus] = useState('Starting local globe…');
  const [coverageStatus, setCoverageStatus] = useState('');
  const [cameraStatus, setCameraStatus] = useState('');
  const token = (import.meta.env.VITE_CESIUM_ION_TOKEN ?? '').trim();
  const photorealistic = contentMode === 'photorealistic' && Boolean(token) &&
    import.meta.env.VITE_CESIUM_ENABLE_PHOTOREALISTIC !== 'false';

  useEffect(() => { clickRef.current = onSelect; }, [onSelect]);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed() || !focusArea) return;
    const center = Cartesian3.fromDegrees(focusArea.longitude, focusArea.latitude, 0);
    const radius = Math.max(focusArea.widthM * Math.SQRT2 / 2, 250);
    setCameraStatus(`Centering ${focusArea.key}…`);
    viewer.camera.flyToBoundingSphere(new BoundingSphere(center, radius), {
      duration: 0.9,
      offset: new HeadingPitchRange(
        CesiumMath.toRadians(18),
        CesiumMath.toRadians(-42),
        Math.max(focusArea.widthM * 1.4, 1100),
      ),
      complete: () => setCameraStatus(`Centered on ${focusArea.key}`),
    });
  }, [focusArea?.key, focusArea?.latitude, focusArea?.longitude, focusArea?.widthM]);

  useEffect(() => {
    if (!host.current) return;
    if (token) Ion.defaultAccessToken = token;
    const viewer = new Viewer(host.current, {
      animation: false,
      baseLayer: token && !photorealistic ? undefined : false,
      baseLayerPicker: false,
      fullscreenButton: false,
      geocoder: false,
      homeButton: false,
      infoBox: false,
      navigationHelpButton: false,
      sceneModePicker: true,
      selectionIndicator: false,
      timeline: false,
      terrain: token ? Terrain.fromWorldTerrain({requestVertexNormals: true}) : undefined,
    });
    viewerRef.current = viewer;
    viewer.scene.globe.baseColor = Color.fromCssColorString('#0b1829');
    // Google Photorealistic 3D Tiles already provide their own terrain mesh.
    // Keep World Terrain available for AGL height sampling, but do not render
    // or depth-test the globe against Google's sometimes sub-ellipsoid mesh.
    viewer.scene.globe.show = !photorealistic;
    viewer.scene.globe.depthTestAgainstTerrain = Boolean(token) && !photorealistic;
    viewer.camera.flyTo({
      destination: Cartesian3.fromDegrees(anchor.longitude, anchor.latitude, 5200),
      orientation: {heading: 0, pitch: CesiumMath.toRadians(-38), roll: 0},
      duration: 0,
    });

    let removePhotoProgress: (() => void) | undefined;
    let removePhotoReady: (() => void) | undefined;
    let removePhotoFailure: (() => void) | undefined;
    if (photorealistic) {
      setStatus('Connecting to Google Photorealistic 3D Tiles…');
      createGooglePhotorealistic3DTileset(
        {onlyUsingWithGoogleGeocoder: true},
        {
          // Cesium recommends 8–16 as a high-fidelity range. The previous 1.25
          // setting over-refined the entire 6 km overview and thrashed textures.
          maximumScreenSpaceError: 8,
          dynamicScreenSpaceError: true,
          dynamicScreenSpaceErrorDensity: 1.5e-4,
          dynamicScreenSpaceErrorFactor: 12,
          progressiveResolutionHeightFraction: 0.3,
          foveatedScreenSpaceError: true,
          preloadFlightDestinations: true,
          cacheBytes: PHOTO_TILE_CACHE_MB * 1024 * 1024,
          maximumCacheOverflowBytes: PHOTO_TILE_CACHE_OVERFLOW_MB * 1024 * 1024,
        },
      )
        .then(tileset => {
          if (viewer.isDestroyed()) return;
          // Google Photorealistic 3D Tiles are opaque by default. Do not apply
          // an alpha style: it makes buildings look translucent and causes the
          // RF layer to show through their walls and roofs.
          viewer.scene.primitives.add(tileset);
          let failedTiles = 0;
          const readyStatus = () => {
            const cacheMb = Math.round(tileset.totalMemoryUsageInBytes / (1024 * 1024));
            setStatus(failedTiles
              ? `Google Photo 3D degraded · ${failedTiles} failed tile${failedTiles === 1 ? '' : 's'} · ${cacheMb}/${PHOTO_TILE_CACHE_MB} MB cache`
              : `Google Photo 3D ready · ${cacheMb}/${PHOTO_TILE_CACHE_MB} MB tile cache`);
          };
          removePhotoProgress = tileset.loadProgress.addEventListener(
            (pendingRequests: number, processingTiles: number) => {
              if (pendingRequests || processingTiles) {
                setStatus(`Google Photo 3D loading · ${pendingRequests} requests · ${processingTiles} processing${failedTiles ? ` · ${failedTiles} failed` : ''}`);
              } else {
                readyStatus();
              }
            },
          );
          removePhotoReady = tileset.allTilesLoaded.addEventListener(readyStatus);
          removePhotoFailure = tileset.tileFailed.addEventListener(() => {
            failedTiles += 1;
            readyStatus();
          });
        })
        .catch(error => {
          if (!viewer.isDestroyed()) setStatus(`Photorealistic tiles unavailable · ${String(error)}`);
        });
    } else if (token && import.meta.env.VITE_CESIUM_ENABLE_OSM_BUILDINGS !== 'false') {
      createOsmBuildingsAsync()
        .then(tileset => viewer.scene.primitives.add(tileset))
        .then(() => setStatus('Cesium World Terrain + OSM Buildings'))
        .catch(() => setStatus('Cesium terrain connected · OSM buildings unavailable'));
    } else {
      setStatus(token ? 'Cesium World Terrain connected' : 'Local globe · add Cesium ion token for global terrain/buildings');
    }

    const handler = new ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction((movement: {position: Cartesian2}) => {
      const ray = viewer.camera.getPickRay(movement.position);
      const picked = ray ? viewer.scene.globe.pick(ray, viewer.scene) : undefined;
      const cartesian = picked ?? viewer.camera.pickEllipsoid(movement.position);
      if (!cartesian) return;
      const location = Cartographic.fromCartesian(cartesian);
      clickRef.current({
        latitude: CesiumMath.toDegrees(location.latitude),
        longitude: CesiumMath.toDegrees(location.longitude),
      });
    }, ScreenSpaceEventType.LEFT_CLICK);

    return () => {
      removePhotoProgress?.();
      removePhotoReady?.();
      removePhotoFailure?.();
      handler.destroy();
      viewerRef.current = null;
      viewer.destroy();
    };
  }, [anchor.latitude, anchor.longitude, photorealistic, token]);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    const sources: GeoJsonDataSource[] = [];
    let cancelled = false;
    const planningBuildings = contentMode === 'planning' ? buildings : {type: 'FeatureCollection', features: []};
    Promise.all([
      GeoJsonDataSource.load(planningBuildings as any, {clampToGround: false}),
      GeoJsonDataSource.load(stations as any, {clampToGround: true}),
    ]).then(([buildingSource, stationSource]) => {
      if (cancelled || viewer.isDestroyed()) return;
      sources.push(buildingSource, stationSource);
      for (const entity of buildingSource.entities.values) {
        if (!entity.polygon) continue;
        const height = Number(entity.properties?.height_m?.getValue() ?? 10);
        entity.polygon.material = new ColorMaterialProperty(Color.fromCssColorString('#29435e').withAlpha(0.88));
        entity.polygon.outline = new ConstantProperty(true);
        entity.polygon.outlineColor = new ConstantProperty(Color.fromCssColorString('#55728e').withAlpha(0.7));
        entity.polygon.heightReference = new ConstantProperty(HeightReference.CLAMP_TO_GROUND);
        entity.polygon.extrudedHeight = new ConstantProperty(height);
        entity.polygon.extrudedHeightReference = new ConstantProperty(HeightReference.RELATIVE_TO_GROUND);
      }
      for (const entity of stationSource.entities.values) {
        // GeoJSON points default to Cesium's map-pin billboard, which reads like a
        // chat bubble. Replace it explicitly with a compact RF mast glyph.
        entity.point = undefined;
        entity.billboard = new BillboardGraphics({
          image: '/cell-tower.svg',
          width: 34,
          height: 43,
          verticalOrigin: VerticalOrigin.BOTTOM,
          heightReference: HeightReference.CLAMP_TO_GROUND,
          scaleByDistance: new NearFarScalar(500, 1.15, 16_000, 0.42),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        });
      }
      viewer.dataSources.add(buildingSource);
      viewer.dataSources.add(stationSource);
    }).catch(() => setStatus(previous => `${previous} · local scene overlay unavailable`));
    return () => {
      cancelled = true;
      if (!viewer.isDestroyed()) sources.forEach(source => viewer.dataSources.remove(source, true));
    };
  }, [buildings, contentMode, stations]);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    const imageryLayers: Array<any> = [];
    const entities: Array<any> = [];
    coverage.forEach(tile => {
      const [west, south, east, north] = tile.bounds_wgs84;
      const image = coverageImage(tile);
      const rectangle = Rectangle.fromDegrees(west, south, east, north);
      if (photorealistic) {
        // Use an elevated, translucent analysis surface. This avoids the
        // streamed Google terrain/building mesh clipping a near-ground RF
        // rectangle. The offset is display-only and never changes RF values.
        entities.push(viewer.entities.add({
          name: `Simulated ${tile.metric} · ${tile.tile}`,
          rectangle: {
            coordinates: rectangle,
            material: new ImageMaterialProperty({
              image,
              transparent: true,
              color: Color.WHITE.withAlpha(PHOTO_COVERAGE_ALPHA),
            }),
            height: PHOTO_COVERAGE_HEIGHT_AGL_M,
            heightReference: HeightReference.RELATIVE_TO_TERRAIN,
            granularity: CesiumMath.toRadians(0.0001),
            outline: false,
          },
        }));
      } else {
        const layer = viewer.imageryLayers.addImageryProvider(new SingleTileImageryProvider({
          url: typeof image === 'string' ? image : image.toDataURL('image/png'),
          tileWidth: tile.pixel_width ?? (typeof image === 'string' ? 256 : image.width),
          tileHeight: tile.pixel_height ?? (typeof image === 'string' ? 256 : image.height),
          rectangle,
        }));
        layer.alpha = 0.78;
        imageryLayers.push(layer);
      }
    });
    const metric = coverage[0]?.metric.toUpperCase() ?? '';
    const resolution = coverage[0]?.cell_size_m;
    const frequency = coverage[0]?.frequency_group_mhz;
    const frequencyLabel = coverage.length
      ? frequency == null ? ' · combined bands' : ` · ${frequency.toFixed(1)} MHz`
      : '';
    const tileLabel = coverage.length === 1 ? 'raster' : 'tiles';
    setCoverageStatus(coverage.length
      ? `${coverage.length} simulated ${metric} ${tileLabel}${frequencyLabel} · ${resolution} m cells${photorealistic ? ` · elevated RF display +${PHOTO_COVERAGE_HEIGHT_AGL_M} m AGL` : ''}`
      : 'Simulation overlay off');
    viewer.scene.requestRender();
    return () => {
      if (viewer.isDestroyed()) return;
      imageryLayers.forEach(layer => viewer.imageryLayers.remove(layer, true));
      entities.forEach(entity => viewer.entities.remove(entity));
    };
  }, [coverage, contentMode, photorealistic]);

  return <>
    <div ref={host} className="cesium-host" />
    <div className="cesium-status">
      {status}<strong>{coverageStatus}</strong>{cameraStatus && <span>{cameraStatus}</span>}
    </div>
  </>;
}

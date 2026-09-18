import {useEffect, useRef, useState} from 'react';
import {
  BillboardGraphics,
  BoundingSphere,
  Cartesian2,
  Cartesian3,
  Cartographic,
  Cesium3DTileStyle,
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
import {coverageCanvas, CoverageTile} from './coverage';

type FeatureCollection = {type: 'FeatureCollection'; features: Array<any>};
type Coordinate = {latitude: number; longitude: number};
type FocusArea = {key: string; latitude: number; longitude: number; widthM: number};
export type CesiumContentMode = 'photorealistic' | 'planning';

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
      terrain: token && !photorealistic ? Terrain.fromWorldTerrain({requestVertexNormals: true}) : undefined,
    });
    viewerRef.current = viewer;
    viewer.scene.globe.baseColor = Color.fromCssColorString('#0b1829');
    viewer.scene.globe.depthTestAgainstTerrain = Boolean(token) && !photorealistic;
    viewer.camera.flyTo({
      destination: Cartesian3.fromDegrees(anchor.longitude, anchor.latitude, 5200),
      orientation: {heading: 0, pitch: CesiumMath.toRadians(-38), roll: 0},
      duration: 0,
    });

    if (photorealistic) {
      createGooglePhotorealistic3DTileset(
        {onlyUsingWithGoogleGeocoder: true},
        {maximumScreenSpaceError: 3, dynamicScreenSpaceError: true},
      )
        .then(tileset => {
          tileset.style = new Cesium3DTileStyle({color: 'color("white", 0.80)'});
          viewer.scene.primitives.add(tileset);
        })
        .then(() => setStatus('Google Photorealistic 3D Tiles · streamed by Cesium ion · visual layer'))
        .catch(error => setStatus(`Photorealistic tiles unavailable · ${String(error)}`));
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
      const canvas = coverageCanvas(tile.values, tile.metric);
      const rectangle = Rectangle.fromDegrees(west, south, east, north);
      if (photorealistic) {
        // Photorealistic 3D Tiles are a mesh above globe imagery. Put the RF
        // texture on a transparent analysis plane so the mesh cannot hide it.
        entities.push(viewer.entities.add({
          name: `Simulated ${tile.metric} · ${tile.tile}`,
          rectangle: {
            coordinates: rectangle,
            material: new ImageMaterialProperty({
              image: canvas,
              transparent: true,
              color: Color.WHITE.withAlpha(0.92),
            }),
            height: 120,
            heightReference: HeightReference.NONE,
            outline: false,
          },
        }));
      } else {
        const layer = viewer.imageryLayers.addImageryProvider(new SingleTileImageryProvider({
          url: canvas.toDataURL('image/png'),
          tileWidth: canvas.width,
          tileHeight: canvas.height,
          rectangle,
        }));
        layer.alpha = 0.78;
        imageryLayers.push(layer);
      }
    });
    const metric = coverage[0]?.metric.toUpperCase() ?? '';
    const resolution = coverage[0]?.cell_size_m;
    setCoverageStatus(coverage.length
      ? `${coverage.length} simulated ${metric} tiles · ${resolution} m cells${photorealistic ? ' · RF plane +120 m' : ''}`
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

/* Pushes Mapbox's stock dark-v11 style (base gray ~16% lightness) toward the
   near-black look the app used to have with hand-tuned CARTO/Google tiles.
   Call once the underlying mapboxgl.Map fires 'style.load'. */
window.LS_applyDarkMapboxStyle = function (map) {
    const set = (id, prop, val) => {
        try { if (map.getLayer(id)) map.setPaintProperty(id, prop, val); } catch (e) {}
    };

    // dark-v11 declares a globe projection, which curves the world at low zoom
    // and drifts out of alignment with the Leaflet overlays (markers, the scan
    // circle) that are positioned in Mercator. Force the flat projection here so
    // a style (re)load can never hand the globe back.
    try {
        if (map.getProjection && map.getProjection().name !== 'mercator') {
            map.setProjection('mercator');
        }
    } catch (e) {}

    // Base canvas — land/buildings stay near-black. Water is navy rather than
    // black so continents still read as shapes when zoomed all the way out,
    // where black land on black ocean used to merge into one flat void.
    set('land', 'background-color', '#0a0a0a');
    set('national-park', 'fill-color', '#0a0a0a');
    set('landuse', 'fill-color', '#0a0a0a');
    set('land-structure-polygon', 'fill-color', '#0a0a0a');
    set('land-structure-line', 'line-color', '#141414');
    set('water', 'fill-color', '#0b1f45');
    set('waterway', 'line-color', '#123a63');
    set('building', 'fill-color', '#0d0d0d');
    set('building', 'fill-outline-color', '#000000');
    set('aeroway-polygon', 'fill-color', '#141414');
    set('aeroway-line', 'line-color', '#242424');

    // Roads — dimmed so they read against the darker canvas without vanishing.
    [
        'road-simple', 'road-path', 'road-path-trail', 'road-path-cycleway-piste',
        'road-steps', 'road-pedestrian', 'tunnel-simple', 'tunnel-path',
        'tunnel-path-trail', 'tunnel-path-cycleway-piste', 'tunnel-steps',
        'tunnel-pedestrian', 'bridge-simple', 'bridge-case-simple', 'bridge-path',
        'bridge-path-trail', 'bridge-path-cycleway-piste', 'bridge-steps',
        'bridge-pedestrian',
    ].forEach(id => set(id, 'line-color', '#242424'));
    set('road-rail', 'line-color', '#141414');
    set('bridge-rail', 'line-color', '#141414');

    // Boundary halos — keep the borders themselves, darken their glow.
    set('admin-1-boundary-bg', 'line-color', '#0a0a0a');
    set('admin-0-boundary-bg', 'line-color', '#0a0a0a');
};

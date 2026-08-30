/* Pushes Mapbox's stock dark-v11 style (base gray ~16% lightness) toward the
   near-black look the app used to have with hand-tuned CARTO/Google tiles.
   Call once the underlying mapboxgl.Map fires 'style.load'. */
window.LS_applyDarkMapboxStyle = function (map) {
    const set = (id, prop, val) => {
        try { if (map.getLayer(id)) map.setPaintProperty(id, prop, val); } catch (e) {}
    };

    // Base canvas — land/water/buildings become near-black instead of dark gray.
    set('land', 'background-color', '#0a0a0a');
    set('national-park', 'fill-color', '#0a0a0a');
    set('landuse', 'fill-color', '#0a0a0a');
    set('land-structure-polygon', 'fill-color', '#0a0a0a');
    set('land-structure-line', 'line-color', '#141414');
    set('water', 'fill-color', '#000000');
    set('waterway', 'line-color', '#000000');
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

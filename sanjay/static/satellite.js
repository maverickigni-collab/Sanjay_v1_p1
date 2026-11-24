'use strict';
(async () => {
  const container = document.getElementById('operations-map');
  if (!container) return;
  const status = document.getElementById('imagery-status');
  const picker = document.getElementById('map-source');
  const onlineHost = document.getElementById('online-map');
  const loading = document.getElementById('map-loading');
  const storageKey = 'sanjay-basemap-v2';
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  try {
    const [metadata, online, geography, streetConfig] = await Promise.all([
      '/assets/maps/satellite.json', '/assets/maps/online.json', '/assets/maps/four-camps.json', '/assets/maps/streets.json'
    ].map(async url => {
      const response = await fetch(url);
      if (!response.ok) throw new Error('Missing local map data');
      return response.json();
    }));
    if (geography.test_only !== true || geography.camps.length !== 4 || geography.zones.features.length !== 4) throw new Error('Invalid four-camp fixture');
    const bounds = L.latLngBounds(metadata.bounds);
    const overviewBounds = document.body.dataset.linkedCamps === 'true' ? L.geoJSON(geography.zones).getBounds().extend(geography.camps.map(c=>[c.coordinates[1],c.coordinates[0]])) : bounds;
    const map = L.map(container, {minZoom: document.body.dataset.linkedCamps === 'true' ? 3 : 10, maxZoom: 16, zoomSnap: 0.25, zoomDelta: 0.5,
      maxBounds: bounds, maxBoundsViscosity: 1, scrollWheelZoom: false,
      zoomAnimation: !reducedMotion, fadeAnimation: !reducedMotion, attributionControl: true});
    map.fitBounds(bounds, {padding: [16, 16]});
    L.control.scale({imperial: false}).addTo(map);
    const streetURL = new URL(streetConfig.url.replace(/\{[zxy]\}/g, '0'));
    if (streetURL.origin !== 'https://tile.openstreetmap.org' || streetURL.username || streetURL.password) throw new Error('Unexpected street tile source');
    const streets = L.tileLayer(streetConfig.url, {maxNativeZoom: streetConfig.max_native_zoom, maxZoom: 19,
      attribution: streetConfig.attribution, referrerPolicy: 'strict-origin-when-cross-origin',
      updateWhenIdle: true, updateWhenZooming: false, keepBuffer: 1, detectRetina: false});
    let streetLoads = 0, streetErrors = 0, streetDeadline;
    let offlineReason = 'Zone map.';
    let satelliteCreditVisible = true;
    let ready = false;
    let roadDetail;
    SanjayLocationPicker.addRoads(map).then(layer=>{roadDetail=layer;if(container.dataset.source==='offline')layer.addTo(map);}).catch(()=>{document.getElementById('offline-provenance').textContent+=' Saved road detail unavailable.';});
    let mode = 'street';
    try { const saved = localStorage.getItem(storageKey); if (['street', 'offline', 'hybrid'].includes(saved)) mode = saved; } catch (_) { /* Storage is optional. */ }
    picker.value = mode;
    const acquired = new Intl.DateTimeFormat('en-GB', {day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC'}).format(new Date(metadata.acquired));
    document.getElementById('offline-provenance').textContent = `Saved Sentinel-2: ${acquired}, native ${metadata.native_resolution_m}m pixels. AOI SCL cloud ${metadata.aoi_quality.cloud_percent.toFixed(1)}%, shadow ${metadata.aoi_quality.shadow_percent.toFixed(1)}%, no data ${metadata.aoi_quality.nodata_percent.toFixed(1)}%. ${document.body.dataset.linkedCamps === 'true' ? 'Initial TEST zones used this rectangle; edited areas may overlap or extend outside it. Not municipal boundaries.' : 'Four TEST zones cover this demo rectangle, not official city limits.'} Host points are provisional; sites are not authorised shelters.`;
    function showOffline(reason) {
      offlineReason = reason;
      clearTimeout(streetDeadline);
      if (map.hasLayer(streets)) map.removeLayer(streets);
      image.setOpacity(1);
      if (!satelliteCreditVisible) { map.attributionControl.addAttribution(metadata.attribution); satelliteCreditVisible = true; }
      onlineHost.replaceChildren(); onlineHost.hidden = true; container.hidden = false;
      container.dataset.source = 'offline'; if(roadDetail&&!map.hasLayer(roadDetail))roadDetail.addTo(map); map.setMaxZoom(19); map.invalidateSize();
      status.textContent = ready ? `${reason} Saved satellite · 10m + saved OSM road detail · ${acquired} · TEST zones, not official boundaries.` : 'Loading saved satellite image…';
      loading.hidden = ready;
    }
    function showMode() {
      if (mode === 'offline' || !navigator.onLine) { showOffline(mode === 'offline' ? 'Zone map.' : 'Network disconnected.'); return; }
      if(roadDetail&&map.hasLayer(roadDetail))map.removeLayer(roadDetail);
      if (mode === 'street') {
        onlineHost.replaceChildren(); onlineHost.hidden = true; container.hidden = false; loading.hidden = true;
        container.dataset.source = 'street'; image.setOpacity(0); map.setMaxZoom(19); map.invalidateSize();
        if (satelliteCreditVisible) { map.attributionControl.removeAttribution(metadata.attribution); satelliteCreditVisible = false; }
        if (!map.hasLayer(streets)) streetLoads = 0;
        status.textContent = streetLoads ? 'Online street map · OpenStreetMap · TEST zones visible. Not live road conditions.' : 'Loading online street map… Saved satellite is available if loading fails.';
        if (!map.hasLayer(streets)) streets.addTo(map);
        return;
      }
      clearTimeout(streetDeadline);
      if (map.hasLayer(streets)) map.removeLayer(streets);
      container.hidden = true; onlineHost.hidden = false; loading.hidden = true;
      container.dataset.source = 'online'; onlineHost.replaceChildren();
      const url = new URL(online.url);
      if (url.origin !== 'https://www.google.com' || url.pathname !== '/maps/embed') throw new Error('Unexpected online source');
      const frame = document.createElement('iframe');
      // shortcut: the Google embed cannot carry our overlays, use a licensed API for integrated online imagery.
      frame.title = 'Google satellite detail of Vadodara; test zones are not shown here.';
      frame.referrerPolicy = 'no-referrer'; frame.allowFullscreen = true; frame.src = url.href;
      frame.addEventListener('error', () => { if (mode === 'hybrid' && onlineHost.contains(frame)) showOffline('Online embed failed.'); });
      onlineHost.append(frame);
      // iframe.onload cannot establish that cross-origin imagery tiles loaded.
      status.textContent = 'Google detail · no zone overlay. Select a camp or All zones to return to the planning map.';
    }
    function setMode(value) {
      mode = ['street', 'offline', 'hybrid'].includes(value) ? value : 'offline'; picker.value = mode;
      try { localStorage.setItem(storageKey, mode); } catch (_) { /* Mode still works without storage. */ }
      showMode();
    }
    const image = L.imageOverlay('/assets/maps/vadodara-sentinel.jpg', bounds, {attribution: metadata.attribution, interactive: false});
    image.on('load', () => { ready = true; if (container.dataset.source === 'offline') showOffline(offlineReason); });
    image.on('error', () => {
      ready = false; loading.hidden = true;
      if (container.dataset.source === 'offline') status.textContent = 'Saved imagery is unavailable. Zone outlines do not replace the missing basemap.';
    });
    image.addTo(map);
    streets.on('loading', () => {
      if (container.dataset.source !== 'street') return;
      streetLoads = 0; streetErrors = 0;
      clearTimeout(streetDeadline);
      streetDeadline = setTimeout(() => {
        if (mode === 'street' && container.dataset.source === 'street' && streetLoads === 0) showOffline('Street service timed out.');
      }, 8000);
    });
    streets.on('tileload', () => {
      if (container.dataset.source !== 'street') return;
      streetLoads += 1; clearTimeout(streetDeadline);
      status.textContent = 'Online street map · OpenStreetMap · TEST zones visible. Not live road conditions.';
    });
    streets.on('tileerror', () => {
      if (container.dataset.source !== 'street') return;
      streetErrors += 1;
      if (streetLoads === 0 && streetErrors >= 3) showOffline('Street service unavailable.');
    });
    map.attributionControl.addAttribution('Initial site references: <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer noopener">© OSM contributors</a> (N/E/W); South: <a href="https://admissionschool.com/school/billabong-high-international-school-vadodara-corporation-vadodara" target="_blank" rel="noreferrer noopener">AdmissionSchool</a>');
    const zones = L.layerGroup().addTo(map), sites = L.layerGroup().addTo(map);
    const zoneLayers = new Map(), markers = new Map();
    let selected = document.body.dataset.linkedCamps === 'true' ? document.body.dataset.node : 'south';
    function updateSelection() {
      document.querySelectorAll('[data-camp-detail]').forEach(el => { el.hidden = el.dataset.campDetail !== selected; });
      document.querySelectorAll('[data-select-camp]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.selectCamp === selected)));
      zoneLayers.forEach((layer, id) => layer.setStyle({weight: id === selected ? 3 : 2, fillOpacity: id === selected ? 0.22 : 0.09}));
      markers.forEach((marker, id) => marker.getElement()?.classList.toggle('selected', id === selected));
      container.dataset.selectedCamp = selected;
      window.dispatchEvent(new CustomEvent('sanjay-camp-selected', {detail: selected}));
      document.getElementById('map-status').textContent = `${selected} TEST camp selected. Authoring node remains Camp ${document.body.dataset.node}; ${document.body.dataset.linkedCamps === 'true' ? 'map inspection does not change the acting camp.' : 'this map camp has no linked supply ledger.'}`;
    }
    function selectCamp(id, focus) {
      if (window.sanjayEditing || !zoneLayers.has(id)) return;
      selected = id;
      if (mode === 'hybrid') setMode(navigator.onLine ? 'street' : 'offline');
      if (!map.hasLayer(zones)) { map.addLayer(zones); document.getElementById('show-zones').checked = true; }
      if (!map.hasLayer(sites)) { map.addLayer(sites); document.getElementById('show-camps').checked = true; }
      updateSelection();
      if (focus === 'site') {
        const camp = geography.camps.find(c => c.id === id);
        const point = [camp.coordinates[1], camp.coordinates[0]];
        const zoom = container.dataset.source === 'street' ? 17 : 15;
        if (reducedMotion) map.setView(point, zoom, {animate: false});
        else map.flyTo(point, zoom, {duration: 0.65});
      } else if (focus) {
        const options = {padding: [24, 24], maxZoom: 14, duration: 0.65, animate: !reducedMotion};
        if (reducedMotion) map.fitBounds(zoneLayers.get(id).getBounds(), options);
        else map.flyToBounds(zoneLayers.get(id).getBounds(), options);
      }
    }
    L.geoJSON(geography.zones, {
      style: feature => ({color: feature.properties.color, weight: 2, fillOpacity: 0.09, dashArray: '9 5', className: 'zone-boundary'}),
      onEachFeature: (feature, layer) => {
        const id = feature.properties.id;
        zoneLayers.set(id, layer);
        layer.on('click', () => selectCamp(id, true));
        layer.on('add', () => {
          const element = layer.getElement(); element.dataset.zoneId = id;
          element.setAttribute('tabindex', '0'); element.setAttribute('role', 'button');
          element.setAttribute('aria-label', `Inspect ${id} TEST service zone`);
          element.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); selectCamp(id, true); }
          });
        });
        const p = feature.properties.label_position;
        // Compass IDs are validated; facility names never enter HTML here.
        L.marker([p[1], p[0]], {interactive: false, keyboard: false, zIndexOffset: -2000,
          icon: L.divIcon({className: `zone-label zone-${id}`, html: id.toUpperCase(), iconSize: [90, 24], iconAnchor: [45, 12]})}).addTo(zones);
      }
    }).addTo(zones);
    geography.camps.forEach(camp => {
      const letter = camp.id[0].toUpperCase();
      const marker = L.marker([camp.coordinates[1], camp.coordinates[0]], {
        title: `TEST ${camp.id}: ${camp.host_name}; ${camp.coordinate_status || 'provisional'} point`, alt: `${camp.id} test camp`,
        icon: L.divIcon({className: `zone-pin zone-${camp.id}`, html: `<span>${letter}</span>`, iconSize: [34, 34], iconAnchor: [17, 17]})
      }).addTo(sites);
      const label = document.createElement('span'); label.textContent = `TEST ${letter} · ${camp.short_name}`;
      marker.bindTooltip(label, {permanent: true, direction: camp.id === 'south' ? 'left' : 'right', offset: [camp.id === 'south' ? -16 : 16, 0], className: 'camp-tooltip'});
      marker.on('click', () => selectCamp(camp.id, 'site'));
      marker.on('add', () => { marker.getElement().dataset.testCamp = camp.id; });
      marker.getElement().dataset.testCamp = camp.id; markers.set(camp.id, marker);
    });
    L.rectangle(bounds, {color: '#d6dee8', weight: 1, dashArray: '2 4', fill: false, interactive: false, className: 'demo-coverage-edge'}).addTo(zones);
    document.getElementById('fit-zones').addEventListener('click', () => {
      if (mode === 'hybrid') setMode(navigator.onLine ? 'street' : 'offline');
      map.addLayer(zones); map.addLayer(sites);
      document.getElementById('show-zones').checked = true; document.getElementById('show-camps').checked = true;
      map.invalidateSize({pan: false});
      map.fitBounds(overviewBounds, {padding: [16, 16], animate: false});
    });
    document.querySelectorAll('[data-select-camp]').forEach(button => button.addEventListener('click', () => selectCamp(button.dataset.selectCamp, true)));
    document.getElementById('show-zones').addEventListener('change', event => { if (event.target.checked) zones.addTo(map); else map.removeLayer(zones); });
    document.getElementById('show-camps').addEventListener('change', event => { if (event.target.checked) sites.addTo(map); else map.removeLayer(sites); });
    picker.addEventListener('change', () => setMode(picker.value));
    window.addEventListener('offline', () => { if (mode !== 'offline') showOffline('Network disconnected.'); });
    window.addEventListener('online', () => { if (mode !== 'offline') showMode(); });
    new ResizeObserver(() => { if (!container.hidden) map.invalidateSize(); }).observe(container);
    if (document.body.dataset.linkedCamps === 'true') {
      window.sanjayMap = {map, geography};
      window.dispatchEvent(new CustomEvent('sanjay-map-ready', {detail: window.sanjayMap}));
      document.querySelector('.map-instructions').textContent = 'Solid: physical perimeter. Dashed: service area. Reports require review; no map layer establishes current passability.';
    }
    updateSelection(); showMode();
  } catch (error) {
    loading.hidden = true; picker.disabled = true;
    status.textContent = `Zone map unavailable: ${error.message}. Supply records remain usable.`;
  }
})();


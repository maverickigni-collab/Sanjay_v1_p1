'use strict';
window.SanjayLocationPicker=(()=>{
  let roads;
  async function addRoads(map){
    // shortcut: bounded Canvas geometry and viewport labels; use vector tiles for larger coverage.
    roads=roads||fetch('/assets/maps/road-detail.json').then(r=>{if(!r.ok)throw Error('Saved road detail unavailable');return r.json();});
    const data=await roads,renderer=L.canvas({padding:0.2}),group=L.layerGroup(),labels=L.layerGroup();
    for(const [name,z] of [['saved-buildings',350],['saved-labels',450]])if(!map.getPane(name))map.createPane(name).style.zIndex=z;
    if(data.buildings)L.geoJSON(data.buildings,{renderer:L.canvas({pane:'saved-buildings',padding:0.2}),interactive:false,style:{color:'#9baebd',weight:0.7,fillColor:'#ccd7df',fillOpacity:0.9}}).addTo(group);
    const layers=data.features.map(f=>L.layerGroup([
      L.geoJSON(f,{renderer,interactive:false,style:{color:'#94a8b8',weight:f.properties.class==='major'?7:4.5,smoothFactor:0.8}}),
      L.geoJSON(f,{renderer,interactive:false,style:{color:'#fff',weight:f.properties.class==='major'?4.5:2.5,smoothFactor:0.8}})
    ]));
    labels.addTo(group);
    const update=()=>{
      layers.forEach((layer,i)=>{const visible=i===0||map.getZoom()>=14;if(visible&&!group.hasLayer(layer))group.addLayer(layer);else if(!visible&&group.hasLayer(layer))group.removeLayer(layer);});
      labels.clearLayers();if(!map.hasLayer(group)||map.getZoom()<14)return;
      const occupied=new Set(),bounds=map.getBounds(),size=map.getSize();let count=0;
      for(const item of data.labels||[]){
        if(count>=35)break;if(item.rank===3&&map.getZoom()<16||!bounds.contains([item.lat,item.lon]))continue;
        const p=map.latLngToContainerPoint([item.lat,item.lon]);
        if(p.x<45||p.y<75||p.x>size.x-45||p.y>size.y-60)continue;
        const cell=Math.floor(p.x/150)+':'+Math.floor(p.y/55);if(occupied.has(cell))continue;occupied.add(cell);
        const text=document.createElement('span');text.className='offline-map-label '+item.kind;text.textContent=item.name;
        L.marker([item.lat,item.lon],{pane:'saved-labels',interactive:false,keyboard:false,icon:L.divIcon({className:'offline-label-anchor',html:text,iconSize:[1,1],iconAnchor:[0,Math.hypot(p.x-size.x/2,p.y-size.y/2)<40?-30:0]})}).addTo(labels);count++;
      }
    };
    map.on('zoomend moveend',update);group.on('add',update);update();
    group.getAttribution=()=>`${data.source.attribution} · saved roads ${data.source.acquired_utc.slice(0,10)} · buildings near test camps only · not live access`;
    return group;
  }
  function create(id,metadata,onSelect,onInvalidate,status,form){
    const map=L.map(id,{scrollWheelZoom:false,minZoom:10,maxZoom:19,preferCanvas:true}).fitBounds(metadata.bounds);
    const image=L.imageOverlay('/assets/maps/vadodara-sentinel.jpg',metadata.bounds,{attribution:metadata.attribution});
    if(map.getZoom()<14)map.setZoom(14,{animate:false});
    L.control.scale({imperial:false}).addTo(map);
    const host=map.getContainer();host.classList.add('location-picker');host.classList.add('offline-street-map');host.setAttribute('aria-label','Drag map beneath fixed pin, then confirm location. Arrow keys pan; plus and minus zoom.');
    const pin=document.createElement('div');pin.className='centre-pin';pin.setAttribute('aria-hidden','true');pin.innerHTML='<svg viewBox="0 0 40 48"><path d="M20 48C16 39 2 29 2 19a18 18 0 1 1 36 0c0 10-14 20-18 29Z" fill="#70bdff" stroke="#112b42" stroke-width="2"/><circle cx="20" cy="19" r="7" fill="#112b42"/></svg>';host.append(pin);
    const caption=document.createElement('span');caption.className='picker-map-detail';caption.textContent='Loading offline streets…';host.append(caption);
    const toggle=document.createElement('button');toggle.type='button';toggle.className='picker-basemap-toggle';toggle.textContent='Satellite';toggle.disabled=true;toggle.setAttribute('aria-label','Switch to saved 10 metre satellite background');host.append(toggle);L.DomEvent.disableClickPropagation(toggle);
    let satellite=false;toggle.onclick=()=>{satellite=!satellite;if(satellite){image.addTo(map);host.classList.remove('offline-street-map');}else{map.removeLayer(image);host.classList.add('offline-street-map');}toggle.textContent=satellite?'Street map':'Satellite';toggle.setAttribute('aria-label',satellite?'Switch to offline street background':'Switch to saved 10 metre satellite background');caption.textContent=satellite?'Saved satellite · 10 m · not live':'Offline streets · buildings near test camps';};
    addRoads(map).then(layer=>{layer.addTo(map);toggle.disabled=false;caption.textContent='Offline streets · buildings near test camps';}).catch(()=>{image.addTo(map);host.classList.remove('offline-street-map');caption.textContent='Road detail unavailable · satellite 10 m only';});
    map.on('movestart',()=>{if(form.hidden)return;host.classList.add('picker-moving');host.classList.remove('picker-confirmed');onInvalidate();status.textContent='Move the map beneath the pin, then confirm this location.';});
    map.on('moveend',()=>host.classList.remove('picker-moving'));
    function confirm(){if(form.hidden)return;const p=map.getCenter();host.classList.add('picker-confirmed');onSelect(p.lat,p.lng);}
    const picker={map,confirm,setLocation(lat,lon,zoom=17){map.setView([lat,lon],zoom,{animate:false});confirm();},clear(){host.classList.remove('picker-confirmed');}};
    for(const field of [form.elements.lat,form.elements.lon])field.addEventListener('change',()=>{const lat=form.elements.lat,lon=form.elements.lon;if(lat.value!==''&&lon.value!==''&&lat.validity.valid&&lon.validity.valid)picker.setLocation(Number(lat.value),Number(lon.value));});
    if(form.elements.lat.value!==''&&form.elements.lon.value!=='')picker.setLocation(Number(form.elements.lat.value),Number(form.elements.lon.value));
    return picker;
  }
  return {create,addRoads};
})();

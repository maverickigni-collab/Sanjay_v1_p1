"""Map-centred operator shell; schematic geography is never operational evidence."""
import html

from .geography import load_test_geography


def esc(value):
    return html.escape(str(value), quote=True)


def map_panel(snapshot):
    data = snapshot.get("geography") or load_test_geography()
    details = []
    for camp in data['camps']:
        if snapshot.get('registry'):
            from .ops_ui import camp_detail
            details.append(camp_detail(camp,snapshot));continue
        key = camp['id']
        lon, lat = camp['coordinates']
        details.append(f'''<section data-camp-detail="{key}" {'hidden' if key != 'south' else ''}>
<h2>{key.title()} test camp</h2><h3 class="host-name">{esc(camp['host_name'])}</h3><p class="help">{esc(camp['address'])}</p>
<p class="test-assignment">Test assignment only · not an authorised relief site</p>
<section class="detail-section"><h3>Responsibility</h3><p>{key.title()} service zone</p><p class="help">Synthetic planning boundary inside the saved Vadodara demo area. Not an official municipal zone.</p></section>
<section class="detail-section"><h3>Host & location</h3><p>{esc(camp['host_type'])}</p><p class="help">{lat}, {lon} · provisional point</p><p class="help">Physical camp perimeter: not surveyed or drawn.</p></section>
<dl class="camp-status"><div><dt>Capacity</dt><dd>Unknown</dd></div><div><dt>Supplies</dt><dd>Not linked</dd></div><div><dt>Host permission</dt><dd>Not requested</dd></div></dl>
<details class="source-details"><summary>Location sources</summary><p>{esc(camp['point_note'])}</p><a href="{esc(camp['location_source'])}" target="_blank" rel="noreferrer noopener">Coordinate source</a> · <a href="{esc(camp['identity_source'])}" target="_blank" rel="noreferrer noopener">Official host information</a></details></section>''')
    node = snapshot['node']
    stock = snapshot['state']['stocks'].get(node, {'on_hand': 0, 'reserved': 0})
    statuses = {g['status'] for g in snapshot['state']['grants'].values() if g['supplier'] == node}
    target, label = 'requests', 'Review supply requests'
    for stage, action in (('approved', 'Review approved departure'), ('accepted', 'Review dispatch approval'), ('granted', 'Accept & reserve supplies')):
        if stage in statuses:
            target, label = ('commitments' if stage == 'granted' else 'dispatch'), action
            break
    if snapshot['read_only']:
        target, label = 'history', 'Inspect event history'
    legacy = f'''<details class="legacy-ledger"><summary>Separate A/B supply demo</summary><p>Authoring node: Camp {node}. These signed stock records are not assigned to a geographic test camp.</p><dl><div class="stock-row"><dt>On hand</dt><dd>{stock['on_hand']}</dd></div><div class="stock-row"><dt>Reserved</dt><dd>{stock['reserved']}</dd></div><div class="stock-row"><dt>Available</dt><dd>{stock['on_hand']-stock['reserved']}</dd></div></dl><a href="/?view={target}">{label}</a></details>'''
    if snapshot.get('registry'):legacy = f'<p class="help">Acting signed origin: {node}. Map inspection never changes it. Local four-node exchange is same-computer emulation.</p>'
    legend=''
    if snapshot.get('registry'):
        from .ops_ui import medical_legend
        legend=medical_legend()
    return f'''<div class="map-workspace"><section class="map-column" aria-label="Interactive camp and zone map">
<div class="map-heading"><h1>Vadodara · test zones</h1><div class="map-toolbar"><button type="button" id="fit-zones">All zones</button><label class="source-picker"><span class="visually-hidden">Map view</span><select id="map-source"><option value="street">Street map · online</option><option value="offline">Satellite · offline 10m</option><option value="hybrid">Google photo · no zones</option></select></label></div></div>
<p class="zone-scope">4 TEST service zones · saved city demo area, not municipal boundaries</p>
<div class="map-frame"><div id="operations-map" tabindex="0" role="group" aria-label="Camp and service-zone map. Arrow keys pan, plus and minus zoom."></div><div id="online-map" hidden></div><div id="map-loading">Loading map and test zones…</div></div>
<p id="imagery-status" class="imagery-status" role="status" aria-live="polite">Preparing zone map…</p>
<p class="map-instructions">Click a pin for site detail, or a zone for its coverage. Dashed lines are test boundaries. Camp footprints are not drawn. Not for navigation.</p>
{legend}<details class="imagery-info"><summary>Source, coverage & privacy</summary><p id="offline-provenance">Source metadata is loading.</p><p>Online streets use OpenStreetMap with our four zones and camp pins on the same map. Saved 10m Sentinel-2 imagery is the offline fallback. Google photo detail is a separate view without overlays. Street tile colours are adapted locally in dark mode; satellite images are not filtered.</p><p>Online streets send your IP, viewed tile area and app origin to OpenStreetMap. Google photo mode connects to Google. No supply records or operator access tokens are sent. Manual offline mode removes both remote map sources; tiles are not bulk-downloaded or saved as an offline pack.</p><a href="https://www.google.com/permissions/geoguidelines/" target="_blank" rel="noreferrer noopener">Google mapping terms</a> · <a href="https://dataspace.copernicus.eu/terms-and-conditions" target="_blank" rel="noreferrer noopener">Copernicus terms</a> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer noopener">OSM attribution</a> · <a href="https://operations.osmfoundation.org/policies/tiles/" target="_blank" rel="noreferrer noopener">Tile policy</a> · <a href="https://www.openstreetmap.org/fixthemap" target="_blank" rel="noreferrer noopener">Report basemap issue</a></details>
</section><aside class="camp-inspector" aria-label="Selected test camp"><div id="camp-details">{''.join(details)}</div>{legacy}<p id="map-status" class="visually-hidden" role="status" aria-live="polite">South test camp selected. Supply authoring node remains Camp {node}.</p></aside></div>'''



def shell(snapshot, csrf, view, content, message=''):
    node = snapshot['node']
    nav = ''.join(f'<a href="/?view={target}" {"aria-current=page" if target == view else ""}>{label}</a>' for target, label in (
        ('map', 'Map'), ('requests', 'Requests'), ('commitments', 'Stock & allocations'), ('dispatch', 'Dispatch'), ('receipts', 'Receipts'), ('reports', 'Reports'), ('exchange', 'Exchange updates'), ('history', 'Event history')))
    operational = bool(snapshot.get('registry'))
    if operational:
        nav += ''.join(f'<a href="/?view={target}">{label}</a>' for target,label in (('facilities','Infrastructure'),('routes','Road options'),('phone','Phone invitation'),('network','Camp Wi-Fi'),('links','Camp connections')))
    geography = (snapshot.get('geography') or load_test_geography()) if view in ('map','routes') else None
    camps = ''.join(f'<button type="button" class="camp-choice" data-select-camp="{c["id"]}" aria-pressed="{str(c["id"] == "south").lower()}"><span class="zone-chip zone-{c["id"]}">{c["id"][0].upper()}</span><span>{c["id"].title()}<small>{esc(c["short_name"])}</small></span></button>' for c in geography['camps']) if geography else '<a class="text-link" href="/?view=map">Return to test-zone map</a>'
    layers = '<fieldset class="layer-options"><legend>Show on map</legend><label><input type="checkbox" id="show-zones" checked>Service zones</label><label><input type="checkbox" id="show-camps" checked>Test camp sites</label></fieldset><p class="sidebar-note">4 map-test camps only. Their supply ledgers are not connected to the A/B workflow.</p>' if geography else ''
    if operational and geography:
        layers = '<fieldset class="layer-options"><legend>Map layers</legend>' + ''.join(f'<label><input type="checkbox" id="show-{key}" checked>{label}</label>' for key,label in (('zones','Service areas'),('camps','Camp sites'),('perimeters','Physical perimeters'),('facilities','Infrastructure within 3 km'),('reports','Active reports'))) + '</fieldset>'
    pending = sum(e['status'] == 'pending' for e in snapshot['events'])
    rejected = sum(e['status'] == 'rejected' for e in snapshot['events'])
    review = f'<a href="/?view=history">{pending} pending · {rejected} requiring review</a>' if pending or rejected else 'No pending or rejected local events'
    flash = f'<div class="flash" role="status">{esc(message)}</div>' if message else ''
    recovery = '<p class="notice">Recovery copy: inspection only. Writing is disabled.</p>' if snapshot['read_only'] else ''
    map_assets = f'<link rel="stylesheet" href="/assets/leaflet/leaflet.css"><script nonce="{esc(csrf)}" src="/assets/leaflet/leaflet.js" defer></script><script nonce="{esc(csrf)}" src="/assets/location-picker.js" defer></script><script nonce="{esc(csrf)}" src="/assets/satellite.js" defer></script>' if view in ('map','routes') else ''
    if operational and view=='reports':map_assets=f'<link rel="stylesheet" href="/assets/leaflet/leaflet.css"><script nonce="{esc(csrf)}" src="/assets/leaflet/leaflet.js" defer></script><script nonce="{esc(csrf)}" src="/assets/location-picker.js" defer></script>'
    extra_assets = f'<link rel="stylesheet" href="/assets/ops.css"><link rel="stylesheet" href="/assets/interactions.css"><script nonce="{esc(csrf)}" src="/assets/interactions.js" defer></script><script nonce="{esc(csrf)}" src="/assets/ops.js" defer></script>' if operational else ''
    acting = ('<label>Acting camp<select id="acting-camp">'+''.join(f'<option {"selected" if c["id"]==node else ""} value="{c["id"]}">{esc(c["name"])}</option>' for c in snapshot['registry']['camps'] if c['id'] in snapshot.get('actor_nodes',[x['id'] for x in snapshot['registry']['camps']]))+'</select></label>') if operational else f'Authoring node: Camp {node} · A/B supply demo'
    linked = ' data-linked-camps="true"' if operational else ''
    portal_note = 'Phone submission is separate; invitation in navigation.' if operational else 'Phone portal not available yet.'
    if operational:
        review = str(sum(r['status']=='unverified' for r in snapshot['registry']['reports']))+' reports awaiting review'
        from .ops_ui import editor
        content += editor()
    navigation=f'<div id="branched-menu-root"><nav aria-label="Workspace">{nav}</nav></div>'
    if operational:navigation='<details id="workspace-navigation" class="workspace-navigation" open><summary>Workspace navigation</summary>'+navigation+'</details>'
    return f'''<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="csrf-token" content="{esc(csrf)}"><title>SANJAY · Camp {node} · {esc(view.title())}</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/assets/app.css">{map_assets}{extra_assets}<script nonce="{esc(csrf)}" src="/assets/app.js" defer></script></head>
<body data-view="{view}" data-node="{node}"{linked}><a class="skip-link" href="#main">Skip to main content</a><header class="app-header"><a class="wordmark" href="/">SANJAY</a><span class="app-description">Relief coordination</span><span class="authoring">{acting}</span><button type="button" id="theme-toggle" aria-label="Use light theme" aria-pressed="false">Light mode</button></header>
<div class="app-layout"><aside class="app-sidebar">{navigation}<div class="camp-list test-camp-list"><h2>Test camps</h2>{camps}</div>{layers}<p class="sidebar-note">Operator interface · localhost only.<br>{portal_note}</p></aside><div class="main-column"><main id="main">{flash}{recovery}{content}</main><footer class="work-queue"><div><strong>Local record review</strong><span>{review}</span></div><a href="/?view=reports">{'Review reports' if operational else 'Reporting setup'}</a></footer><div class="prototype-note">Fictional prototype · One trusted local operator also performs approvals. No current real-world availability is claimed.</div></div></div></body></html>'''


def reports_panel():
    return '''<section class="page-heading"><h1>Reports & reporting</h1><p>Phone reporting is not connected in this build.</p></section><section class="ledger reporting-notice"><h2>No report intake is running</h2><p>This service is restricted to this computer. Phones cannot submit reports yet.</p><p>Next slice: a separate local-network submission portal, operator review, and reviewed map pins. No reports or live counts are simulated here.</p><a class="text-link" href="/?view=map">Return to map</a></section>'''

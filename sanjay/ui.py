"""Server-rendered field ledger. All displayed quantities come from durable state."""
import html

from .dashboard import map_panel, reports_panel, shell


def esc(value):
    return html.escape(str(value), quote=True)


def ref(value, prefix):
    return prefix + '-' + value[:6].upper()


def render(snapshot, csrf, message='', view='map'):
    state, node = snapshot['state'], snapshot['node']
    operational = bool(snapshot.get('registry'))
    valid = ('map', 'reports', 'requests', 'commitments', 'dispatch', 'exchange', 'receipts', 'history') + (('facilities','routes','phone','network','links') if operational else ())
    view = view if view in valid else 'map'
    inventory = state['stocks'].get(node, {'on_hand': 0, 'reserved': 0})
    on_hand, reserved = inventory['on_hand'], inventory['reserved']
    pending = sum(e['status'] == 'pending' for e in snapshot['events'])
    rejected = sum(e['status'] == 'rejected' for e in snapshot['events'])
    suppliers = [n for n in snapshot.get('trusted_nodes',('A','B')) if n != node]
    supplier_options = ''.join(f'<option value="{esc(n)}">Camp {esc(n)}</option>' for n in suppliers)
    def qty(default):
        return f'<label class="field">Quantity<span class="quantity-field"><input name="quantity" type="number" min="1" max="1000000" value="{default}" required><span>food packets</span></span></label>'
    def select(key, label, options):
        return '<label class="field">' + label + '<select name="' + key + '" required><option value="">Select a record</option>' + ''.join(f'<option value="{esc(k)}">{esc(v)}</option>' for k, v in options) + '</select></label>'
    def form(kind, title, fields, button, description='', danger=False):
        if snapshot['read_only']:
            return '<div class="quiet-note">Recovery copy. Inspection only; writing is disabled.</div>'
        return (f'<form class="action-sheet" data-kind="{kind}" method="post" action="/command">'
                f'<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="kind" value="{kind}">'
                f'<input type="hidden" name="return_view" value="{view}">'
                + (f'<input type="hidden" name="camp" value="{node}">' if operational else '') +
                f'<div class="sheet-label">CAMP {node} / LOCAL ACTION</div><h3>{title}</h3>'
                + (f'<p class="sheet-description">{description}</p>' if description else '') + fields
                + f'<button class="button {"departure" if danger else "primary"}" type="submit">{button}<span aria-hidden="true">↗</span></button>'
                '<div class="form-foot">Saved locally. Other camps learn through exchange.</div></form>')
    def badge(status):
        label = {'granted': 'Awaiting acceptance', 'accepted': 'Stock reserved', 'approved': 'Approved · not departed', 'dispatched': 'Departed', 'applied': 'Applied', 'pending': 'Pending', 'rejected': 'Needs review'}.get(status, status)
        return f'<span class="status status-{esc(status)}"><i></i>{esc(label)}</span>'
    def empty(title, text):
        return f'<div class="empty"><span class="empty-mark" aria-hidden="true">—</span><h3>{title}</h3><p>{text}</p></div>'
    def table(headers, rows, empty_title, empty_text):
        if not rows:
            return empty(empty_title, empty_text)
        return '<div class="table-scroll"><table><thead><tr>' + ''.join('<th>' + h + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + v + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'
    own_requests = [(k, r) for k, r in state['requests'].items() if r['owner'] == node and r['granted'] < r['quantity']]
    request_rows = [[f'<span class="record-ref" title="{esc(k)}">{ref(k,"REQ")}</span>', f'Camp {r["owner"]}', f'<strong>{r["quantity"]}</strong>', str(r['granted']), f'<span class="number-accent">{r["quantity"]-r["received"]}</span>'] for k, r in state['requests'].items()]
    requests_table = table(['Reference', 'Requesting camp', 'Requested', 'Allocated', 'Still needed'], request_rows, 'No needs recorded here.', 'Create the first request, or import a signed update from another camp.')
    request_form = form('request', 'Record a supply need', qty(100), 'Save request', 'Tell the other camp what is needed. A request does not move or reserve stock.')
    requests_panel = f'<div class="workspace-grid"><section class="ledger"><div class="section-top"><h2>Request register</h2><span class="count">{len(request_rows):02d} RECORDS</span></div>{requests_table}<div class="ledger-foot">A request remains unmet until receipt is confirmed, not when a supplier promises delivery.</div></section><aside>{request_form}<div class="margin-note"><span>01 / THE PRINCIPLE</span><p>A durable record.<br>Not another message<br>that gets lost.</p></div></aside></div>'
    grant_rows = [[f'<span class="record-ref">{ref(k,"ALC")}</span>', f'{ref(g["request_id"],"REQ")}<small>Supplier · Camp {g["supplier"]}</small>', f'<strong>{g["quantity"]}</strong>', badge(g['status'])] for k, g in state['grants'].items()]
    grant_table = table(['Allocation', 'Linked request', 'Packets', 'State'], grant_rows, 'No promises made yet.', 'Allocate a supplier from the requesting camp. The supplier must accept before stock is reserved.')
    allocation = form('grant', 'Choose a supplier', select('request_id', 'Request to allocate', [(k, f'{ref(k,"REQ")} · {r["quantity"]-r["granted"]} unallocated') for k, r in own_requests]) + f'<label class="field">Supplying camp<select name="supplier">{supplier_options}</select></label>' + qty(100), 'Allocate supplies', 'Only the requesting camp allocates its own need.') if own_requests else '<div class="quiet-note"><strong>Allocation belongs to the requesting camp.</strong><p>Create a request here first, or import the other camp’s allocation.</p></div>'
    acceptable = [(k, f'{ref(k,"ALC")} · {g["quantity"]} packets') for k, g in state['grants'].items() if g['supplier'] == node and g['status'] == 'granted']
    acceptance = form('accept', 'Accept & reserve stock', select('grant_id', 'Allocation to accept', acceptable), 'Accept and reserve', 'On-hand stock stays the same. Only available stock falls.') if acceptable else ''
    commitments_panel = f'<div class="workspace-grid"><section class="ledger"><div class="section-top"><h2>Supply commitments</h2><span class="count">{len(grant_rows):02d} ALLOCATIONS</span></div>{grant_table}<div class="ledger-foot">An allocation is a proposal. An accepted reservation is a commitment backed by stock.</div></section><aside>{acceptance}{allocation}<div class="margin-note"><span>02 / THE PRINCIPLE</span><p>Promise only what<br>you can set aside.</p></div></aside></div>'
    approved = [(k, g) for k, g in state['grants'].items() if g['supplier'] == node and g['status'] == 'approved']
    waiting = [(k, f'{ref(k,"ALC")} · {g["quantity"]} packets') for k, g in state['grants'].items() if g['supplier'] == node and g['status'] == 'accepted']
    approval = form('approve', 'Authorise the dispatch', select('grant_id', 'Reserved allocation', waiting), 'Approve dispatch', 'Explicit approval is required. Approval alone does not reduce on-hand stock.') if waiting else ''
    departure = form('dispatch', 'Record actual departure', select('grant_id', 'Approved allocation', [(k, f'{ref(k,"ALC")} · {g["quantity"]} packets') for k, g in approved]), 'Record actual departure', 'Use only after the goods leave. This deducts the full allocation from on-hand and releases its reservation.', True) if approved else ''
    dispatch_rows = [[f'<span class="record-ref">{ref(k,"DSP")}</span>', f'Camp {d["supplier"]}<span class="route-arrow">→</span>Camp {d["destination"]}', f'<strong>{d["quantity"]}</strong>', str(d['received']), f'<span class="number-accent">{d["quantity"]-d["received"]}</span>'] for k, d in state['dispatches'].items()]
    departure_table = table(['Dispatch', 'Movement', 'Sent', 'Received', 'Unconfirmed Difference'], dispatch_rows, 'Nothing has left the camp.', 'Reserved supplies stay here until an approved departure is recorded.')
    departure_actions = approval + departure or '<div class="quiet-note"><strong>No dispatch action is ready.</strong><p>Accept an allocation at the supplying camp before approval. A departed shipment cannot be dispatched again.</p></div>'
    dispatch_panel = f'<div class="workspace-grid"><section class="ledger"><div class="section-top"><h2>Departure register</h2><span class="count">{len(dispatch_rows):02d} MOVEMENTS</span></div>{departure_table}<div class="ledger-foot">Not received yet does not mean lost. Unconfirmed shipment difference and unmet demand remain separate.</div></section><aside>{departure_actions}<div class="margin-note"><span>03 / THE PRINCIPLE</span><p>Approval is intent.<br>Departure is an event.</p></div></aside></div>'
    awaiting_receipt = [(k, f'{ref(k,"DSP")} · {d["quantity"]-d["received"]} outstanding') for k, d in state['dispatches'].items() if d['destination'] == node and d['received'] < d['quantity']]
    receipt_form = form('receipt', 'Confirm received quantity', select('dispatch_id', 'Incoming dispatch', awaiting_receipt) + qty(90), 'Confirm received quantity', 'Record only the goods actually received. Partial receipts are supported.') if awaiting_receipt else empty('No incoming receipt is ready.', 'Import the supplying camp’s signed departure record first.')
    receipts_panel = '<div class="workspace-grid"><section class="ledger"><div class="section-top"><h2>Incoming shipments</h2></div>' + departure_table + '</section><aside>' + receipt_form + '</aside></div>'
    exchange_panel = f'''<div class="workspace-grid"><section class="ledger exchange-ledger"><div class="section-top"><h2>Carry the record, intact.</h2><span class="count">SIGNED FILE EXCHANGE</span></div><p class="exchange-intro">Export here. Carry the file. Import there.</p><p>No automatic synchronisation is claimed. An export does not prove delivery or acknowledgement.</p><a class="button primary export-link" href="/export" download>Download signed update bundle<span aria-hidden="true">↗</span></a><div class="exchange-rule"><b>01</b><p>Choose the receiving camp deliberately.</p></div><div class="exchange-rule"><b>02</b><p>Keep the original signed bytes. Never include a private key.</p></div><div class="exchange-rule"><b>03</b><p>Repeated imports do not repeat stock movements.</p></div></section><aside class="action-sheet"><div class="sheet-label">CAMP {node} / RECEIVE UPDATES</div><h3>Import a signed record</h3><p class="sheet-description">Public identities must already be trusted. Malformed or forged bundles are rejected.</p><input type="hidden" name="csrf" value="{csrf}"><label class="file-drop">Select update file<input id="bundle" type="file" accept=".json,application/json"><span>JSON · maximum 1 MB</span></label><button class="button primary" id="import" type="button">Import selected update<span aria-hidden="true">↗</span></button><p id="import-result" class="form-foot" role="status">Only received updates appear in this ledger.</p></aside></div>'''
    history_rows = [[f'Camp {e["origin"]}', f'{e["seq"]:03d}', esc(e['kind'].replace('_',' ').title()), badge(e['status']), esc(e['reason']) or '—'] for e in snapshot['events']]
    history_panel = '<section class="ledger"><div class="section-top"><h2>The local event trail</h2><span class="count">APPEND-ONLY APPLICATION HISTORY</span></div>' + table(['Origin','Sequence','Action','Outcome','Details'], history_rows, 'No events yet.', 'Saved actions and received updates will appear here.') + '<div class="ledger-foot">A signature establishes node-key authorship, not real-world truth. Origin sequence is not a global clock.</div></section>'
    panels = {'requests': requests_panel, 'commitments': commitments_panel, 'dispatch': dispatch_panel, 'exchange': exchange_panel, 'receipts': receipts_panel, 'history': history_panel}
    titles = {'requests': 'Supply requests', 'commitments': 'Stock & allocations',
              'dispatch': 'Approval & departure', 'exchange': 'Exchange updates',
              'receipts': 'Incoming receipts', 'history': 'Event history'}
    stock_initial = not any(e['origin'] == node and e['kind'] == 'stock' and e['status'] == 'applied' for e in snapshot['events'])
    initial_control = '<details class="stock-settings"><summary>A/B supply demo — initial stock</summary><p class="help">Writes to the original authoring node, not a selected map-test camp.</p>' + form('stock', 'Set initial stock once', qty(200), 'Set initial stock once', 'One-time declaration, not a recurring top-up.') + '</details>' if stock_initial else ''
    if operational and view in ('reports','facilities','routes','phone','network','links'):
        from .ops_ui import panel
        content = panel(snapshot,view) + (map_panel(snapshot) if view=='routes' else '')
    elif view == 'map':
        content = map_panel(snapshot)
    elif view == 'reports':
        content = reports_panel()
    else:
        inventory_summary = f'<div class="inventory-summary" aria-label="Local recorded inventory"><span>Camp {node} · food packets</span><span>On hand <strong data-metric="on-hand">{on_hand}</strong></span><span>Reserved <strong data-metric="reserved">{reserved}</strong></span><span>Available <strong data-metric="available">{on_hand-reserved}</strong></span></div>'
        content = '<section class="page-heading"><h1>' + titles[view] + '</h1></section>' + inventory_summary + panels[view]
        if operational and node not in state['stocks']:
            content = content.replace('data-metric="on-hand">0','data-metric="on-hand">Unknown').replace('data-metric="available">0','data-metric="available">Unknown')
        if view == 'dispatch' and not operational:
            content = '<p class="notice">Road options are not available in this build. Verify the route independently before departure.</p>' + content
    if operational:
        initial_control = initial_control.replace('A/B supply demo — initial stock','Acting camp — initial stock').replace('Writes to the original authoring node, not a selected map-test camp.','Writes to the acting signed camp named in the header, not a map inspection selection.')
        if view=='dispatch':content = '<p class="notice">Before actual departure, <a href="/?view=routes">choose and review an offline road option</a>. Route is advisory, not verified safe.</p>'+content
    return shell(snapshot, csrf, view, content + initial_control, message)


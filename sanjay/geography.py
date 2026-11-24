"""Validate the four-camp map fixture without changing signed ledger identities."""
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

FIXTURE = Path(__file__).with_name('static') / 'maps/four-camps.json'
DIRECTIONS = ('south', 'north', 'east', 'west')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def ring_area(ring):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))) / 2


def point_inside(point, ring):
    """Strict containment for the small convex test polygons, not a routing engine."""
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:]):
        cross = (x-a[0])*(b[1]-a[1]) - (y-a[1])*(b[0]-a[0])
        if abs(cross) < 1e-12 and min(a[0],b[0]) <= x <= max(a[0],b[0]) and min(a[1],b[1]) <= y <= max(a[1],b[1]):
            return False
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


def validate_test_geography(data):
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data.get('schema_version') != 1 or data.get('test_only') is not True:
        raise ValueError('Unsupported or non-test camp fixture')
    bounds = data.get('scope_bounds')
    if not isinstance(bounds, list) or len(bounds) != 2 or any(not isinstance(p,list) or len(p)!=2 or not all(number(v) for v in p) for p in bounds):
        raise ValueError('Invalid test coverage bounds')
    (south,west),(north,east) = bounds
    if not (-90 < south < north < 90 and -180 < west < east < 180):
        raise ValueError('Invalid geographic bounds')
    reference = data.get('partition_reference', {})
    cx, cy = reference.get('lon'), reference.get('lat')
    if not number(cx) or not number(cy) or not west < cx < east or not south < cy < north:
        raise ValueError('Partition centre must be inside demo coverage')
    centre=[cx,cy]
    nw,ne,se,sw=[west,north],[east,north],[east,south],[west,south]
    expected={'north':[centre,nw,ne,centre], 'east':[centre,ne,se,centre],
              'south':[centre,se,sw,centre], 'west':[centre,sw,nw,centre]}
    camps=data.get('camps',[])
    features=data.get('zones',{}).get('features',[])
    if not isinstance(camps,list) or len(camps)!=4 or not isinstance(features,list) or len(features)!=4:
        raise ValueError('Exactly four test camps and four zones are required')
    if any(not isinstance(c,dict) for c in camps) or {c.get('id') for c in camps} != set(DIRECTIONS):
        raise ValueError('Camp IDs must be the four compass directions')
    zones={}
    for f in features:
        if not isinstance(f,dict) or f.get('type')!='Feature':
            raise ValueError('Invalid zone feature')
        props=f.get('properties',{})
        zone_id=props.get('id')
        if zone_id not in expected or zone_id in zones or props.get('camp_id') != zone_id:
            raise ValueError('Zone identity mismatch')
        geometry=f.get('geometry',{})
        if geometry.get('type')!='Polygon' or geometry.get('coordinates') != [expected[zone_id]]:
            raise ValueError('Zone partition has a gap, overlap or altered boundary')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', props.get('color','')):
            raise ValueError('Invalid zone colour')
        if not point_inside(props.get('label_position',[]), expected[zone_id]):
            raise ValueError('Zone label must be inside its zone')
        zones[zone_id]=f
    extent_area=(east-west)*(north-south)
    if not math.isclose(sum(ring_area(r) for r in expected.values()), extent_area, rel_tol=1e-9):
        raise ValueError('Zones do not cover demo extent exactly')
    for camp in camps:
        if camp.get('zone_id') != camp['id'] or camp.get('test_only') is not True:
            raise ValueError('Camp assignment mismatch')
        point=camp.get('coordinates')
        if not isinstance(point,list) or len(point)!=2 or not all(number(v) for v in point) or not point_inside(point,expected[camp['id']]):
            raise ValueError('Camp host is outside its assigned zone')
        if any(camp.get(k) is not None for k in ('physical_perimeter','capacity','stock','ledger_origin')):
            raise ValueError('Do not invent footprints, capacity, stock or ledger bindings')
        if camp.get('permission_status') != 'not_requested' or camp.get('coordinate_status') != 'provisional':
            raise ValueError('Test host approval/location status must remain explicit')
        for key in ('location_source','identity_source'):
            url=urlsplit(camp.get(key,''))
            if url.scheme!='https' or not url.netloc or url.username or url.password:
                raise ValueError('Source must be a public HTTPS URL')
    return data


def load_test_geography():
    return validate_test_geography(json.loads(FIXTURE.read_text(encoding='utf-8')))

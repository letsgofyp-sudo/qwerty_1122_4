import requests

from ..constants import orsApiKey as api_key


def _decode_ors_polyline(encoded):
    """Decode an OpenRouteService / Google-style encoded polyline string.

    Returns list of (lat, lng) tuples. ORS uses 1e5 precision for encodedpolyline.
    """
    if not encoded:
        return []

    coords = []
    index = 0
    lat = 0
    lng = 0

    length = len(encoded)

    while index < length:
        result = 0
        shift = 0

        while True:
            if index >= length:
                break
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break

        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        result = 0
        shift = 0

        while True:
            if index >= length:
                break
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break

        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coords.append((lat / 1e5, lng / 1e5))

    return coords


def fetch_route_geometry_osm(points):
    """Fetch dense road-following geometry from an OpenStreetMap-based directions API.

    :param points: list of (lat, lng) tuples.
    :return: list of {"lat": float, "lng": float} along the road, or [] on failure.
    """
    try:
        print("[ROUTE_GEOMETRY][OSM] points:", points)
        if not points or len(points) < 2:
            print("[ROUTE_GEOMETRY][OSM] not enough points")
            return []

        # OpenRouteService-style API expects [lng, lat]
        coords = [[float(lng), float(lat)] for (lat, lng) in points]
        print("[ROUTE_GEOMETRY][OSM] coords for API:", coords)

        if not api_key:
            print("[ROUTE_GEOMETRY][OSM] missing api_key")
            return []

        url = "https://api.openrouteservice.org/v2/directions/driving-car"
        headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }

        # Request directions; newer ORS versions may return encoded polyline by default
        body = {
            "coordinates": coords,
            "instructions": False,
            "geometry_simplify": False,
        }

        print("[ROUTE_GEOMETRY][OSM] POST", url)
        resp = requests.post(url, json=body, headers=headers, timeout=15)
        print("[ROUTE_GEOMETRY][OSM] status", resp.status_code)
        print("[ROUTE_GEOMETRY][OSM] body", resp.text[:400])
        resp.raise_for_status()
        data = resp.json()

        # ORS v2 directions: geometry is under routes[0]["geometry"]
        routes = data.get("routes") or []
        if not routes:
            print("[ROUTE_GEOMETRY][OSM] no routes in response")
            return []

        geom = routes[0].get("geometry")

        # Case 1: GeoJSON LineString (some ORS configs / older versions)
        if isinstance(geom, dict) and geom.get("type") == "LineString":
            line = []
            for lng, lat in geom.get("coordinates", []):
                try:
                    line.append({"lat": float(lat), "lng": float(lng)})
                except Exception:
                    continue
            print("[ROUTE_GEOMETRY][OSM] extracted points from GeoJSON:", len(line))
            return line

        # Case 2: encoded polyline string (default in newer ORS versions)
        if isinstance(geom, str):
            decoded = _decode_ors_polyline(geom)
            line = []
            for lat, lng in decoded:
                try:
                    line.append({"lat": float(lat), "lng": float(lng)})
                except Exception:
                    continue
            print("[ROUTE_GEOMETRY][OSM] extracted points from encoded polyline:", len(line))
            return line

        print("[ROUTE_GEOMETRY][OSM] unexpected geometry format:", type(geom), geom)
        return []
    except Exception as e:
        print("[ROUTE_GEOMETRY][OSM] failed to fetch geometry:", e)
        return []


def update_route_geometry_from_stops(route, normalized_stops):
    """Given a Route instance and a list of normalized stops, fetch and update route_geometry.

    normalized_stops: list of dicts with 'lat' and 'lng'.
    """
    try:
        waypoints = []
        for s in normalized_stops:
            lat = s.get("lat")
            lng = s.get("lng")
            if lat is not None and lng is not None:
                waypoints.append((lat, lng))

        geometry = fetch_route_geometry_osm(waypoints)
        if geometry:
            route.route_geometry = geometry
        route.save()
    except Exception as exc:
        print("[ROUTE_GEOMETRY][ROUTE_UPDATE] failed to update route geometry:", exc)
